/** P5-05: the "What changed" panel renders the headline + grouped sections
 * from a fixture, and gives the versions-changed case its own alarm
 * treatment — a different machine is not a different market. */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunDiffPanel } from "@/components/RunDiffPanel";
import { RunDiffSchema, type RunDiff } from "@/lib/api/types";

const BASE: RunDiff = RunDiffSchema.parse({
  diff_format: 1,
  symbol: "XAUUSD",
  earlier: {
    run_id: "gold-1",
    started_at: "2026-08-01T09:00:00+00:00",
    symbol: "XAUUSD",
    timeframe: "1d",
    trigger: "loop",
  },
  later: {
    run_id: "gold-2",
    started_at: "2026-08-01T10:00:00+00:00",
    symbol: "XAUUSD",
    timeframe: "1d",
    trigger: "loop",
  },
  headline: "BUY 62 → rejected at critic",
  headline_driver: "verdict",
  precedence: ["versions", "verdict", "gate", "evidence", "data", "confidence", "none"],
  versions: { comparable: true, changed: false, fields: [], note: null },
  verdict: {
    before: { action: "BUY", confidence: 62, rejected_at: null, execution_status: "accepted:paper" },
    after: { action: null, confidence: null, rejected_at: "critic", execution_status: null },
    action_changed: true,
    confidence_delta: null,
    rejection_changed: true,
    summary: "BUY 62 → rejected at critic",
  },
  gates: {
    changed: [
      {
        gate: "risk",
        before_passed: true,
        after_passed: false,
        before_reasons: [],
        after_reasons: ["stop too wide"],
      },
    ],
    added: [],
    removed: [],
  },
  evidence: {
    flipped: [
      {
        agent_id: "rsi",
        team: "technical",
        before_direction: "bullish",
        after_direction: "bearish",
        before_confidence: 70,
        after_confidence: 65,
        before_claim: "rsi says bullish",
        after_claim: "rsi says bearish",
      },
    ],
    newly_abstaining: [
      { agent_id: "vol", team: "technical", before_direction: "neutral", before_confidence: 50 },
    ],
    newly_speaking: [
      { agent_id: "flows", team: "technical", after_direction: "bullish", after_confidence: 60 },
    ],
    confidence_movers: [
      { agent_id: "macd", team: "technical", before: 55, after: 80, delta: 25, direction: "bearish" },
      { agent_id: "rsi", team: "technical", before: 70, after: 65, delta: -5, direction: "bearish" },
    ],
    n_confidence_movers: 4,
  },
  data: {
    pct_threshold: 10,
    metrics: [
      { name: "ATR_14", before: 2, after: 3, delta: 1, pct_change: 50, unit: "usd" },
    ],
    feeds_lost: ["news:reuters"],
    feeds_restored: [],
    feeds_still_missing: [],
    regime: { before: "trending_up", after: "ranging", changed: true },
    bars: {
      before_n: 60,
      after_n: 62,
      before_last_bar: "2026-07-31T00:00:00+00:00",
      after_last_bar: "2026-08-01T00:00:00+00:00",
      new_bars: 2,
      before_last_close: 2400,
      after_last_close: 2415,
    },
  },
});

