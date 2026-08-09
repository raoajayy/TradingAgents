/** Book drift halts NEW entries while exits keep working. That halt used to
 * be invisible: an ETH-USD SELL recorded "accepted" while the router never
 * saw it (2026-08-09). The banner is safety chrome — it must state the halt,
 * name the reason, and never stack on top of a full trading halt. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EntriesBlockedBanner } from "@/app/StatusStrip";
import type { SystemStatus } from "@/lib/api/types";

const useStatus = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/queries", () => ({ useStatus }));

function renderWith(status: Partial<SystemStatus>) {
  useStatus.mockReturnValue({ data: status });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EntriesBlockedBanner />
    </QueryClientProvider>,
  );
}

describe("EntriesBlockedBanner", () => {
  it("states the halt and names the drifting symbol", () => {
    renderWith({
      trading_halted: false,
      entries_blocked: {
        blocked: true,
        reason: "book drift — unknown on venue: BTC-USD",
      },
    } as Partial<SystemStatus>);
    const banner = screen.getByTestId("entries-blocked-banner");
    expect(banner).toHaveTextContent(/NEW ENTRIES BLOCKED/i);
    expect(banner).toHaveTextContent("BTC-USD");
    // the operator must know exits still work — this is not a full halt
    expect(banner).toHaveTextContent(/Exits and flatten still work/i);
    expect(banner).toHaveAttribute("role", "alert");
  });

  it("renders nothing when the book is in sync", () => {
    renderWith({
      trading_halted: false,
      entries_blocked: { blocked: false, reason: "" },
    } as Partial<SystemStatus>);
    expect(screen.queryByTestId("entries-blocked-banner")).toBeNull();
  });

  it("defers to the full halt banner instead of stacking two alarms", () => {
    renderWith({
      trading_halted: true,
      entries_blocked: { blocked: true, reason: "book drift" },
    } as Partial<SystemStatus>);
    expect(screen.queryByTestId("entries-blocked-banner")).toBeNull();
  });

  it("renders nothing when the server omits the field (older backend)", () => {
    renderWith({ trading_halted: false } as Partial<SystemStatus>);
    expect(screen.queryByTestId("entries-blocked-banner")).toBeNull();
  });
});
