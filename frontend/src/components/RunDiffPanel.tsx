/** P5-05 "What changed": why this run differs from the previous run for the
 * same symbol — verdict, gates, agent stances, data, and (loudest of all)
 * the P3-07 version stamp.
 *
 * The versions case is deliberately visually distinct: when git_sha /
 * prompt_hash / config_hash moved, the machine itself changed, so NOTHING
 * below is honestly attributable to the market. That framing is the whole
 * point of the panel, so it gets its own alarm treatment rather than
 * another neutral row. */
import { AlertTriangle, ArrowRight, Cpu } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/EmptyState";
import type { RunDiff } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const DRIVER_LABEL: Record<RunDiff["headline_driver"], string> = {
  versions: "the machine changed",
  verdict: "the verdict moved",
  gate: "a gate flipped",
  evidence: "an agent changed its mind",
  data: "the data moved",
  confidence: "only confidence moved",
  none: "nothing material changed",
};

function Section({
  title,
  children,
  testId,
}: {
  title: string;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <section data-testid={testId} className="space-y-1">
      <h4 className="text-[10.5px] font-bold tracking-wide text-fg-subtle uppercase">
        {title}
      </h4>
      <div className="space-y-1 text-[11.5px]">{children}</div>
    </section>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-[10px] bg-surface-2 px-2.5 py-1.5">
      {children}
    </div>
  );
}

function Arrow() {
  return <ArrowRight size={11} className="shrink-0 text-fg-subtle" aria-hidden />;
}

