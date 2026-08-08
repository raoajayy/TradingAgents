/** Favorites strip above the TV chart: one chip per tradeable symbol with
 * an asset icon and the live Hermes mid-price; the active route's chip is
 * highlighted and a click switches /trade/:symbol. Prices ride the same
 * shared stream socket as the chart's realtime bars. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { subscribeStreamPair, streamPairOf } from "@/lib/tv/datafeed";
import type { SymbolSpec } from "@/lib/api/types";
import { useUiStore } from "@/stores/ui";
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

function FavoriteChip({
  spec,
  active,
  onRemove,
}: {
  spec: SymbolSpec;
  active: boolean;
  onRemove: () => void;
}) {
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
        "group relative flex h-11 shrink-0 items-center gap-2.5 rounded-xl border px-3 text-left transition-colors",
        active
          ? "border-accent bg-accent-muted"
          : "border-border bg-surface hover:border-border-strong",
      )}
    >
      {/* remove affordance: appears on hover/focus, never mid-tap targets */}
      <span
        role="button"
        tabIndex={0}
        aria-label={`Remove ${spec.symbol} from favorites`}
        data-testid="favorite-remove"
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.stopPropagation();
            onRemove();
          }
        }}
        className="absolute -right-1.5 -top-1.5 hidden h-4 w-4 items-center justify-center rounded-full border border-border bg-surface text-[10px] leading-none text-fg-subtle shadow-sm hover:text-bear group-focus-within:flex group-hover:flex"
      >
        ×
      </span>
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
  const [addOpen, setAddOpen] = useState(false);
  const favoriteNames = useUiStore((s) => s.favorites);
  const addFavorite = useUiStore((s) => s.addFavorite);
  const removeFavorite = useUiStore((s) => s.removeFavorite);

  const chartable = symbols.filter((s) => s.tradeable && s.pyth_symbol);
  if (chartable.length === 0) return null;
  const availableNames = chartable.map((s) => s.symbol);
  const shownNames = favoriteNames ?? availableNames; // null = all (default)
  const shown = chartable.filter((s) => shownNames.includes(s.symbol));
  const addable = chartable.filter((s) => !shownNames.includes(s.symbol));

  return (
    <div
      data-testid="favorites-bar"
      className="mb-2 flex shrink-0 items-center gap-2 overflow-x-auto px-1 py-1.5"
    >
      <span className="shrink-0 self-center text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-subtle">
        Favorites
      </span>
      {shown.map((spec) => (
        <FavoriteChip
          key={spec.symbol}
          spec={spec}
          active={spec.symbol === active}
          onRemove={() => removeFavorite(spec.symbol, availableNames)}
        />
      ))}
      {addable.length > 0 && (
        <div className="relative shrink-0">
          <button
            aria-label="Add favorite"
            aria-expanded={addOpen}
            data-testid="favorite-add"
            onClick={() => setAddOpen((v) => !v)}
            className="flex h-11 w-11 items-center justify-center rounded-xl border border-dashed border-border text-lg leading-none text-fg-subtle hover:border-border-strong hover:text-fg"
          >
            +
          </button>
          {addOpen && (
            <div
              data-testid="favorite-add-menu"
              className="absolute left-0 top-12 z-20 flex min-w-36 flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-lg"
            >
              {addable.map((spec) => (
                <button
                  key={spec.symbol}
                  data-symbol={spec.symbol}
                  onClick={() => {
                    addFavorite(spec.symbol, availableNames);
                    setAddOpen(false);
                  }}
                  className="px-3 py-2 text-left font-mono text-xs text-fg hover:bg-accent-muted"
                >
                  {spec.pyth_symbol ? streamPairOf(spec.pyth_symbol) : spec.symbol}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
