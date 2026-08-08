import { describe, expect, it } from "vitest";

import {
  TF_TO_RESOLUTION,
  assetTypeOf,
  marksFromRuns,
  resolutionSeconds,
  streamPairOf,
  tickBucket,
  udfToBars,
} from "@/lib/tv/datafeed";

describe("udfToBars", () => {
  it("maps UDF parallel arrays to ms-time bar objects", () => {
    const bars = udfToBars({
      s: "ok",
      t: [100, 160],
      o: [1, 2],
      h: [3, 4],
      l: [0.5, 1.5],
      c: [2, 3],
      v: [10, 20],
    });
    expect(bars).toEqual([
      { time: 100_000, open: 1, high: 3, low: 0.5, close: 2, volume: 10 },
      { time: 160_000, open: 2, high: 4, low: 1.5, close: 3, volume: 20 },
    ]);
  });

  it("empty history (closed market) yields no bars", () => {
    expect(udfToBars({ s: "ok", t: [] })).toEqual([]);
  });
});

describe("resolutionSeconds", () => {
  it("handles minutes, daily, weekly", () => {
    expect(resolutionSeconds("1")).toBe(60);
    expect(resolutionSeconds("240")).toBe(14_400);
    expect(resolutionSeconds("1D")).toBe(86_400);
    expect(resolutionSeconds("1W")).toBe(604_800);
  });
});

describe("tickBucket", () => {
  it("weekly buckets anchor to MONDAY 00:00 UTC (epoch is a Thursday)", () => {
    // Fri 2026-08-07 12:00 UTC → its week began Mon 2026-08-03 00:00 UTC
    const friday = Date.UTC(2026, 7, 7, 12, 0, 0);
    expect(tickBucket(friday, "1W")).toBe(Date.UTC(2026, 7, 3));
    // a Monday tick starts ITS OWN week, not the previous one
    const monday = Date.UTC(2026, 7, 3, 0, 0, 1);
    expect(tickBucket(monday, "1W")).toBe(Date.UTC(2026, 7, 3));
    // year boundary: Thu 2026-01-01 belongs to the week of Mon 2025-12-29
    const newYear = Date.UTC(2026, 0, 1, 9, 0, 0);
    expect(tickBucket(newYear, "1W")).toBe(Date.UTC(2025, 11, 29));
  });

  it("daily buckets align to 00:00 UTC, intraday to the resolution grid", () => {
    const ts = Date.UTC(2026, 7, 7, 13, 47, 9);
    expect(tickBucket(ts, "1D")).toBe(Date.UTC(2026, 7, 7));
    expect(tickBucket(ts, "60")).toBe(Date.UTC(2026, 7, 7, 13));
    expect(tickBucket(ts, "5")).toBe(Date.UTC(2026, 7, 7, 13, 45));
  });
});

describe("assetTypeOf", () => {
  it("classifies crypto, metal, and FX", () => {
    expect(assetTypeOf("BTC-USD")).toBe("crypto");
    expect(assetTypeOf("XAUUSD")).toBe("commodity");
    expect(assetTypeOf("EURUSD")).toBe("forex");
    expect(assetTypeOf("USDJPY")).toBe("forex");
  });
});

describe("streamPairOf", () => {
  it("strips the Pyth asset-class prefix for the Hermes stream", () => {
    expect(streamPairOf("Crypto.BTC/USD")).toBe("BTC/USD");
    expect(streamPairOf("Metal.XAU/USD")).toBe("XAU/USD");
    expect(streamPairOf("FX.USD/JPY")).toBe("USD/JPY");
  });
});

describe("TF_TO_RESOLUTION", () => {
  it("covers every dashboard timeframe", () => {
    for (const tf of ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]) {
      expect(TF_TO_RESOLUTION[tf]).toBeTruthy();
    }
  });
});

describe("marksFromRuns", () => {
  const run = (overrides: object) => ({
    run_id: "r1",
    time: 500 as number | null,
    action: "BUY" as string | null,
    rejected_at: null,
    confidence: 72 as number | null, // server scale: 0–100
    market_regime: "trend" as string | null,
    geometry: null,
    span: null,
    ...overrides,
  });

  it("maps decided runs inside the window to labelled marks", () => {
    const marks = marksFromRuns([run({})], 0, 1000);
    expect(marks).toHaveLength(1);
    expect(marks[0]).toMatchObject({ id: "r1", time: 500, label: "B" });
    expect(marks[0]!.text).toContain("confidence 72%");
    expect(marks[0]!.text).toContain("trend");
  });

  it("skips rejected/undecided runs and runs outside the window", () => {
    expect(marksFromRuns([run({ action: null })], 0, 1000)).toEqual([]);
    expect(marksFromRuns([run({ time: null })], 0, 1000)).toEqual([]);
    expect(marksFromRuns([run({ time: 5000 })], 0, 1000)).toEqual([]);
  });

  it("labels SELL and HOLD distinctly", () => {
    const marks = marksFromRuns(
      [run({ action: "SELL" }), run({ run_id: "r2", action: "HOLD" })],
      0,
      1000,
    );
    expect(marks.map((m) => m.label)).toEqual(["S", "H"]);
  });
});
