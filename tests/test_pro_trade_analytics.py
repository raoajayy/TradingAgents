"""Trade-analytics coverage (F6): enrich_trades field correctness,
regime_breakdown grouping, and agent_attribution vote scoring."""

from datetime import timedelta

from tests.pro_fakes import BASE_TS
from tradingagents.pro.backtest.agent_attribution import agent_attribution
from tradingagents.pro.backtest.broker import ClosedTrade
from tradingagents.pro.backtest.regime_breakdown import regime_breakdown
from tradingagents.pro.backtest.trade_log import EnrichedTrade, enrich_trades


def _closed(n: int, pnl: float, side: str = "BUY") -> ClosedTrade:
    return ClosedTrade(
        symbol="BTC", side=side, quantity=1.0, entry_price=100.0,
        exit_price=100.0 + (pnl if side == "BUY" else -pnl),
        opened_at=BASE_TS + timedelta(days=n),
        closed_at=BASE_TS + timedelta(days=n, hours=12),
        pnl=pnl, reason="take_profit" if pnl >= 0 else "stop",
        recommendation_id=f"r{n}")


def test_enrich_trades_derives_fields_from_closed_trades():
    trades = [_closed(0, 5.0), _closed(2, -3.0)]
    rows = enrich_trades(trades, {}, 100_000.0)
    assert [r.outcome for r in rows] == ["Win", "Loss"]
    assert rows[0].net_pnl == 5.0 and rows[1].net_pnl == -3.0
    assert rows[0].holding_hours == 12.0
    assert rows[0].pct_return == round(5.0 / 100.0, 6)  # net / entry notional
    # no decision state supplied → no votes, regime unknown
    assert rows[0].vote_breakdown == [] and rows[0].market_regime is None


def test_regime_breakdown_groups_and_scores():
    rows = enrich_trades([_closed(0, 5.0), _closed(2, -3.0), _closed(4, 8.0)],
                         {}, 100_000.0)
    stats = regime_breakdown(rows)
    assert len(stats) == 1 and stats[0].regime == "unknown"
    s = stats[0]
    assert s.n_trades == 3
    assert s.total_net_pnl == 10.0
    assert s.win_rate == round(2 / 3, 4)
    assert s.best_trade == 8.0 and s.worst_trade == -3.0


def _enriched(direction: str, outcome: str, net_pnl: float, regime: str,
              votes: list[dict]) -> EnrichedTrade:
    return EnrichedTrade(
        trade_id="t", recommendation_id="r", symbol="BTC", direction=direction,
        opened_at="2024-01-01T00:00:00", closed_at="2024-01-02T00:00:00",
        holding_hours=24.0, entry_price=100.0, exit_price=105.0, stop_loss=95.0,
        take_profits=[], position_size=1.0, exit_reason="take_profit",
        gross_pnl=net_pnl, net_pnl=net_pnl, commission=0.0,
        pct_return=net_pnl / 100.0, risk_reward=2.0, confidence=70,
        market_regime=regime, strategy=regime, portfolio_pct_equity=None,
        outcome=outcome, chief_quant="pass", risk_engine="pass",
        vote_breakdown=votes)


def test_agent_attribution_scores_aligned_vs_opposed_votes():
    # "bull" votes BUY on two winning longs → perfect hit rate, positive pnl;
    # "bear" votes SELL against them → opposed, negative attribution
    votes = [{"agent_id": "bull", "vote": "BUY", "confidence": 80},
             {"agent_id": "bear", "vote": "SELL", "confidence": 60}]
    trades = [_enriched("BUY", "Win", 100.0, "trending_up", votes),
              _enriched("BUY", "Win", 50.0, "trending_up", votes)]
    scores = {s.agent_id: s for s in agent_attribution(trades)}
    assert scores["bull"].hit_rate == 1.0
    assert scores["bull"].attributed_pnl == 150.0
    assert scores["bear"].hit_rate == 0.0
    assert scores["bear"].attributed_pnl == -150.0


def test_agent_attribution_empty_without_votes():
    rows = enrich_trades([_closed(0, 5.0), _closed(2, -3.0)], {}, 100_000.0)
    assert agent_attribution(rows) == []
