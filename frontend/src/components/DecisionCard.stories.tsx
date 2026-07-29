import type { Meta, StoryObj } from "@storybook/react-vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import { DecisionCard } from "./DecisionCard";

// the card reads the live regime (drift badge); stories run offline, so
// queries stay pending and the badge simply doesn't render
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, enabled: false } },
});

const meta: Meta<typeof DecisionCard> = {
  title: "Trading/DecisionCard",
  component: DecisionCard,
  decorators: [
    (Story) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{Story()}</MemoryRouter>
      </QueryClientProvider>
    ),
  ],
};
export default meta;
type Story = StoryObj<typeof DecisionCard>;

export const Buy: Story = {
  args: {
    runId: "abc",
    rec: {
      action: "BUY",
      confidence: 72,
      market_regime: "trending_up",
      entry_price: 130,
      stop_loss: 125,
      take_profits: [
        { price: 135, size_fraction: 0.5 },
        { price: 140, size_fraction: 0.5 },
      ],
      position_size: { quantity: 76.9231, pct_of_equity: 10 },
      risk_reward: 1.5,
      vote_tally: { BUY: 46, HOLD: 0, SELL: 0 },
      n_evidence: 45,
      n_counterarguments: 2,
      invalidation: "A close below the shown stop level.",
      counterarguments: [
        {
          agent_id: "macro_bear",
          direction: "bearish",
          confidence: 55,
          claim: "Real yields rising; historically caps gold rallies.",
        },
      ],
      historical_analogs: [
        {
          description: "2024-03 breakout in similar low-vol regime",
          similarity: 0.82,
          outcome: "ran +6% before mean reversion",
        },
      ],
    },
  },
};

/** Hero variant with the trust surfaces: empirical calibration proof under
 * the confidence, and the strongest objection surfaced (fixes #1 + #3). */
export const Hero: Story = {
  args: {
    hero: true,
    kicker: "AI DECISION — XAUUSD · 1D",
    runId: "abc",
    rec: {
      ...(Buy.args!.rec as object),
      p_win: { p_win: 0.68, n: 12, basis: "confidence 70–80 bucket" },
    },
  },
};

/** Unanimous call: no agent argued the other side — the honest, informative
 * empty-dissent state (fix #1), not a hidden one. */
export const HeroUnanimous: Story = {
  args: {
    hero: true,
    kicker: "AI DECISION — XAUUSD · 1D",
    runId: "abc",
    rec: {
      ...(Buy.args!.rec as object),
      counterarguments: [],
      n_counterarguments: 0,
      p_win: null,
    },
  },
};

export const Rejected: Story = {
  args: {
    rec: {
      status: "rejected",
      rejection: {
        stage: "risk_gate",
        reasons: ["VaR95 0.0388 exceeds max daily loss 0.0300"],
      },
    },
  },
};

export const NoRuns: Story = {
  args: { rec: { status: "no recommendation" } },
};
