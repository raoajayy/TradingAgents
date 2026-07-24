"""Monte Carlo trade-bootstrap (F6 coverage): determinism, ordered percentiles,
prob_loss, and error cases."""

import pytest

from tradingagents.pro.backtest.montecarlo import (
    MonteCarloSummary,
    bootstrap_paths,
    monte_carlo_summary,
)

_PNLS = [500.0, -300.0, 800.0, -200.0, 400.0, -100.0]


def test_bootstrap_is_seed_deterministic():
    a = bootstrap_paths(_PNLS, 100_000.0, n_paths=50, seed=7)
    b = bootstrap_paths(_PNLS, 100_000.0, n_paths=50, seed=7)
    assert a == b
    # each path starts at initial equity and has len(pnls)+1 points
    assert all(p[0] == 100_000.0 and len(p) == len(_PNLS) + 1 for p in a)
    # a different seed changes the draw
    assert bootstrap_paths(_PNLS, 100_000.0, n_paths=50, seed=8) != a


def test_summary_percentiles_are_ordered_and_deterministic():
    s = monte_carlo_summary(_PNLS, 100_000.0, n_paths=500, seed=7)
    assert isinstance(s, MonteCarloSummary) and s.n_paths == 500
    assert s.final_equity_p5 <= s.final_equity_p50 <= s.final_equity_p95
    assert s.max_drawdown_p50 <= s.max_drawdown_p95
    assert 0.0 <= s.prob_loss <= 1.0
    assert monte_carlo_summary(_PNLS, 100_000.0, n_paths=500, seed=7) == s


def test_all_losing_pnls_give_high_prob_loss():
    s = monte_carlo_summary([-100.0, -200.0, -50.0], 10_000.0, n_paths=200, seed=1)
    assert s.prob_loss == 1.0  # every reshuffle of only-losses ends below start


def test_errors_on_degenerate_input():
    with pytest.raises(ValueError, match="at least 2 trades"):
        bootstrap_paths([100.0], 100_000.0)
    with pytest.raises(ValueError, match="initial_equity"):
        bootstrap_paths(_PNLS, 0.0)
