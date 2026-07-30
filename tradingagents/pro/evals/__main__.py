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
                        help="historical cut points for --ablation (default 5)")
    parser.add_argument("--memorization-audit", action="store_true",
                        help="P2-03: re-run the frozen stability snapshots "
                             "named vs anonymized (tickers/dates masked) and "
                             "compare action agreement per symbol")
    parser.add_argument("--seed", type=int, default=0,
                        help="label-mapping seed for --memorization-audit "
                             "(default 0)")
    args = parser.parse_args()

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
    elif args.memorization_audit:
        n_runs = len(DEFAULT_CASE_NAMES) * args.samples * 2  # named + anon arms
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
        rows = run_ablation_series(bundle, abl_config, points=args.points,
                                   agent_workers=8)
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
