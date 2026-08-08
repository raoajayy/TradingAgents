/** TradingView JS-API datafeed backed by:
 *  - history: /api/tv/history (our FastAPI proxy of the Pyth UDF history
 *    API — upstream sends no CORS headers, and the proxy adds auth)
 *  - live ticks: wss://datafeeds-590z.onrender.com (Pyth/Hermes firehose:
 *    pushes a snapshot then per-second {symbol,"BTC/USD",mid,...} ticks
 *    for every pair; no subscribe handshake — we filter client-side)
 *  - marks: /api/chart/annotations — the AI's decision history rendered
 *    as B/S/H marks on the bars where the runs decided.
 */
import { apiFetch } from "@/lib/api/client";
import {
  ChartAnnotationsSchema,
  SymbolsSchema,
  type SymbolSpec,
} from "@/lib/api/types";
import type {
  TVBar,
  TVDatafeed,
  TVMark,
  TVPeriodParams,
  TVSymbolInfo,
} from "./types";

export const SUPPORTED_RESOLUTIONS = [
  "1", "5", "15", "30", "60", "240", "1D", "1W",
];

/** ui-store timeframe strings → TV resolutions (for the initial interval) */
export const TF_TO_RESOLUTION: Record<string, string> = {
  "1m": "1",
  "5m": "5",
  "15m": "15",
  "30m": "30",
  "1h": "60",
  "4h": "240",
  "1d": "1D",
  "1w": "1W",
};

export function resolutionSeconds(resolution: string): number {
  if (resolution === "1D" || resolution === "D") return 86_400;
  if (resolution === "1W" || resolution === "W") return 604_800;
  return Number(resolution) * 60;
}

// epoch day zero (1970-01-01) is a THURSDAY; upstream weekly bars start
// Monday 00:00 UTC (verified live), so weekly buckets need this anchor —
// raw floor(ts/week) would roll live weekly bars every Thursday
const WEEK_ANCHOR_MS = 4 * 86_400_000; // first Monday: 1970-01-05

/** UTC-aligned bar bucket for a tick timestamp (ms), matching the
 * upstream bar grid: minutes/days from epoch, weeks from Monday. */
export function tickBucket(tsMs: number, resolution: string): number {
  const barMs = resolutionSeconds(resolution) * 1000;
  if (resolution === "1W" || resolution === "W") {
    return (
      Math.floor((tsMs - WEEK_ANCHOR_MS) / barMs) * barMs + WEEK_ANCHOR_MS
    );
  }
  return Math.floor(tsMs / barMs) * barMs;
}

/** "Crypto.BTC/USD" → "BTC/USD", the pair key the Hermes stream uses */
export function streamPairOf(pythSymbol: string): string {
  const dot = pythSymbol.indexOf(".");
  return dot >= 0 ? pythSymbol.slice(dot + 1) : pythSymbol;
}

interface UdfHistory {
  s: string;
  errmsg?: string;
  t?: number[];
  o?: number[];
  h?: number[];
  l?: number[];
  c?: number[];
  v?: number[];
}

/** UDF parallel arrays → TV bar objects (ms times, ascending) */
export function udfToBars(udf: UdfHistory): TVBar[] {
  const { t = [], o = [], h = [], l = [], c = [], v = [] } = udf;
  return t.map((time, i) => ({
    time: time * 1000,
    open: o[i]!,
    high: h[i]!,
    low: l[i]!,
    close: c[i]!,
    volume: v[i],
  }));
}

type AnnotationRuns = ReturnType<typeof ChartAnnotationsSchema.parse>["runs"];

/** AI runs → TV marks. Only decided runs (BUY/SELL/HOLD) become marks —
 * feed-starved rejections are infra noise, not trading signal (the same
 * honesty rule the Home page follows). */
export function marksFromRuns(
  runs: AnnotationRuns,
  from: number,
  to: number,
): TVMark[] {
  const styles: Record<string, { bg: string; label: string }> = {
    BUY: { bg: "#16a34a", label: "B" },
    SELL: { bg: "#dc2626", label: "S" },
    HOLD: { bg: "#64748b", label: "H" },
  };
  return runs.flatMap((run) => {
    if (run.time == null || run.time < from || run.time > to) return [];
    const style = styles[(run.action ?? "").toUpperCase()];
    if (!style) return [];
    // confidence arrives 0–100 from the server (displayed raw everywhere)
    const confidence =
      run.confidence != null
        ? ` · confidence ${run.confidence.toFixed(0)}%`
        : "";
    return [{
      id: run.run_id,
      time: run.time,
      color: { border: style.bg, background: style.bg },
      text: `AI ${run.action}${confidence}${
        run.market_regime ? ` · ${run.market_regime}` : ""
      }`,
      label: style.label,
      labelFontColor: "#ffffff",
      minSize: 18,
    }];
  });
}

// ---------------------------------------------------------------------------
// live stream: one shared socket for every chart/cell on the page

const STREAM_URL =
  (import.meta.env.VITE_TV_STREAM_URL as string | undefined) ??
  "wss://datafeeds-590z.onrender.com/";

