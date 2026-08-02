import { describe, expect, it } from "vitest";

import {
  countPrepended,
  nextViewport,
  type BarsMeta,
} from "@/components/charts/viewport";

const meta = (firstTime: number, lastTime: number, len: number): BarsMeta => ({
  firstTime,
  lastTime,
  len,
});

// 300 bars, one per day, ending "now"; the user is zoomed into the last 60
const PREV = meta(1_000, 1_299, 300);
const ZOOMED = { from: 240, to: 299 };
const SCROLLED_BACK = { from: 100, to: 159 };

describe("nextViewport", () => {
  it("fits on first render", () => {
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: undefined,
        prev: null,
        len: 300,
        leading: 0,
        range: null,
      }),
    ).toEqual({ kind: "fit" });
  });

  it("fits when the dataset changes (symbol / timeframe / replay)", () => {
    expect(
      nextViewport({
        key: "BTC:1d:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 300,
        leading: 0,
        range: ZOOMED,
      }),
    ).toEqual({ kind: "fit" });
  });

  it("holds position when older bars are paged in", () => {
    // THE regression: this used to fall through to fitContent() as soon as
    // a live tick changed the last bar time, crushing 600 bars into view
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 600,
        leading: 300,
        range: ZOOMED,
      }),
    ).toEqual({ kind: "range", from: 540, to: 599 });
  });

  it("holds position when a page and a live append land together", () => {
    // 300 prepended AND 1 appended; the user was pinned right, so follow
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 601,
        leading: 300,
        range: ZOOMED,
      }),
    ).toEqual({ kind: "range", from: 541, to: 600 });
  });

  it("follows the tip on a live append when pinned to it", () => {
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 301,
        leading: 0,
        range: ZOOMED,
      }),
    ).toEqual({ kind: "range", from: 241, to: 300 });
  });

  it("does not yank the viewport forward when reading history", () => {
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 301,
        leading: 0,
        range: SCROLLED_BACK,
      }),
    ).toEqual({ kind: "range", from: 100, to: 159 });
  });

  it("leaves the zoom alone on an indicator / volume / theme rebuild", () => {
    // identical bars, rebuilt only because the series were re-added
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 300,
        leading: 0,
        range: ZOOMED,
      }),
    ).toEqual({ kind: "range", from: 240, to: 299 });
  });

  it("fits when the window shrinks out from under the same key", () => {
    expect(
      nextViewport({
        key: "BTC:1h:live",
        prevKey: "BTC:1h:live",
        prev: PREV,
        len: 120,
        leading: 0,
        range: ZOOMED,
      }),
    ).toEqual({ kind: "fit" });
  });
});

describe("countPrepended", () => {
  const bars = (times: number[]) => times.map((time) => ({ time }));

  it("counts bars added before the previous first bar", () => {
    expect(countPrepended(bars([97, 98, 99, 100, 101]), meta(100, 101, 2))).toBe(
      3,
    );
  });

  it("returns 0 for a pure append", () => {
    expect(countPrepended(bars([100, 101, 102]), meta(100, 101, 2))).toBe(0);
  });

  it("returns 0 when there is no previous render", () => {
    expect(countPrepended(bars([100, 101]), null)).toBe(0);
  });

  it("returns 0 when the old first bar has fallen out of the window", () => {
    expect(countPrepended(bars([200, 201]), meta(100, 101, 2))).toBe(0);
  });
});
