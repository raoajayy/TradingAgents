/** What the chart should do with its visible range after a series rebuild.
 *
 * The rule this replaces was "preserve the viewport if the last bar time
 * is unchanged, otherwise fitContent()". That made a paged chart unusable:
 * pan a few px into the pre-history whitespace and 300 older bars are
 * fetched; the next 30s bars poll then appends a bar, the last-bar-time
 * check fails, and fitContent() crushes 600-1200 bars into the pane —
 * hairline candles with the price axis autoscaled across months. Switching
 * timeframe "fixed" it only because that drops the paged bars.
 *
 * The honest invariant: fitContent() belongs to a change of DATASET
 * (symbol, timeframe, replay mode) — never to a live append, a paged
 * prepend, or a rebuild caused by toggling an indicator or the theme
 * (those reset the user's zoom today too).
 *
 * Pure so the whole matrix is testable without a canvas. */

export type BarsMeta = {
  /** time of bars[0] on the previous render — locates prepended bars */
  firstTime: number;
  lastTime: number;
  len: number;
};

export type ViewportAction =
  | { kind: "fit" }
  | { kind: "range"; from: number; to: number };

export interface ViewportInput {
  /** identity of the plotted dataset, e.g. "BTC-USD:1h:live" */
  key: string | undefined;
  prevKey: string | undefined;
  prev: BarsMeta | null;
  /** bar count after the rebuild */
  len: number;
  /** how many bars were prepended (index of prev.firstTime in the new array) */
  leading: number;
  /** the range the user was looking at, in logical (bar index) space */
  range: { from: number; to: number } | null;
}

export function nextViewport(input: ViewportInput): ViewportAction {
  const { key, prevKey, prev, len, leading, range } = input;

  // first render, a genuinely different dataset, or nothing remembered
  if (prev == null || range == null || key == null || key !== prevKey) {
    return { kind: "fit" };
  }

  const trailing = len - prev.len - leading;
  // the window moved out from under us (server returned a different slice
  // under the same key) — no honest way to map the old range onto it
  if (leading === 0 && trailing < 0) return { kind: "fit" };

  let { from, to } = range;
  // prepend: every bar shifted right by `leading`, so shift the viewport
  // with them and the candles under the cursor do not move
  from += leading;
  to += leading;
  // live append: follow the tip ONLY if the user was already pinned to it.
  // Someone reading history should not be yanked forward by a new bar.
  if (trailing > 0 && range.to >= prev.len - 1) {
    from += trailing;
    to += trailing;
  }
  return { kind: "range", from, to };
}

/** Where the previously-first bar now sits — i.e. how many bars were
 * prepended. Returns 0 when nothing was prepended or the old first bar is
 * gone (a moved window, which nextViewport treats as a new dataset). */
export function countPrepended(
  bars: readonly { time: number }[],
  prev: BarsMeta | null,
): number {
  if (prev == null) return 0;
  const idx = bars.findIndex((b) => b.time >= prev.firstTime);
  return idx > 0 ? idx : 0;
}