/** Signed percent, or an honest "—" when the base reading was zero. */
function pct(value: number | null): string {
  return value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function gateWord(passed: boolean | null): string {
  return passed == null ? "unknown" : passed ? "pass" : "fail";
}

export function RunDiffPanel({ diff }: { diff: RunDiff }) {
  const versionsChanged = diff.versions.changed === true;
  const { verdict, gates, evidence, data } = diff;
  const verdictMoved =
    verdict.action_changed ||
    verdict.rejection_changed ||
    (verdict.confidence_delta ?? 0) !== 0;
  const dataMoved =
    data.metrics.length > 0 ||
    data.feeds_lost.length > 0 ||
    data.feeds_restored.length > 0 ||
    data.regime.changed === true;
  const evidenceMoved =
    evidence.flipped.length > 0 ||
    evidence.newly_abstaining.length > 0 ||
    evidence.newly_speaking.length > 0 ||
    evidence.confidence_movers.length > 0;

  return (
    <div className="space-y-3" data-testid="run-diff">
      <div
        data-testid="run-diff-headline"
        data-driver={diff.headline_driver}
        className={cn(
          "flex items-start gap-2 rounded-[12px] border px-3 py-2 text-[13px]",
          versionsChanged
            ? "border-bear/50 bg-bear-muted"
            : "border-border bg-surface-2",
        )}
      >
        {versionsChanged ? (
          <Cpu size={15} className="mt-0.5 shrink-0 text-bear" aria-hidden />
        ) : null}
        <span>
          <span
            className={cn("font-bold", versionsChanged && "text-bear")}
          >
            {diff.headline}
          </span>
          <span className="ml-1.5 text-[11px] text-fg-subtle">
            ({DRIVER_LABEL[diff.headline_driver]})
          </span>
        </span>
      </div>

      <p className="text-[11px] text-fg-subtle" data-testid="run-diff-runs">
        {new Date(diff.earlier.started_at).toLocaleString()} → {" "}
        {new Date(diff.later.started_at).toLocaleString()} · {diff.symbol}
      </p>

      {/* versions first and loudest: a different machine is not a
          different market, and every section below inherits that caveat */}
      {versionsChanged && (
        <Section title="Versions — different machine" testId="run-diff-versions">
          <div className="rounded-[10px] border border-bear/40 bg-bear-muted px-2.5 py-2">
            <p className="flex items-start gap-1.5 font-semibold text-bear">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden />
              The code that produced these two decisions is not the same.
            </p>
            <ul className="mt-1.5 space-y-0.5 font-mono text-[10.5px] text-fg-muted">
              {diff.versions.fields.map((f) => (
                <li key={f.field}>
                  {f.field}: {String(f.before ?? "—")} → {String(f.after ?? "—")}
                </li>
              ))}
            </ul>
            {diff.versions.note && (
              <p className="mt-1.5 text-[11px] text-fg-muted">{diff.versions.note}</p>
            )}
          </div>
        </Section>
      )}

      {diff.versions.comparable === false && (
        <p
          className="text-[11px] text-fg-subtle"
          data-testid="run-diff-versions-unknown"
        >
          One of these runs carries no version stamp — whether the code
          changed between them is unknown, not unchanged.
        </p>
      )}

      {verdictMoved && (
        <Section title="Verdict" testId="run-diff-verdict">
          <Row>
            <span className="font-semibold">{verdict.summary}</span>
            {verdict.confidence_delta != null && verdict.confidence_delta !== 0 && (
              <Badge variant={verdict.confidence_delta > 0 ? "bull" : "bear"}>
                {verdict.confidence_delta > 0 ? "+" : ""}
                {verdict.confidence_delta} conf
              </Badge>
            )}
          </Row>
        </Section>
      )}

      {gates.changed.length > 0 && (
        <Section title="Gates" testId="run-diff-gates">
          {gates.changed.map((g) => (
            <Row key={g.gate}>
              <span className="font-mono font-semibold">{g.gate}</span>
              <Badge variant={g.before_passed ? "bull" : "bear"}>
                {gateWord(g.before_passed)}
              </Badge>
              <Arrow />
              <Badge variant={g.after_passed ? "bull" : "bear"}>
                {gateWord(g.after_passed)}
              </Badge>
              {(g.after_reasons[0] ?? g.before_reasons[0]) && (
                <span className="text-fg-muted">
                  {g.after_reasons[0] ?? g.before_reasons[0]}
                </span>
              )}
            </Row>
          ))}
        </Section>
      )}

      {evidenceMoved && (
        <Section title="Evidence" testId="run-diff-evidence">
          {evidence.flipped.map((f) => (
            <Row key={`flip-${f.agent_id}`}>
              <span className="font-mono font-semibold">{f.agent_id}</span>
              <Badge variant={f.before_direction === "bullish" ? "bull" : "bear"}>
                {f.before_direction} {f.before_confidence}
              </Badge>
              <Arrow />
              <Badge variant={f.after_direction === "bullish" ? "bull" : "bear"}>
                {f.after_direction} {f.after_confidence}
              </Badge>
            </Row>
          ))}
          {evidence.newly_abstaining.map((a) => (
            <Row key={`abstain-${a.agent_id}`}>
              <span className="font-mono font-semibold">{a.agent_id}</span>
              <Badge variant="stale">went silent</Badge>
              {a.before_direction && (
                <span className="text-fg-muted">
                  was {a.before_direction} {a.before_confidence}
                </span>
              )}
            </Row>
          ))}
          {evidence.newly_speaking.map((s) => (
            <Row key={`speak-${s.agent_id}`}>
              <span className="font-mono font-semibold">{s.agent_id}</span>
              <Badge variant="accent">now speaking</Badge>
              {s.after_direction && (
                <span className="text-fg-muted">
                  {s.after_direction} {s.after_confidence}
                </span>
              )}
            </Row>
          ))}
          {evidence.confidence_movers.length > 0 && (
            <div
              className="flex flex-wrap gap-1.5 pt-0.5"
              data-testid="run-diff-movers"
            >
              {evidence.confidence_movers.map((m) => (
                <span
                  key={`mover-${m.agent_id}`}
                  className="inline-flex items-center gap-1 rounded-[8px] bg-surface-2 px-2 py-[3px] font-mono text-[10.5px]"
                  title={`${m.agent_id}: ${m.before} → ${m.after}`}
                >
                  {m.agent_id}
                  <span className={m.delta > 0 ? "text-bull" : "text-bear"}>
                    {m.delta > 0 ? "+" : ""}
                    {m.delta}
                  </span>
                </span>
              ))}
              {evidence.n_confidence_movers > evidence.confidence_movers.length && (
                <span className="text-[10.5px] text-fg-subtle">
                  +{evidence.n_confidence_movers - evidence.confidence_movers.length}{" "}
                  smaller moves
                </span>
              )}
            </div>
          )}
        </Section>
      )}

      {dataMoved && (
        <Section title="Data" testId="run-diff-data">
          {data.feeds_lost.length > 0 && (
            <Row>
              <Badge variant="bear">feed lost</Badge>
              <span className="font-mono">{data.feeds_lost.join(", ")}</span>
              <span className="text-fg-muted">— the machine reasoned on less</span>
            </Row>
          )}
          {data.feeds_restored.length > 0 && (
            <Row>
              <Badge variant="bull">feed restored</Badge>
              <span className="font-mono">{data.feeds_restored.join(", ")}</span>
            </Row>
          )}
          {data.regime.changed === true && (
            <Row>
              <span className="font-semibold">regime</span>
              <span className="font-mono">{data.regime.before}</span>
              <Arrow />
              <span className="font-mono">{data.regime.after}</span>
            </Row>
          )}
          {data.metrics.map((m) => (
            <Row key={m.name}>
              <span className="font-mono font-semibold">{m.name}</span>
              <span className="font-mono text-fg-muted">
                {m.before} <Arrow /> {m.after}
              </span>
              <span className={m.delta > 0 ? "text-bull" : "text-bear"}>
                {pct(m.pct_change)}
              </span>
            </Row>
          ))}
          {data.metrics.length > 0 && (
            <p className="text-[10.5px] text-fg-subtle">
              Readings moving less than {data.pct_threshold}% are omitted as
              immaterial.
            </p>
          )}
        </Section>
      )}

      {diff.headline_driver === "none" && (
        <EmptyState
          kind="empty"
          title="Same call, same reasons"
          detail="Verdict, gates, agent stances and inputs all match the previous run."
          className="py-3"
        />
      )}

      {data.bars.new_bars != null && data.bars.new_bars > 0 && (
        <p className="text-[10.5px] text-fg-subtle" data-testid="run-diff-bars">
          Price window advanced {data.bars.new_bars} bar
          {data.bars.new_bars === 1 ? "" : "s"} ({data.bars.before_n} →{" "}
          {data.bars.after_n} bars).
        </p>
      )}
    </div>
  );
}
