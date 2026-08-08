/** One-time runtime loader for the TradingView Charting Library. The
 * library is a UMD loader that pulls hashed chunks from
 * /charting_library/bundles/ and its widget iframe must be same-origin,
 * so it is mirrored into our static build (scripts/mirror_charting_library.py)
 * rather than hotlinked. */
import type { TradingViewNamespace } from "./types";

const LIBRARY_SRC = "/charting_library/charting_library.standalone.js";

let pending: Promise<TradingViewNamespace> | null = null;

export function loadChartingLibrary(): Promise<TradingViewNamespace> {
  if (window.TradingView?.widget) return Promise.resolve(window.TradingView);
  pending ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = LIBRARY_SRC;
    script.async = true;
    script.onload = () => {
      if (window.TradingView?.widget) resolve(window.TradingView);
      else reject(new Error("charting library loaded but TradingView missing"));
    };
    script.onerror = () => {
      // clear so a transient network failure can be retried on next mount
      pending = null;
      script.remove();
      reject(new Error(`failed to load ${LIBRARY_SRC}`));
    };
    document.head.appendChild(script);
  });
  return pending;
}
