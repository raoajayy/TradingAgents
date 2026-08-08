/** Favorites strip above the TV chart: one chip per tradeable symbol with
 * an asset icon and the live Hermes mid-price; the active route's chip is
 * highlighted and a click switches /trade/:symbol. Prices ride the same
 * shared stream socket as the chart's realtime bars. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { subscribeStreamPair, streamPairOf } from "@/lib/tv/datafeed";
import type { SymbolSpec } from "@/lib/api/types";
import { fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICONS: Record<string, { glyph: string; bg: string }> = {
  "BTC-USD": { glyph: "₿", bg: "#f7931a" },
  "ETH-USD": { glyph: "Ξ", bg: "#627eea" },
  "SOL-USD": { glyph: "◎", bg: "#9945ff" },
  XAUUSD: { glyph: "Au", bg: "#d4a017" },
  EURUSD: { glyph: "€", bg: "#2563eb" },
  USDJPY: { glyph: "¥", bg: "#dc2626" },
};

function useLiveMid(pythSymbol: string | null | undefined): number | null {
  const [mid, setMid] = useState<number | null>(null);
  useEffect(() => {
    if (!pythSymbol) return;
    setMid(null);
    return subscribeStreamPair(streamPairOf(pythSymbol), (tick) =>
      setMid(tick.mid),
    );
  }, [pythSymbol]);
  return mid;
}

function FavoriteChip({ spec, active }: { spec: SymbolSpec; active: boolean }) {
  const navigate = useNavigate();
  const mid = useLiveMid(spec.pyth_symbol);
  const icon = ICONS[spec.symbol] ?? { glyph: spec.symbol[0]!, bg: "#64748b" };
  const pair = spec.pyth_symbol ? streamPairOf(spec.pyth_symbol) : spec.symbol;
  return (
    <button
      data-testid="favorite-chip"
      data-symbol={spec.symbol}
      aria-pressed={active}
      onClick={() => navigate(`/trade/${spec.symbol}`)}
      className={cn(
        "flex shrink-0 items-center gap-2 rounded-xl border px-2.5 py-1.5 text-left transition-colors",
        active
          ? "border-accent bg-accent-muted"
          : "border-border bg-surface hover:border-border-strong",
      )}
    >
      <span
        aria-hidden="true"
        className="flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold text-white"
        style={{ backgroundColor: icon.bg }}
      >
        {icon.glyph}
      </span>
      <span className="leading-tight">
        <span className="block text-xs font-bold text-fg">{pair}</span>
        <span className="block font-mono text-[11px] tabular text-fg-muted">
          {mid != null ? fmtPrice(mid) : "—"}
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
      className="mb-2 flex shrink-0 items-center gap-2 overflow-x-auto"
    >
      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">
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
