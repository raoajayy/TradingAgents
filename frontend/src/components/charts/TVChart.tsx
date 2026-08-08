/** TradingView Charting Library chart (replaced the hand-rolled
 * lightweight-charts PriceChart). Bars come from the Pyth history proxy,
 * live ticks from the Hermes stream, AI decisions as datafeed marks —
 * see lib/tv/datafeed.ts. The library itself is mirrored same-origin
 * under /charting_library/ (its iframe cannot be scripted cross-origin). */
import { useEffect, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { chartColors } from "@/components/charts/useLightweightChart";
import { createDatafeed } from "@/lib/tv/datafeed";
import { loadChartingLibrary } from "@/lib/tv/loader";
import type { TVWidget } from "@/lib/tv/types";
import { useUiStore } from "@/stores/ui";

const STATE_PREFIX = "pro-tv-state:";

function tvTheme(theme: string): "Dark" | "Light" {
  return theme === "dark" ? "Dark" : "Light";
}

/** candle/background overrides so the widget matches our design tokens */
function tokenOverrides(): Record<string, string | number | boolean> {
  const colors = chartColors();
  return {
    "paneProperties.background": colors.bg,
    "paneProperties.backgroundType": "solid",
    "mainSeriesProperties.candleStyle.upColor": colors.bull,
    "mainSeriesProperties.candleStyle.downColor": colors.bear,
    "mainSeriesProperties.candleStyle.borderUpColor": colors.bull,
    "mainSeriesProperties.candleStyle.borderDownColor": colors.bear,
    "mainSeriesProperties.candleStyle.wickUpColor": colors.bull,
    "mainSeriesProperties.candleStyle.wickDownColor": colors.bear,
  };
}

function loadSavedState(persistKey: string): object | undefined {
  try {
    const raw = localStorage.getItem(STATE_PREFIX + persistKey);
    return raw ? (JSON.parse(raw) as object) : undefined;
  } catch {
    return undefined;
  }
}

export function TVChart({
  symbol,
  interval,
  persistKey,
  className,
}: {
  symbol: string;
  interval: string;
  /** set on the main chart only: TV drawings/studies survive reloads */
  persistKey?: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetRef = useRef<TVWidget | null>(null);
  const readyRef = useRef(false);
  const theme = useUiStore((s) => s.theme);
  const [failed, setFailed] = useState<string | null>(null);

  // mount once; symbol/interval/theme changes are applied via the API
  useEffect(() => {
    let disposed = false;
    loadChartingLibrary()
      .then((TV) => {
        if (disposed || !containerRef.current) return;
        const widget = new TV.widget({
          container: containerRef.current,
          library_path: "/charting_library/",
          symbol,
          interval,
          datafeed: createDatafeed(),
          locale: "en",
          autosize: true,
          theme: tvTheme(useUiStore.getState().theme),
          saved_data: persistKey ? loadSavedState(persistKey) : undefined,
          auto_save_delay: 5,
          loading_screen: { backgroundColor: chartColors().bg },
          overrides: tokenOverrides(),
          disabled_features: [
            "header_symbol_search",
            "symbol_search_hot_key",
            // Trading Platform edition widgets — no broker is wired here;
            // orders route through our own arming/venue path, never TV
            "trading_account_manager",
            "order_panel",
            "buy_sell_buttons",
            "dom_widget",
            "open_account_manager",
          ],
        });
        widgetRef.current = widget;
        widget.onChartReady(() => {
          if (disposed) return;
          readyRef.current = true;
          if (persistKey) {
            widget.subscribe("onAutoSaveNeeded", () =>
              widget.save((state) => {
                try {
                  localStorage.setItem(
                    STATE_PREFIX + persistKey,
                    JSON.stringify(state),
                  );
                } catch {
                  /* quota — persistence is best-effort */
                }
              }),
            );
          }
        });
      })
      .catch((error) => {
        if (!disposed) setFailed(String(error));
      });
    return () => {
      disposed = true;
      readyRef.current = false;
      try {
        widgetRef.current?.remove();
      } catch {
        /* already gone with the DOM */
      }
      widgetRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- construct once
  }, []);

  useEffect(() => {
    if (readyRef.current) widgetRef.current?.activeChart().setSymbol(symbol);
  }, [symbol]);

  useEffect(() => {
    if (readyRef.current) {
      widgetRef.current?.activeChart().setResolution(interval);
    }
  }, [interval]);

  useEffect(() => {
    const widget = widgetRef.current;
    if (!readyRef.current || !widget) return;
    void widget.changeTheme(tvTheme(theme)).then(() => {
      // changeTheme resets colors to the stock palette; re-assert tokens
      widget.applyOverrides(tokenOverrides());
    });
  }, [theme]);

  if (failed) {
    return (
      <EmptyState
        kind="error"
        title="Chart engine unavailable"
        detail={failed}
        className={className}
      />
    );
  }
  return (
    <div
      ref={containerRef}
      data-testid="tv-chart"
      className={className ?? "h-full w-full"}
    />
  );
}
