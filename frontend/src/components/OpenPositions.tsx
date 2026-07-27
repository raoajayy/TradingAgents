/** The live book: open positions with unrealized P&L + an exposure summary.
 * Shared by the Portfolio "Open risk" card (full) and the Home briefing
 * (compact — the returning trader's first question is "what am I holding").
 * Renders honest empty/degraded states; marks non-live marks (EOD/entry). */
import { EmptyState } from "./EmptyState";
import type { PortfolioStats, SystemStatus } from "@/lib/api/types";
import { fmtPnl, fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

type Position = NonNullable<SystemStatus["open_positions"]>[number];
type Exposure = NonNullable<PortfolioStats["exposure"]>;

export function ExposureSummary({ exposure }: { exposure: Exposure }) {
  if (exposure.n_priced === 0) return null;
  return (
    <div
      className="flex flex-wrap gap-x-[18px] gap-y-1 rounded-[12px] bg-surface-2 px-3.5 py-2 text-xs text-fg-muted tabular"
      data-testid="exposure-summary"
    >
      <span>gross <span className="font-bold text-fg">{exposure.gross_exposure_pct?.toFixed(1)}%</span></span>
      <span>net <span className={cn("font-bold", (exposure.net_exposure_pct ?? 0) >= 0 ? "text-bull" : "text-bear")}>{exposure.net_exposure_pct?.toFixed(1)}%</span></span>
      <span>long <span className="font-bold text-bull">{exposure.long_exposure_pct?.toFixed(1)}%</span></span>
      <span>short <span className="font-bold text-bear">{exposure.short_exposure_pct?.toFixed(1)}%</span></span>
      <span>largest <span className="font-bold text-fg">{exposure.largest_position_pct?.toFixed(1)}%</span></span>
      <span>slots <span className="font-bold text-fg">{exposure.n_positions}/{exposure.max_open_positions}</span></span>
    </div>
  );
}

export function OpenPositions({
  positions,
  exposure,
  unrealizedTotal,
  compact = false,
}: {
  positions: Position[] | undefined;
  exposure: Exposure | undefined;
  unrealizedTotal: number | null | undefined;
  /** Home briefing: drop the entry column + total footnote to fit the cell. */
  compact?: boolean;
}) {
  if ((positions?.length ?? 0) === 0) {
    return (
      <EmptyState
        kind="empty"
        title="No open positions"
        detail="Open risk appears here the moment the book is non-flat."
      />
    );
  }
  return (
    <div className="space-y-2">
      {exposure && <ExposureSummary exposure={exposure} />}
      <table className="w-full text-sm tabular">
        <thead>
          <tr className="border-b border-border text-left text-xs text-fg-subtle">
            <th className="py-1 font-medium">symbol</th>
            <th className="py-1 text-right font-medium">qty</th>
            {!compact && <th className="py-1 text-right font-medium">entry</th>}
            <th className="py-1 text-right font-medium">mark</th>
            <th className="py-1 text-right font-medium">unrealized</th>
            <th className="py-1 text-right font-medium">exposure</th>
          </tr>
        </thead>
        <tbody>
          {positions!.map((p) => (
            <tr key={p.symbol}>
              <td className="py-1 font-mono">{p.symbol}</td>
              <td className="py-1 text-right">
                {p.quantity > 0 ? "+" : ""}
                {p.quantity}
              </td>
              {!compact && (
                <td className="py-1 text-right font-mono">{fmtPrice(p.entry_price)}</td>
              )}
              <td className="py-1 text-right font-mono">
                {fmtPrice(p.mark_price)}
                {p.mark_source && p.mark_source !== "live" && (
                  <span className="ml-1 text-[10px] uppercase text-stale">
                    {p.mark_source}
                  </span>
                )}
              </td>
              <td
                data-testid="position-unrealized"
                className={cn(
                  "py-1 text-right font-mono",
                  p.unrealized_pnl != null &&
                    (p.unrealized_pnl >= 0 ? "text-bull" : "text-bear"),
                )}
              >
                {fmtPnl(p.unrealized_pnl)}
              </td>
              <td className="py-1 text-right">
                {p.exposure_pct != null ? `${p.exposure_pct.toFixed(1)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!compact && unrealizedTotal != null && (
        <p className="text-xs text-fg-muted">
          total unrealized{" "}
          <span
            className={cn(
              "font-mono font-semibold tabular",
              unrealizedTotal >= 0 ? "text-bull" : "text-bear",
            )}
          >
            {fmtPnl(unrealizedTotal)}
          </span>{" "}
          · marks labeled EOD/entry when no live tick is available
        </p>
      )}
      {compact && unrealizedTotal != null && (
        <p className="text-xs text-fg-muted">
          total unrealized{" "}
          <span
            className={cn(
              "font-mono font-semibold tabular",
              unrealizedTotal >= 0 ? "text-bull" : "text-bear",
            )}
          >
            {fmtPnl(unrealizedTotal)}
          </span>
        </p>
      )}
    </div>
  );
}
