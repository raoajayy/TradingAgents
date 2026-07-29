/** P2-09: parse an evidence data ref into a chart-plottable price level.
 *
 * Evidence refs are `{name, value}` pairs (EvidenceItemSchema). Only values
 * that live on the PRICE axis qualify: an RSI of 27.4 or a MACD histogram
 * drawn as a gold price line would be nonsense. Scalar refs therefore pass
 * a price-family allowlist (the same families PriceChart overlays on the
 * price pane, plus the snapshot's LAST_CLOSE / LAST_PRICE refs); object
 * values carrying `{low, high}` zones or a `{price}` field (order-block /
 * liquidity levels) qualify by shape regardless of name. Everything else —
 * oscillators, counts, strings, malformed objects — returns null and the
 * chip renders exactly as before (non-clickable).
 */

export type RefLevel =
  | { kind: "line"; label: string; price: number }
  | { kind: "zone"; label: string; low: number; high: number };

/** Scalar ref names whose value is a price: SMA_n / EMA_n overlays,
 * VWAP, Bollinger + Supertrend lines (rendered as `NAME.line` by the
 * evidence renderer), and the snapshot's last close / last trade. */
const PRICE_SCALAR = new RegExp(
  "^(?:" +
    "(?:SMA|EMA)_\\d{1,3}" +
    "|VWAP" +
    "|LAST_CLOSE" +
    "|LAST_PRICE" +
    "|BOLL\\.(?:upper|middle|lower)" +
    "|SUPERTREND\\.(?:line|upper|lower)" +
    ")$",
);

function isPrice(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function levelFromRef(ref: {
  name: string;
  // optional: zod's z.unknown() marks the field optional in EvidenceItem
  value?: unknown;
}): RefLevel | null {
  const { name, value } = ref;
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const v = value as Record<string, unknown>;
    if (isPrice(v.low) && isPrice(v.high)) {
      return {
        kind: "zone",
        label: name,
        low: Math.min(v.low, v.high),
        high: Math.max(v.low, v.high),
      };
    }
    if (isPrice(v.price)) return { kind: "line", label: name, price: v.price };
    return null;
  }
  if (isPrice(value) && PRICE_SCALAR.test(name)) {
    return { kind: "line", label: name, price: value };
  }
  return null;
}

/** Serialize a level into /trade/:symbol search params (the navigation
 * carrier: shareable, and cleared for free when the URL changes). */
export function levelToSearchParams(level: RefLevel): URLSearchParams {
  const params = new URLSearchParams();
  params.set("label", level.label);
  if (level.kind === "line") {
    params.set("level", String(level.price));
  } else {
    params.set("low", String(level.low));
    params.set("high", String(level.high));
  }
  return params;
}

/** Inverse of levelToSearchParams; null when params are absent/garbled. */
export function levelFromSearchParams(
  params: URLSearchParams,
): RefLevel | null {
  const label = params.get("label");
  if (!label) return null;
  const num = (key: string): number | null => {
    const raw = params.get(key);
    if (raw == null || raw.trim() === "") return null;
    const parsed = Number(raw);
    return isPrice(parsed) ? parsed : null;
  };
  const low = num("low");
  const high = num("high");
  if (low != null && high != null) {
    return {
      kind: "zone",
      label,
      low: Math.min(low, high),
      high: Math.max(low, high),
    };
  }
  const price = num("level");
  if (price != null) return { kind: "line", label, price };
  return null;
}
