/** Market Intelligence: regime, macro calendar, metric groups, and the
 * feed-coverage panel — unsubscribed paid feeds render locked with
 * honest copy. Weakness as trust signal. */
import { Lock } from "lucide-react";
import { useState } from "react";

import { AlertBuilder } from "@/components/AlertBuilder";
import { EmptyState } from "@/components/EmptyState";
import { StatCard } from "@/components/StatCard";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SkeletonCard } from "@/components/ui/skeleton";
import { useCalendar, useCorrelations, useIntel, useOverview } from "@/lib/api/queries";
import type { Intel } from "@/lib/api/types";
import { fmtCountdown, fmtDateTime, fmtMetricValue, relativeAge } from "@/lib/format";
import { countdownExpired, useCountdown } from "@/lib/useCountdown";
import { cn } from "@/lib/utils";

const GROUPS: { title: string; names: string[] }[] = [
  {
    title: "BTC derivatives",
    names: ["FUNDING_RATE", "MARK_PRICE", "OPEN_INTEREST", "ORDERBOOK_IMBALANCE"],
  },
  {
    title: "On-chain & sentiment",
    names: ["MVRV", "ACTIVE_ADDRESSES", "REALIZED_CAP", "FEAR_GREED", "HASH_RATE"],
  },
  {
    title: "Gold cross-asset",
    names: ["DXY", "US10Y_NOMINAL", "US10Y_REAL", "XAU_XAG_CORR", "SILVER"],
  },
  {
    title: "Macro (FRED)",
    names: ["CPI_YOY", "FED_FUNDS_RATE", "PPI_YOY", "NFP"],
  },
  {
    title: "Gold positioning & vol",
    names: ["GOLD_COT_NET", "GOLD_VOL_INDEX"],
  },
];

/** P2-11 liquidation heat strip: price buckets over SAMPLED Binance
 * forceOrder events. Magnitudes are floors by construction (Binance pushes
 * at most one liquidation order per second) — the disclaimer renders under
 * the strip, always. */
function LiquidationHeatStrip({ heatmap }: { heatmap: Intel["liquidation_heatmap"] }) {
  const entries = Object.entries(heatmap ?? {});
  if (entries.length === 0)
    return (
      <EmptyState
        kind="waiting"
        title="No sampled liquidations yet"
        detail="stream inactive or warming up — buckets appear once forceOrder events arrive"
      />
    );
  return (
    <div data-testid="liq-heatmap" className="space-y-3">
      {entries.map(([symbol, buckets]) => {
        if (buckets.length === 0) return null;
        const max = Math.max(...buckets.map((b) => b.notional), 1);
        const first = buckets[0]!;
        const last = buckets[buckets.length - 1]!;
        return (
          <div key={symbol}>
            <div className="mb-1 font-mono text-xs font-semibold">{symbol}</div>
            <div className="flex h-6 w-full overflow-hidden rounded border border-border/60">
              {buckets.map((bucket, i) => (
                <div
                  key={i}
                  className="grow border-r border-border/30 last:border-r-0"
                  title={`${bucket.low.toFixed(0)}–${bucket.high.toFixed(0)}: $${Math.round(
                    bucket.notional,
                  ).toLocaleString()} across ${bucket.count} sampled event${
                    bucket.count === 1 ? "" : "s"
                  }`}
                  style={{
                    background: `color-mix(in srgb, var(--bear) ${Math.round(
                      (bucket.notional / max) * 100,
                    )}%, transparent)`,
                  }}
                />
              ))}
            </div>
            <div className="mt-0.5 flex justify-between font-mono text-[10px] tabular text-fg-subtle">
              <span>{first.low.toFixed(0)}</span>
              <span>{last.high.toFixed(0)}</span>
            </div>
          </div>
        );
      })}
      <p className="text-xs text-fg-subtle">
        Sampled — not total volume: Binance streams at most one forceOrder per
        second per symbol, so notionals are a floor and counts an intensity
        signal (last 2h).
      </p>
    </div>
  );
}

