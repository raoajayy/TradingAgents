/** Crosshair + visible-range synchronization across charts in a group.
 * Time-based only: each chart keeps its own price scale (overlaying two
 * assets on one scale would be dishonest visual math). An isSyncing
 * guard breaks the feedback loop. */
import type { IChartApi, MouseEventParams } from "lightweight-charts";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";

interface SyncRegistry {
  /** id identifies the CHART; group identifies the sync set. Keying the
   * map by group (as this used to) meant every chart in a group evicted
   * the previous one, so the map never held more than one entry: peer
   * sync was silently dead and one cell's unmount deleted another cell's
   * registration.
   *
   * isDisposed: charts are torn down by useLightweightChart's cleanup
   * before this registration's cleanup runs on unmount — unsubscribing
   * a disposed chart throws, and remove() already dropped the handlers */
  register: (
    id: string,
    group: string,
    chart: IChartApi,
    isDisposed: () => boolean,
  ) => () => void;
  /** true while a peer-driven range/crosshair push is in flight. Consumers
   * (PriceChart's load-older trigger) must ignore range events they did
   * not cause, or a peer's scroll pages in history on every chart. */
  isSyncing: () => boolean;
}

type Entry = { group: string; chart: IChartApi };

export function createSyncRegistry(): SyncRegistry {
  const charts = new Map<string, Entry>();
  let syncing = false;

  const eachPeer = (id: string, group: string, fn: (chart: IChartApi) => void) => {
    if (syncing) return;
    syncing = true;
    try {
      charts.forEach((entry, peerId) => {
        if (peerId === id || entry.group !== group) return;
        fn(entry.chart);
      });
    } finally {
      syncing = false;
    }
  };

  return {
    isSyncing: () => syncing,
    register(id, group, chart, isDisposed) {
      charts.set(id, { group, chart });

      const onRange = () => {
        // TIME range, not logical: peers plot different symbols and
        // timeframes, so a bar-index range means nothing to them — it
        // lands on an unrelated date window. (The header above always
        // claimed time-based; the logical push was never exercised
        // because the map only ever held one chart.)
        const visible = chart.timeScale().getVisibleRange();
        if (!visible) return;
        eachPeer(id, group, (peer) => peer.timeScale().setVisibleRange(visible));
      };

      const onCrosshair = (params: MouseEventParams) => {
        eachPeer(id, group, (peer) => {
          if (params.time == null) {
            peer.clearCrosshairPosition();
            return;
          }
          // sync by time against the peer's first series
          const peerSeries = peer.panes()[0]?.getSeries()[0];
          if (peerSeries) {
            peer.setCrosshairPosition(NaN, params.time, peerSeries);
          }
        });
      };

      chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
      chart.subscribeCrosshairMove(onCrosshair);
      return () => {
        charts.delete(id);
        if (isDisposed()) return;
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
        chart.unsubscribeCrosshairMove(onCrosshair);
      };
    },
  };
}

const SyncContext = createContext<SyncRegistry | null>(null);

export function ChartSyncProvider({ children }: { children: ReactNode }) {
  const registryRef = useRef<SyncRegistry>(undefined as unknown as SyncRegistry);
  if (registryRef.current === undefined) registryRef.current = createSyncRegistry();

  return (
    <SyncContext.Provider value={registryRef.current}>
      {children}
    </SyncContext.Provider>
  );
}

/** Called by PriceChart: joins the surrounding sync group when syncId set.
 * Returns an isSyncing probe (undefined outside a provider). */
export function useChartSync(
  syncId: string | undefined,
  chartRef: RefObject<IChartApi | null>,
): (() => boolean) | undefined {
  const registry = useContext(SyncContext);
  // stable per component instance — this is the chart identity the group
  // map keys on, so two cells in one group no longer collide
  const instanceId = useId();
  useEffect(() => {
    const chart = chartRef.current;
    if (!syncId || !registry || !chart) return;
    return registry.register(instanceId, syncId, chart, () => chartRef.current !== chart);
  }, [syncId, registry, chartRef, instanceId]);
  return registry?.isSyncing;
}
