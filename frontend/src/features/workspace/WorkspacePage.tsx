/** Trading Workspace: a full-bleed TradingView terminal (Pyth history +
 * Hermes live stream via lib/tv/datafeed), AI decision history as marks.
 * TV owns ALL chart chrome now — symbol search, timeframes, indicators,
 * drawings, multi-chart layouts, fullscreen. The page adds only what TV
 * can't know: route sync (/trade/:symbol), the open-position badge, the
 * evidence-level chip, and the macro event strip. */
import { useEffect, useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { Card, CardContent } from "@/components/ui/card";
import { TVChart } from "@/components/charts/TVChart";
import { FavoritesBar } from "./FavoritesBar";
import { useCalendar, useStatus, useSymbols } from "@/lib/api/queries";
import { fmtCountdown, fmtPnl, fmtPrice } from "@/lib/format";
import { TF_TO_RESOLUTION } from "@/lib/tv/datafeed";
import { levelFromSearchParams, type RefLevel } from "@/lib/levelFromRef";
import { countdownExpired, useCountdown } from "@/lib/useCountdown";
import { useUiStore } from "@/stores/ui";

export default function WorkspacePage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol ?? "BTC-USD";
  const navigate = useNavigate();
  // evidence-chip level (P2-09): carried in the URL by the Decisions page
  // level chips, cleared by the badge's × (or simply by navigating away)
  const [searchParams, setSearchParams] = useSearchParams();
  const refLevel = useMemo<RefLevel | null>(
    () => levelFromSearchParams(searchParams),
    [searchParams],
  );
  const { timeframe, setSymbol } = useUiStore();

  const status = useStatus();
  const calendar = useCalendar();
  const symbols = useSymbols();

  // keep global symbol in sync with the route
  useEffect(() => {
    if (useUiStore.getState().symbol !== symbol) setSymbol(symbol);
  }, [symbol, setSymbol]);

  // TV owns interval switching once mounted; ui-store timeframe seeds it
  const interval = TF_TO_RESOLUTION[timeframe] ?? "60";

  // open position for this symbol: the server's unrealized P&L in a badge
  // (no client math — Constraint 2)
  const openPosition = useMemo(
    () =>
      status.data?.open_positions?.find(
        (p) => p.symbol === symbol && p.quantity !== 0,
      ) ?? null,
    [status.data, symbol],
  );

  // the server-computed next MAJOR event (countdown-capable) beats the
  // first row of the raw release list (review P1.1); the countdown ticks
  // locally between refetches (R2.3)
  const nextMajor = calendar.data?.next_major;
  const nextMajorRemaining = useCountdown(nextMajor?.at ?? null);
  const nextRelease = nextMajor ?? calendar.data?.releases[0];

  return (
    <Card
      data-testid="chart-card"
      className="flex h-full flex-col bg-surface !p-2"
    >
      <CardContent className="flex min-h-0 flex-1 flex-col overflow-hidden !p-0">
        <FavoritesBar symbols={symbols.data ?? []} active={symbol} />
        <div
          className="relative min-h-0 min-w-0 flex-1"
          data-testid="main-chart-tile"
        >
          <div className="absolute inset-0 overflow-hidden rounded-lg">
            <TVChart
              symbol={symbol}
              interval={interval}
              persistKey="workspace-main"
              // TV's own symbol search drives the route now
              onSymbolChange={(next) =>
                navigate(`/trade/${next}`, { replace: true })
              }
            />
          </div>
          {/* overlays: everything TV can't know, floated over the chart in
              ONE wrapping row — separate left/right absolutes collided on
              narrow screens and swallowed each other's clicks */}
          <div className="pointer-events-none absolute inset-x-14 top-2 z-10 flex flex-wrap justify-end gap-2">
            {refLevel && (
              <span
                data-testid="level-badge"
                className="pointer-events-auto mr-auto inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface/90 px-2 py-1 font-mono text-xs text-accent"
              >
                {refLevel.label}{" "}
                {refLevel.kind === "line"
                  ? fmtPrice(refLevel.price)
                  : `${fmtPrice(refLevel.low)}–${fmtPrice(refLevel.high)}`}
                <button
                  aria-label="Clear plotted level"
                  data-testid="level-clear"
                  className="font-semibold hover:text-fg"
                  onClick={() => setSearchParams({}, { replace: true })}
                >
                  ×
                </button>
              </span>
            )}
            {openPosition?.unrealized_pnl != null && (
              <div
                data-testid="position-badge"
                className={
                  "pointer-events-auto rounded-lg border border-border bg-surface/90 px-2 py-1 font-mono text-xs tabular " +
                  (openPosition.unrealized_pnl >= 0
                    ? "text-bull"
                    : "text-bear")
                }
              >
                {openPosition.quantity > 0 ? "long" : "short"}{" "}
                {Math.abs(openPosition.quantity)} ·{" "}
                {fmtPnl(openPosition.unrealized_pnl)}
                <span className="ml-1 text-fg-subtle">paper</span>
              </div>
            )}
          </div>
        </div>
        {nextRelease && (
          <p
            className="mt-2 shrink-0 px-1 text-xs text-fg-subtle"
            data-testid="event-strip"
          >
            next macro event:{" "}
            <span className="text-fg-muted">{nextRelease.release}</span>
            {nextMajor && !countdownExpired(nextMajorRemaining) ? (
              <>
                {" in "}
                <span className="font-mono tabular text-fg-muted">
                  {fmtCountdown(nextMajorRemaining ?? nextMajor.seconds_until)}
                </span>
                {nextMajor.time_et &&
                  ` (${nextMajor.date} ${nextMajor.time_et} ET)`}
              </>
            ) : (
              <> on {nextRelease.date}</>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
