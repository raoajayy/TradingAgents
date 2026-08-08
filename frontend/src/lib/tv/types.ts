/** Minimal typings for the TradingView Charting Library (v30) surface we
 * actually touch. The library ships no ESM types here — it is loaded at
 * runtime from /charting_library/ (same-origin requirement), so we declare
 * just enough structure to keep our call sites honest. */

export interface TVBar {
  time: number; // milliseconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface TVSymbolInfo {
  name: string;
  ticker: string;
  description: string;
  type: string;
  session: string;
  timezone: string;
  exchange: string;
  listed_exchange: string;
  format: "price";
  minmov: number;
  pricescale: number;
  has_intraday: boolean;
  has_weekly_and_monthly: boolean;
  supported_resolutions: string[];
  visible_plots_set: "ohlcv" | "ohlc";
  volume_precision: number;
  data_status: "streaming" | "endofday";
}

export interface TVMark {
  id: string;
  time: number; // seconds
  color: { border: string; background: string };
  text: string;
  label: string;
  labelFontColor: string;
  minSize: number;
}

export interface TVPeriodParams {
  from: number;
  to: number;
  firstDataRequest: boolean;
  countBack?: number;
}

export interface TVDatafeed {
  onReady(cb: (config: object) => void): void;
  searchSymbols(
    userInput: string,
    exchange: string,
    symbolType: string,
    onResult: (items: object[]) => void,
  ): void;
  resolveSymbol(
    symbolName: string,
    onResolve: (info: TVSymbolInfo) => void,
    onError: (reason: string) => void,
  ): void;
  getBars(
    symbolInfo: TVSymbolInfo,
    resolution: string,
    periodParams: TVPeriodParams,
    onResult: (bars: TVBar[], meta: { noData: boolean }) => void,
    onError: (reason: string) => void,
  ): void;
  subscribeBars(
    symbolInfo: TVSymbolInfo,
    resolution: string,
    onTick: (bar: TVBar) => void,
    listenerGuid: string,
  ): void;
  unsubscribeBars(listenerGuid: string): void;
  getMarks?(
    symbolInfo: TVSymbolInfo,
    from: number,
    to: number,
    onDataCallback: (marks: TVMark[]) => void,
    resolution: string,
  ): void;
}

export interface TVSubscription {
  subscribe(obj: object | null, callback: () => void): void;
  unsubscribe(obj: object | null, callback: () => void): void;
}

export interface TVChartApi {
  setSymbol(symbol: string, callback?: () => void): void;
  setResolution(resolution: string, callback?: () => void): void;
  resolution(): string;
  symbol(): string;
  onSymbolChanged(): TVSubscription;
}

export interface TVWidget {
  onChartReady(cb: () => void): void;
  subscribe(event: string, cb: () => void): void;
  activeChart(): TVChartApi;
  changeTheme(theme: "Dark" | "Light"): Promise<void>;
  applyOverrides(overrides: Record<string, string | number | boolean>): void;
  save(cb: (state: object) => void): void;
  remove(): void;
}

export interface TVWidgetOptions {
  container: HTMLElement;
  library_path: string;
  symbol: string;
  interval: string;
  datafeed: TVDatafeed;
  locale: string;
  autosize: boolean;
  theme: "Dark" | "Light";
  timezone?: string;
  saved_data?: object;
  auto_save_delay?: number;
  loading_screen?: { backgroundColor?: string; foregroundColor?: string };
  overrides?: Record<string, string | number | boolean>;
  disabled_features?: string[];
  enabled_features?: string[];
}

export interface TradingViewNamespace {
  widget: new (options: TVWidgetOptions) => TVWidget;
  version?: () => string;
}

declare global {
  interface Window {
    TradingView?: TradingViewNamespace;
  }
}
