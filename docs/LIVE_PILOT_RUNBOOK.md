# Live Dust-Pilot Runbook (P3-01) — Binance Futures

The arming ceremony and operating procedure for the first real-order
pilot: minimum-size ("dust") orders on **Binance USDⓈ-M FUTURES
TESTNET** (`testnet.binancefuture.com`), graduating to mainnet dust only
after the owner sign-off at the bottom of this document. This runbook is
specific to P3-01; the general live subsystem procedures (host
requirements, Telegram alerts, dead-man switch, promotion policy) live in
`LIVE_TRADING_RUNBOOK.md` and still apply.

**The system is inert by default.** The Binance adapter is constructed
without an arming hook granting nothing: every operation — including
reads — raises `ExecutionNotEnabled` until a pair is armed at a live tier
through the ceremony below. There is no environment variable, config
flag, or code path that skips the ceremony.

## Safety invariants (what the code enforces, so you can verify it)

1. **No naked entries.** An entry without a protective stop is REJECTED
   before any network call. With one, the reduce-only `STOP_MARKET` stop
   is placed on the venue in the same submission flow as the entry
   (risk #48: an LLM/app/laptop outage must never leave an unprotected
   position — the stop rests on Binance, not in our process).
2. **Stop fails ⇒ position dies.** If the entry fills but the stop cannot
   be placed, the adapter immediately flattens the entry (reduce-only
   market) and fires a critical alert. If even the flatten fails, the
   kill switch engages and a `MANUAL INTERVENTION` alert fires.
3. **Hard notional cap.** Orders above `risk.max_notional_per_trade`
   from `live.yaml` are refused adapter-side (in addition to the
   LiveGateChain). Reduce-only closes are exempt — flattening is never
   blocked.
4. **Mainnet is opt-in twice.** The mainnet base URL refuses to construct
   without `PRO_BINANCE_MAINNET_ACK=dust-pilot-approved`, and mainnet
   keys use different env var names than testnet keys.
5. **Everything is audited.** Orders, refusals, flattens, arming
   transitions, and drills land in the hash-chained `audit.jsonl`.

## Phase A — Testnet pilot

### A.1 Prerequisites

- [ ] An always-on Linux host with NTP (see LIVE_TRADING_RUNBOOK host
      section). Docker Desktop / laptops are fine for **testnet only**.
- [ ] Binance Futures **testnet** account at
      <https://testnet.binancefuture.com> with an API key/secret.
      Testnet funds are play money; no withdrawal scope exists.
- [ ] `PRO_DASHBOARD_TOKEN` set (≥16 chars).
- [ ] `live.yaml` written from `deploy/live.yaml.example` with **every**
      risk limit typed out by the operator. For the dust pilot set
      `max_notional_per_trade` to roughly 2× the venue minimum order
      value (BTCUSDT minimum is 0.001 BTC — at $100k that is ~$100, so a
      cap of ~$250 is appropriate). The loader refuses missing keys.
- [ ] Latest `readiness-report` run with zero FAILs.

### A.2 Secrets setup (operator-typed, never committed)

Type these into the deployment environment yourself — do not paste them
into chat tools, tickets, or files tracked by git:

```
export BINANCE_TESTNET_API_KEY=...      # testnet key
export BINANCE_TESTNET_API_SECRET=...   # testnet secret
export PRO_LIVE_EXCHANGE=binance        # select the P3-01 adapter
export PRO_LIVE_VENUE=testnet           # (default) never 'production' in Phase A
export PRO_LIVE_CONFIG=/path/to/live.yaml
```

Keys are read at call time and redacted from every log line and
exception. Verify anyway after the first order:
`grep -c "$BINANCE_TESTNET_API_SECRET" logs/*` must be 0.

### A.3 Arming ceremony (per pair, expiring)

Arming uses the existing tiers (`shadow` → `canary` → `live`) and the
existing CLI ceremony; nothing about P3-01 shortcuts it:

```
tradingagents-pro readiness-report                 # every FAIL blocks arming
tradingagents-pro arm-live --config live.yaml --pair BTC-USD \
    --operator <you> --ttl-days 14                 # canary first, testnet
```

- `arm-live` self-checks, prints venue balance + limits + worst-case
  daily loss, and requires the generated confirmation phrase typed back.
- Arming expires (`--ttl-days`, default 30). Expiry silently demotes to
  paper; re-arming repeats the full ceremony.
- Start at **canary**: the router clamps every order to the venue
  minimum size regardless of what the sizing model wanted.
- Confirm the amber `LIVE — ARMED (canary)` banner in the dashboard.

### A.4 The 10-fill acceptance checklist (testnet)

The pilot's AC: **10 real testnet fills recorded with TCA** before any
mainnet conversation. For each fill, verify and tick:

| # | Fill (coid) | Venue stop resting? | TCA captured? | Reconcile clean? | Notes |
|---|-------------|--------------------|---------------|------------------|-------|
| 1 |             |                    |               |                  |       |
| … |             |                    |               |                  |       |
| 10|             |                    |               |                  |       |

Per-fill verification:

1. **Fill**: `audit.jsonl` has `order_result` with `status: filled` (or
   `submitted` followed by the poll-absorbed fill) and `route: live`.
