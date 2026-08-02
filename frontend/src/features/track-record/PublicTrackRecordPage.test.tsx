/** P4-02: the public track-record page renders the ledger + honest
 * aggregates from a fixture, and explains itself when no token is given. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PublicTrackRecordPage, {
  type TrackRecordPayload,
} from "./PublicTrackRecordPage";

const FIXTURE: TrackRecordPayload = {
  ledger: [
    {
      run_id: "run-won",
      symbol: "XAUUSD",
      started_at: "2026-07-30T09:00:00+00:00",
      action: "BUY",
      confidence: 72,
      versions: { git_sha: "53f8007deadbeef", prompt_hash: "abcdef0123456789" },
      rejected_at: null,
      open: false,
      outcome: { pnl: 250, won: true, closed_at: "2026-07-31T09:00:00+00:00" },
    },
    {
      run_id: "run-open",
      symbol: "BTC-USD",
      started_at: "2026-08-01T09:00:00+00:00",
      action: "SELL",
      confidence: 61,
      versions: { git_sha: "53f8007deadbeef", prompt_hash: "abcdef0123456789" },
      rejected_at: null,
      open: true,
      outcome: null,
    },
    {
      run_id: "run-rejected",
      symbol: "BTC-USD",
      started_at: "2026-08-02T09:00:00+00:00",
      action: "BUY",
      confidence: 55,
      versions: { git_sha: "53f8007deadbeef", prompt_hash: "abcdef0123456789" },
      rejected_at: "risk_gate",
      open: false,
      outcome: null,
    },
  ],
  aggregates: {
    n_decisions: 3,
    n_rejected: 1,
    n_open: 1,
    n_graded: 1,
    win_rate: 1.0,
    win_rate_n: 1,
    avg_r: 0.8,
    avg_r_n: 1,
    calibration: {
      brier: 0.078,
      n: 1,
      buckets: [
        { confidence_lo: 0, confidence_hi: 20, n: 0, p_win: null },
        { confidence_lo: 20, confidence_hi: 40, n: 0, p_win: null },
        { confidence_lo: 40, confidence_hi: 60, n: 0, p_win: null },
        { confidence_lo: 60, confidence_hi: 80, n: 1, p_win: 1.0 },
        { confidence_lo: 80, confidence_hi: 100, n: 0, p_win: null },
      ],
    },
  },
  methodology: {
    pre_registered: "…",
    graded_post_hoc: "…",
    rejections_included: "…",
    open_positions_shown: "…",
  },
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PublicTrackRecordPage />
    </QueryClientProvider>,
  );
}

describe("PublicTrackRecordPage", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/public/track-record?token=t0ken");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(FIXTURE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("renders the ledger with outcome badges and truncated versions", async () => {
    renderPage();
    expect(screen.getByTestId("track-record")).toBeInTheDocument();

    // the bearer token from ?token= reaches the public endpoint
    const call = vi.mocked(fetch).mock.calls[0]!;
    expect(String(call[0])).toContain("/public/v1/track-record");
    expect(
      (call[1]?.headers as Record<string, string>).Authorization,
    ).toBe("Bearer t0ken");

    const rows = await screen.findAllByTestId("track-record-row");
    expect(rows).toHaveLength(3);
    // graded, open, and rejected decisions all shown — no trimming
    expect(screen.getByText("won +250.00")).toBeInTheDocument();
    expect(screen.getAllByText("open").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("rejected · risk_gate")).toBeInTheDocument();
    // versions truncated: 8 chars of git sha + 8 of prompt hash
    expect(screen.getAllByText("53f8007d · abcdef01")).toHaveLength(3);
  });

  it("shows headline stats with sample sizes and the methodology note", async () => {
    renderPage();
    await screen.findAllByTestId("track-record-row");

    expect(screen.getByText("decisions")).toBeInTheDocument();
    // win rate headline + the 60-80 calibration bucket both read 100.0%
    expect(screen.getAllByText("100.0%")).toHaveLength(2);
    expect(screen.getByText("0.80")).toBeInTheDocument(); // avg R
    expect(screen.getAllByText("n=1").length).toBeGreaterThanOrEqual(2);

    const note = screen.getByTestId("track-record-methodology");
    expect(note).toHaveTextContent(/pre-registered at run time/i);
    expect(note).toHaveTextContent(/graded post-hoc/i);
    expect(note).toHaveTextContent(/rejections included/i);
    expect(note).toHaveTextContent(/open positions shown/i);
  });

  it("explains itself when no token is supplied", async () => {
    window.history.pushState({}, "", "/public/track-record");
    renderPage();
    expect(
      await screen.findByText(/needs a read-only access token/i),
    ).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});
