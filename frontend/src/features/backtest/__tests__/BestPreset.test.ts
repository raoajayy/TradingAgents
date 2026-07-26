import { describe, expect, it } from "vitest";

import { BacktestPresetSchema, type BacktestPreset } from "@/lib/api/types";

/** The one-click "Load best preset" selects the recommended row for the current
 * strategy — mirror of the predicate used in BacktestPage's recommendedForStrategy. */
function pickRecommended(
  presets: BacktestPreset[],
  strategyId: string,
): BacktestPreset | null {
  return (
    presets.find((p) => p.recommended && p.strategy_id === strategyId) ?? null
  );
}

describe("BacktestPreset schema", () => {
  it("parses the recommended flag", () => {
    const p = BacktestPresetSchema.parse({
      strategy_id: "trend_following_v2",
      symbol: "ETH-USD",
      timeframe: "1d",
      params: { donchian_period: 20 },
      oos_sharpe: 0.097,
      deflated_sharpe: 1.0,
      recommended: true,
    });
    expect(p.recommended).toBe(true);
  });

  it("defaults recommended to false when absent (older API rows)", () => {
    const p = BacktestPresetSchema.parse({
      strategy_id: "momentum_v1",
      symbol: "ETH-USD",
      timeframe: "4h",
    });
    expect(p.recommended).toBe(false);
  });
});

describe("pickRecommended", () => {
  const rows: BacktestPreset[] = [
    BacktestPresetSchema.parse({
      strategy_id: "volatility_breakout_v1", symbol: "SOL-USD",
      timeframe: "1d", oos_sharpe: 0.123, recommended: true,
    }),
    BacktestPresetSchema.parse({
      strategy_id: "volatility_breakout_v1", symbol: "ETH-USD",
      timeframe: "4h", oos_sharpe: 0.054, recommended: false,
    }),
    BacktestPresetSchema.parse({
      strategy_id: "trend_following_v1", symbol: "ETH-USD",
      timeframe: "1d", oos_sharpe: 0.075, recommended: true,
    }),
  ];

  it("returns the strategy's recommended cell", () => {
    const best = pickRecommended(rows, "volatility_breakout_v1");
    expect(best?.symbol).toBe("SOL-USD");
    expect(best?.timeframe).toBe("1d");
  });

  it("returns null for a strategy with no recommended row", () => {
    expect(pickRecommended(rows, "rules_v1")).toBeNull();
  });
});
