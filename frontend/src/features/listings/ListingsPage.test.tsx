/** P4-03 slice 2: the listings page renders the catalogue from a fixture,
 * shows the publish gate's 422 failure reasons VERBATIM, validates the
 * config JSON before any request, and renders read-only for viewers. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ListingsPage from "./ListingsPage";
import type { Listing } from "@/lib/api/types";
import { useSessionStore } from "@/stores/session";

const GATE_FAILURES = [
  "n_graded=0 is below the minimum of 30 graded outcomes",
  "brier is missing or null — an unmeasured brier must not be dressed as a (perfect) zero",
];

const FIXTURE: Listing[] = [
  {
    id: "lst-draft",
    owner_email: "op@example.com",
    kind: "strategy",
    title: "Momentum crossover",
    description: "A draft with no graded record yet",
    config: { strategy_id: "sma_cross" },
    calibration: null,
    status: "draft",
    created_at: "2026-07-30T09:00:00Z",
    updated_at: "2026-07-30T09:00:00Z",
  },
  {
    id: "lst-published",
    owner_email: "op@example.com",
    kind: "prompt",
    title: "Macro debate prompt",
    description: "Passed the gate",
    config: { prompt: "…" },
    calibration: { n_graded: 42, brier: 0.18, win_rate: 0.61, win_rate_n: 42 },
    status: "published",
    created_at: "2026-07-20T09:00:00Z",
    updated_at: "2026-07-29T09:00:00Z",
  },
  {
    id: "lst-delisted",
    owner_email: "op@example.com",
    kind: "strategy",
    title: "Retired mean-reverter",
    description: "Soft-retired",
    config: { strategy_id: "meanrev" },
    calibration: { n_graded: 35, brier: 0.22, win_rate: 0.51, win_rate_n: 35 },
    status: "delisted",
    created_at: "2026-06-01T09:00:00Z",
    updated_at: "2026-07-01T09:00:00Z",
  },
];

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function stubFetch() {
  const mock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST" && url.includes("/publish")) {
        // the honest gate: the 422 carries the specific failures
        return json(
          {
            detail: {
              published: false,
              listing_id: "lst-draft",
              failures: GATE_FAILURES,
            },
          },
          422,
        );
      }
      if (method === "GET" && url.includes("/api/listings")) {
        return json({ listings: FIXTURE });
      }
      return json({});
    },
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ListingsPage />
    </QueryClientProvider>,
  );
}

describe("ListingsPage", () => {
  let fetchMock: ReturnType<typeof stubFetch>;

  beforeEach(() => {
    fetchMock = stubFetch();
    useSessionStore.setState({ role: "operator", identity: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useSessionStore.setState({ role: null, identity: null });
  });

  it("renders listings with status badges and calibration summaries", async () => {
    renderPage();
    const rows = await screen.findAllByTestId("listing-row");
    expect(rows).toHaveLength(3);

    const statuses = screen.getAllByTestId("listing-status");
    expect(statuses.map((s) => s.textContent)).toEqual([
      "draft",
      "published",
      "delisted",
    ]);

    // calibration summary: n, brier, and win rate WITH its own n
    expect(
      screen.getByText("n=42 · brier 0.180 · win 61.0% (n=42)"),
    ).toBeInTheDocument();
    // a missing record is stated, never dressed as zeros
    expect(screen.getByText("no graded record")).toBeInTheDocument();
    // the timestamp column is present
    expect(screen.getAllByText(/^updated /)).toHaveLength(3);
  });

  it("shows the publish gate's 422 failure reasons verbatim in the row", async () => {
    renderPage();
    await screen.findAllByTestId("listing-row");

    // only the draft row offers publish
    const publishButtons = screen.getAllByTestId("listing-publish");
    expect(publishButtons).toHaveLength(1);
    fireEvent.click(publishButtons[0]!);

    const failures = await screen.findByTestId("listing-publish-failures");
    for (const reason of GATE_FAILURES) {
      expect(failures).toHaveTextContent(reason); // verbatim, not paraphrased
    }
  });

  it("rejects invalid config JSON in the create form before any request", async () => {
    renderPage();
    await screen.findAllByTestId("listing-row");

    fireEvent.change(screen.getByTestId("listing-title"), {
      target: { value: "Bad config listing" },
    });
    fireEvent.change(screen.getByTestId("listing-config"), {
      target: { value: "{not json" },
    });
    fireEvent.click(screen.getByTestId("listing-create"));

    expect(
      await screen.findByTestId("listing-config-error"),
    ).toHaveTextContent(/not valid JSON/);
    // no POST /api/listings ever left the client
    const posts = fetchMock.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith("/api/listings") && init?.method === "POST",
    );
    expect(posts).toHaveLength(0);
  });

  it("renders read-only for viewer sessions", async () => {
    useSessionStore.setState({ role: "viewer", identity: "v@example.com" });
    renderPage();
    await screen.findAllByTestId("listing-row");

    expect(screen.getByTestId("listings-readonly-note")).toBeInTheDocument();
    expect(screen.queryByTestId("listing-create")).not.toBeInTheDocument();
    expect(screen.queryByTestId("listing-publish")).not.toBeInTheDocument();
    expect(screen.queryByTestId("listing-edit")).not.toBeInTheDocument();
    expect(screen.queryByTestId("listing-delist")).not.toBeInTheDocument();
  });
});
