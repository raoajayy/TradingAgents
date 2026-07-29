import { describe, expect, it } from "vitest";

import {
  levelFromRef,
  levelFromSearchParams,
  levelToSearchParams,
} from "@/lib/levelFromRef";

describe("levelFromRef", () => {
  it("parses price-family scalar refs as lines", () => {
    expect(levelFromRef({ name: "SMA_50", value: 2315.4 })).toEqual({
      kind: "line",
      label: "SMA_50",
      price: 2315.4,
    });
    expect(levelFromRef({ name: "EMA_21", value: 101 })).toEqual({
      kind: "line",
      label: "EMA_21",
      price: 101,
    });
    expect(levelFromRef({ name: "LAST_CLOSE", value: 2330 })).toEqual({
      kind: "line",
      label: "LAST_CLOSE",
      price: 2330,
    });
    expect(levelFromRef({ name: "LAST_PRICE", value: 2331.5 })).toEqual({
      kind: "line",
      label: "LAST_PRICE",
      price: 2331.5,
    });
    expect(levelFromRef({ name: "VWAP", value: 2320.02 })).toEqual({
      kind: "line",
      label: "VWAP",
      price: 2320.02,
    });
    expect(levelFromRef({ name: "BOLL.upper", value: 2350 })).toEqual({
      kind: "line",
      label: "BOLL.upper",
      price: 2350,
    });
    expect(levelFromRef({ name: "SUPERTREND.line", value: 2290 })).toEqual({
      kind: "line",
      label: "SUPERTREND.line",
      price: 2290,
    });
  });

  it("rejects scalars that don't live on the price axis", () => {
    // oscillators / non-price readings: numeric but not chartable as price
    expect(levelFromRef({ name: "RSI_14", value: 27.4 })).toBeNull();
    expect(levelFromRef({ name: "MACD.signal", value: -0.8 })).toBeNull();
    expect(levelFromRef({ name: "ATR_14", value: 2.5 })).toBeNull();
    expect(levelFromRef({ name: "ADX", value: 31.2 })).toBeNull();
    expect(levelFromRef({ name: "DXY", value: 104.2 })).toBeNull();
    expect(levelFromRef({ name: "BARS_SHOWN", value: 60 })).toBeNull();
  });

  it("rejects non-numeric refs", () => {
    expect(levelFromRef({ name: "SESSION", value: "regular" })).toBeNull();
    expect(
      levelFromRef({ name: "NEWS_1", value: "Fed holds rates steady" }),
    ).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: "2315.4" })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: null })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: undefined })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: NaN })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: Infinity })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: -1 })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: 0 })).toBeNull();
    expect(levelFromRef({ name: "SMA_50", value: true })).toBeNull();
  });

  it("parses {low, high} object values as zones regardless of name", () => {
    expect(
      levelFromRef({ name: "order_block", value: { low: 2280, high: 2295 } }),
    ).toEqual({ kind: "zone", label: "order_block", low: 2280, high: 2295 });
    // reversed bounds normalize
    expect(
      levelFromRef({
        name: "liquidity_zone",
        value: { low: 2295, high: 2280 },
      }),
    ).toEqual({ kind: "zone", label: "liquidity_zone", low: 2280, high: 2295 });
  });

  it("parses {price} object values as lines regardless of name", () => {
    expect(
      levelFromRef({ name: "liquidity_sweep", value: { price: 2301.5 } }),
    ).toEqual({ kind: "line", label: "liquidity_sweep", price: 2301.5 });
  });

  it("rejects malformed object values", () => {
    expect(levelFromRef({ name: "order_block", value: {} })).toBeNull();
    expect(
      levelFromRef({ name: "order_block", value: { low: 2280 } }),
    ).toBeNull();
    expect(
      levelFromRef({ name: "order_block", value: { low: "a", high: "b" } }),
    ).toBeNull();
    expect(
      levelFromRef({ name: "order_block", value: { price: -5 } }),
    ).toBeNull();
    expect(levelFromRef({ name: "order_block", value: [2280, 2295] })).toBeNull();
  });
});

describe("level search params round-trip", () => {
  it("round-trips a line level", () => {
    const level = { kind: "line", label: "SMA_50", price: 2315.4 } as const;
    expect(levelFromSearchParams(levelToSearchParams(level))).toEqual(level);
  });

  it("round-trips a zone level", () => {
    const level = {
      kind: "zone",
      label: "order_block",
      low: 2280,
      high: 2295,
    } as const;
    expect(levelFromSearchParams(levelToSearchParams(level))).toEqual(level);
  });

  it("returns null for absent or garbled params", () => {
    expect(levelFromSearchParams(new URLSearchParams())).toBeNull();
    expect(
      levelFromSearchParams(new URLSearchParams("label=SMA_50")),
    ).toBeNull();
    expect(
      levelFromSearchParams(new URLSearchParams("label=SMA_50&level=abc")),
    ).toBeNull();
    expect(
      levelFromSearchParams(new URLSearchParams("level=2315.4")),
    ).toBeNull();
    expect(
      levelFromSearchParams(new URLSearchParams("label=x&low=1")),
    ).toBeNull();
  });

  it("normalizes reversed zone bounds from the URL", () => {
    expect(
      levelFromSearchParams(
        new URLSearchParams("label=ob&low=2295&high=2280"),
      ),
    ).toEqual({ kind: "zone", label: "ob", low: 2280, high: 2295 });
  });
});
