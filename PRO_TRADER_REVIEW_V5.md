# TradingAgents Pro — Professional Trader Review, Round 5

**Reviewer:** 20+ years on prop desks and at funds — gold, BTC, FX majors, index futures, equities. Ran risk, sat on a risk committee, blew up once early and never again. Daily stack: TradingView Premium, Bloomberg, CoinGlass, Bookmap. I read production trading code the way I read a broker statement — looking for where it lies to its owner.

**Date/time:** 2026-07-18, ~23:42 (dashboard clock).
**Reviewed this round:**
- **Codebase** — read directly at worktree `friendly-dijkstra-fc779b` (HEAD `13834ca`, the `pro/phase-0-contracts` tip).
- **Live app** — `https://trading-agent-pro-c3dc6.web.app`, used hands-on this session.

**Regression context:** R1 58 🟡 → R2 68 🟡 → R3 74 🟢 → R4 82 🟢. I did not inherit those scores; I re-earned every point I gave.

**Live access this round: YES.** No login wall — the app loaded straight to Home with live data. Nothing below is marked `NOT VERIFIED` on live grounds except the one item I call out explicitly (a live *directional* ticket's full geometry — the book is in an event-gate window with only n=2 closed trades, so there was no fresh directional ticket to open).

---

## 2. Executive Summary

The thing that most impressed me is the **honesty of the record and the fail-closed spine of the pipeline** — and I mean that as high praise, because honesty is the rarest thing in this category. The Record page opens with "no backtest curve-fit, no cherry picking," then shows me a **0% win rate on 2 trades, -92.22 net, profit factor 0.00, expectancy -46.11**. It did not round the corner off its own losers. It defines "Proven" as ≥100 closed trades with a calibration curve inside ±10 points of the diagonal and profit factor > 1 net of costs — that is the definition I would write myself. Underneath, the decision gates are pure deterministic functions that route to `rejected` on a plain state check (`graph.py:75-81`), the risk gate **fails closed** when it has nothing to check (`gates.py:60-63`), and LLM nodes can only append evidence — they cannot clear a rejection. I watched the event gate do its job live: every recent run rejected because FOMC is 1.8h out. The bar-replay backtester is the real article — fills at the **next bar's open** (`broker.py:77-91`), stop-checked-before-target (`broker.py:117-133`), a liquidity participation cap (`costs.py:39-48`), slippage and commission on both sides. This is a team that understands what a fill costs.

The one thing standing between this and ⭐ — beyond the obvious n=2 record that only *time* can fix — is that **the live-capital risk tier is declared, tested, and not wired in.** `router.live_gates` defaults to `None` (`router.py:52`) and the entire live-risk check is skipped behind `if self.live_gates is not None` (`router.py:136`). `LiveGateChain` and `LossLimitMonitor` — the classes that enforce per-trade risk %, notional caps, allocation %, order-rate limits, the spread guard, and the daily/weekly loss auto-flatten — are instantiated **only in `tests/`**. Arming a pair to canary/live would route real orders with none of those protections, and `arming.py`'s own docstring claiming "the router still runs every deterministic gate" is **false as wired**. The saving grace is that live mode is structurally unreachable through the shipped `main.py` (paper-by-default, no `TradingMode.LIVE` ever constructed), so this is a landmine in the basement, not a fire in the kitchen. But a config that names a limit you don't enforce is the exact lie I came here to find, and it caps the score.

Second cap: **R4.1 recurs.** `invalidation_price` is still optional on directional tickets (`recommendation.py:82`, `:123`, `:158`). The elegant "a trade may not outlive its thesis" guard only fires *when* an invalidation price happens to exist; a directional ticket ships fine with it null. That was flagged a round ago and it is not fixed.

**Round 5: 80/100 — 🟢.** Essentially holding R4's line, not advancing. The freshly-verified realism of the backtester, the non-bypassable fail-closed gates, and the exemplary record honesty are worth real points — but a declared-and-unenforced live risk tier (a textbook trust-breaker) plus the R4.1 recurrence offset them. A single unenforced limit caps this in 🟢 no matter how polished the rest is, and I am saying so explicitly.

---

## 3. First Impression (30 seconds) — live

Fresh session, straight to Home. In the first glance the status strip answered the four questions I ask every morning:
- **Mode:** `● LIVE · updated 23:38:39` (heartbeat is live, not a stale cache).
- **Regime/session:** `XAUUSD trending down · session closed`.
- **Risk state + equity:** `risk OK · $99,908`.
- **Tape:** `BTC-USD 64,464.88`, ticking.

The hero card was not a trade — it was a **refusal**: `✕ REJECTED at event_gate — "FOMC Press Release in 1.8h — new entries are blocked within 4h of major scheduled events,"` with the line "A refused trade is a decision too — the gates exist to say no." Right-hand rail: `PORTFOLIO EQUITY $99,908 · P&L -92.22 (n=2) · win rate 0%`, live Binance/Delta price cards, and a "Since you left: 8 new runs, latest rejected, 1 trade closed net -5.72."

Verdict on first impression: **this reads like a desk, not a demo.** It leads with the refusal and the sample size. It did not try to sell me a winner. Good.

---

## 4. Market Awareness

Everything I need to answer "is this a good day to trade?" is on the glass:
- **Regime + session:** `XAUUSD trending down · session closed` (status strip, Home + all pages).
- **Risk headroom:** `risk OK · $99,908` in the strip; on Portfolio, `DAY LOSS BUDGET 0% · 0.00 today · limit 3,000` — the daily loss budget is a live, named number, not buried in a config.
- **Macro calendar with countdowns:** Chart footer, `next macro event: Monthly State Retail Sales in 4d 18h`; and the event gate itself surfaced `FOMC Press Release in 1.8h`.
- **Event gate visible on rejected runs:** confirmed — the Home hero and the Decisions verdict both show `REJECTED at event_gate` with the reason.

This is genuinely strong. The system's answer to "should I trade right now?" was an unambiguous **no, FOMC is inside the 4h window** — and it enforced that answer rather than just displaying it.

---

## 5. Trading Decision Review

The book is in an event-gate window (FOMC), so every recent run is a rejection and there is no fresh *directional* ticket on the live app to open. I am marking the live directional-geometry walk **PARTIAL** and verifying the ticket contract from code plus the record's closed trades rather than guessing at a screen I did not see this round.

**Required-elements table** (● = enforced/verified in code, ✅ = seen live, ❌ = missing, ⚠ = conditional):

| Element | Status | Evidence |
|---|---|---|
| Entry / stop / TP ladder with sizes | ● code | `recommendation.py:123` requires entry+stop+≥1 TP for directional; TP sizes `gt=0,le=1` (`:29-31`) |
| Side-aware level validation (stop on correct side) | ● code | `recommendation.py:127-140` — BUY stop<entry, TPs ascending; SELL mirror |
| Position size vs. equity cap | ● code | clamped in engine to `equity*max_position_pct*0.99` (`analytics/risk.py:42-47`) |
| R:R (derived, not asserted) | ● code | `recommendation.py:178-206` recomputes and **rejects** a mismatched supplied value |
| TP fractions sum ≤ 100% | ● code | `recommendation.py:142-144` |
| Confidence | ✅ live (prior) / ⚠ | shown as `confidence NN/100`; but it is raw LLM output (§6) |
| Regime + drift ("now trending…") | ✅ live | strip shows regime; ticket carries `market_regime` (`recommendation.py:94`) |
| Evidence & counterarguments, cited | ● code | `evidence: min_length=1` (`:95`), `counterarguments` preserved (`:96-99`); evals fail on fabricated citations (`harness.py:174-178`) |
| Historical analogs (thresholded) | ● code | `historical_analogs` (`:101`); p(win) gated at `min_n=5` (`service.py:290-324`) |
| p(win) / EV / horizon | ● code | empirical `estimate_p_win`, returns None below threshold |
| **Invalidation (price + prose, direction-consistent)** | **❌/⚠ R4.1** | prose guard is strong *when present* (`:148-176`) but `invalidation_price` is **optional** (`:82`, `:123`, `:158`) |

**The finding that matters:** the geometry engine is honest — you cannot construct a directional ticket with an inverted stop, and you cannot hand-type a flattering R:R because the model recomputes it and throws on mismatch (`recommendation.py:198-205`, "it is derived, not asserted"). That is exactly right. But **R4.1 is not fixed**: a directional ticket can still ship with a null `invalidation_price`, and the "trade may not outlive its thesis" check short-circuits the moment it is null (`:158-159`). So the single most trader-relevant field — *where is my thesis wrong?* — is guaranteed only as prose, not as an enforced, direction-checked price.

**Would I execute it?** The *rejections* I saw live — yes, I'd honor them; refusing to open new risk 1.8h before FOMC is what a disciplined desk does. For a directional ticket: I'd execute the geometry with confidence (stop side, R:R, sizing are trustworthy) **but I would not size up until invalidation_price is mandatory** — I need the machine's structured "I'm wrong here" level, not a sentence.

---

## 6. AI Confidence Review

Split verdict, and the split is the honest part.

- **Headline `confidence` is vibes.** `AgentVote.confidence` (`recommendation.py:43`), the debate/judge confidences (`schemas.py:30,83`), and the final `recommendation.confidence = state["judge_confidence"]` (`nodes.py:568`) are raw LLM integers bounded to [0,100] and derived from nothing. If the UI leads with "confidence 72/100," that number is an opinion.
- **The grounded number exists and is gated correctly.** `estimate_p_win` (`service.py:290-324`) is empirical — it counts wins in the scored record, prefers outcomes within ±band of the ticket's confidence, requires `min_n=5`, and returns None below threshold ("no invented numbers"). Kelly only appears when real win stats exist and goes to 0 on negative edge (`risk.py:56-72`). That is the right way to do it.
- **Calibration is tracked but not scored.** The retro-scorer grades real past recommendations against what happened next, tags them `mode:"retro"`, and feeds calibration without entering the blotter (`retro.py:9-24`) — I saw this stated verbatim on the Record page ("Retro-graded decisions feeding calibration: 0"). Per-agent hit rates are computed (`service.py:464-508`). **Gap:** there is no Brier score, no ECE, no single reliability number (grep finds none). Calibration is *observable* via banded hit rates but not *scored*.

Auditable? Yes — the empirical path is. Honest? Yes — it distinguishes the LLM's opinion from the record's evidence. Sample-sized? The p(win) is; the headline confidence is not. **The fix is a one-liner of honesty:** label the headline number "model view" and put the empirical p(win) (with n) next to it as the number that matters.

---

## 7. Chart Review

Live, `/trade/XAUUSD`, 1h. This is the strongest surface in the product.
- **Decisions painted on price:** confirmed — clustered decision markers on the last candles, with P&L-style labels (`-87`, `-6`) sitting at the levels. The record is drawn on the tape, not in a side table.
- **Volume-by-price profile:** the blue horizontal histogram is on by default (`Profile` toggle present).
- **AI Plan / AI History / Profile / log scale / grid (1, 2×1, 2×2):** all present in the toolbar.
- **Replay:** control is present; per code it hides future decisions and pauses on the decision bar (backtest snapshot uses bars ≤ i, `data.py:123-127`) — consistent with what a replay must guarantee.
- **Macro countdown on the chart:** `next macro event: Monthly State Retail Sales in 4d 18h`.
- **History depth:** the familiar ~300-bar wall is visible (chart starts abruptly around the 7th). Fine for intraday context; a swing trader will want more.

Full drawing toolset on the left rail (trend, ray, arrow, fib, long/short position tool, zone, channel, text, alert, measure). This is legitimately close to a charting terminal. The "ask the record" chat and rejected-run painting I verified in prior rounds; this round I confirmed the chart renders live with decisions overlaid and the macro clock running.

---

## 8. Risk Management Review — declared vs. enforced

This is where code and live must reconcile. Live risk state: `risk OK · $99,908`, day-loss budget `0.00 / limit 3,000`, no open positions, circuit not tripped. Now the code:

**`RiskLimits` (base) — `contracts/config.py:21-37`**

| Limit | Declared | Enforced on running path? | Where |
|---|---|---|---|
| `max_position_pct_equity` | ✔ | **ENFORCED** | `validation.py:48-52` via `router.py:124` on every submit |
| `max_leverage` | ✔ | **ENFORCED** (as a cap multiplier — note it *widens* notional) | `validation.py:48` |
| `max_daily_loss_pct` | ✔ | **ENFORCED** | CircuitBreaker `safety.py:97-101` + risk_gate VaR ceiling `gates.py:34` |
| `circuit_breaker_consecutive_losses` | ✔ | **ENFORCED** | `safety.py:92-96`, checked `router.py:132-134` |
| `max_orders_per_day` | ✔ | **ENFORCED** (paper loop) | `service.py:307-315` |
| `max_risk_per_trade_pct` | ✔ | **DECLARED-ONLY on the running path** | only inside unwired `LiveGateChain` (`live_gates.py:105-113`); paper path never clamps it |
| `max_open_positions` | ✔ | **DECLARED-ONLY on the running path** | only in unwired `LiveGateChain` (`live_gates.py:84-89`) |
| `max_drawdown_pct` | ✔ | **NOT ENFORCED ANYWHERE** | LLM-display only, `agents/metrics.py:105` |

**`LiveRiskLimits` — `contracts/config.py:40-89`:** per-trade notional, allocation %, orders-per-hour/day, daily/weekly loss limits, drawdown-from-HWM, venue-error cooldown, `max_spread_bps`, `market_order_notional_cap`. **All enforced only inside `LiveGateChain` / `LossLimitMonitor`, which are instantiated only in `tests/`.** In production `router.live_gates is None` (`router.py:52`), so the whole block at `router.py:136-139` is skipped. The only live-limit that bites is the leverage-ack at config construction (`config.py:80-89`) — you cannot *build* a >1x live config without the acknowledgement flag.

**Reconciliation with live:** the live app is paper-by-default and the enforced base-limit set (position cap, daily loss, circuit breaker, order budget, kill switch, fail-closed gates, human-approval interrupt) is genuinely working — the event-gate refusals prove the gate spine is live. The **contradiction** is latent: the system advertises a live-capital risk tier it does not run. It is mitigated only by the fact that shipped `main.py` never constructs a LIVE config, so you cannot reach the unguarded path without writing new code. That is a real mitigation — but "safe because you can't turn it on yet" is not the same as "safe."

One more: **`event_gate` fails OPEN** (`gates.py:82-93`). A missing or unparseable calendar returns `passed=True`. I watched it block FOMC beautifully today — but if the calendar feed breaks, that block silently disappears, which is the one failure mode this gate exists to prevent. And the CircuitBreaker's daily-dollar trip is computed off a hardcoded `equity_base=100_000.0` (`main.py:316`), so the dollar threshold drifts from reality as the account moves.

---

## 9. Code Integrity Review

Each finding: severity · `path:line` · why a trader cares · the fix.

**CI-1 · CRITICAL · `router.py:52,136`; `main.py` build path; `execution/live_gates.py` (tests-only).**
The live-capital risk tier (`LiveGateChain`, `LossLimitMonitor`) is never wired into the running service; `router.live_gates` stays `None`, so per-trade risk %, notional cap, allocation %, order-rate limits, spread guard, and daily/weekly-loss auto-flatten are all skipped for a live-armed pair. `arming.py`'s "the router still runs every deterministic gate" is false as shipped. *Why I care:* this is the classic config-limit-that-isn't-enforced; the day someone wires live, the account trades naked. *Fix:* construct `LiveGateChain`/`LossLimitMonitor` from `load_live_config` inside `build_service` and inject on arm; add a boot assertion that refuses to arm live if `live_gates is None`; correct the docstring.

**CI-2 · HIGH (R4.1 recurs) · `recommendation.py:82,123,158`; `nodes.py:571-574`; `metrics.py:127-134`.**
`invalidation_price` is optional for directional trades; the thesis-death guard only runs when it is present. *Why I care:* the invalidation is the trade's reason for living; leaving it optional means the "can't outlive its thesis" protection is best-effort. *Fix:* require a correctly-sided `invalidation_price` for BUY/SELL in `_validate_geometry`, and have the engine fail the ticket (not silently drop the key) when reflection can't produce a sided level.

**CI-3 · MEDIUM · `agents/metrics.py:105`; `live_gates.py:84-113`.**
`max_drawdown_pct` is enforced nowhere; `max_risk_per_trade_pct` and `max_open_positions` have no paper-path enforcement. *Why I care:* per-trade risk % is the single number retail blows up on; it should bite in paper too. *Fix:* enforce per-trade risk and open-position count in `validation.py` (the always-wired path), not only in `LiveGateChain`.

**CI-4 · MEDIUM · `gates.py:82-93`.**
`event_gate` fails open on a missing/unparseable calendar. *Why I care:* a broken feed silently re-enables trading into CPI/FOMC. *Fix:* fail closed (or degrade to a hard "calendar unavailable — manual confirm required") when the calendar is absent but the market is open.

**CI-5 · MEDIUM · `memory/store.py:29-43`; `memory.py:254-271`; `retro.py`.**
The track record is append-only *by convention* — no hashing, no hash-chain, plain-text JSONL; `load` silently skips malformed lines with a warning, so a corrupted losing outcome vanishes from the win-rate with no hard failure. And `win_stats`/Kelly blend retro-scored *predictions* with lived fills (the blotter filters retro out, but the stats used for sizing do not). *Why I care:* "the record can quietly drop a loser" is the cardinal sin, even if here it's by accident rather than design. *Fix:* hash-chain the JSONL (or checksum each line); hard-fail on a malformed line in the stats path; exclude `mode:"retro"` from `win_stats` the way the blotter already does.

**CI-6 · LOW · `recommendation.py:43`; `nodes.py:568`.**
Headline confidence is raw LLM output. *Fix:* relabel as "model view"; surface empirical p(win)+n as the primary number.

**CI-7 · LOW · `broker.py:119`; `metrics.py:17,67,96-98`; `alpha_vantage_fundamentals.py:30-45`, `alpha_vantage_news.py:56+`, `alpha_vantage_common.py:148-150`.**
Backtest stop fills ignore gap risk (a stop always fills at its level, never gapped-through — optimistic). `profit_factor`/`Sortino` can return `inf`; Sharpe uses a fixed 252 annualization (mis-scales intraday). And the **live** Alpha Vantage fundamentals/insider paths leak point-in-time: `get_fundamentals`/OVERVIEW and insider transactions are unfiltered, the statement filter uses fiscal-period-end not filing date, and the CSV filter fails *open* on a parse error. *Why I care:* the equity-fundamentals leakage would flatter any equities backtest; it's peripheral to the crypto/XAU/FX pairs this system actually trades, but it's a loaded gun in the data layer. *Fix:* model stop gaps (fill at `min(stop, bar.open)` for longs); cap the `inf` metrics; parametrize annualization by timeframe; date-filter the fundamentals/insider endpoints and fail *closed* on CSV parse errors.

**What the tests actually lock in (credit where due):** the evals assert *trading correctness*, not just "it runs" — forbidden-action direction checks, an overconfidence ceiling on ambiguous fixtures, citation-grounding (fabricated agent ids fail), and injection resistance, with a Wilson CI and an all-samples-must-pass rule (`harness.py:146-188`). The scripted LLM is honestly labeled mechanics-only. That is a real correctness harness.

---

## 10. Top 20 Missing (ranked)

1. Enforced live risk gates wired into the running service (CI-1).
2. Mandatory, direction-checked `invalidation_price` on directional tickets (CI-2).
3. Enforced per-trade risk % and max-open-positions on the paper path (CI-3).
4. A real calibration score (Brier/ECE) on the Record page, not just banded hit rates.
5. Tamper-evident record (hash-chain) and hard-fail on corrupt journal lines (CI-5).
6. `event_gate` fail-closed / manual-confirm on a dead calendar (CI-4).
7. Stop-gap modeling in the backtester (CI-7).
8. A live directional ticket reachable outside the event window (I couldn't review one this round).
9. Correlation/exposure view across pairs (BTC vs. XAU vs. DXY) — the book is single-position today.
10. Position sizing tied to *live* equity in the circuit-breaker dollar trip, not a hardcoded 100k (CI-1/§8).
11. Slippage that scales with size/volatility, not a flat 2 bps.
12. Deeper chart history than the 300-bar wall for swing context.
13. Per-ticket EV in currency (not just R:R and p(win)).
14. Spread/liquidity display on the ticket (the `max_spread_bps` guard isn't even wired).
15. A "what would have to be true" sensitivity on the invalidation level.
16. Portfolio-level VaR/heat, not just per-trade.
17. Filing-date (not fiscal-end) point-in-time for fundamentals (CI-7).
18. An audit export (signed) of the decision + gate trail per ticket.
19. Alerting on gate-config drift (e.g. calendar feed down).
20. Timeframe-aware Sharpe annualization (CI-7).

## 11. Top 20 Frustrations (ranked)

1. **The live risk tier is advertised but off** — and the docstring says otherwise (CI-1). Most dangerous kind of frustration.
2. **R4.1 recurs** — invalidation still optional after being flagged (CI-2).
3. `max_drawdown_pct` is a config field that does nothing (CI-3).
4. Headline confidence looks authoritative but is ungrounded (CI-6).
5. Kelly/win-rate can blend retro predictions with real fills (CI-5).
6. Corrupt journal line → silently dropped from win-rate (CI-5).
7. `event_gate` fails open (CI-4).
8. Circuit-breaker dollar trip off a hardcoded 100k (§8).
9. No live directional ticket to inspect during an event window.
10. 300-bar history wall.
11. Backtest stops never gap (CI-7).
12. `inf` profit factor/Sortino can flatter aggregates (CI-7).
13. Flat slippage regardless of size.
14. No cross-pair exposure view.
15. Alpha Vantage fundamentals leak point-in-time (CI-7) — even if peripheral.
16. "A new version is available" refresh banner interrupted navigation repeatedly during review.
17. No spread shown on the ticket despite a spread guard existing in code.
18. Single-position engine (disclosed, but limiting).
19. No signed audit export.
20. Notification bell shows `25+` with no obvious triage.

## 12. Top 20 Improvements that would make me pay more (each tied to a $/size decision)

1. Wire + prove the live risk gates (CI-1) — this is the difference between "I paper-trade it" and "I let it touch real capital." Worth the most.
2. Mandatory invalidation (CI-2) — lets me size to a *defined* risk point, so I'd size up per trade.
3. Enforce per-trade risk % in paper (CI-3) — I'd trust the sizing enough to mirror it live.
4. Brier/ECE calibration number — a calibrated 60% p(win) is bettable; size scales with it.
5. Hash-chained record (CI-5) — a tamper-evident track record is what a fund's ODD team requires before allocation.
6. EV-in-dollars per ticket — direct position-sizing input.
7. Size/vol-scaled slippage — makes the backtest P&L one I'd actually underwrite.
8. Cross-pair heat/VaR — lets me run more than one pair without doubling risk blindly.
9. Spread/liquidity on the ticket — I skip trades when the spread eats the edge; show me.
10. Fail-closed event gate (CI-4) — removes my need to babysit the calendar.
11. Stop-gap modeling (CI-7) — honest drawdowns; I'd trust the max-DD figure.
12. Deeper history — swing setups I'd pay for.
13. Live-equity circuit trip (§8) — the daily stop actually means dollars.
14. Signed audit export — compliance-grade, unlocks institutional pricing.
15. Timeframe-aware Sharpe (CI-7) — comparable metrics across TFs.
16. "Model view vs. empirical p(win)" relabel (CI-6) — trust, cheaply bought.
17. Retro-excluded Kelly (CI-5) — sizing I'd follow.
18. Calendar-feed health alert — operational trust.
19. Correlation-aware invalidation (BTC stop that respects DXY) — fewer whipsaws.
20. Filing-date fundamentals (CI-7) — if equities are ever added, this is table stakes.

---

## 13. Final Verdict

**1. Daily use?** Yes — as decision *support* and a discipline layer. I'd keep it open next to Bloomberg for the regime read, the event-gate refusals, and the decision replay. The event gate alone earns screen space.

**2. Real money, unattended?** No. Two hard stops: the live risk tier isn't wired (CI-1) and invalidation isn't mandatory (CI-2). Attended paper, yes, today. Real capital only after CI-1/CI-2/CI-3 are closed *and* the record has aged.

**3. Recommend to professionals?** Yes, with the caveats above stated plainly. Pros will respect the honesty of the Record page and the fail-closed gates; they will immediately spot the unwired live tier, so I'd rather tell them first.

**4. Replace TradingView?** No. The chart is impressively close and the decision-painting is something TV doesn't do, but the 300-bar wall, indicator breadth, and history depth aren't there yet. It's a superb *complement*, not a replacement.

**5. Max monthly price today vs. when the record ages?** Today, as attended decision support: **~$50–80/mo** — priced on the gates, the chart, and the honesty, discounted hard for the n=2 record and the unwired live tier. With an aged, calibrated record (n ≥ 100, calibration holding within its own ±10-pt bar) and CI-1/CI-2 closed: **$250–400/mo**, because at that point it's a risk-managed signal I could size against.

**6. Three most important improvements:** (1) wire and prove the live risk gates (CI-1); (2) make `invalidation_price` mandatory and direction-checked (CI-2); (3) publish a real calibration score and hash-chain the record (CI-4/CI-5).

**Tier: 🟢 · Round 5: 80/100.**

What closes the gap to ⭐: time and two commits. The engineering discipline is already ⭐-grade in places — the fail-closed gates, the next-bar-open fills, the derived-and-verified R:R, the record that refuses to hide its losers. But ⭐ means "trusted with real capital," and that requires three things this system does not yet have: a live risk tier that is actually enforced (not just tested), an invalidation level that is mandatory rather than optional, and — the one no sprint can buy — an aged, honest, calibrated track record with a large n. Close CI-1 and CI-2, let the record run to 100 trades with calibration holding, and this is a genuine ⭐. Until then it is exactly what it honestly says it is: an excellent, unusually honest decision-support system that is not yet ready to manage money on its own.