2. **Venue stop resting**: on testnet.binancefuture.com → Open Orders,
   a reduce-only `STOP_MARKET` exists for the position at the ticket's
   stop price (also visible via the dashboard's open-orders view).
3. **TCA captured**: the trade journal entry carries
   `tca.arrival_mid`, `tca.entry_slippage_bps`, and (best-effort)
   `tca.markouts_bps` — same shape as paper (P1-04), now against real
   venue fills.
4. **Reconcile clean**: no `reconciliation` audit entries with
   `in_sync: false` attributable to the fill.

Also across the 10 fills: at least one reduce-only TP ladder leg placed,
at least one entry refused by a gate (prove the refusal path fires), and
zero naked-position alerts.

### A.5 Kill-switch drill (mandatory before sign-off)

With a pair armed on TESTNET:

```
PRO_DATA_DIR=/data ./scripts/pro_live_drill.sh
```

The script prints every step, demands the typed phrase `RUN DRILL` plus
your operator name, then: places a min-size protected entry → verifies
the reduce-only STOP_MARKET is resting on the venue → engages the kill
switch → cancels + flattens everything → verifies the venue reports a
flat book → disarms all pairs → writes a `kill_switch_drill` record to
the hash-chained audit log. A failed step still flattens and disarms.

The drill refuses to run against a non-testnet adapter, an unarmed pair,
or without an operator identity. Re-run it after any change to the
execution layer and at least monthly while the pilot is live.

### A.6 Rollback / disarm (any time, no questions)

- Soft: `tradingagents-pro disarm --pair BTC-USD --operator <you>` (or
  let the TTL expire). New live orders stop; resting stops remain and
  keep protecting open positions.
- Hard: dashboard **EMERGENCY FLATTEN** button or
  `tradingagents-pro flatten --confirm` — cancel all, close all
  (reduce-only), engage kill switch, disarm all pairs.
- Shell-only fallback: `touch /data/KILL` (the file-backed kill switch)
  blocks all new entries even if the CLI is unavailable.
- Unset `PRO_LIVE_EXCHANGE` / remove credentials and restart to remove
  the live route entirely; armed pairs are then refused, never silently
  paper-filled.

## Phase B — Mainnet dust (owner sign-off required first)

Do not begin Phase B until the sign-off section below is completed.

1. Create a **mainnet** API key with futures-trading scope only (no
   withdrawal), IP-whitelisted to the deployment host.
2. Export mainnet secrets (different names by design):
   `BINANCE_API_KEY` / `BINANCE_API_SECRET`, plus the double opt-in:
   `PRO_LIVE_VENUE=production` and
   `PRO_BINANCE_MAINNET_ACK=dust-pilot-approved`.
3. Dust-size config: in `live.yaml`, keep `max_notional_per_trade` at
   the venue-minimum ceiling (~$250 for BTCUSDT),
   `live_max_account_allocation_pct` ≤ 5, `max_orders_per_day` ≤ 10,
   `max_leverage: 1`. Fund the futures wallet with only what the pilot
   may lose (≤ $500 recommended).
4. Re-run the full arming ceremony at **canary** (venue-minimum sizing)
   — mainnet arming is a new ceremony, never inherited from testnet.
5. Repeat the 10-fill AC checklist and the kill-switch drill on mainnet
   dust before considering the `live` tier.

## OWNER SIGN-OFF

Signing here authorizes Phase B (real funds). By signing, the owner
accepts specifically:

- **Real capital can be lost** — up to the funded futures-wallet balance
  in the worst case (venue failure, gap through stops, liquidation
  cascade), not merely `daily_loss_limit_pct`. Protective stops are
  STOP_MARKET orders: they bound losses at the trigger, not at the fill,
  and slippage in a gap is unbounded by the stop price.
- The strategy's live expectancy is **unproven**; testnet fills prove the
  pipeline, not profitability.
- Venue/counterparty risk: Binance custody, API outages during open
  positions (mitigated — not eliminated — by venue-resting stops and the
  dead-man switch).
- Operational risk: the operator team is the on-call; the kill-switch
  drill and flatten procedures above are the accepted remediations.
- Regulatory/tax obligations for derivatives trading in the operator's
  jurisdiction are the owner's responsibility (see P4-02 legal-counsel
  gate before any scale-up beyond dust).

Prerequisites checked before signing (all must be true):

- [ ] 10 testnet fills recorded with TCA (checklist A.4 complete)
- [ ] Kill-switch drill PASSED on testnet within the last 7 days
      (audit `kill_switch_drill` entry, `passed: true`)
- [ ] `live.yaml` reviewed line-by-line by the owner
- [ ] Alerting verified end-to-end (a test critical alert reached the
      owner's phone)
- [ ] Always-on host with NTP for mainnet (no laptops)

```
Owner:      ____________________   Date: __________
Operator:   ____________________   Date: __________
Scope approved: mainnet dust, venue-minimum orders, cap $______ notional
Review date (pilot ends / re-sign): __________
```

Record the signed copy's location in `audit.jsonl` via
`tradingagents-pro note` (or a manual audit append) so the arming
ceremony's evidence chain references it.
