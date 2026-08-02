/** P4-03 slice 2: operator-facing marketplace listings (NO payments).
 *
 * The honest-gate UX is the point of this page: publishing runs the
 * backend calibration gate (service.listing_gate) and a 422's failure
 * sentences are rendered VERBATIM in the row — "n_graded=0 is below the
 * minimum of 30 graded outcomes" is shown, never hidden or paraphrased.
 * Editing the config artifact of a published listing demotes it to draft
 * (store rule) — the form says so before the save. Viewer sessions render
 * read-only: mutations would 403 at the middleware anyway. */
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SkeletonCard } from "@/components/ui/skeleton";
import {
  createListing,
  delistListing,
  ListingPublishBlocked,
  publishListing,
  updateListing,
  useListings,
} from "@/lib/api/queries";
import type { Listing } from "@/lib/api/types";
import { fmtDateTime, fmtPct } from "@/lib/format";
import { useIsOperator } from "@/stores/session";

const STATUS_VARIANT = {
  draft: "neutral",
  published: "bull",
  delisted: "locked",
} as const;

const TEXTAREA_CLS =
  "w-full rounded-xl border border-border bg-surface-2 px-3 py-2 font-mono " +
  "text-xs placeholder:text-fg-subtle focus-visible:outline-2 " +
  "focus-visible:outline-accent";

/** "n=42 · brier 0.180 · win 61.0% (n=42)" — every rate carries its own
 * sample size; a missing field renders "—", never a fake zero. */
function calibrationSummary(cal: Listing["calibration"]): string {
  if (cal == null) return "no graded record";
  const n = cal.n_graded != null ? String(cal.n_graded) : "—";
  const brier = cal.brier != null ? cal.brier.toFixed(3) : "—";
  const win =
    cal.win_rate != null
      ? `${fmtPct(cal.win_rate)} (n=${cal.win_rate_n ?? "—"})`
      : "—";
  return `n=${n} · brier ${brier} · win ${win}`;
}

/** Parse the config textarea: must be a JSON object (the backend rejects
 * non-objects with a 422; catching it here keeps the error next to the
 * field). Returns [config, null] or [null, error message]. */
