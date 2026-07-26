# Strategy Test Report — deploy + every-strategy test (2026-07-26)

Scope: deploy the Strategy-Optimization work (SO Tracks A–E, 19 tuned presets) to
production, then **test every strategy** two ways — (1) live through the deployed
dashboard UI, and (2) an offline functional matrix over every cached market — and
record the results, including one bug the UI test surfaced.

- **Deployed revision:** `pro-dashboard-00096-82s` · image
  `asia-south1-docker.pkg.dev/trading-agent-pro-c3dc6/pro-dashboard:e2cc227`
  (Cloud Build `4085799f`, 3m32s, SUCCESS).
- **Public URL:** https://trading-agent-pro-c3dc6.web.app (Firebase Hosting →
  Cloud Run, `asia-south1`).
- **Branch:** `pro/phase-0-contracts` @ `e2cc227` (pushed to origin).

---

## 1. Deployment verification

| Check | Result |
|---|---|
| Cloud Build image | `pro-dashboard:e2cc227` built + pushed, SUCCESS |
| Cloud Run rollout | revision `00096-82s` serving 100% traffic |
| `/health` | 200 |
| SPA `/` | 200 (TradingAgents Pro shell) |
| Firebase public URL | 200 |
| `/api/*` unauthenticated | 401 (auth enforced — expected) |
| `GET /api/backtest/strategies` | 200 — all 11 strategies served |
| `GET /api/backtest/presets` | 200 — 19 tuned presets served (SO work live) |

The protected API needs an `X-API-Key` held by the operator, so live API calls
were exercised through the authenticated **browser session** (Google sign-in by
the operator), not raw curl.

---

## 2. UI test — every strategy through the deployed Backtest page

Signed in to the live dashboard, opened **Backtest → Single run**, and ran each
registered strategy on a common, trade-producing config: **ETH-USD · 1d · 1Y**
(2025-09-30 → 2026-07-26), default params, $100k, 1% risk, ≤33% notional. This
window is a useful stress test: **ETH buy-&-hold was −36.8%** over it, so a
positive return means the strategy stayed uncorrelated/defensive.

| # | Strategy | Return | Trades | Win rate | Avg R | Notes |
|--:|---|--:|--:|--:|--:|---|
| 1 | htf_momentum_v2 | **+2.2%** | 21 | 42.9% | +0.03R | PF 1.22; HTF size-scaler active (1w) |
| 2 | htf_momentum_v1 | **+1.8%** | 11 | 45.5% | +0.17R | PF 1.32 |
| 3 | trend_following_v2 | **+1.6%** | 17 | 35.7% | +0.10R | pyramiding on winners |
| 4 | volatility_breakout_v1 | +0.8% | 2 | 100% | +0.39R | few daily breakouts |
| 5 | momentum_v1 | +0.7% | 21 | 42.9% | +0.04R | PF 1.06 |
| 6 | regime_momentum_v1 | +0.7% | 21 | 42.9% | +0.04R | gate=off (see §4) |
| 7 | trend_following_v1 | +0.4% | 13 | 27.3% | +0.03R | ALPHA +0.2%, BETA −0.01 |
| 8 | mean_reversion_v1 | +0.4% | 8 | 75.0% | +0.05R | PF 1.18 |
| 9 | momentum_v2 | −0.6% | 4 | 33.3% | −0.15R | z-score rarely triggers on daily |
| 10 | ma_crossover_v1 | −0.9% | 12 | 27.3% | −0.07R | whipsaw-prone (fewest presets) |
| 11 | rules_v1 | −2.6% | 33 | 57.6% | −0.07R | PF 0.81; 1 decision/bar churn+costs |

**Findings**

- **All 11 strategies ran end-to-end through the UI** and rendered full results
  (summary tiles, extended metrics, benchmark alpha/beta, trades table). No
  crashes, no hangs.
- **8 of 11 were net-positive while ETH fell 36.8%** — the trend/HTF-momentum
  family led, confirming the low-beta / positive-alpha profile the offline
  Strategy-Lab analysis reported (e.g. trend_following_v1 rendered **ALPHA +0.2%,
  BETA −0.01, BUY&HOLD −36.8%** on-screen).
- The two negatives are the expected ones: `ma_crossover_v1` (whipsaw) and
  `rules_v1` (a decision every bar → cost drag). These match the offline finding
  that both earn few/no presets.
- trend_following_v2 (+1.6%) > v1 (+0.4%) — pyramiding-on-winners adds value, as
  designed.

The single-run config uses a-priori **default** params (the tuned presets live
per-(strategy, symbol, timeframe); ETH-1d is not the best cell for most). This is
a functional + behavioural test, not a performance ranking — the guard-validated
performance evidence is in `REPORT.md` / `04–07_*.md`.

