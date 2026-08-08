/** Trading Workspace: the TradingView Charting Library front and center
 * (Pyth history + Hermes live stream via lib/tv/datafeed), with the AI's
 * decision history rendered as marks on the bars where the runs decided.
 * TV's own toolbar owns timeframes, indicators, and drawings now — the
 * card chrome keeps only what TV can't know: our symbol registry, the
 * open-position badge, and the macro event strip. Multi-chart layouts are
 * TV-native (header layout toggle). */
import { Maximize2 } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TVChart } from "@/components/charts/TVChart";
import { useCalendar, useStatus, useSymbols } from "@/lib/api/queries";
import { fmtCountdown, fmtPnl, fmtPrice } from "@/lib/format";
import { TF_TO_RESOLUTION } from "@/lib/tv/datafeed";
import { levelFromSearchParams, type RefLevel } from "@/lib/levelFromRef";
import { countdownExpired, useCountdown } from "@/lib/useCountdown";
import { useUiStore } from "@/stores/ui";

// tradeable pairs come from /api/symbols (server-driven, same set as the
// run dialog); the registry's other symbols (DXY, US10Y, …) are
// correlation inputs, not chartable workspaces
const TRADE_SYMBOL_LABELS: Record<string, string> = {
  "BTC-USD": "BTC-USD · Bitcoin",
  XAUUSD: "XAUUSD · Gold",
  "ETH-USD": "ETH-USD · Ethereum",
  "SOL-USD": "SOL-USD · Solana",
  EURUSD: "EURUSD · Euro / US Dollar",
  USDJPY: "USDJPY · US Dollar / Yen",
};
const FALLBACK_TRADE_SYMBOLS = ["BTC-USD", "XAUUSD"];

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
  const chartCardRef = useRef<HTMLDivElement | null>(null);

  const symbols = useSymbols();
  const status = useStatus();
  const calendar = useCalendar();

  // keep global symbol in sync with the route
  if (useUiStore.getState().symbol !== symbol) setSymbol(symbol);

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

  // full-screen: button + `f` shortcut (dispatched as a window event)
  const toggleFullscreen = () => {
    const el = chartCardRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen();
  };
  useEffect(() => {
    const handler = () => toggleFullscreen();
    window.addEventListener("pro:fullscreen", handler);
    return () => window.removeEventListener("pro:fullscreen", handler);
  }, []);

  // the server-computed next MAJOR event (countdown-capable) beats the
  // first row of the raw release list (review P1.1); the countdown ticks
  // locally between refetches (R2.3)
  const nextMajor = calendar.data?.next_major;
  const nextMajorRemaining = useCountdown(nextMajor?.at ?? null);
  const nextRelease = nextMajor ?? calendar.data?.releases[0];

  return (
    <Card
      ref={chartCardRef}
      data-testid="chart-card"
      className="flex h-full flex-col bg-surface"
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2 whitespace-nowrap !text-lg !font-extrabold !normal-case !tracking-normal !text-fg">
          <label htmlFor="trade-symbol" className="sr-only">
            Trading pair
          </label>
          <select
            id="trade-symbol"
            data-testid="symbol-select"
            value={symbol}
            onChange={(event) => navigate(`/trade/${event.target.value}`)}
            className="cursor-pointer rounded-[10px] border border-border bg-transparent px-1.5 py-0.5 text-lg font-extrabold tracking-[-0.01em] text-fg hover:border-border-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {(symbols.data?.filter((s) => s.tradeable).map((s) => s.symbol) ??
              FALLBACK_TRADE_SYMBOLS).map((s) => (
              <option key={s} value={s}>
                {TRADE_SYMBOL_LABELS[s] ?? s}
              </option>
            ))}
          </select>
          <Badge variant="bull">live</Badge>
        </CardTitle>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {refLevel && (
            <span
              data-testid="level-badge"
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent-muted px-2 py-1 font-mono text-xs text-accent"
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
          <Button
            size="icon"
            variant="outline"
            aria-label="Full screen (f)"
            onClick={toggleFullscreen}
          >
            <Maximize2 size={13} />
          </Button>
        </div>
      </CardHeader>
      {/* overflow-hidden: last line of defence. Nothing inside may paint
          outside the card's rounded border. */}
      <CardContent className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {/* multi-chart layouts are TV-native now (header layout toggle,
            Trading Platform edition) — no external grid */}
        <div
          className="relative min-h-0 min-w-0 flex-1"
          data-testid="main-chart-tile"
        >
          <div className="absolute inset-0 overflow-hidden rounded-lg">
            <TVChart
              symbol={symbol}
              interval={interval}
              persistKey="workspace-main"
            />
          </div>
          {openPosition?.unrealized_pnl != null && (
            <div
              data-testid="position-badge"
              className={
                "absolute right-2 top-2 z-10 rounded-lg border border-border bg-surface/90 px-2 py-1 font-mono text-xs tabular " +
                (openPosition.unrealized_pnl >= 0 ? "text-bull" : "text-bear")
              }
            >
              {openPosition.quantity > 0 ? "long" : "short"}{" "}
              {Math.abs(openPosition.quantity)} ·{" "}
              {fmtPnl(openPosition.unrealized_pnl)}
              <span className="ml-1 text-fg-subtle">paper</span>
            </div>
          )}
        </div>
        {nextRelease && (
          <p
            className="mt-[10px] shrink-0 text-xs text-fg-subtle"
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
