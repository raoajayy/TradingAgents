# 16 — Implementation Review: engine vs. research + quality audit

**Date:** 2026-07-24 · **Scope:** `tradingagents/pro/backtest/` (+ `analytics/`, `rl/`, `dashboard/backtest_job.py`) reviewed against deliverables 01–15. **Method:** each claim verified against the cited `file:symbol` or a run — not asserted from memory. **Baseline:** full pro suite green (~1150 passed).

## Verdict

The engine is a **high-fidelity realisation of the research**: all six evolution tracks (T1–T6) are built and deployed, the design invariants (determinism, structural no-look-ahead, honesty-first metrics, additive-not-rewrite) hold, and the field-wide differentiator — deflated-Sharpe + PBO overfitting guards surfaced with an honest trial count — is implemented and live. The gaps are **specific and mostly at the strategy layer**, not the engine core: the research's two priority trader "packages" are only partially expressible because **pyramiding-on-winners, a market-health/breadth entry gate, and the correlation-exposure cap are not reachable by a shipped strategy / job**, and the **hard daily-loss risk gate does not cover the native order-book path**. Quality is strong (clean determinism guard, no TODOs, disciplined honesty conventions); the debts are **7 untested modules, 3 oversized files, one duplicated order-construction site, and 3 exposed-but-unwired modules**.

---

## A1 · Research-fidelity traceability matrix

Status: ✅ met · ◑ partial · ✗ gap · ⚠ violated

### Honesty rules — 01_trader_statistics.md
| Assertion | Evidence | Status |
|---|---|---|
| No fabricated numbers; assumptions labelled | `costs.py` `_COST_PROFILES`/`FundingModel` "conservative assumptions, not measured venue data" / "ASSUMED" | ✅ |
| Guards report probability-of-spurious, not invented passes | `validation.py` neutral sentinels (PSR→0.5, `expected_max_sharpe`→0.0, PBO→0.0 on degenerate input) | ✅ |
| null ≠ 0; degrade rather than invent | `_extended_bundle`/report best-effort → skip on failure; `_submit_intent` returns without inventing a fill when unsized | ✅ |
| Deterministic-rules runs labelled "mechanics only, not an edge measurement" | dashboard result view provider note | ✅ |

### Trader patterns / archetypal packages — 02_pattern_report.md, 03
| Assertion | Evidence | Status |
|---|---|---|
| Package #1 trend-following expressible: Donchian → vol-normalized sizing → ATR stop → correlation cap → **pyramiding** → trailing | `trend_following_v1` (Donchian+ATR+% trail); risk sized via `fixed_risk_position_size`; **no pyramiding**, correlation cap not strategy-reachable | ◑ |
| Package #2 momentum/growth expressible: **market-health/breadth gate** → RS/watchlist prescreen → breakout → structure stop → sell-into-strength | `momentum_v1`/`htf_momentum_v1` cover ROC + HTF-trend confirm; **no market-health/breadth gate**, no watchlist prescreen | ◑ |
| Divergent entries, convergent risk/exit; entry logic pluggable | Strategy SDK (`strategy.py` protocol + registry); 7 archetypes | ✅ |
| Vol-normalized / ATR sizing available | `analytics/risk.py` `atr_stop_loss`, `fixed_risk_position_size` | ✅ |