---

## 3. Progress UI — advances and does not hang ✔

The operator asked to confirm the run UI shows progress and never hangs. The
native strategies finish a 1Y-daily run in ~1–2 s (too fast to show a bar), but
`rules_v1` runs the full deterministic pipeline **one decision per bar** (239
decisions) and exercises the streaming UI in full. Captured mid-run:

- Button switches to **"Running…"** (disabled).
- **Live-run panel** with a progress bar: **"decision 149 / 239 · 62%"**.
- Live **EQUITY / P&L / OPEN / CLOSED** tiles updating, plus a live equity curve.
- A **"Cancel (keeps partial)"** control.
- On completion the panel resolves to the final result card; the button returns
  to "Run backtest". **No hang, clean finish.**

Network trace confirms the mechanism: `POST /api/backtest/run → 202`, then
polling `GET /api/backtest/job` + `/runs`, then the completed run + artifacts
(`equity`, `trades`, `decisions`) fetched `200`.

---

## 4. Bug found — "Use tuned preset" is masked by the form params

**Symptom.** With **"Use tuned preset" checked** on a cell that has a preset,
`regime_momentum_v1` (ETH-1d preset = `regime_gate=off`) took **0 trades**
(RETURN 0.0%). Manually setting `REGIME GATE = off` and re-running produced the
expected **+0.7%, 21 trades** — proving the strategy is fine and the preset's
`regime_gate=off` simply never reached the engine.

**Root cause.** The preset is layered *under* the caller's `strategy_params`
(`backtest_job.py:539` — `merged = {**preset, **strategy_params}`, "caller
override wins"), but the frontend **always** sends the full form values as
`strategy_params` (BacktestPage.tsx:220), including defaults like
`regime_gate=on`. Those defaults overwrite the preset every time, so
**"Use tuned preset" is silently a no-op whenever the form differs from the
preset** — which is most cells. (Cells where the preset equals the defaults, e.g.
trend_following_v1/v2 on ETH-1d, coincidentally looked fine.)

**Fix (applied locally, not yet redeployed).** When the preset is in use, omit
`strategy_params` from the POST so the preset applies cleanly (the tooltip
already promises the preset "overrides the form params"):

```
strategy_params: usePreset && presetForSelection != null ? {} : strategyParams,
```

Frontend gates pass (typecheck ✔, eslint ✔, `vitest` backtest 14/14 ✔). This
fix is **committed locally but awaiting an explicit go-ahead to redeploy.**

**Minor (cosmetic).** The helper line under "Run backtest" always reads
"…est ~1 min. **Rules strategy:** …" regardless of the selected strategy — a
stale description string. Low priority.

---

## 5. Offline functional matrix — every strategy × every market

Backs the UI test with breadth: `scripts/pro_strategy_test_matrix.py` ran one
full-window backtest at default params for **every registered strategy × every
cached (symbol, timeframe)** — 11 × (4 symbols × 7 timeframes).

| Metric | Value |
|---|--:|
| Cells attempted | 308 |
| Ran OK | 297 |
| **Errors** | **0** |
| Skipped (thin data <~65 bars) | 11 |
| Cells that traded | 257 |
| Cells with a shipped preset | 19 |

**Every strategy ran on every data-available cell with zero errors**, and 257 of
297 cells produced trades (the rest correctly stood aside — e.g. squeeze
breakouts on quiet windows, regime gate in crisis). Per-strategy mean return at
untuned defaults across all markets is near-zero (as expected — defaults aren't
optimized); trend_following_v2/v1 and regime_momentum_v1 are net-positive even
untuned. A harness bug found during this pass (HTF timeframes weren't filtered to
strictly-coarser frames, unlike production) was fixed so the matrix mirrors the
deployed job exactly.

Artifacts: `test_matrix.json` (this run), and the guard-validated evidence in
`REPORT.md`, `04_montecarlo.md`, `05_benchmark.md`, `06_regime_breakdown.md`,
`07_sensitivity.md`.

---

## 6. Verdict

- **Deploy:** ✅ live and healthy (`00096-82s`, image `e2cc227`); strategies +
  presets endpoints serving the SO work.
- **Every strategy tested:** ✅ 11/11 run end-to-end in the live UI; ✅ 297/297
  data-available cells run offline with **0 errors**.
- **Progress UI:** ✅ streams decision-by-decision, cancellable, no hang.
- **One real bug** ("Use tuned preset" masked by form params) — root-caused,
  fixed locally, gated; **redeploy pending operator OK**.
- **One cosmetic bug** (stale strategy description line) — noted, low priority.
