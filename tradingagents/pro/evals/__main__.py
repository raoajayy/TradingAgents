"""Run the decision evals against a real model:

    python -m tradingagents.pro.evals [--samples N] [--tag TAG] [--limit K]

Requires provider credentials (env or repo-root .env). Provider/model via
TRADINGAGENTS_LLM_PROVIDER / _QUICK_THINK_LLM / _DEEP_THINK_LLM. Exits
nonzero on failure or when the provider never answered, so CI can gate.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from tradingagents.contracts import AssetClass, ModelRouting, ProConfig
from tradingagents.pro.evals.golden import golden_cases
from tradingagents.pro.evals.harness import run_decision_evals
from tradingagents.pro.models import bundle_from_config
from tradingagents.pro.observability import CostTrackingLLM, price_for

EST_COST_PER_CASE_RUN = 0.20  # measured on the first live runs (gpt-5.4-mini/gpt-5.5)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tradingagents.pro.evals")
    parser.add_argument("--samples", type=int, default=1,
                        help="runs per case (default 1)")
    parser.add_argument("--tag", default=None,
                        help="only cases with this tag (direction/ambiguous/"
                             "injection/intraday/gap)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of cases")
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    parser.add_argument("--stability", action="store_true",
                        help="P1-01: run the pass^k stability harness instead "
                             "of the golden evals")
    parser.add_argument("--k", type=int, default=10,
                        help="runs per frozen snapshot for --stability "
                             "(default 10)")
    parser.add_argument("--ablation", action="store_true",
                        help="P1-02: pipeline vs single strong model on "
                             "historical cut points, retro-scored")
    parser.add_argument("--points", type=int, default=5,
                        help="historical cut points for --ablation / "
                             "--crowding-study (default 5)")
    parser.add_argument("--crowding-study", action="store_true",
                        help="P5-04: crowding study — our pipeline vs a "
                             "population of generic LLM traders (3 "
                             "documented prompt styles) reading the SAME "
                             "evidence pack; reports agreement, Cohen's "
                             "kappa, crowding score and rolling drift")
    parser.add_argument("--memorization-audit", action="store_true",
                        help="P2-03: re-run the frozen stability snapshots "
                             "named vs anonymized (tickers/dates masked) and "
                             "compare action agreement per symbol")
    parser.add_argument("--seed", type=int, default=0,
                        help="label-mapping seed for --memorization-audit "
                             "(default 0)")
    parser.add_argument("--factor-mine", action="store_true",
                        help="P3-03: LLM factor-mining loop — propose "
                             "formulaic factors, evaluate OOS (purged IC) "
                             "vs Alpha158-style baselines, report survivors")
    parser.add_argument("--iterations", type=int, default=3,
                        help="proposal rounds for --factor-mine (default 3)")
    parser.add_argument("--self-assessment", action="store_true",
                        help="P3-08: generate the RTS-6-flavored quarterly "
                             "self-assessment from the event store (no "
                             "model calls); requires --start and --end. "
                             "Reads the P2-01 SQLite event store when its "
                             "DB exists, else the legacy file layout. "
                             "Against PROD data: restore the Litestream "
                             "replica first (mirror scripts/"
                             "pro_restore_drill.sh) and point "
                             "TRADINGAGENTS_PRO_DB at the restored DB.")
    parser.add_argument("--start", default=None,
                        help="period start YYYY-MM-DD (--self-assessment)")
    parser.add_argument("--end", default=None,
                        help="period end YYYY-MM-DD, inclusive "
                             "(--self-assessment)")
    parser.add_argument("--build-corpus", action="store_true",
                        help="P4-01: build the graded-outcome training "
                             "corpus from the event store (no model "
                             "calls) and print the fine-tune gate "
                             "verdict (>=200 graded outcomes). Reads the "
                             "store exactly like --self-assessment; "
                             "against PROD data restore the Litestream "
                             "replica first and point TRADINGAGENTS_PRO_DB "
                             "at the restored DB.")
    parser.add_argument("--include-rejections", action="store_true",
                        help="also emit rejected runs as outcome-less, "
                             "reward-0 abstention examples "
                             "(--build-corpus)")
    parser.add_argument("--out", default=None,
                        help="output JSONL path (--build-corpus; default "
                             "docs/evals/corpus_<stamp>.jsonl)")
    args = parser.parse_args()

    if args.build_corpus:
        # zero-LLM: same store resolution as --self-assessment — the P2-01
        # SQLite event store when its DB exists, else the legacy file layout
        from datetime import datetime, timezone
        from pathlib import Path

        from tradingagents.pro.dashboard.prefs import default_data_dir
        from tradingagents.pro.dashboard.recorder import PipelineRecorder
        from tradingagents.pro.evals.corpus import (
            bars_from_runs,
            build_training_corpus,
            corpus_summary,
            gate_verdict,
            write_corpus_jsonl,
        )
        from tradingagents.pro.memory import ProMemory
        from tradingagents.pro.store import SqliteMemoryStore, default_db_path

        data = default_data_dir()
        if default_db_path().exists():
            from tradingagents.pro.store import EventStore

            store = EventStore()
            recorder_runs = PipelineRecorder(store=store).runs
            memory = ProMemory(store=SqliteMemoryStore(store))
        else:
            memory_path = data / "memory.jsonl"
            memory = (ProMemory(store_path=memory_path)
                      if memory_path.exists() else ProMemory())
            recorder_runs = PipelineRecorder(store_dir=data / "runs").runs
        examples = build_training_corpus(
            recorder_runs, memory, bars_from_runs(recorder_runs),
            include_rejections=args.include_rejections)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = (Path(args.out) if args.out
               else Path("docs/evals") / f"corpus_{stamp}.jsonl")
        write_corpus_jsonl(examples, out)
        summary = corpus_summary(examples)
        print(f"corpus: {summary['total']} example(s) from "
              f"{len(recorder_runs)} stored run(s) "
              f"({summary['graded']} graded, {summary['ungraded']} ungraded)")
        for symbol, count in sorted(summary["by_symbol"].items()):
            print(f"  {symbol}: {count}")
        for klass, count in sorted(summary["by_outcome"].items()):
            print(f"  outcome {klass}: {count}")
        print(gate_verdict(summary["graded"]))
        print(f"wrote {out}")
        return 0

    if args.self_assessment:
        # zero-LLM: reads the shared /data volume like the operator CLI —
        # runs, memory, and the hash-chained audit log; never a provider key
        from datetime import date
        from pathlib import Path

        from tradingagents.pro.dashboard.prefs import default_data_dir
        from tradingagents.pro.dashboard.recorder import PipelineRecorder
        from tradingagents.pro.evals.self_assessment import (
            generate_self_assessment,
        )
        from tradingagents.pro.memory import ProMemory

        if not args.start or not args.end:
            print("--self-assessment requires --start and --end "
                  "(YYYY-MM-DD)", file=sys.stderr)
            return 2
        try:
            start = date.fromisoformat(args.start)
            end = date.fromisoformat(args.end)
        except ValueError as exc:
            print(f"bad date: {exc}", file=sys.stderr)
            return 2
        data = default_data_dir()
        # P3-08: the P2-01 SQLite event store is the source of truth for
        # runs and memory wherever it exists (prod, and any dir restored
        # from the Litestream replica — see scripts/pro_restore_drill.sh +
        # TRADINGAGENTS_PRO_DB); the legacy one-file-per-run/JSONL layout
        # remains the fallback for pre-P2-01 data dirs. The audit log is a
        # hash-chained FILE in both layouts.
        from tradingagents.pro.store import (
            SqliteMemoryStore,
            default_db_path,
        )

        if default_db_path().exists():
            from tradingagents.pro.store import EventStore

            store = EventStore()
            recorder_runs = PipelineRecorder(store=store).runs
            memory = ProMemory(store=SqliteMemoryStore(store))
        else:
            memory_path = data / "memory.jsonl"
            memory = (ProMemory(store_path=memory_path)
                      if memory_path.exists() else ProMemory())
            recorder_runs = PipelineRecorder(store_dir=data / "runs").runs
        doc = generate_self_assessment(
            recorder_runs=recorder_runs,
            memory=memory,
            audit_path=data / "audit.jsonl",
            metrics=None,
            start=start,
            end=end,
        )
        out_dir = Path("docs/evals")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"self_assessment_{start}_{end}.md"
        out.write_text(doc, encoding="utf-8")
        print(doc)
        print(f"\nwrote {out}")
        return 0

    cases = golden_cases()
    if args.tag:
        cases = [c for c in cases if args.tag in c.tags]
    if args.limit:
        cases = cases[: args.limit]
    if args.list:
        for case in cases:
            print(f"{case.name}  tags={','.join(case.tags)}  {case.notes}")
        return 0

    load_dotenv()  # repo-root .env, if present
    routing = ModelRouting(
        llm_provider=os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "openai"),
        quick_think_llm=os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "gpt-5.4-mini"),
        deep_think_llm=os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "gpt-5.5"),
    )
    from tradingagents.llm_clients.api_key_env import get_api_key_env

    key_env = get_api_key_env(routing.llm_provider)
    if key_env and not os.environ.get(key_env):
        # claude-cli authenticates via its own login locally; the env token
        # is only required headless (Cloud Run). Warn and proceed — a real
        # auth failure surfaces on the first call.
        if routing.llm_provider == "claude-cli":
            print(f"{key_env} not set; relying on the CLI's own login",
                  file=sys.stderr)
        else:
            print(f"{key_env} not set (env or .env); aborting", file=sys.stderr)
            return 2

    from tradingagents.pro.evals.stability import DEFAULT_CASE_NAMES

    if args.stability:
        n_runs = len(DEFAULT_CASE_NAMES) * args.k
    elif args.factor_mine:
        n_runs = args.iterations  # one proposal call per round
    elif args.memorization_audit:
        n_runs = len(DEFAULT_CASE_NAMES) * args.samples * 2  # named + anon arms
    elif args.crowding_study:
        n_runs = args.points  # one pipeline run per cut; baselines are single calls
    else:
        n_runs = len(cases) * args.samples
    price_scale = price_for(routing.llm_provider).input_per_mtok / 3.0
    est = n_runs * EST_COST_PER_CASE_RUN * price_scale
    if args.memorization_audit:
        print(f"memorization audit: {len(DEFAULT_CASE_NAMES)} frozen snapshots "
              f"x {args.samples} samples x 2 arms (named/anonymized) = "
              f"{n_runs} pipeline runs (~${est:.2f} estimated at "
              f"{routing.llm_provider} rates)\n")
    elif args.ablation:
        print(f"ablation: {args.points} historical cut points x 2 arms "
              f"(~${args.points * EST_COST_PER_CASE_RUN * price_scale:.2f} "
              f"estimated at {routing.llm_provider} rates)\n")
    elif args.crowding_study:
        from tradingagents.pro.analytics.crowding import MIN_OVERLAP
        from tradingagents.pro.evals.crowding_study import baseline_prompts

        n_styles = len(baseline_prompts())
        print(f"crowding study: {args.points} historical cut points x "
              f"(1 pipeline run + {n_styles} baseline calls on the same "
              f"evidence pack) (~${est:.2f} estimated at "
              f"{routing.llm_provider} rates; the pipeline run dominates, "
              f"a baseline is one call)\n"
              f"  note: the crowding score needs >= {MIN_OVERLAP} "
              f"DIRECTIONAL (non-HOLD) decisions of ours to report a "
              f"number at all — it stays null on a short study\n")
    elif args.factor_mine:
        print(f"factor mining: {args.iterations} proposal rounds "
              f"(~${est:.2f} estimated at {routing.llm_provider} rates; "
              f"evaluation itself is model-free)\n")
    elif args.stability:
        print(f"stability: {len(DEFAULT_CASE_NAMES)} frozen snapshots x "
              f"k={args.k} = {n_runs} pipeline runs (~${est:.2f} estimated "
              f"at {routing.llm_provider} rates)\n")
    else:
        print(f"running {len(cases)} cases x {args.samples} samples = {n_runs} "
              f"pipeline runs (~${est:.2f} estimated at {routing.llm_provider} rates)\n")

    config = ProConfig(asset=AssetClass.GOLD, max_debate_rounds=1, models=routing)
    # low temperature for eval comparability across runs; note reasoning
    # models may ignore it and no setting makes runs bit-identical (see the
    # base README's reproducibility section) — N-sample stats are the fix
    bundle = bundle_from_config(config, temperature=0.2)
    price = price_for(routing.llm_provider)
    bundle.quick = CostTrackingLLM(bundle.quick, price=price)
    deep_tracker = CostTrackingLLM(bundle.deep, price=price)
    bundle.deep = deep_tracker if bundle.deep is not bundle.quick else bundle.quick

    if args.factor_mine:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        import pandas as pd

        from tradingagents.contracts import Timeframe
        from tradingagents.pro.evals.factor_mining import mine
        from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed

        bars = DeltaExchangeFeed().get_bars("BTCUSD", Timeframe.H4, limit=1000)
        bars_df = pd.DataFrame(
            {"open": [b.open for b in bars], "high": [b.high for b in bars],
             "low": [b.low for b in bars], "close": [b.close for b in bars],
             "volume": [b.volume for b in bars]})
        report = mine(bundle.deep, bars_df, iterations=args.iterations,
                      symbol="BTC-USD")
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": routing.llm_provider,
            "quick": routing.quick_think_llm,
            "deep": routing.deep_think_llm,
            **report,
        }
        out_dir = Path("docs/evals")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = out_dir / f"factors_{stamp}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n")
        c = report["counts"]
        print(f"proposed={c['proposed']} invalid={c['invalid']} "
              f"weak={c['weak']} duplicate={c['duplicate']} "
              f"survivors={c['survivors']} (ic bar {report['ic_bar']:.4f})")
        for s in report["survivors"]:
            print(f"  SURVIVOR {s['name']}: ic_mean={s['ic_mean']:+.4f} "
                  f"ic_ir={s['ic_ir']} folds={s['n_folds']}  {s['expression']}")
        if not report["survivors"]:
            print("  no survivors — documented negative result "
                  "(see AC of P3-03)")
        print("  registration is a separate operator step: "
              "agents.computed_factor.store_survivors(store, survivors)")
        deep_report = bundle.deep.report
        print(f"\ndeep-model calls: {deep_report.calls}, "
              f"est cost ${deep_report.est_cost_usd:.2f}")
        print(f"wrote {out}")
        return 0

    if args.memorization_audit:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        from tradingagents.pro.evals.anonymize import run_memorization_audit

        rows = run_memorization_audit(bundle, config, samples=args.samples,
                                      seed=args.seed, agent_workers=8)
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": routing.llm_provider,
            "quick": routing.quick_think_llm,
            "deep": routing.deep_think_llm,
            "samples": args.samples,
            "seed": args.seed,
            "rows": rows,
        }
        out_dir = Path("docs/evals")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = out_dir / f"memorization_{stamp}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"{'case':<34} {'symbol':<8} {'token':<8} "
              f"{'named':<10} {'anon':<10} {'agree':<6} flag")
        for r in rows:
            named = "/".join(r["named_actions"])
            anon = "/".join(r["anon_actions"])
            print(f"{r['case']:<34} {r['symbol']:<8} {r['token']:<8} "
                  f"{named:<10} {anon:<10} {r['action_agreement']:<6.0%} "
                  f"{'CONTAMINATION?' if r['flagged'] else 'ok'}")
        print(f"\nwrote {out}")
        return 0

    if args.ablation:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        from tradingagents.contracts import AssetClass as AC
        from tradingagents.pro.evals.ablation import run_ablation_series

        abl_config = ProConfig(asset=AC.BITCOIN, max_debate_rounds=1,
                               models=routing)
        # P3-02: point-in-time macro reads for the historical cuts — the
        # default event store's vintages (latest_as_known). Best-effort:
        # an unopenable store degrades to reader-less builds, never aborts
        # the series.
        vintage_reader = None
        try:
            from tradingagents.pro.store import EventStore

            vintage_reader = EventStore()
        except Exception as exc:  # noqa: BLE001 — degrade, don't abort
            print(f"vintage store unavailable ({exc}); ablation builds "
                  "without point-in-time macro replay", file=sys.stderr)
        rows = run_ablation_series(bundle, abl_config, points=args.points,
                                   agent_workers=8,
                                   vintage_reader=vintage_reader)
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": routing.llm_provider,
            "quick": routing.quick_think_llm,
            "deep": routing.deep_think_llm,
            "symbol": "BTC-USD",
            "points": rows,
        }
        out_dir = Path("docs/evals")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = out_dir / f"ablation_{stamp}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n")
        for row in rows:
            for a in row["arms"]:
                print(f"{row['as_of']} {a['arm']:>12}: action={a['action']} "
                      f"conf={a['confidence']} rejected={a['rejected']} "
                      f"pnl={a['pnl']} exit={a['exit_reason']}")
        print(f"\nwrote {out}")
        return 0

    if args.crowding_study:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        from tradingagents.contracts import AssetClass as AC
        from tradingagents.pro.evals.crowding_study import (
            historical_snapshots,
            study,
        )

        crowd_config = ProConfig(asset=AC.BITCOIN, max_debate_rounds=1,
                                 models=routing)
        snapshots = historical_snapshots(points=args.points, symbol="BTC-USD",
                                         asset=AC.BITCOIN)
        report = study(bundle, crowd_config, snapshots, agent_workers=8)
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": routing.llm_provider,
            "quick": routing.quick_think_llm,
            "deep": routing.deep_think_llm,
            **report,
        }
        out_dir = Path("docs/evals")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = out_dir / f"crowding_{stamp}.json"
        out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        crowding = report["crowding"]
        print(f"points={report['n_points']} aligned={crowding['n_aligned']} "
              f"directional={crowding['n_directional']} "
              f"scored={crowding['n_scored']} "
              f"no_consensus={crowding['n_no_consensus']}")
        for pair in report["agreement"]["pairs"]:
            agree = ("n/a" if pair["agreement"] is None
                     else f"{pair['agreement']:.0%}")
            kappa = "n/a" if pair["kappa"] is None else f"{pair['kappa']:+.2f}"
            print(f"  {pair['a']:>20} vs {pair['b']:<20} n={pair['n']:<4} "
                  f"agree={agree:<6} kappa={kappa}")
        if crowding["crowding"] is None:
            print("  crowding: n/a — too few scorable directional decisions "
                  "(honest hole, not zero)")
        else:
            kappa_consensus = crowding["kappa_vs_consensus"]
            kappa_text = ("n/a" if kappa_consensus is None
                          else f"{kappa_consensus:+.2f}")
            print(f"  crowding={crowding['crowding']:.0%} "
                  f"distinctiveness={crowding['distinctiveness']:.0%} "
                  f"kappa_vs_consensus={kappa_text}")
        deep_report = bundle.deep.report
        print(f"\ndeep-model calls: {deep_report.calls}, "
              f"est cost ${deep_report.est_cost_usd:.2f}")
        print(f"wrote {out}")
        return 0

    if args.stability:
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        from tradingagents.pro.evals.stability import run_stability_evals

        results = run_stability_evals(bundle, config, k=args.k,
                                      agent_workers=8)
        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": routing.llm_provider,
            "quick": routing.quick_think_llm,
            "deep": routing.deep_think_llm,
            "k": args.k,
            "results": [r.as_dict() for r in results],
        }
        out_dir = Path("docs/evals")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = out_dir / f"stability_{stamp}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n")
        for r in results:
            print(f"{r.symbol} [{r.case}] k={r.k}: "
                  f"action_flip={r.action_flip_rate:.0%} "
                  f"conf_stddev={r.confidence_stddev:.1f} "
                  f"gate_flip={r.gate_flip_rate:.0%}")
        print(f"\nwrote {out}")
        return 0

    report = run_decision_evals(bundle, config, cases=cases,
                                samples=args.samples, agent_workers=8)
    print(report.summary())
    quick_report = bundle.quick.report
    print(f"\nquick-model calls: {quick_report.calls}, "
          f"est cost ${quick_report.est_cost_usd:.2f}")
    total_calls = quick_report.calls
    if bundle.deep is not bundle.quick:
        print(f"deep-model calls: {bundle.deep.report.calls}, "
              f"est cost ${bundle.deep.report.est_cost_usd:.2f}")
        total_calls += bundle.deep.report.calls
    if total_calls == 0:
        # every stage abstained because the provider never answered — that is
        # a provider/credentials failure, not an eval pass
        print("\nERROR: zero successful model calls (provider outage, bad key, "
              "or insufficient quota); eval results are vacuous", file=sys.stderr)
        return 2
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
