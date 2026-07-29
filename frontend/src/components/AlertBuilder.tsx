/** P2-07 custom alert-builder: metric condition alerts over the intel
 * data dictionary (METRIC_INFO keys served on /api/intel.metric_keys).
 * Notify-only — a crossing raises the bell + Telegram, never an order. */
import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createConditionAlert,
  deleteConditionAlert,
  useConditionAlerts,
} from "@/lib/api/queries";
import type { ConditionAlert } from "@/lib/api/types";

const OPERATORS = [
  ["gt", "> above"],
  ["lt", "< below"],
  ["crosses_above", "crosses above"],
  ["crosses_below", "crosses below"],
] as const;
type Operator = (typeof OPERATORS)[number][0];

const OPERATOR_GLYPH: Record<Operator, string> = {
  gt: ">",
  lt: "<",
  crosses_above: "↗",
  crosses_below: "↘",
};

const selectClass =
  "h-9 rounded-xl border border-border bg-surface-2 px-2 text-sm " +
  "focus-visible:outline-2 focus-visible:outline-accent";

export function AlertBuilder({ metricKeys }: { metricKeys: string[] }) {
  const client = useQueryClient();
  const alerts = useConditionAlerts();
  const [metric, setMetric] = useState(metricKeys[0] ?? "");
  const [operator, setOperator] = useState<Operator>("gt");
  const [threshold, setThreshold] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const rows: ConditionAlert[] = (alerts.data ?? []).filter((a) => a.active);

  const submit = async () => {
    const value = Number(threshold);
    if (!metric || !Number.isFinite(value)) return;
    setError(null);
    try {
      await createConditionAlert(client, { metric, operator, threshold: value, note });
      setThreshold("");
      setNote("");
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div className="space-y-2 text-sm" data-testid="alert-builder">
      {rows.length === 0 ? (
        <p className="text-xs text-fg-subtle">
          No condition alerts yet. A crossing notifies (bell, Telegram when
          configured) — it never trades.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((alert) => (
            <li
              key={alert.id}
              data-testid="condition-alert-row"
              className="flex items-center justify-between gap-2 rounded-[12px] bg-surface-2 px-3 py-[7px] text-[12.5px]"
            >
              <span className="font-mono font-bold tabular">
                {alert.metric} {OPERATOR_GLYPH[alert.operator]} {alert.threshold}
              </span>
              <span className="grow truncate text-xs text-fg-subtle">{alert.note}</span>
              <Badge className="px-1.5 text-[10px]">
                {alert.operator.replaceAll("_", " ")}
              </Badge>
              <button
                onClick={() => void deleteConditionAlert(client, alert.id)}
                aria-label={`Delete alert on ${alert.metric}`}
                className="flex size-[22px] shrink-0 items-center justify-center rounded-[7px] text-fg-subtle hover:bg-bear-muted hover:text-bear"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-wrap items-center gap-1.5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <select
          value={metric}
          onChange={(event) => setMetric(event.target.value)}
          aria-label="Alert metric"
          data-testid="alert-builder-metric"
          className={selectClass}
        >
          {metricKeys.map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
        <select
          value={operator}
          onChange={(event) => setOperator(event.target.value as Operator)}
          aria-label="Alert operator"
          data-testid="alert-builder-operator"
          className={selectClass}
        >
          {OPERATORS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <Input
          type="number"
          step="any"
          placeholder="threshold"
          value={threshold}
          onChange={(event) => setThreshold(event.target.value)}
          aria-label="Alert threshold"
          data-testid="alert-builder-threshold"
          className="w-28 tabular"
        />
        <Input
          placeholder="note (optional)"
          value={note}
          maxLength={200}
          onChange={(event) => setNote(event.target.value)}
          aria-label="Alert note"
          className="w-40"
        />
        <Button size="sm" type="submit" data-testid="alert-builder-create">
          Create
        </Button>
      </form>
      {error && <p className="text-xs text-bear">{error}</p>}
    </div>
  );
}