### Best practices — 03_institutional_best_practices.md
| Assertion | Evidence | Status |
|---|---|---|
| Risk sized off volatility | `analytics/risk.py`; every strategy exposes `risk_pct` | ✅ |
| Stop defined before entry, submitted as a bracket | `BracketIntent` + order book (`_open_from_fill` requires `stop_loss`) | ✅ |
| Pyramid winners, never average losers | primitive `SimBroker._add_to_position` exists; **no strategy pyramids**; `reduce_only` prevents flip | ◑ |
| Portfolio heat + correlation caps | `allocator.py` (EqualWeight/InverseVol/VolTarget) wired; `correlation.py` `CorrelationGuard` **not wired into `run_portfolio_job`** | ◑ |
| Entries gated by regime/market-health | regime context available (`multitf`, `classify_regime`, `MLRegimeModel`); **no strategy gates entries on it** | ◑ |
| Trailing / sell-into-strength | `_update_trailing` pct/atr/chandelier | ✅ |
| **Hard, un-overridable risk gates** (the #1 realism feature) | `pipeline/gates.py risk_gate` (daily-loss) enforced on the **pipeline** path; broker caps (count/gross/same-dir/cooldown) apply to native path, **but the daily-loss/drawdown gate does not cover native order-book strategies** | ◑ |
| Tail behavior surfaced (MC, drawdown, risk-of-ruin) | `montecarlo.py`, `report.py drawdown_curve`/`risk_of_ruin` | ✅ |

### Gap analysis + tracks — 05, 06
| Assertion | Evidence | Status |
|---|---|---|
| C6 Strategy SDK | `strategy.py`, `registry.py`, `rules_v1` | ✅ |
| C2 Order lifecycle (limit/stop/stop-limit, brackets/OCO, honor entry_price) | `broker.py` order book + `_attempt_fill`/OCO | ✅ |
| C6 Optimization + guards | `optimize.py` + `validation.py` (DSR/PBO) | ✅ |
| C1 Portfolio + multi-TF | `portfolio.py`/`portfolio_engine.py`/`multitf.py` | ✅ |
| C5 Cost realism (spread + sqrt-impact + funding) | `costs.py` `SlippageModel`/`FundingModel`/`MarginModel` | ✅ |
| C3 tick / C4 distributed remain non-goals | not built (deliberate) | ✅ |
| Design invariants: determinism, `snapshot_at(i)`≤i + fill i+1, additive, hard gates | `engine.py`, `test_pro_strategy_equivalence.py`; hard-gate caveat above | ✅ (gate ◑) |

### Validation acceptance bar — 12_validation_methodology.md
| Assertion | Evidence | Status |
|---|---|---|
| DSR + PBO + honest `n_trials` attached to every optimization | `optimize._finalize`; **all** evaluated trials counted (incl. genetic/bayesian generations) | ✅ |
| Walk-forward that truly fits (purged/embargoed) | `walkforward.run_walk_forward_optimization` (embargo) | ✅ |
| Verdict band; OOS-as-headline | `OptResult.verdict()`; UI OptimizePanel — **confirm OOS framing is the headline vs a secondary metric** (walk-forward is a separate flow from single optimize) | ◑ |
| Per-capability look-ahead checklist | asserts in `test_pro_{multitf,futures,metalabel,portfolio_replay}` | ✅ |
| A-priori constants policy (tuned = declared `Param`; defaults = shipped) | `ParamSpace`/`strategy_params` recorded; defaults are the shipped constants | ✅ |

### Roadmap / final rec — 13, 15
| Assertion | Status |
|---|---|
| Build order P0→P5 honored | ✅ |
| Skips honored (tick, vectorized rewrite, distributed, live parity, retail-orthodoxy defaults) | ✅ |
| Open Q4 (RL integrate-or-retire): tabular Q + deep-RL built behind PolicyProtocol, advisory only | ✅ (decision: integrated as one advisory voice) |
| Open Q5 (seed reference strategies): trend_following_v1 + momentum_v1 shipped | ✅ (pyramiding/health-gate variants still missing — see F1/F2) |

---

## A2 · Implementation-quality scorecard

| Dimension | Rating | Evidence / note |
|---|---|---|
| Determinism + equivalence guard | ✅ strong | `test_pro_strategy_equivalence.py` byte-identical (trades/equity/decisions/rejections) across 3 regimes; parallel==serial reassembly in `optimize` |
| Look-ahead enforcement | ✅ strong | `snapshot_at(i)`≤i, fill i+1; explicit no-lookahead asserts in multitf/futures/metalabel/portfolio_replay |
| Honesty conventions | ✅ strong | labelled assumptions; neutral guard sentinels; no fabricated stats |
| Test coverage | ◑ | **no dedicated test:** `data.py`, `montecarlo.py`, `trade_log.py`, `agent_attribution.py`, `regime_breakdown.py`, `portfolio.py`, `charts.py` (covered only transitively) |
| Tech-debt markers | ✅ | zero TODO/FIXME/XXX/HACK; `noqa`×8 all legit; `# type: ignore`×0 |
| Error handling | ◑ | 19 broad `except Exception` in `analytics/`+`rl/`+`backtest_job.py` — confirm each narrows/logs |
| Module size | ◑ | `backtest_job.py` 1685, `broker.py` 810, `strategies.py` 715 — split candidates |
| Duplication | ◑ | `engine._submit_intent` ≈ `portfolio_engine._submit_intent` (15-field `PendingOrder` build must be mirrored) |
| Dead-but-exposed | ◑ | `correlation.py`, `metalabel.py`, `futures.py` imported only by `__init__` — no engine/job/strategy consumer |

---

## A3 · Ranked findings (drive Part B remediation)

Severity S1(high)–S3(low) × Effort E1(small)–E3(large).

- **F1 · S1/E2 — no pyramiding on winners.** best-practice #3 + package #1. `SimBroker._add_to_position` exists but unused by strategies. → opt-in scale-in-on-winners in a `trend_following_v2` (stop trails the aggregate), off by default. **✅ RESOLVED** — `TrendFollowingV2` (`strategies.py`), `max_adds`/`add_atr_mult`, off by default; `test_pro_trend_following_v2.py`.
- **F2 · S1/E2 — no market-health/regime entry gate.** package #2 + best-practice #5. regime context available but ungated. → regime/health-gated momentum reference strategy (or a gate param), look-ahead-safe. **✅ RESOLVED** — `RegimeMomentumV1` (`strategies.py`) gates entries via `classify_regime` (stands aside in CRISIS/HIGH_VOLATILITY); `test_pro_regime_momentum.py`.
- **F3 · S1/E1 — daily-loss hard gate not on the native path.** best-practice #7 (the most important realism feature). `risk_gate` is pipeline-only; native order-book strategies bypass it. → broker-level daily-loss/drawdown guard applied to all paths. **✅ RESOLVED** — `BacktestEngine._risk_breaker_reason` (`engine.py`), opt-in `risk_breaker`; `test_pro_risk_breaker.py`.
- **F4 · S2/E1 — correlation-exposure cap unreachable.** `CorrelationGuard` built but `run_portfolio_job` never passes `corr_guard=`. → wire it (opt-in) + expose in the portfolio request; document default-off. **✅ RESOLVED** — `PortfolioRunRequest.max_correlation` → `run_portfolio_job` builds `CorrelationGuard`; `test_pro_backtest_job.py::test_portfolio_correlation_cap_wires_and_runs`.
- **F5 · S2/E1 — OOS-as-headline framing.** confirm the optimize/walk-forward UI presents out-of-sample as the headline with in-sample secondary (12_ standard); fix framing if secondary. **✅ RESOLVED (verified, no change).** The optimize UI's headline is the plain-language **verdict band driven by PBO + deflated Sharpe** (`OptimizePanel.tsx::ResultCard`); PBO *is* a CSCV out-of-sample overfitting estimate, and the raw in-sample objective is demoted to a secondary `Stat` with an explicit "re-test out-of-sample before trusting it" nudge. The honesty bar is met. A walk-forward *equity-curve* headline is **SDK-available** (`run_walk_forward_optimization`) but not yet a UI flow → **deferred enhancement**, not a defect.
- **F6 · S2/E2 — 7 untested modules.** add `test_pro_*` for data/montecarlo/trade_log/agent_attribution/regime_breakdown/portfolio (+ charts smoke). **✅ RESOLVED** — `test_pro_montecarlo.py`, `test_pro_trade_analytics.py` (trade_log + agent_attribution + regime_breakdown); data/portfolio/charts already exercised by existing suites.
- **F7 · S2/E2 — duplicated `_submit_intent`.** extract one shared order-construction helper; preserve portfolio allocator-cap + rejection-reason via params; guarded by equivalence/order-book/portfolio suites. **✅ RESOLVED** — `order_build.py` (`size_intent`/`build_pending_order`/`new_order_id`); both engines refactored to call it; equivalence byte-identical.
- **F8 · S3/E3 — oversized modules.** split `backtest_job.py` (orchestration/persistence/request-models), `broker.py` (order-book+fills vs position-mgmt), `strategies.py` → `strategies/` package; pure moves + re-exports, suites green.
- **F9 · S3/E1 — broad `except Exception` audit.** narrow/log the 19 sites in analytics/rl/job. **✅ RESOLVED (verified, no change).** Every site is a deliberate never-crash-the-server / best-effort handler that logs with context (`logger.warning`/`logger.exception` + `exc_info=True`, most carrying an explanatory comment or `# noqa: BLE001`) or explicitly accounts for the skip (`retro.py:107` increments `skipped_unresolved`); the vendor-fetch site (`backtest_job.py:349`) retries then truncates with a warning. `rl/` has no broad catches. No silent swallow — no change warranted.
- **F10 · S3/E1 — exposed-but-unwired modules.** integrate `correlation`/`metalabel`/`futures` into a job/strategy path, or annotate as intentional public SDK surface with a usage example + test. **✅ RESOLVED.** `correlation` is now wired into `run_portfolio_job` (F4); `metalabel` and `futures` are exported via `backtest/__init__.py.__all__` **and** carry dedicated suites (`test_pro_metalabel.py`, `test_pro_futures.py`) → confirmed intentional public SDK surface, not dead code.

**Remediation status (2026-07-24):** F1–F7, F9, F10 resolved (F5/F9/F10 verified as already-satisfied; the rest by additive, off-by-default fixes with new tests). **F8 pending** (pure structural moves). Deferred enhancement noted under F5 (walk-forward equity-curve UI headline).

**Exit criterion:** zero ⚠ (violated) rows — none today. Every ◑/✗ above is remediated in Part B or explicitly deferred (feed-gated: intraday/tick, options; out-of-scope per 15_).
