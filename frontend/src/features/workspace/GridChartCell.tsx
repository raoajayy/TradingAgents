/** One cell of the multi-chart grid (review P2.6): its own symbol and
 * timeframe. Deliberately lean — a bare TV chart; the full toolkit
 * (indicators, drawings, saved layout) lives on the main chart. */
import { TVChart } from "@/components/charts/TVChart";
import { useSymbols } from "@/lib/api/queries";
import { TF_TO_RESOLUTION } from "@/lib/tv/datafeed";
import type { GridCell } from "@/stores/ui";

export function GridChartCell({
  cell,
  onChange,
}: {
  cell: GridCell;
  onChange: (next: Partial<GridCell>) => void;
}) {
  const symbols = useSymbols();
  // only Pyth-charted symbols are offered — the TV datafeed has no source
  // for the daily-only correlation series (DXY, US10Y, …)
  const chartable = (symbols.data ?? []).filter((s) => s.pyth_symbol);
  const spec = chartable.find((s) => s.symbol === cell.symbol);
  const timeframes = (spec?.timeframes ?? ["1d"]).filter(
    (tf) => TF_TO_RESOLUTION[tf],
  );
  const activeTf = timeframes.includes(cell.timeframe)
    ? cell.timeframe
    : timeframes[timeframes.length - 1]!;

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
          {chartable.map((s) => (
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
        <div className="absolute inset-0 overflow-hidden rounded-lg">
          <TVChart
            symbol={cell.symbol}
            interval={TF_TO_RESOLUTION[activeTf] ?? "1D"}
          />
        </div>
      </div>
    </div>
  );
}
