import { describe, expect, it, vi } from "vitest";

import { createSyncRegistry } from "@/components/charts/ChartSync";

/** Minimal IChartApi surface the registry touches. */
function stubChart(visible: { from: number; to: number } | null = { from: 1, to: 2 }) {
  const handlers: {
    range: (() => void)[];
    crosshair: ((p: unknown) => void)[];
  } = { range: [], crosshair: [] };
  const series = { id: "series" };
  const chart = {
    setVisibleRange: vi.fn(),
    setCrosshairPosition: vi.fn(),
    clearCrosshairPosition: vi.fn(),
    timeScale: () => ({
      getVisibleRange: () => visible,
      setVisibleRange: chart.setVisibleRange,
      subscribeVisibleLogicalRangeChange: (fn: () => void) => handlers.range.push(fn),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
    }),
    subscribeCrosshairMove: (fn: (p: unknown) => void) => handlers.crosshair.push(fn),
    unsubscribeCrosshairMove: vi.fn(),
    panes: () => [{ getSeries: () => [series] }],
    fireRange: () => handlers.range.forEach((fn) => fn()),
    fireCrosshair: (p: unknown) => handlers.crosshair.forEach((fn) => fn(p)),
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return chart as any;
}

describe("chart sync registry", () => {
  it("pushes ranges to peers in the same group only", () => {
    const registry = createSyncRegistry();
    const a = stubChart({ from: 10, to: 20 });
    const b = stubChart();
    const c = stubChart();
    registry.register("a", "workspace", a, () => false);
    registry.register("b", "workspace", b, () => false);
    registry.register("c", "other", c, () => false);

    a.fireRange();
    expect(b.setVisibleRange).toHaveBeenCalledWith({ from: 10, to: 20 });
    expect(c.setVisibleRange).not.toHaveBeenCalled();
    expect(a.setVisibleRange).not.toHaveBeenCalled(); // never itself
  });

  it("keeps other charts registered when one unmounts", () => {
    // the regression: the map was keyed by GROUP, so registering b evicted
    // a, and b's teardown then deleted the group's only entry
    const registry = createSyncRegistry();
    const a = stubChart({ from: 5, to: 9 });
    const b = stubChart();
    const c = stubChart();
    registry.register("a", "workspace", a, () => false);
    const unregisterB = registry.register("b", "workspace", b, () => false);
    registry.register("c", "workspace", c, () => false);

    unregisterB();
    a.fireRange();
    expect(c.setVisibleRange).toHaveBeenCalledWith({ from: 5, to: 9 });
    expect(b.setVisibleRange).not.toHaveBeenCalled();
  });

  it("does not re-enter when a peer's handler fires during a push", () => {
    const registry = createSyncRegistry();
    const a = stubChart({ from: 1, to: 2 });
    const b = stubChart({ from: 3, to: 4 });
    registry.register("a", "workspace", a, () => false);
    registry.register("b", "workspace", b, () => false);
    // b echoes the push back, as lightweight-charts does in practice
    b.setVisibleRange.mockImplementation(() => b.fireRange());

    a.fireRange();
    expect(b.setVisibleRange).toHaveBeenCalledTimes(1);
    expect(a.setVisibleRange).not.toHaveBeenCalled();
  });

  it("exposes isSyncing so consumers can ignore peer-driven events", () => {
    const registry = createSyncRegistry();
    const a = stubChart({ from: 1, to: 2 });
    const b = stubChart();
    registry.register("a", "workspace", a, () => false);
    registry.register("b", "workspace", b, () => false);

    let sawSyncing = false;
    b.setVisibleRange.mockImplementation(() => {
      sawSyncing = registry.isSyncing();
    });
    expect(registry.isSyncing()).toBe(false);
    a.fireRange();
    expect(sawSyncing).toBe(true);
    expect(registry.isSyncing()).toBe(false);
  });

  it("mirrors and clears the crosshair across peers", () => {
    const registry = createSyncRegistry();
    const a = stubChart();
    const b = stubChart();
    registry.register("a", "workspace", a, () => false);
    registry.register("b", "workspace", b, () => false);

    a.fireCrosshair({ time: 1_700 });
    expect(b.setCrosshairPosition).toHaveBeenCalledWith(
      NaN,
      1_700,
      expect.anything(),
    );
    a.fireCrosshair({ time: null });
    expect(b.clearCrosshairPosition).toHaveBeenCalled();
  });
});
