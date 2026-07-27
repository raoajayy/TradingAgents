/** dedupeAlerts collapses identical infra repeats into ×N without losing
 * distinct alerts — the fix for a feed outage flooding the feed. */
import { describe, expect, it } from "vitest";

import { dedupeAlerts } from "@/lib/alerts";
import type { Alert } from "@/lib/api/types";

const a = (over: Partial<Alert>): Alert => ({
  time: "2026-07-27T10:00:00Z",
  run_id: "r1",
  severity: "info",
  text: "feed unavailable: coinmetrics",
  ...over,
});

describe("dedupeAlerts", () => {
  it("collapses identical alerts into one row with a summed count", () => {
    const out = dedupeAlerts([
      a({ time: "2026-07-27T10:00:00Z" }),
      a({ time: "2026-07-27T11:00:00Z" }),
      a({ time: "2026-07-27T09:00:00Z" }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]!.count).toBe(3);
    expect(out[0]!.time).toBe("2026-07-27T11:00:00Z"); // newest kept
  });

  it("keeps distinct alerts separate and respects existing counts", () => {
    const out = dedupeAlerts([
      a({ text: "feed unavailable: coinmetrics", count: 5 }),
      a({ text: "feed unavailable: coinmetrics" }),
      a({ severity: "critical", text: "prompt injection quarantined" }),
      a({ text: "trade rejected at join", severity: "warning" }),
    ]);
    expect(out).toHaveLength(3);
    const feed = out.find((x) => x.text.includes("coinmetrics"))!;
    expect(feed.count).toBe(6); // 5 + 1
    expect(out.find((x) => x.severity === "critical")).toBeTruthy();
  });

  it("groups by severity too — same text at different severities stays split", () => {
    const out = dedupeAlerts([
      a({ text: "x", severity: "info" }),
      a({ text: "x", severity: "warning" }),
    ]);
    expect(out).toHaveLength(2);
  });

  it("orders by most recent occurrence", () => {
    const out = dedupeAlerts([
      a({ text: "old", time: "2026-07-27T08:00:00Z" }),
      a({ text: "new", time: "2026-07-27T12:00:00Z" }),
    ]);
    expect(out[0]!.text).toBe("new");
  });

  it("empty in, empty out", () => {
    expect(dedupeAlerts([])).toEqual([]);
  });
});
