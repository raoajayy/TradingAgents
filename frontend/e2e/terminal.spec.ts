import { expect, test, type Locator, type Page } from "@playwright/test";


const TOKEN = "e2e-token";

/** The Trade page's primary chart (TradingView widget wrapper). */
const mainChart = (page: Page): Locator =>
  page.getByTestId("main-chart-tile").getByTestId("tv-chart");

/** The TV library renders inside a same-origin iframe; its presence is
 * the "chart is up" signal (the old canvas assertions were LWC-specific). */
const chartFrame = (page: Page): Locator => mainChart(page).locator("iframe");

async function unlock(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByTestId("token-input").fill(TOKEN);
  await page.getByTestId("token-submit").click();
  await expect(page.getByTestId("conn-state")).toBeVisible({ timeout: 15_000 });
}

test.describe("auth", () => {
  test("token gate: wrong token stays locked, right token unlocks", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("token-input").fill("wrong");
    await page.getByTestId("token-submit").click();
    await expect(page.getByTestId("token-input")).toBeVisible();
    await page.getByTestId("token-input").fill(TOKEN);
    await page.getByTestId("token-submit").click();
    await expect(page.getByTestId("decision-card")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("api rejects unauthenticated requests", async ({ request }) => {
    const denied = await request.get("/api/overview");
    expect(denied.status()).toBe(401);
    const ok = await request.get("/api/overview", {
      headers: { "X-API-Key": TOKEN },
    });
    expect(ok.status()).toBe(200);
  });
});

test.describe("terminal", () => {
  test.beforeEach(async ({ page }) => unlock(page));

  test("home answers the 5-second questions", async ({ page }) => {
    // safe? — risk badge; AI stance? — decision card; P&L? — snapshot
    await expect(page.getByTestId("risk-badge")).toContainText(/risk OK|KILL|BREAKER|monitor/);
    await expect(page.getByTestId("decision-card")).toContainText("BUY");
    await expect(page.getByTestId("decision-card")).toContainText("confidence");
    await expect(page.getByTestId("invalidation")).toBeVisible();
    // trust-first hero: the strongest objection (or an honest no-dissent
    // note) is surfaced at the moment of decision, not hidden (fix #1)
    await expect(page.getByTestId("dissent")).toBeVisible();
    await expect(page.getByText("P&L", { exact: false }).first()).toBeVisible();
    // Home reimagining: what am I holding? — open positions surfaced on Home
    await expect(page.getByTestId("exposure-summary").first()).toBeVisible();
    await expect(page.getByTestId("position-unrealized").first()).toBeVisible();
    // the system-health banner is conditional on degraded feeds: present iff
    // there are missing feeds (states the outage in one line instead of noise)
    const degraded = await page.evaluate(async () => {
      const r = await fetch("/api/overview", { credentials: "include" });
      const d = (await r.json()) as { missing_feeds?: string[] };
      return (d.missing_feeds ?? []).length > 0;
    });
    if (degraded) {
      await expect(page.getByTestId("system-health-banner")).toBeVisible();
    } else {
      await expect(page.getByTestId("system-health-banner")).toHaveCount(0);
    }
  });

  test("decision center shows debate, gates, and leaderboard", async ({
    page,
  }) => {
    await page.goto("/decisions");
    await expect(page.getByTestId("gate-waterfall")).toBeVisible();
    await expect(page.getByTestId("debate-timeline")).toBeVisible();
    await expect(page.getByTestId("agent-leaderboard")).toBeVisible();
    await expect(page.getByTestId("debate-timeline")).toContainText("judge");
  });

  test("decision pipeline board renders and inspects stages", async ({
    page,
  }) => {
    await page.goto("/decisions");
    const board = page.getByTestId("decision-pipeline");
    await expect(board).toBeVisible();
    // default selection is the judge with its real verdict
    await expect(page.getByTestId("pipeline-detail")).toContainText("Judge — Aldous");
    // clicking a station swaps the detail bar to that stage's output
    await page.getByTestId("pipeline-station-risk_gate").click();
    await expect(page.getByTestId("pipeline-detail")).toContainText("Risk gate — Imara");
    await expect(page.getByTestId("pipeline-replay")).toBeEnabled();
  });

  test("run pinning survives navigation", async ({ page }) => {
    await page.goto("/decisions");
    const first = page.getByTestId("run-rail").locator("button").first();
    await first.click();
    await expect(page).toHaveURL(/\/decisions\/[0-9a-f-]+/);
    const url = page.url();
    await page.reload();
    expect(page.url()).toBe(url);
    await expect(page.getByTestId("debate-timeline")).toBeVisible();
  });

  test("workspace renders a chart for gold", async ({ page }) => {
    await page.goto("/trade/XAUUSD");
    await expect(chartFrame(page)).toBeVisible({ timeout: 20_000 });
  });

  test("chart symbol dropdown switches between BTC and gold", async ({
    page,
  }) => {
    await page.goto("/trade");
    await expect(page.getByTestId("symbol-select")).toHaveValue("BTC-USD");
    await page.getByTestId("symbol-select").selectOption("XAUUSD");
    await expect(page).toHaveURL(/\/trade\/XAUUSD/);
    await expect(chartFrame(page)).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("symbol-select").selectOption("BTC-USD");
    await expect(page).toHaveURL(/\/trade\/BTC-USD/);
  });

  // P2-10: FX majors surface wherever tradeable symbols are enumerated —
  // the dropdown is server-driven via /api/symbols, so EURUSD/USDJPY
  // appearing here proves the whole chain (registry → API → UI)
  test("FX pairs are tradeable: EURUSD in the dropdown and charted", async ({
    page,
  }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(String(err)));

    await page.goto("/trade");
    const select = page.getByTestId("symbol-select");
    // wait for the server-driven list (the pre-fetch fallback has no FX)
    await expect(select.locator('option[value="EURUSD"]')).toHaveCount(1, {
      timeout: 15_000,
    });
    const values = await select
      .locator("option")
      .evaluateAll((opts) => opts.map((o) => o.getAttribute("value")));
    expect(values.length).toBeGreaterThanOrEqual(4);
    expect(values).toContain("EURUSD");
    expect(values).toContain("USDJPY");

    await select.selectOption("EURUSD");
    await expect(page).toHaveURL(/\/trade\/EURUSD/);
    // the TV datafeed resolves EURUSD via its pyth_symbol and the proxy
    // serves bars (synthetic in e2e), so the widget mounts its iframe
    await expect(chartFrame(page)).toBeVisible({ timeout: 20_000 });
    expect(pageErrors).toEqual([]);
  });

  // regression: unmounting a page disposed the chart instance before
  // dependent effect cleanups ran and the workspace crashed to its error
  // boundary on the return visit. With TV this doubles as the widget
  // remove()-on-unmount check.
  test("workspace survives trade → portfolio → trade SPA navigation", async ({
    page,
    isMobile,
  }) => {
    test.skip(isMobile, "keyboard chords are desktop UX");
    const pageErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(String(err)));

    // default symbol is BTC-USD (no seeded chart data); x toggles to XAUUSD
    await page.keyboard.press("x");
    await page.keyboard.press("g");
    await page.keyboard.press("t");
    await expect(page).toHaveURL(/\/trade\/XAUUSD/);
    const canvas = chartFrame(page);
    await expect(canvas).toBeVisible({ timeout: 20_000 });

    // portfolio must mount (and later unmount) its EquityCurve chart —
    // that unmount is what used to throw
    await page.keyboard.press("g");
    await page.keyboard.press("p");
    await expect(page.getByTestId("equity-curve")).toBeVisible({
      timeout: 20_000,
    });

    await page.keyboard.press("g");
    await page.keyboard.press("t");
    await expect(canvas).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/failed to render/)).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });

  test("portfolio links trades to reasoning and exports CSV", async ({
    page,
  }) => {
    await page.goto("/portfolio");
    await expect(page.getByTestId("trades-table")).toBeVisible();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: /CSV/ }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/journal-\d+\.csv/);
  });

  test("command palette navigates", async ({ page, isMobile }) => {
    test.skip(isMobile, "palette is keyboard-driven");
    await page.keyboard.press("ControlOrMeta+k");
    await page.getByPlaceholder(/Search commands/).fill("Portfolio");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/portfolio/);
  });

  test("keyboard chords: g d goes to decisions", async ({ page, isMobile }) => {
    test.skip(isMobile, "keyboard chords are desktop UX");
    await page.keyboard.press("g");
    await page.keyboard.press("d");
    await expect(page).toHaveURL(/\/decisions/);
  });

  test("intel shows honest feed coverage", async ({ page }) => {
    await page.goto("/intel");
    // first load may take the full vendor deadline (~10s) when egress
    // to feeds is blocked — the page must still render, with gaps disclosed
    await expect(page.getByText("Not subscribed")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Coinglass")).toBeVisible();
  });

  test("sse stream is reachable with the session cookie", async ({ page }) => {
    const status = await page.evaluate(async () => {
      // fetch resolves on response headers; cancel the infinite body
      const response = await fetch("/api/stream", {
        headers: { Accept: "text/event-stream" },
      });
      const type = response.headers.get("content-type") ?? "";
      response.body?.cancel();
      return { ok: response.ok, type };
    });
    expect(status.ok).toBe(true);
    expect(status.type).toContain("text/event-stream");
  });

  test("the bell reflects the dashboard's alert feed", async ({ page }) => {
    // these were two unbridged stores: the Alerts panel derives entries
    // from run records and persists nothing, the bell reads the persisted
    // notification ring. The bell read "No notifications yet" next to a
    // full Alerts panel.
    await page.goto("/");
    const alerts = await page.evaluate(async () => {
      const r = await fetch("/api/alerts", { credentials: "include" });
      return (await r.json()) as { alerts: { text: string }[] };
    });
    expect(alerts.alerts.length).toBeGreaterThan(0);

    await page.getByRole("button", { name: /^Notifications/ }).click();
    const center = page.getByTestId("notification-center");
    await expect(center).toBeVisible();
    for (const alert of alerts.alerts) {
      await expect(center).toContainText(alert.text);
    }
  });
});