interface StreamTick {
  mid: number;
  timestamp: number; // ms
}

type TickHandler = (tick: StreamTick) => void;

// a healthy firehose ticks every second; 30s of silence means the socket
// half-died (idle proxy, sleeping dyno) and must be recycled — without
// this, charts freeze while still LOOKING live (the honesty rule)
const SILENCE_LIMIT_MS = 30_000;

class PythStream {
  private socket: WebSocket | null = null;
  private handlers = new Map<string, Set<TickHandler>>(); // pair → handlers
  private retryMs = 1_000;
  private lastMessageAt = 0;
  private watchdog: number | null = null;

  subscribe(pair: string, handler: TickHandler): () => void {
    let set = this.handlers.get(pair);
    if (!set) this.handlers.set(pair, (set = new Set()));
    set.add(handler);
    this.ensureOpen();
    return () => {
      set.delete(handler);
      if (set.size === 0) this.handlers.delete(pair);
      if (this.handlers.size === 0) this.close();
    };
  }

  private ensureOpen() {
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    try {
      this.socket = new WebSocket(STREAM_URL);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.lastMessageAt = Date.now();
    this.watchdog ??= window.setInterval(() => {
      if (
        this.socket?.readyState === WebSocket.OPEN &&
        Date.now() - this.lastMessageAt > SILENCE_LIMIT_MS
      ) {
        this.socket.close(); // onclose path reconnects with backoff
      }
    }, 10_000);
    this.socket.onopen = () => {
      this.retryMs = 1_000;
      this.lastMessageAt = Date.now();
    };
    this.socket.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      let parsed: unknown;
      try {
        parsed = JSON.parse(String(event.data));
      } catch {
        return;
      }
      const msg = parsed as {
        type?: string;
        data?: unknown;
      };
      if (msg.type === "prices" && Array.isArray(msg.data)) {
        for (const row of msg.data) this.dispatch(row);
      } else if (msg.type === "snapshot" && msg.data && typeof msg.data === "object") {
        for (const row of Object.values(msg.data)) this.dispatch(row);
      }
    };
    this.socket.onclose = () => {
      this.socket = null;
      if (this.handlers.size > 0) this.scheduleRetry();
    };
    this.socket.onerror = () => this.socket?.close();
  }

  private dispatch(row: unknown) {
    const tick = row as { symbol?: string; mid?: number; timestamp?: number };
    if (!tick.symbol || typeof tick.mid !== "number") return;
    const set = this.handlers.get(tick.symbol);
    if (!set) return;
    const payload = { mid: tick.mid, timestamp: tick.timestamp ?? Date.now() };
    for (const handler of set) handler(payload);
  }

  private scheduleRetry() {
    const delay = this.retryMs;
    this.retryMs = Math.min(this.retryMs * 2, 30_000);
    window.setTimeout(() => {
      if (this.handlers.size > 0) this.ensureOpen();
    }, delay);
  }

  private close() {
    if (this.watchdog != null) {
      window.clearInterval(this.watchdog);
      this.watchdog = null;
    }
    this.socket?.close();
    this.socket = null;
  }
}

const stream = new PythStream();

/** Live mid-price ticks for UI chrome (favorites bar) — same shared
 * Hermes socket the chart's subscribeBars uses. Returns an unsubscribe. */
export function subscribeStreamPair(
  pair: string,
  handler: (tick: { mid: number; timestamp: number }) => void,
): () => void {
  return stream.subscribe(pair, handler);
}

// ---------------------------------------------------------------------------
// datafeed

// /api/symbols is already served react-query-cached elsewhere; the datafeed
// keeps its own tiny promise cache to stay framework-independent
let symbolsCache: Promise<SymbolSpec[]> | null = null;
function fetchSymbols(): Promise<SymbolSpec[]> {
  symbolsCache ??= apiFetch<unknown>("/api/symbols")
    .then((raw) => SymbolsSchema.parse(raw))
    .catch((error) => {
      symbolsCache = null; // transient failures must not poison the cache
      throw error;
    });
  return symbolsCache;
}

// TV calls getMarks on EVERY visible-range change (× panes in multi-chart
// layouts); annotations move on run cadence, not scroll cadence — a short
// single-flight cache stops the pan/zoom fetch spam
const MARKS_TTL_MS = 60_000;
const marksCache = new Map<
  string,
  { at: number; promise: Promise<AnnotationRuns> }
>();
function fetchRuns(symbol: string): Promise<AnnotationRuns> {
  const cached = marksCache.get(symbol);
  if (cached && Date.now() - cached.at < MARKS_TTL_MS) return cached.promise;
  const promise = apiFetch<unknown>(
    `/api/chart/annotations?symbol=${encodeURIComponent(symbol)}`,
  )
    .then((raw) => ChartAnnotationsSchema.parse(raw).runs)
    .catch((error) => {
      marksCache.delete(symbol);
      throw error;
    });
  marksCache.set(symbol, { at: Date.now(), promise });
  return promise;
}

/** asset class from our dashboard naming (BTC-USD crypto, XAUUSD metal,
 * the rest FX) — shared by resolveSymbol and the search dialog rows */
