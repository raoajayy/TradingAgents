/** P4-02 PUBLIC track record — the contamination-proof live ledger.
 *
 * Mounted OUTSIDE AppShell/AuthGate at /public/track-record (the in-app
 * /track-record page is the operator's richer view): visitors never see
 * the login screen. Auth: the page calls GET /public/v1/track-record with
 * a read-only Bearer token that the operator issues via POST /api/tokens
 * (scope read:decisions) and supplies either as ?token=<value> in the URL
 * or baked into the build as VITE_TRACK_RECORD_TOKEN. The backend route
 * itself is feature-flagged (PRO_PUBLIC_TRACK_RECORD=1, default off
 * pending legal counsel) — while it's off this page shows "not published".
 */
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SkeletonCard } from "@/components/ui/skeleton";
import { fmtPct, fmtPnl } from "@/lib/format";

export interface TrackRecordRow {
  run_id: string;
  symbol: string;
  started_at: string;
  action: string | null;
  confidence: number | null;
  versions: { git_sha: string; prompt_hash: string } | null;
  rejected_at: string | null;
  open: boolean;
  outcome: { pnl: number | null; won: boolean | null; closed_at: string } | null;
}

export interface TrackRecordPayload {
  ledger: TrackRecordRow[];
  aggregates: {
    n_decisions: number;
    n_rejected: number;
    n_open: number;
    n_graded: number;
    win_rate: number | null;
    win_rate_n: number;
    avg_r: number | null;
    avg_r_n: number;
    calibration: {
      brier: number | null;
      n: number;
      buckets: {
        confidence_lo: number;
        confidence_hi: number;
        n: number;
        p_win: number | null;
      }[];
    };
  };
  methodology: Record<string, string>;
}

function readToken(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("token");
  if (fromUrl) return fromUrl;
  const fromEnv = import.meta.env.VITE_TRACK_RECORD_TOKEN;
  return fromEnv?.trim() ? fromEnv.trim() : null;
}

