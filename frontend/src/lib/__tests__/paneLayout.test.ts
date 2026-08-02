import { afterEach, describe, expect, it } from "vitest";

import {
  loadPaneFactors,
  savePaneFactors,
} from "@/components/charts/paneLayout";

afterEach(() => localStorage.clear());

describe("pane factor persistence", () => {
  it("round-trips factors keyed by surface and pane count", () => {
    savePaneFactors("workspace-main", 3, [3, 0.8, 1]);
    expect(loadPaneFactors("workspace-main", 3)).toEqual([3, 0.8, 1]);
    expect(loadPaneFactors("workspace-main", 4)).toBeNull(); // other shape
  });

  it("isolates surfaces that share a pane count", () => {
    // the bug: main chart and grid cells are both 2-pane layouts, so a
    // click on a small grid cell used to overwrite the main chart's
    // proportions through the shared key "2"
    savePaneFactors("workspace-main", 2, [400, 78]);
    savePaneFactors("workspace-grid", 2, [210, 78]);
    expect(loadPaneFactors("workspace-main", 2)).toEqual([400, 78]);
    expect(loadPaneFactors("workspace-grid", 2)).toEqual([210, 78]);
  });

  it("refuses to restore a collapsed price pane", () => {
    // lightweight-charts clamps at 30px; once persisted the user cannot
    // drag it back, so treat it as corruption rather than a preference
    savePaneFactors("workspace-main", 2, [30, 400]);
    expect(loadPaneFactors("workspace-main", 2)).toBeNull();
  });

  it("rejects malformed saves and loads", () => {
    savePaneFactors("workspace-main", 3, [3, 0.8]); // wrong length
    expect(loadPaneFactors("workspace-main", 3)).toBeNull();
    savePaneFactors("workspace-main", 2, [3, 0]); // non-positive factor
    expect(loadPaneFactors("workspace-main", 2)).toBeNull();
    localStorage.setItem("pro-pane-factors", "{not json");
    expect(loadPaneFactors("workspace-main", 2)).toBeNull();
  });
});