export function assetTypeOf(symbol: string): "crypto" | "forex" | "commodity" {
  if (symbol.endsWith("-USD")) return "crypto";
  return symbol === "XAUUSD" ? "commodity" : "forex";
}

function toSymbolInfo(spec: SymbolSpec): TVSymbolInfo {
  const type = assetTypeOf(spec.symbol);
  const isCrypto = type === "crypto";
  const isJpyQuote = spec.symbol.endsWith("JPY");
  const isFx = type === "forex";
  return {
    name: spec.symbol,
    ticker: spec.symbol,
    description: spec.symbol,
    type,
    // the Pyth oracle feed's bars are UTC-day aligned for EVERY asset
    // (verified upstream — even FX rolls at 00:00 UTC, not NY 17:00), so
    // the session must say so; FX weekends appear honestly as gaps
    session: "24x7",
    timezone: "Etc/UTC",
    exchange: "Pyth",
    listed_exchange: "Pyth",
    format: "price",
    minmov: 1,
    // JPY quotes carry 3 decimals, other FX 5, crypto/gold 2
    pricescale: isJpyQuote ? 1_000 : isFx ? 100_000 : 100,
    has_intraday: true,
    has_weekly_and_monthly: true,
    supported_resolutions: SUPPORTED_RESOLUTIONS,
    // Pyth is an oracle price feed — volume only exists for crypto
    visible_plots_set: isCrypto ? "ohlcv" : "ohlc",
    volume_precision: 2,
    data_status: "streaming",
  };
}

export function createDatafeed(): TVDatafeed {
  // last bar per subscription, the seam between history and the stream
  const lastBars = new Map<string, TVBar>();
  const unsubscribers = new Map<string, () => void>();
  const pythBySymbol = new Map<string, string>();

  return {
    onReady(cb) {
      window.setTimeout(() =>
        cb({
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          supports_marks: true,
          supports_timescale_marks: false,
          supports_time: false,
        }),
      );
    },

    searchSymbols(userInput, _exchange, _symbolType, onResult) {
      void fetchSymbols().then((specs) => {
        const needle = userInput.toUpperCase();
        onResult(
          specs
            .filter((s) => s.pyth_symbol && s.symbol.includes(needle))
            .map((s) => ({
              symbol: s.symbol,
              full_name: s.symbol,
              description: s.pyth_symbol!,
              exchange: "Pyth",
              type: assetTypeOf(s.symbol),
            })),
        );
      });
    },

    resolveSymbol(symbolName, onResolve, onError) {
      fetchSymbols()
        .then((specs) => {
          const spec = specs.find((s) => s.symbol === symbolName);
          if (!spec?.pyth_symbol) {
            onError(`no TV data source for ${symbolName}`);
            return;
          }
          pythBySymbol.set(spec.symbol, spec.pyth_symbol);
          onResolve(toSymbolInfo(spec));
        })
        .catch((error) => onError(String(error)));
    },

    getBars(symbolInfo, resolution, periodParams: TVPeriodParams, onResult, onError) {
      const { from, to, firstDataRequest } = periodParams;
      apiFetch<UdfHistory>(
        `/api/tv/history?symbol=${encodeURIComponent(symbolInfo.name)}` +
          `&resolution=${encodeURIComponent(resolution)}&from=${from}&to=${to}`,
      )
        .then((udf) => {
          if (udf.s === "error") {
            onError(udf.errmsg ?? "history error");
            return;
          }
          const bars = udfToBars(udf);
          if (firstDataRequest && bars.length > 0) {
            lastBars.set(`${symbolInfo.name}#${resolution}`, bars[bars.length - 1]!);
          }
          onResult(bars, { noData: bars.length === 0 });
        })
        .catch((error) => onError(String(error)));
    },

    subscribeBars(symbolInfo, resolution, onTick, listenerGuid) {
      const pyth = pythBySymbol.get(symbolInfo.name);
      if (!pyth) return;
      const barKey = `${symbolInfo.name}#${resolution}`;
      const unsubscribe = stream.subscribe(streamPairOf(pyth), (tick) => {
        const bucket = tickBucket(tick.timestamp, resolution);
        const last = lastBars.get(barKey);
        let next: TVBar;
        if (last && bucket <= last.time) {
          next = {
            ...last,
            high: Math.max(last.high, tick.mid),
            low: Math.min(last.low, tick.mid),
            close: tick.mid,
          };
        } else {
          next = {
            time: bucket,
            open: tick.mid,
            high: tick.mid,
            low: tick.mid,
            close: tick.mid,
            volume: 0,
          };
        }
        lastBars.set(barKey, next);
        onTick(next);
      });
      unsubscribers.set(listenerGuid, unsubscribe);
    },

    unsubscribeBars(listenerGuid) {
      unsubscribers.get(listenerGuid)?.();
      unsubscribers.delete(listenerGuid);
    },

    getMarks(symbolInfo, from, to, onDataCallback) {
      fetchRuns(symbolInfo.name)
        .then((runs) => onDataCallback(marksFromRuns(runs, from, to)))
        .catch(() => onDataCallback([]));
    },
  };
}