async function fetchTrackRecord(): Promise<TrackRecordPayload> {
  const token = readToken();
  if (!token) throw new Error("no-token");
  const resp = await fetch("/public/v1/track-record?limit=100", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (resp.status === 404) throw new Error("not-published");
  if (!resp.ok) throw new Error(`http-${resp.status}`);
  return (await resp.json()) as TrackRecordPayload;
}

function Stat({ label, value, n }: { label: string; value: string; n?: number }) {
  return (
    <div className="rounded-xl border border-border px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-fg-subtle">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-lg tabular text-fg">{value}</div>
      {n != null && <div className="text-[11px] text-fg-subtle">n={n}</div>}
    </div>
  );
}

function OutcomeBadge({ row }: { row: TrackRecordRow }) {
  if (row.rejected_at)
    return <Badge variant="neutral">rejected · {row.rejected_at}</Badge>;
  if (row.open) return <Badge variant="accent">open</Badge>;
  if (row.outcome == null) return <Badge>not traded</Badge>;
  return (
    <Badge variant={row.outcome.won ? "bull" : "bear"}>
      {row.outcome.won ? "won" : "lost"} {fmtPnl(row.outcome.pnl)}
    </Badge>
  );
}

const short = (hash: string | undefined) => (hash ? hash.slice(0, 8) : "—");

export default function PublicTrackRecordPage() {
  const query = useQuery({
    queryKey: ["public-track-record"],
    queryFn: fetchTrackRecord,
    retry: false,
    refetchOnWindowFocus: false,
  });

  return (
    <div
      className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6"
      data-testid="track-record"
    >
      <header className="space-y-1">
        <h1 className="text-xl font-bold">TradingAgents Pro — live track record</h1>
        <p className="text-sm text-fg-muted">
          Every decision pre-registered at run time, graded after the fact.
        </p>
      </header>

      {/* methodology BEFORE the numbers — the page can't move goalposts */}
      <Card>
        <CardContent className="py-3 text-xs text-fg-subtle">
          <p data-testid="track-record-methodology">
            Methodology: decisions are pre-registered at run time (the
            timestamp and code/prompt version stamp below existed before the
            outcome), graded post-hoc from the trade journal only,
            rejections included, open positions shown. Nothing is
            retro-adjusted or trimmed. Paper/live trading record — not
            investment advice.
          </p>
        </CardContent>
      </Card>

      {query.isPending ? (
        <SkeletonCard lines={6} />
      ) : query.isError ? (
        <Card>
          <CardContent className="py-6 text-sm text-fg-muted">
            {query.error.message === "no-token" ? (
              <>
                This page needs a read-only access token. Ask the operator
                for a <code>read:decisions</code> token and open{" "}
                <code>/public/track-record?token=…</code>.
              </>
            ) : query.error.message === "not-published" ? (
              <>The track record is not published yet.</>
            ) : (
              <>Could not load the track record ({query.error.message}).</>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Headline record</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                <Stat
                  label="decisions"
                  value={String(query.data.aggregates.n_decisions)}
                />
                <Stat
                  label="rejected"
                  value={String(query.data.aggregates.n_rejected)}
                />
                <Stat label="open" value={String(query.data.aggregates.n_open)} />
                <Stat
                  label="graded"
                  value={String(query.data.aggregates.n_graded)}
                />
                <Stat
                  label="win rate"
                  value={fmtPct(query.data.aggregates.win_rate)}
                  n={query.data.aggregates.win_rate_n}
                />
                <Stat
                  label="avg R"
                  value={query.data.aggregates.avg_r?.toFixed(2) ?? "—"}
                  n={query.data.aggregates.avg_r_n}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Calibration</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="text-xs text-fg-subtle">
                Stated confidence vs realized win rate (Brier{" "}
                {query.data.aggregates.calibration.brier?.toFixed(3) ?? "—"},
                n={query.data.aggregates.calibration.n}; 0.25 = coin flip).
              </p>
              <div className="grid grid-cols-5 gap-2">
                {query.data.aggregates.calibration.buckets.map((b) => (
                  <div
                    key={b.confidence_lo}
                    className="rounded-xl border border-border px-2 py-1.5 text-center"
                  >
                    <div className="text-[11px] text-fg-subtle">
                      {b.confidence_lo}–{b.confidence_hi}
                    </div>
                    <div className="font-mono tabular">
                      {b.p_win != null ? fmtPct(b.p_win) : "—"}
                    </div>
                    <div className="text-[11px] text-fg-subtle">n={b.n}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Pre-registered decisions</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wide text-fg-subtle">
                    <th className="py-1.5 pr-3 font-medium">registered (UTC)</th>
                    <th className="py-1.5 pr-3 font-medium">symbol</th>
                    <th className="py-1.5 pr-3 font-medium">action</th>
                    <th className="py-1.5 pr-3 font-medium">conf</th>
                    <th className="py-1.5 pr-3 font-medium">versions</th>
                    <th className="py-1.5 font-medium">outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.ledger.map((row) => (
                    <tr
                      key={row.run_id}
                      className="border-t border-border/60"
                      data-testid="track-record-row"
                    >
                      <td className="py-1.5 pr-3 font-mono text-xs tabular">
                        {row.started_at.slice(0, 16).replace("T", " ")}
                      </td>
                      <td className="py-1.5 pr-3">{row.symbol}</td>
                      <td className="py-1.5 pr-3">{row.action ?? "—"}</td>
                      <td className="py-1.5 pr-3 font-mono tabular">
                        {row.confidence ?? "—"}
                      </td>
                      <td
                        className="py-1.5 pr-3 font-mono text-xs text-fg-subtle"
                        title="git sha · prompt hash, stamped before the outcome existed"
                      >
                        {row.versions
                          ? `${short(row.versions.git_sha)} · ${short(row.versions.prompt_hash)}`
                          : "—"}
                      </td>
                      <td className="py-1.5">
                        <OutcomeBadge row={row} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {query.data.ledger.length === 0 && (
                <p className="py-4 text-center text-sm text-fg-subtle">
                  No decisions recorded yet.
                </p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
