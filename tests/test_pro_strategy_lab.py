"""Offline tests for the Strategy Lab harness (scripts/pro_strategy_lab.py).

All deterministic + synthetic (no vendor, no cache): they prove the plumbing —
window planning, walk-forward + guard wiring, cache round-trip — and enforce the
a-priori-range policy (every lab-grid value must be inside the strategy's
DECLARED param domain).
"""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.pro.backtest.registry import strategy_param_space

# import the script module by path (scripts/ is not a package)
_SPEC = importlib.util.spec_from_file_location(
    "pro_strategy_lab",
    Path(__file__).resolve().parent.parent / "scripts" / "pro_strategy_lab.py")
lab = importlib.util.module_from_spec(_SPEC)
sys.modules["pro_strategy_lab"] = lab  # so the module's dataclasses resolve
_SPEC.loader.exec_module(lab)


def _synthetic_bars(n: int, seed: int = 11) -> list[OHLCVBar]:
    random.seed(seed)
    bars, price = [], 1000.0
    for i in range(n):
        drift = 5.0 if (i // 80) % 2 == 0 else -3.5
        price = max(50.0, price + drift + random.uniform(-7, 7))
        o = price
        c = price + random.uniform(-4, 4)
        h = max(o, c) + abs(random.uniform(0, 8))
        low = max(0.1, min(o, c) - abs(random.uniform(0, 8)))
        bars.append(OHLCVBar(timeframe=Timeframe.H1, start=BASE_TS + timedelta(hours=i),
                             open=o, high=h, low=low, close=c, volume=1000.0))
    return bars


class TestGridsRespectDeclaredDomains:
    def test_every_lab_grid_value_is_in_the_registered_domain(self):
        """The a-priori-range policy: the Lab may only sweep values the strategy
        actually declares. Guards against a grid drifting outside the ParamSpace."""
        for sid, grid in lab.LAB_GRIDS.items():
            registered = strategy_param_space(sid)
            for param in grid:
                assert param.name in registered, f"{sid}: {param.name} not declared"
                real = registered._by_name[param.name]
                for choice in param.choices:
                    assert real.contains(choice), (
                        f"{sid}.{param.name}={choice!r} outside declared domain")

    def test_all_strategies_have_a_grid(self):
        assert len(lab.LAB_GRIDS) == 11  # + momentum_v2 + htf_momentum_v2
        assert "momentum_v2" in lab.LAB_GRIDS and "htf_momentum_v2" in lab.LAB_GRIDS


class TestWindowPlan:
    def test_too_few_bars_returns_none(self):
        assert lab.window_plan(150) is None

    def test_enough_bars_yields_at_least_two_windows(self):
        plan = lab.window_plan(900)
        assert plan is not None
        train, test, step, embargo = plan
        assert train >= 200 and test >= 120 and embargo == 5
        wins = lab.walk_forward_opt_windows(900, train, test, step, embargo)
        assert len(wins) >= 2


class TestCacheRoundTrip:
    def test_write_then_load_is_lossless(self, tmp_path, monkeypatch):
        bars = _synthetic_bars(120)
        # point the loader at a temp cache dir
        monkeypatch.setattr(lab, "CACHE_DIR", tmp_path)
        # write via the fetch script's format (mirror on the fly)
        import json
        p = tmp_path / "BTC-USD_1h.jsonl"
        p.write_text("\n".join(json.dumps({
            "t": b.start.isoformat(), "o": b.open, "h": b.high,
            "l": b.low, "c": b.close, "v": b.volume}) for b in bars) + "\n")
        loaded = lab.load_cached_bars("BTC-USD", Timeframe.H1)
        assert len(loaded) == len(bars)
        assert loaded[0].close == pytest.approx(bars[0].close)
        assert loaded[-1].start == bars[-1].start


class TestEvaluateCell:
    def test_insufficient_data_is_not_scored(self):
        r = lab.evaluate_cell("trend_following_v1", "BTC-USD", Timeframe.H1,
                              _synthetic_bars(150), objective="sharpe", max_workers=1)
        assert r.status == "insufficient-data"
        assert r.oos_sharpe is None and not r.passes_guard

    def test_scored_cell_has_walk_forward_and_guards(self):
        r = lab.evaluate_cell("trend_following_v1", "BTC-USD", Timeframe.H1,
                              _synthetic_bars(700), objective="sharpe", max_workers=1)
        assert r.status == "ok"
        assert r.windows >= 2
        assert r.n_trials == 12                     # 2×2×3 grid (donchian×stop×trail_mode)
        assert r.oos_sharpe is not None
        assert r.deflated_sharpe is not None and r.pbo is not None
        assert r.most_common_params                 # walk-forward chose params
        assert isinstance(r.passes_guard, bool)
