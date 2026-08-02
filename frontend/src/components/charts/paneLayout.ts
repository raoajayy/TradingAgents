/** Pane-height persistence (review P2.3): user-dragged pane proportions
 * survive indicator toggles and reloads.
 *
 * Keyed by SURFACE + pane count. Pane count alone is not enough: the
 * workspace's main chart (factors ~[400,78]) and every grid cell
 * (~[210,78]) are both 2-pane layouts, so they collided on key "2" and a
 * click on a small grid cell rewrote the main chart's proportions. Within
 * one surface, keying by count is still right — the same layout shape
 * restores the same proportions regardless of which oscillators are
 * showing (per-indicator keys would fragment endlessly). */

const STORAGE_KEY = "pro-pane-factors";

/** Below this share of total height the price pane is unusable, and the
 * user cannot drag it back: lightweight-charts clamps panes at 30px and
 * the separator ends up flush against the top of the widget. A stored
 * value that low is corruption (a stray click inside the 9px separator
 * hit zone), not a preference — refuse to restore it. */
const MIN_PRICE_PANE_SHARE = 0.2;

type FactorMap = Record<string, number[]>;

function read(): FactorMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return parsed && typeof parsed === "object" ? (parsed as FactorMap) : {};
  } catch {
    return {};
  }
}

const key = (surface: string, paneCount: number) => `${surface}:${paneCount}`;

export function loadPaneFactors(
  surface: string,
  paneCount: number,
): number[] | null {
  const factors = read()[key(surface, paneCount)];
  if (
    !Array.isArray(factors) ||
    factors.length !== paneCount ||
    !factors.every((f) => Number.isFinite(f) && f > 0)
  ) {
    return null;
  }
  const total = factors.reduce((a, b) => a + b, 0);
  if (factors[0]! / total < MIN_PRICE_PANE_SHARE) return null;
  return factors;
}

export function savePaneFactors(
  surface: string,
  paneCount: number,
  factors: number[],
): void {
  if (
    factors.length !== paneCount ||
    factors.some((f) => !Number.isFinite(f) || f <= 0)
  )
    return;
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...read(), [key(surface, paneCount)]: factors }),
    );
  } catch {
    /* storage full/blocked — proportions just won't persist */
  }
}