test.describe("gaps v8", () => {
  test.beforeEach(async ({ page }) => unlock(page));

  test("decision board + per-symbol ticket survive symbol mismatch", async ({
    page,
  }) => {
    // Home: board with a hero and the second symbol's compact slot (G1)
    await expect(page.getByTestId("decision-board")).toBeVisible();
    // the ticket UI lives on /decisions now (Trade is chart-only); it
    // shows the demo symbol's decision, and the per-symbol endpoint answers
    // for the OTHER symbol without lying
    await page.goto("/decisions");
    await expect(
      page.getByTestId("decision-card").or(
        page.getByText("No decision yet for XAUUSD")),
    ).toBeVisible({ timeout: 10_000 });

    const gold = await page.request.get(
      "/api/recommendation/latest?symbol=XAUUSD",
      { headers: { "X-API-Key": TOKEN } });
    expect(gold.status()).toBe(200);
  });

  test("open positions expose entry/mark/unrealized honestly", async ({
    page,
  }) => {
    const status = await page.request.get("/api/status",
      { headers: { "X-API-Key": TOKEN } });
    const body = await status.json();
    if ((body.open_positions ?? []).length > 0) {
      const pos = body.open_positions[0];
      expect(pos).toHaveProperty("entry_price");
      expect(pos).toHaveProperty("mark_source");
      // open positions live on Portfolio now (Trade is chart-only)
      await page.goto("/portfolio");
      await expect(
        page.getByTestId("position-unrealized").first(),
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test("price alert: create from the portfolio panel, list, delete", async ({
    page,
    isMobile,
  }) => {
    test.skip(isMobile, "alert panel is a desktop panel");
    // price alerts + position plan relocated to Portfolio when Trade
    // became chart-only
    await page.goto("/portfolio");
    const panel = page.getByTestId("price-alerts");
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("position-plan")).toBeVisible();
    await page.getByTestId("price-alert-level").fill("4321.5");
    await page.getByTestId("price-alert-create").click();
    await expect(panel.getByText("4,321.50")).toBeVisible({ timeout: 5_000 });
    // notify-only invariant: creating an alert must not touch the book
    const status = await page.request.get("/api/status",
      { headers: { "X-API-Key": TOKEN } });
    expect((await status.json()).trading_halted).not.toBe(true);
    await panel.getByRole("button", { name: /Delete alert/ }).click();
    await expect(panel.getByText("4,321.50")).toBeHidden({ timeout: 5_000 });
  });

  test("parameterized indicator id resolves server-side", async ({ page }) => {
    const series = await page.request.get(
      "/api/bars/indicators?symbol=XAUUSD&timeframe=1d&names=EMA_21",
      { headers: { "X-API-Key": TOKEN } });
    expect(series.status()).toBe(200);
    expect((await series.json()).EMA_21.params.period).toBe(21);
    const bad = await page.request.get(
      "/api/bars/indicators?symbol=XAUUSD&timeframe=1d&names=EMA_9999",
      { headers: { "X-API-Key": TOKEN } });
    expect(bad.status()).toBe(422);
  });

  test("intel calendar defaults to majors with countdown", async ({ page }) => {
    await page.goto("/intel");
    const toggle = page.getByTestId("calendar-majors-toggle");
    await expect(toggle).toBeVisible({ timeout: 10_000 });
    await expect(toggle).toHaveText(/major only/);
  });

});

test.describe("v2 features", () => {
  test.beforeEach(async ({ page }) => unlock(page));

  // replay + the custom indicator picker were retired with the TradingView
  // migration: TV's own toolbar owns indicators/drawings/replay now

  // the external multi-chart grid was replaced by TV's NATIVE layout
  // switching (header layout toggle, Trading Platform edition). The layout
  // button lives inside the TV iframe; the wiring assertion is that the
  // single TV chart mounts and the old grid chrome is gone.
  test("TV chart owns multi-chart layouts (no external grid chrome)", async ({
    page,
  }) => {
    await page.goto("/trade/XAUUSD");
    await expect(chartFrame(page)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("grid-switch")).toHaveCount(0);
    await expect(page.getByTestId("grid-chart-cell")).toHaveCount(0);
  });

  test("watchlist add and remove persists", async ({ page, isMobile }) => {
    // per-project symbol: both projects share one server + prefs store
    const symbol = isMobile ? "SILVER" : "DXY";
    await page.goto("/");
    const panel = page.getByTestId("watchlist-panel");
    await expect(panel).toBeVisible();
    await panel.getByLabel("Add symbol to watchlist").fill(symbol);
    await panel.getByRole("button", { name: /Add/ }).click();
    await expect(panel.getByRole("link", { name: symbol })).toBeVisible();
    await page.reload();
    await expect(
      page.getByTestId("watchlist-panel").getByRole("link", { name: symbol }),
    ).toBeVisible({ timeout: 15_000 });
    await page
      .getByTestId("watchlist-panel")
      .getByLabel(`remove ${symbol}`)
      .click();
    await expect(
      page.getByTestId("watchlist-panel").getByRole("link", { name: symbol }),
    ).toHaveCount(0);
  });

  test("correlation matrix renders or discloses gaps", async ({ page }) => {
    await page.goto("/intel");
    // either a matrix with data or an honest not-enough-data state
    await expect(
      page
        .getByTestId("correlation-matrix")
        .or(page.getByText("Not enough overlapping data")),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("saved views round-trip through palette and settings", async ({
    page,
    isMobile,
  }) => {
    // desktop-only also avoids racing the shared prefs store
    test.skip(isMobile, "palette is keyboard-driven");
    await page.goto("/portfolio");
    await page.keyboard.press("ControlOrMeta+k");
    await page.getByPlaceholder(/Search commands/).fill("Save current view");
    await page.keyboard.press("Enter");
    await page.goto("/settings");
    const views = page.getByTestId("saved-views");
    await expect(views).toContainText("portfolio");
    await views.getByRole("button").first().click();
    await expect(page.getByTestId("saved-views")).toHaveCount(0);
  });
});

test.describe("v7 on-demand pipeline", () => {
  test("run dialog triggers a run that lands in the rail", async ({
    page,
    isMobile,
  }) => {
    test.skip(isMobile, "run rail is a desktop surface");
    await unlock(page);
    await page.goto("/decisions");

    const rail = page.getByTestId("run-rail");
    await expect(rail.locator("li").first()).toBeVisible();
    const before = await rail.locator("li").count();

    // the Run button lives on the pipeline board header now (the Runs
    // card's duplicate was removed in the horizontal-board parity pass)
    await page.getByTestId("pipeline-run").click();
    await expect(page.getByTestId("pipeline-start")).toBeVisible();
    await expect(page.getByText(/\$0\.10/)).toBeVisible(); // honest cost note
    await page.getByTestId("pipeline-start").click();

    // fake-LLM run completes fast; SSE `run` refetches the rail
    await expect(async () => {
      expect(await rail.locator("li").count()).toBe(before + 1);
    }).toPass({ timeout: 20_000 });

    // newest run carries its timeframe badge
    await page.keyboard.press("Escape");
    await expect(rail.locator("li").first()).toContainText("1d");
  });
});

test.describe("v-golive Phase 4 arming", () => {
  test("paper mode shows no live banner or flatten control", async ({
    page,
  }) => {
    // safety-critical direction: the live-armed banner and its one
    // execution write must NEVER appear while every pair is paper (the
    // demo server's default state).
    await unlock(page);
    await expect(page.getByTestId("risk-badge")).toBeVisible();
    await expect(page.getByTestId("arming-banner")).toHaveCount(0);
    await expect(page.getByTestId("emergency-flatten")).toHaveCount(0);
  });
});

test.describe("v-golive Phase 5 ops", () => {
  test("health endpoint is reachable and unauthenticated", async ({
    request,
  }) => {
    // /health/live is auth-exempt like /healthz; 200 or 503 (a JSON
    // verdict) both count as reachable — never a 401.
    const resp = await request.get("/health/live");
    expect([200, 503]).toContain(resp.status());
    const body = await resp.json();
    expect(body).toHaveProperty("ok");
    expect(body).toHaveProperty("checks");
  });
});

test.describe("chart phase 1: AI decision marks", () => {
  // The AI's decision history reaches the chart through the TV datafeed's
  // getMarks (lib/tv/datafeed.ts) — the widget requests
  // /api/chart/annotations for the active symbol and paints B/S/H marks.
  // Mark pixels live inside the TV iframe (third-party canvas), so the
  // contract asserted here is the wiring: the widget mounts AND issues the
  // marks request for the right symbol.
  test("TV chart requests AI annotations for the active symbol", async ({
    page,
  }) => {
    await unlock(page);
    const marksRequest = page.waitForRequest(
      (req) => req.url().includes("/api/chart/annotations?symbol=XAUUSD"),
      { timeout: 20_000 },
    );
    await page.goto("/trade/XAUUSD");
    await expect(chartFrame(page)).toBeVisible({ timeout: 20_000 });
    await marksRequest;
  });
});

test.describe("P2-09 evidence level chips", () => {
  test.beforeEach(async ({ page }) => unlock(page));

  test("evidence level chip navigates to Trade with the level badge", async ({
    page,
  }) => {
    await page.goto("/decisions");
    await expect(page.getByTestId("evidence-panel")).toBeVisible({
      timeout: 15_000,
    });
    // level-bearing refs (LAST_CLOSE etc.) render as clickable chips;
    // non-numeric refs keep the inert tooltip chip
    const chip = page.getByTestId("level-chip").first();
    await expect(chip).toBeVisible();
    const label = (await chip.innerText()).trim();
    await chip.click();

    // lands on the run's Trade page carrying the level in the URL; the
    // on-chart price line retired with the custom chart — the labeled
    // badge is the surviving surface, and its × clears the URL params
    await expect(page).toHaveURL(/\/trade\/[A-Z0-9-]+\?.*label=/);
    await expect(chartFrame(page)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("level-badge")).toContainText(label);
    await page.getByTestId("level-clear").click();
    await expect(page).not.toHaveURL(/label=/);
    await expect(page.getByTestId("level-badge")).toHaveCount(0);
  });
});

test.describe("backtesting", () => {
  test.beforeEach(async ({ page }) => {
    await unlock(page);
    // one job at a time server-side: a previous spec's run may still be
    // draining — starting a new one would 409 and flake the suite
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get("/api/backtest/job", {
                headers: { "X-API-Key": TOKEN },
              })
            ).json()
          ).status,
        { timeout: 30_000 },
      )
      .not.toBe("running");
  });

  test("configure, view a saved run, and run a live deterministic backtest", async ({
    page,
  }) => {
    await page.goto("/backtest");
    await expect(page.getByTestId("backtest-page")).toBeVisible();
    // controls render, incl. the pre-run plan (decisions + time estimate)
    await expect(page.getByTestId("backtest-asset")).toBeVisible();
    await expect(page.getByTestId("backtest-run")).toBeVisible();
    await expect(page.getByTestId("backtest-plan")).toContainText("decisions");

    // the demo seeds one auto-archived run → Saved Runs + a result view
    await expect(page.getByTestId("backtest-saved-runs")).toContainText("XAUUSD");
    await expect(page.getByText(/Saved run —/)).toBeVisible();

    // run a fresh deterministic backtest (synthetic bars in the demo → offline)
    await page.getByTestId("backtest-asset").selectOption("BTC-USD");
    await page.getByTestId("backtest-run").click();
    // progress/live PnL appears, then a completed result panel
    await expect(
      page.getByTestId("backtest-pnl").or(page.getByText(/Result —/)),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/Result — BTC-USD/)).toBeVisible({ timeout: 30_000 });
    // full-density provenance is disclosed on the result
    await expect(page.getByTestId("backtest-provenance")).toContainText(
      "full density",
    );
    // the completed run is auto-archived alongside the seed (earlier specs
    // on the shared server may have archived their own BTC runs — any match)
    await expect(
      page.getByTestId("backtest-saved-runs").getByText("BTC-USD").first(),
    ).toBeVisible();
  });

  test("cancel keeps a labeled partial and delete removes a run", async ({
    page,
  }) => {
    await page.goto("/backtest");
    // a longer run (30D at 1h) leaves time to cancel mid-flight
    await page.getByTestId("backtest-asset").selectOption("BTC-USD");
    await page.getByTestId("backtest-duration").getByText("30D").click();
    await page.getByTestId("backtest-run").click();
    await expect(page.getByTestId("backtest-cancel")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("backtest-cancel").click();
    // the partial is saved and labeled
    await expect(
      page.getByTestId("backtest-saved-runs").getByText("cancelled"),
    ).toBeVisible({ timeout: 30_000 });

    // delete the cancelled run; its row disappears
    const row = page
      .getByTestId("backtest-saved-runs")
      .locator("tr", { hasText: "cancelled" })
      .first();
    await row.locator('button[aria-label^="Delete run"]').click();
    await expect(
      page.getByTestId("backtest-saved-runs").getByText("cancelled"),
    ).toHaveCount(0, { timeout: 10_000 });
  });
});

test.describe("P4-03 marketplace listings", () => {
  test.beforeEach(async ({ page }) => unlock(page));

  test("create draft → publish blocked with visible reasons → delist", async ({
    page,
  }, testInfo) => {
    // the two projects (desktop/mobile) share one demo server + store,
    // so each run works on its own uniquely-titled listing
    const title = `E2E listing ${testInfo.project.name} ${Date.now()}`;

    await page.goto("/listings");
    await expect(page.getByTestId("listings-page")).toBeVisible();

    // the demo authenticates via PRO_DASHBOARD_TOKEN → API-key sessions
    // are operator by design, so the mutation controls must be present
    await expect(page.getByTestId("listing-create")).toBeVisible();

    // create a draft (kind select + title + config JSON)
    await page.getByTestId("listing-kind").selectOption("strategy");
    await page.getByTestId("listing-title").fill(title);
    await page
      .getByTestId("listing-description")
      .fill("created by the e2e suite");
    await page
      .getByTestId("listing-config")
      .fill('{"strategy_id": "sma_cross", "fast": 10, "slow": 30}');
    await page.getByTestId("listing-create").click();

    const row = page.getByTestId("listing-row").filter({ hasText: title });
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("listing-status")).toHaveText("draft");
    await expect(row.getByTestId("listing-calibration")).toHaveText(
      "no graded record",
    );

    // publish is blocked: a fresh draft has no graded record, and the
    // gate's 422 reasons render VERBATIM in the row (the honest-gate UX)
    await row.getByTestId("listing-publish").click();
    await expect(row.getByTestId("listing-publish-failures")).toContainText(
      "no calibration record attached: publishing requires a graded " +
        "record (n_graded, win_rate, brier)",
    );
    await expect(row.getByTestId("listing-status")).toHaveText("draft");

    // delist is soft and requires an explicit confirm click
    await row.getByTestId("listing-delist").click();
    await row.getByTestId("listing-delist-confirm").click();
    await expect(row.getByTestId("listing-status")).toHaveText("delisted");
    // delisted rows stay visible (soft retire) but lose publish/delist
    await expect(row.getByTestId("listing-publish")).toHaveCount(0);
    await expect(row.getByTestId("listing-delist")).toHaveCount(0);
  });
});