describe("RunDiffPanel", () => {
  it("renders the headline and every grouped section", () => {
    render(<RunDiffPanel diff={BASE} />);

    const panel = screen.getByTestId("run-diff");
    expect(panel).toBeInTheDocument();

    const headline = screen.getByTestId("run-diff-headline");
    expect(headline).toHaveTextContent("BUY 62 → rejected at critic");
    expect(headline).toHaveAttribute("data-driver", "verdict");

    // verdict
    expect(screen.getByTestId("run-diff-verdict")).toBeInTheDocument();

    // gates: both sides of the flip plus the refusal reason, verbatim
    const gates = screen.getByTestId("run-diff-gates");
    expect(within(gates).getByText("risk")).toBeInTheDocument();
    expect(within(gates).getByText("pass")).toBeInTheDocument();
    expect(within(gates).getByText("fail")).toBeInTheDocument();
    expect(within(gates).getByText("stop too wide")).toBeInTheDocument();

    // evidence: the flip carries BOTH stances, never just "changed"
    const evidence = screen.getByTestId("run-diff-evidence");
    expect(within(evidence).getByText("bullish 70")).toBeInTheDocument();
    expect(within(evidence).getByText("bearish 65")).toBeInTheDocument();
    expect(within(evidence).getByText("went silent")).toBeInTheDocument();
    expect(within(evidence).getByText("now speaking")).toBeInTheDocument();
    // top movers shown, the rest counted rather than hidden
    expect(within(evidence).getByText("+2 smaller moves")).toBeInTheDocument();

    // data
    const data = screen.getByTestId("run-diff-data");
    expect(within(data).getByText("feed lost")).toBeInTheDocument();
    expect(within(data).getByText("news:reuters")).toBeInTheDocument();
    expect(within(data).getByText("ATR_14")).toBeInTheDocument();
    expect(within(data).getByText("+50.0%")).toBeInTheDocument();
    expect(within(data).getByText(/immaterial/)).toBeInTheDocument();

    expect(screen.getByTestId("run-diff-bars")).toHaveTextContent(
      "advanced 2 bars",
    );
    // no versions change: no alarm block
    expect(screen.queryByTestId("run-diff-versions")).not.toBeInTheDocument();
  });

  it("gives the versions change its own alarm treatment", () => {
    const diff: RunDiff = {
      ...BASE,
      headline:
        "different machine: git_sha aaaa1111 → bbbb2222 — the code changed, not just the market",
      headline_driver: "versions",
      versions: {
        comparable: true,
        changed: true,
        fields: [
          { field: "git_sha", before: "aaaa1111", after: "bbbb2222" },
          { field: "prompt_hash", before: "p111", after: "p222" },
        ],
        note: "the machine itself changed between these runs, not just the market",
      },
    };
    render(<RunDiffPanel diff={diff} />);

    const headline = screen.getByTestId("run-diff-headline");
    expect(headline).toHaveAttribute("data-driver", "versions");
    expect(headline.className).toContain("bear");
    expect(headline).toHaveTextContent("the machine changed");

    const versions = screen.getByTestId("run-diff-versions");
    expect(versions).toHaveTextContent(
      "The code that produced these two decisions is not the same.",
    );
    expect(versions).toHaveTextContent("git_sha: aaaa1111 → bbbb2222");
    expect(versions).toHaveTextContent("prompt_hash: p111 → p222");
    // the lower-precedence facts stay on screen — headlining is not hiding
    expect(screen.getByTestId("run-diff-gates")).toBeInTheDocument();
    expect(screen.getByTestId("run-diff-evidence")).toBeInTheDocument();
  });

  it("says an unstamped pair is unknown, not unchanged", () => {
    render(
      <RunDiffPanel
        diff={{
          ...BASE,
          versions: { comparable: false, changed: null, fields: [], note: "…" },
        }}
      />,
    );
    expect(screen.getByTestId("run-diff-versions-unknown")).toHaveTextContent(
      /unknown, not unchanged/i,
    );
    expect(screen.queryByTestId("run-diff-versions")).not.toBeInTheDocument();
  });

  it("says so plainly when nothing material changed", () => {
    const diff: RunDiff = {
      ...BASE,
      headline:
        "no material change: same verdict, same gates, same agent stances, no material data drift",
      headline_driver: "none",
      verdict: {
        ...BASE.verdict,
        action_changed: false,
        rejection_changed: false,
        confidence_delta: 0,
        summary: "BUY 62 → BUY 62",
      },
      gates: { changed: [], added: [], removed: [] },
      evidence: {
        flipped: [],
        newly_abstaining: [],
        newly_speaking: [],
        confidence_movers: [],
        n_confidence_movers: 0,
      },
      data: {
        ...BASE.data,
        metrics: [],
        feeds_lost: [],
        feeds_restored: [],
        regime: { before: "ranging", after: "ranging", changed: false },
      },
    };
    render(<RunDiffPanel diff={diff} />);

    expect(screen.getByTestId("run-diff-headline")).toHaveTextContent(
      "no material change",
    );
    expect(screen.getByText("Same call, same reasons")).toBeInTheDocument();
    expect(screen.queryByTestId("run-diff-gates")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-diff-evidence")).not.toBeInTheDocument();
    expect(screen.queryByTestId("run-diff-data")).not.toBeInTheDocument();
  });
});
