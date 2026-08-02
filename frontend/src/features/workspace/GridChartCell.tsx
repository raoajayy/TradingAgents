/** One cell of the multi-chart grid (review P2.6): its own symbol and
 * timeframe, crosshair-synced with the main chart through the workspace
 * sync group. Deliberately lean — candles + volume only; the full
 * toolkit (drawings, indicators, replay) lives on the main chart. */
import { PriceChart } from "@/components/charts/PriceChart";
import { SkeletonCard } from "@/components/ui/skeleton";
import { useBars, useSymbols } from "@/lib/api/queries";
import type { GridCell } from "@/stores/ui";

export function GridChartCell({
  cell,
  onChange,
  syncId,
}: {
  cell: GridCell;
  onChange: (next: Partial<GridCell>) => void;
  syncId: string;
}) {
  const symbols = useSymbols();
  const spec = symbols.data?.find((s) => s.symbol === cell.symbol);
  const timeframes = spec?.timeframes ?? ["1d"];
  const activeTf = timeframes.includes(cell.timeframe)
    ? cell.timeframe
    : timeframes[timeframes.length - 1]!;
  const bars = useBars(cell.symbol, activeTf, 300);

  return (
    // a grid item defaults to min-height:auto (min-content) and would
    // overflow its track; min-h-0 + overflow-hidden keep it in its tile
    <div
      className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-border p-2"
      data-testid="grid-chart-cell"
    >
      <div className="mb-1 flex shrink-0 items-center gap-2 text-xs">
        <select
          value={cell.symbol}
          onChange={(e) => onChange({ symbol: e.target.value })}
          className="rounded-md border border-border bg-surface px-1.5 py-0.5 font-mono font-bold"
          aria-label="Grid cell symbol"
        >
          {(symbols.data ?? []).map((s) => (
            <option key={s.symbol} value={s.symbol}>
              {s.symbol}
            </option>
          ))}
        </select>
        <select
          value={activeTf}
          onChange={(e) => onChange({ timeframe: e.target.value })}
          className="rounded-md border border-border bg-surface px-1.5 py-0.5 font-mono"
          aria-label="Grid cell timeframe"
        >
          {timeframes.map((tf) => (
            <option key={tf} value={tf}>
              {tf}
            </option>
          ))}
        </select>
      </div>
      {/* the chart fills this box absolutely, so the box owns the height */}
      <div className="relative min-h-0 flex-1">
        {bars.data ? (
          <PriceChart
            bars={bars.data}
            style="candles"
            liveSymbol={cell.symbol}
            showVolume
            syncId={syncId}
            fill
            // no longer pixels — only the price:volume pane ratio. 210:78
            // keeps volume readable at ~27% of a small tile (400:78 would
            // squeeze it to 16%).
            height={210}
            datasetKey={`${cell.symbol}:${activeTf}`}
            // cells are interchangeable, so they share one saved layout
            paneLayoutKey="workspace-grid"
          />
        ) : (
          <SkeletonCard lines={4} />
        )}
      </div>
    </div>
  );
}
