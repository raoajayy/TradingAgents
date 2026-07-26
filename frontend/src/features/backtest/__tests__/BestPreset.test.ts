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

/** When "Use tuned preset" is on, the visible param form = a-priori defaults
 * with the preset's tuned values layered on top (mirror of BacktestPage's
 * populate effect). This is what makes the checkbox SHOW the preset values. */
function formParams(
  defaults: Record<string, string | number>,
  preset: Record<string, string | number> | null,
  usePreset: boolean,
): Record<string, string | number> {
  return usePreset && preset ? { ...defaults, ...preset } : { ...defaults };
}

describe("formParams (preset populates the form)", () => {
  const defaults = { lookback: 20, squeeze_pct: 0.05, trail_mode: "pct" };
  const preset = { lookback: 30, squeeze_pct: 0.05, trail_mode: "chandelier" };

  it("shows a-priori defaults when the preset is off", () => {
    expect(formParams(defaults, preset, false)).toEqual(defaults);
  });

  it("overlays the preset values (incl. categoricals) when on", () => {
    const shown = formParams(defaults, preset, true);
    expect(shown.lookback).toBe(30);
    expect(shown.trail_mode).toBe("chandelier");
  });

  it("keeps defaults for params the preset does not specify", () => {
    const shown = formParams({ ...defaults, risk_pct: 1.0 }, { lookback: 30 }, true);
    expect(shown.lookback).toBe(30);
    expect(shown.risk_pct).toBe(1.0); // untouched by the partial preset
  });

  it("falls back to defaults when no preset exists even if toggled on", () => {
    expect(formParams(defaults, null, true)).toEqual(defaults);
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