function parseConfig(
  text: string,
): [Record<string, unknown>, null] | [null, string] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    return [null, `config is not valid JSON: ${(err as Error).message}`];
  }
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return [null, "config must be a JSON object, e.g. {\"strategy_id\": …}"];
  }
  return [parsed as Record<string, unknown>, null];
}

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function CreateListingForm() {
  const client = useQueryClient();
  const [kind, setKind] = useState<"strategy" | "prompt">("strategy");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [configText, setConfigText] = useState("{}");
  const [configError, setConfigError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    const [config, parseError] = parseConfig(configText);
    setConfigError(parseError);
    if (parseError != null || config == null || !title.trim()) return;
    setBusy(true);
    try {
      await createListing(client, {
        kind,
        title: title.trim(),
        description,
        config,
      });
      setTitle("");
      setDescription("");
      setConfigText("{}");
    } catch (err) {
      setSubmitError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>New draft listing</CardTitle>
      </CardHeader>
      <CardContent className="pb-5">
        <form className="space-y-2" onSubmit={(e) => void submit(e)}>
          <div className="flex flex-wrap gap-2">
            <label className="text-sm">
              <span className="sr-only">Kind</span>
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as typeof kind)}
                className="h-9 rounded-xl border border-border bg-surface-2 px-3 text-sm"
                data-testid="listing-kind"
              >
                <option value="strategy">strategy</option>
                <option value="prompt">prompt</option>
              </select>
            </label>
            <Input
              className="min-w-40 flex-1"
              placeholder="Title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              data-testid="listing-title"
            />
          </div>
          <Input
            placeholder="Description (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            data-testid="listing-description"
          />
          <label className="block text-sm">
            <span className="mb-1 block text-[11px] uppercase tracking-wide text-fg-subtle">
              Config (JSON) — the paid artifact; never leaves the operator
              surface
            </span>
            <textarea
              rows={4}
              className={TEXTAREA_CLS}
              value={configText}
              onChange={(e) => {
                setConfigText(e.target.value);
                if (configError) setConfigError(parseConfig(e.target.value)[1]);
              }}
              data-testid="listing-config"
            />
          </label>
          {configError && (
            <p className="text-xs text-bear" data-testid="listing-config-error">
              {configError}
            </p>
          )}
          {submitError && (
            <p className="text-xs text-bear" data-testid="listing-create-error">
              {submitError}
            </p>
          )}
          <Button
            type="submit"
            size="sm"
            disabled={busy || !title.trim()}
            data-testid="listing-create"
          >
            <Plus size={14} aria-hidden="true" /> Create draft
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function EditListingForm({
  listing,
  onDone,
}: {
  listing: Listing;
  onDone: () => void;
}) {
  const client = useQueryClient();
  const originalConfig = JSON.stringify(listing.config, null, 2);
  const [title, setTitle] = useState(listing.title);
  const [description, setDescription] = useState(listing.description);
  const [configText, setConfigText] = useState(originalConfig);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const configChanged = configText !== originalConfig;

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    const patch: Parameters<typeof updateListing>[2] = {};
    if (title.trim() !== listing.title) patch.title = title.trim();
    if (description !== listing.description) patch.description = description;
    if (configChanged) {
      const [config, parseError] = parseConfig(configText);
      if (parseError != null || config == null) {
        setError(parseError);
        return;
      }
      patch.config = config;
    }
    if (Object.keys(patch).length === 0) {
      onDone();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await updateListing(client, listing.id, patch);
      onDone();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="space-y-2 rounded-xl border border-border bg-surface-2/50 p-3"
      onSubmit={(e) => void save(e)}
      data-testid="listing-edit-form"
    >
      <Input
        aria-label="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        data-testid="listing-edit-title"
      />
      <Input
        aria-label="Description"
        placeholder="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        data-testid="listing-edit-description"
      />
      <label className="block text-sm">
        <span className="mb-1 block text-[11px] uppercase tracking-wide text-fg-subtle">
          Config (JSON)
        </span>
        <textarea
          rows={4}
          className={TEXTAREA_CLS}
          value={configText}
          onChange={(e) => setConfigText(e.target.value)}
          data-testid="listing-edit-config"
        />
      </label>
      {listing.status === "published" && configChanged && (
        <p className="text-xs text-stale" data-testid="listing-demote-warning">
          editing the artifact demotes a published listing to draft — it must
          pass the calibration gate again before republishing.
        </p>
      )}
      {error && (
        <p className="text-xs text-bear" data-testid="listing-edit-error">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={busy || !title.trim()}
          data-testid="listing-edit-save"
        >
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function ListingRow({
  listing,
  isOperator,
}: {
  listing: Listing;
  isOperator: boolean;
}) {
  const client = useQueryClient();
  const [failures, setFailures] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmingDelist, setConfirmingDelist] = useState(false);
  const [busy, setBusy] = useState(false);

  const publish = async () => {
    setBusy(true);
    setError(null);
    try {
      await publishListing(client, listing.id);
      setFailures(null);
    } catch (err) {
      if (err instanceof ListingPublishBlocked) setFailures(err.failures);
      else setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const delist = async () => {
    setBusy(true);
    setError(null);
    try {
      await delistListing(client, listing.id);
      setConfirmingDelist(false);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="space-y-2 border-b border-border/50 px-1 py-2.5 last:border-b-0"
      data-testid="listing-row"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge>{listing.kind}</Badge>
        <span className="font-semibold" data-testid="listing-row-title">
          {listing.title}
        </span>
        <Badge
          variant={STATUS_VARIANT[listing.status]}
          data-testid="listing-status"
        >
          {listing.status}
        </Badge>
        <span
          className="font-mono text-xs text-fg-muted"
          data-testid="listing-calibration"
        >
          {calibrationSummary(listing.calibration)}
        </span>
        <span className="text-xs text-fg-subtle">
          updated {fmtDateTime(listing.updated_at)}
        </span>
        {isOperator && (
          <span className="ml-auto flex items-center gap-1.5">
            {listing.status === "draft" && (
              <Button
                size="sm"
                variant="muted"
                disabled={busy}
                onClick={() => void publish()}
                data-testid="listing-publish"
              >
                Publish
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => setEditing((v) => !v)}
              data-testid="listing-edit"
            >
              Edit
            </Button>
            {listing.status !== "delisted" &&
              (confirmingDelist ? (
                <>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() => void delist()}
                    data-testid="listing-delist-confirm"
                  >
                    Confirm delist
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setConfirmingDelist(false)}
                  >
                    Cancel
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-fg-subtle hover:text-bear"
                  disabled={busy}
                  onClick={() => setConfirmingDelist(true)}
                  data-testid="listing-delist"
                >
                  Delist
                </Button>
              ))}
          </span>
        )}
      </div>
      {failures && (
        <div
          className="rounded-xl border border-bear/40 bg-bear-muted px-3 py-2"
          data-testid="listing-publish-failures"
        >
          <div className="text-xs font-semibold text-bear">
            Publish blocked by the calibration gate:
          </div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-bear">
            {failures.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      {error && (
        <p className="text-xs text-bear" data-testid="listing-row-error">
          {error}
        </p>
      )}
      {editing && (
        <EditListingForm listing={listing} onDone={() => setEditing(false)} />
      )}
    </div>
  );
}

export default function ListingsPage() {
  const listings = useListings();
  const isOperator = useIsOperator();

  return (
    <div className="space-y-4" data-testid="listings-page">
      <Card>
        <CardContent className="space-y-1 py-3 text-sm">
          <p className="text-fg">
            Marketplace listings — calibration-gated. Nothing reaches
            "published" without a real graded record.
          </p>
          <p className="text-xs text-fg-subtle">
            Publishing runs the gate (n_graded floor, measured brier, win rate
            with its own n) and shows the failures verbatim. Editing a
            published listing's config demotes it to draft. Delisting is soft
            — a listing that was ever published never just vanishes.
            {!isOperator && (
              <b data-testid="listings-readonly-note">
                {" "}
                Your session is viewer — this page is read-only.
              </b>
            )}
          </p>
        </CardContent>
      </Card>

      {isOperator && <CreateListingForm />}

      <Card>
        <CardHeader>
          <CardTitle>Listings</CardTitle>
        </CardHeader>
        <CardContent className="pb-4">
          {listings.isPending ? (
            <SkeletonCard lines={4} />
          ) : listings.isError ? (
            <EmptyState
              kind="error"
              title="Listings unavailable"
              detail={errText(listings.error)}
            />
          ) : listings.data.listings.length === 0 ? (
            <EmptyState
              kind="empty"
              title="No listings yet"
              detail="Create a draft above; it can only publish once its graded record passes the calibration gate."
            />
          ) : (
            <div data-testid="listings-table">
              {listings.data.listings.map((listing) => (
                <ListingRow
                  key={listing.id}
                  listing={listing}
                  isOperator={isOperator}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
