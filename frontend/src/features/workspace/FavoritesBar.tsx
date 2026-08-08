/** Favorites strip above the TV chart: one chip per tradeable symbol with
 * an asset icon and the live Hermes mid-price; the active route's chip is
 * highlighted and a click switches /trade/:symbol. Prices ride the same
 * shared stream socket as the chart's realtime bars. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { subscribeStreamPair, streamPairOf } from "@/lib/tv/datafeed";
import type { SymbolSpec } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ICONS: Record<string, { glyph: string; bg: string }> = {
  "BTC-USD": { glyph: "₿", bg: "#f7931a" },
  "ETH-USD": { glyph: "Ξ", bg: "#627eea" },
  "SOL-USD": { glyph: "◎", bg: "#9945ff" },
  XAUUSD: { glyph: "Au", bg: "#d4a017" },
  EURUSD: { glyph: "€", bg: "#2563eb" },
  USDJPY: { glyph: "¥", bg: "#dc2626" },
};

// price precision follows the venue convention, same as the chart's
// pricescale: FX majors 5 decimals, JPY quotes 3, everything else 2
const DECIMALS: Record<string, number> = { EURUSD: 5, USDJPY: 3 };

// a price older than this renders dimmed — a frozen stream must never
// keep LOOKING live (the same honesty rule the status chips follow)
const STALE_AFTER_MS = 90_000;

function useLiveMid(
  pythSymbol: string | null | undefined,
): { mid: number | null; stale: boolean } {
  const [mid, setMid] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  useEffect(() => {
    if (!pythSymbol) return;
    setMid(null);
    setStale(false);
    let lastTickAt = Date.now();
    const unsubscribe = subscribeStreamPair(
      streamPairOf(pythSymbol),
      (tick) => {
        lastTickAt = Date.now();
        setMid(tick.mid);
        setStale(false);
      },
    );
    const staleCheck = window.setInterval(
      () => setStale(Date.now() - lastTickAt > STALE_AFTER_MS),
      15_000,
    );
    return () => {
      unsubscribe();
      window.clearInterval(staleCheck);
    };
  }, [pythSymbol]);
  return { mid, stale };
}

function FavoriteChip({ spec, active }: { spec: SymbolSpec; active: boolean }) {
  const navigate = useNavigate();
  const { mid, stale } = useLiveMid(spec.pyth_symbol);
  const icon = ICONS[spec.symbol] ?? { glyph: spec.symbol[0]!, bg: "#64748b" };
  const pair = spec.pyth_symbol ? streamPairOf(spec.pyth_symbol) : spec.symbol;
  const price =
    mid != null
      ? mid.toLocaleString("en-US", {
          minimumFractionDigits: DECIMALS[spec.symbol] ?? 2,
          maximumFractionDigits: DECIMALS[spec.symbol] ?? 2,
        })
      : "—";
  return (
    <button
      data-testid="favorite-chip"
      data-symbol={spec.symbol}
      aria-pressed={active}
      onClick={() => navigate(`/trade/${spec.symbol}`)}
      className={cn(
        "flex h-11 shrink-0 items-center gap-2.5 rounded-xl border px-3 text-left transition-colors",
        active
          ? "border-accent bg-accent-muted"
          : "border-border bg-surface hover:border-border-strong",
      )}
    >
      <span
        aria-hidden="true"
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold leading-none text-white"
        style={{ backgroundColor: icon.bg }}
      >
        {icon.glyph}
      </span>
      <span className="flex flex-col justify-center gap-0.5">
        <span className="text-xs font-bold leading-none text-fg">{pair}</span>
        <span
          className={cn(
            "font-mono text-[11px] leading-none tabular text-fg-muted",
            stale && "opacity-50",
          )}
          title={stale ? "stale — price stream reconnecting" : undefined}
        >
          {price}
        </span>
      </span>
    </button>
  );
}

export function FavoritesBar({
  symbols,
  active,
}: {
  symbols: SymbolSpec[];
  active: string;
}) {
  const favorites = symbols.filter((s) => s.tradeable && s.pyth_symbol);
  if (favorites.length === 0) return null;
  return (
    <div
      data-testid="favorites-bar"
      className="mb-2 flex shrink-0 items-center gap-2 overflow-x-auto px-1 py-0.5"
    >
      <span className="shrink-0 self-center text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-subtle">
        Favorites
      </span>
      {favorites.map((spec) => (
        <FavoriteChip
          key={spec.symbol}
          spec={spec}
          active={spec.symbol === active}
        />
      ))}
    </div>
  );
}