function CorrelationMatrix() {
  const correlations = useCorrelations(30);
  if (correlations.isPending) return <SkeletonCard lines={5} />;
  if (correlations.isError || !correlations.data)
    return <EmptyState kind="error" title="Correlations unavailable" />;
  const { symbols, matrix, missing, used_days } = correlations.data;
  if (symbols.length < 2)
    return (
      <EmptyState
        kind="waiting"
        title="Not enough overlapping data"
        detail={missing.join("; ") || "need at least two series"}
      />
    );
  return (
    <div data-testid="correlation-matrix">
      <table className="w-full text-xs tabular">
        <thead>
          <tr>
            <th />
            {symbols.map((sym) => (
              <th key={sym} className="px-1 pb-1 text-right font-mono font-semibold">
                {sym}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {symbols.map((row) => (
            <tr key={row}>
              <td className="pr-1 font-mono font-semibold">{row}</td>
              {symbols.map((col) => {
                const value = matrix[row]?.[col];
                const strong = value != null && Math.abs(value) >= 0.5 && row !== col;
                return (
                  <td
                    key={col}
                    className={cn(
                      "px-1 py-0.5 text-right",
                      row === col && "text-fg-subtle",
                      strong && "font-bold",
                      strong && value! > 0 && "text-bull",
                      strong && value! < 0 && "text-bear",
                    )}
                  >
                    {value != null ? value.toFixed(2) : "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-fg-subtle">
        Pearson on daily log returns, {used_days} shared days (computed
        server-side, deterministically).
        {missing.length > 0 && <> Unavailable: {missing.join("; ")}</>}
      </p>
    </div>
  );
}

export default function IntelPage() {
  const intel = useIntel();
  const calendar = useCalendar();
  const [majorsOnly, setMajorsOnly] = useState(true);
  const overview = useOverview();
  // live countdown (R2.3) — unconditional, before the early returns
  const nextMajorRemaining = useCountdown(calendar.data?.next_major?.at ?? null);

  if (intel.isPending) return <SkeletonCard lines={8} />;
  if (intel.isError || !intel.data)
    return (
      <EmptyState
        kind="error"
        title="Intelligence feed unavailable"
        detail={String(intel.error ?? "")}
      />
    );

  const metrics = new Map(intel.data.metrics.map((m) => [m.name, m]));

  return (
    <div className="space-y-4">
      {/* mockup: a single inline strip — heading, regime pill, session pill,
          and the "as of…" note all on one row (not a header/content card) */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-[11px] font-bold uppercase tracking-[0.09em] text-fg-subtle">
            Regime board
          </span>
          <Badge variant="accent" className="text-sm font-bold">
            {overview.data?.symbol ?? "—"}: {overview.data?.regime ?? "no runs yet"}
          </Badge>
          <Badge>session: {intel.data.session}</Badge>
          <span className="text-xs text-fg-subtle">
            as of {fmtDateTime(intel.data.as_of)} — regime is computed
            deterministically from trend/volatility inputs, never by an LLM.
          </span>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        {GROUPS.map((group) => {
          const present = group.names.filter((name) =>
            [...metrics.keys()].some((k) => k.includes(name)),
          );
          const rows = [...metrics.entries()].filter(([key]) =>
            group.names.some((name) => key.includes(name)),
          );
          return (
            <Card key={group.title}>
              <CardHeader>
                <CardTitle>{group.title}</CardTitle>
              </CardHeader>
              <CardContent>
                {present.length === 0 ? (
                  <EmptyState
                    kind="waiting"
                    title="No readings"
                    detail={
                      intel.data.missing_feeds.find((f) =>
                        group.title.toLowerCase().includes(f.split(":")[0]!.split("_")[0]!),
                      ) ?? "feed returned nothing this cycle"
                    }
                  />
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {rows.map(([name, metric]) => (
                      // data dictionary: server label + a hover note saying
                      // exactly WHICH series this is (PPIACO-as-"PPI" once
                      // steered the macro debate — never again)
                      <div key={name} title={metric.note ?? undefined}>
                        <StatCard
                          label={metric.label ?? name.replaceAll("_", " ")}
                          value={fmtMetricValue(name, metric.value)}
                          sub={metric.source ?? undefined}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* P2-11: sampled liquidation heat strip (signal-grade, disclaimed) */}
      <Card>
        <CardHeader>
          <CardTitle>Liquidation heatmap (sampled)</CardTitle>
        </CardHeader>
        <CardContent>
          <LiquidationHeatStrip heatmap={intel.data.liquidation_heatmap} />
        </CardContent>
      </Card>

      {/* mockup pairs Cross-asset correlations beside Feed coverage */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Cross-asset correlations (30d)</CardTitle>
          </CardHeader>
          <CardContent>
            <CorrelationMatrix />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Feed coverage</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {intel.data.missing_feeds.length > 0 && (
              <div>
                <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.07em] text-stale">
                  Degraded this cycle
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {intel.data.missing_feeds.map((feed, i) => (
                    <span
                      key={i}
                      className="rounded-full border border-dashed border-stale px-2 py-0.5 font-mono text-[11px] text-stale"
                    >
                      {feed}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div>
              <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.07em] text-locked">
                Not subscribed
              </div>
              <ul className="space-y-1.5">
                {intel.data.unsubscribed_feeds.map((feed) => (
                  <li key={feed.name} className="flex items-center gap-2 text-sm text-locked">
                    <Lock size={13} aria-hidden="true" />
                    <span className="capitalize">{feed.name.replaceAll("_", " ")}</span>
                    <Badge variant="locked">{feed.provider}</Badge>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-fg-subtle">
                These paid feeds are not connected. Agents that depend on them
                declare it in missing_feeds and reduce claim confidence — the
                dashboard never fakes a reading it doesn't have.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* P2-07 custom alert-builder: user conditions over the same metric
          readings the operator defaults watch */}
      <Card>
        <CardHeader>
          <CardTitle>Custom alerts</CardTitle>
        </CardHeader>
        <CardContent>
          <AlertBuilder metricKeys={intel.data.metric_keys ?? []} />
        </CardContent>
      </Card>

      {/* Headlines: the same feed the pipeline's sentiment team reads —
          ingested-but-invisible was a trader-review finding (P1.3) */}
      <Card>
        <CardHeader>
          <CardTitle>Headlines</CardTitle>
        </CardHeader>
        <CardContent>
          {(intel.data.headlines?.length ?? 0) === 0 ? (
            <EmptyState
              kind="waiting"
              title="No headlines this cycle"
              detail={
                intel.data.missing_feeds.find((f) => f.startsWith("news:")) ??
                "news feed returned nothing"
              }
            />
          ) : (
            <ul
              className="max-h-72 space-y-1.5 overflow-y-auto text-[13px]"
              data-testid="headlines"
            >
              {intel.data.headlines!.slice(0, 16).map((h, i) => (
                <li key={i} className="flex items-baseline gap-2 border-b border-border/40 pb-1">
                  <Badge
                    variant={h.symbol === "XAUUSD" ? "accent" : "default"}
                    className="shrink-0 px-1.5 text-[10px]"
                  >
                    {h.symbol.replace("-USD", "")}
                  </Badge>
                  <span className="min-w-0 grow">
                    {h.url ? (
                      <a
                        href={h.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-fg hover:underline"
                      >
                        {h.headline}
                      </a>
                    ) : (
                      <span className="text-fg">{h.headline}</span>
                    )}{" "}
                    <span className="text-xs text-fg-subtle">
                      {h.source}
                      {h.published_at && ` · ${relativeAge(h.published_at)}`}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Economic calendar is an addition beyond the mockup (gaps-v8) */}
      <Card>
        <CardHeader>
          <CardTitle>Economic calendar</CardTitle>
          <button
            onClick={() => setMajorsOnly(!majorsOnly)}
            aria-pressed={majorsOnly}
            data-testid="calendar-majors-toggle"
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs font-semibold",
              majorsOnly
                ? "border-accent bg-accent text-(--on-solid)"
                : "border-border text-fg-subtle hover:text-fg",
            )}
          >
            {majorsOnly ? "major only" : "showing all"}
          </button>
        </CardHeader>
        <CardContent>
            {calendar.isPending ? (
              <SkeletonCard lines={4} />
            ) : (calendar.data?.releases.length ?? 0) === 0 ? (
              <EmptyState
                kind={calendar.data?.missing_feeds.length ? "waiting" : "empty"}
                title={
                  calendar.data?.missing_feeds.length
                    ? "Calendar degraded"
                    : "No releases in the window"
                }
                detail={
                  calendar.data?.missing_feeds[0] ??
                  "FRED reports nothing scheduled in the next 30 days"
                }
              />
            ) : (
              (() => {
                const all = calendar.data!.releases;
                const majors = all.filter((r) => r.major);
                const shown = majorsOnly && majors.length > 0 ? majors : all;
                const nextMajor = calendar.data!.next_major;
                const byDate = new Map<string, typeof shown>();
                for (const release of shown) {
                  byDate.set(release.date,
                             [...(byDate.get(release.date) ?? []), release]);
                }
                return (
                  <div className="space-y-2">
                    {nextMajor && !countdownExpired(nextMajorRemaining) && (
                      <p className="text-xs text-fg-muted" data-testid="next-major">
                        next major:{" "}
                        <span className="font-semibold">{nextMajor.release}</span>{" "}
                        in {fmtCountdown(nextMajorRemaining ?? nextMajor.seconds_until)}
                        {nextMajor.time_et
                          ? ` (${nextMajor.date} ${nextMajor.time_et} ET)`
                          : ` (${nextMajor.date} — time unknown)`}
                      </p>
                    )}
                    {majorsOnly && majors.length === 0 && (
                      <p className="text-xs text-stale">
                        no releases flagged major in this window — showing all
                      </p>
                    )}
                    <ul className="max-h-72 space-y-2 overflow-y-auto text-sm">
                      {[...byDate.entries()].map(([date, releases]) => (
                        <li key={date}>
                          <div className="mb-0.5 font-mono text-xs tabular text-fg-subtle">
                            {date}
                          </div>
                          <ul className="space-y-0.5">
                            {releases.map((release, i) => (
                              <li key={i}
                                  className="flex items-center justify-between gap-2 border-b border-border/40 py-0.5">
                                <span className="text-fg-muted">{release.release}</span>
                                <span className="flex shrink-0 items-center gap-1.5">
                                  {release.time_et && (
                                    <span className="font-mono text-[10px] tabular text-fg-subtle">
                                      {release.time_et} ET
                                    </span>
                                  )}
                                  {release.major && (
                                    <Badge variant="accent" className="px-1.5 text-[10px]">
                                      major
                                    </Badge>
                                  )}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()
            )}
          </CardContent>
      </Card>
    </div>
  );
}
