"""P3-01 real TCA: live-adapter fills feed the same P1-04 capture as
paper fills. Paper captures synchronously inside run_once; live entries
may fill asynchronously via OMS polling — the service stores the
decision-time arrival mid at submission and mirrors the capture when the
venue reports the fill. All stubbed; no pipeline run, no network."""

from types import SimpleNamespace

import pytest

from tests.test_pro_e2e_service import make_service
from tests.test_pro_memory_facade import make_recommendation
from tests.test_pro_pipeline_graph import pipeline_snapshot
from tradingagents.contracts import TradeAction
from tradingagents.pro.execution import ids
from tradingagents.pro.execution.interface import OrderSpec, OrderState
from tradingagents.pro.execution.orders import ManagedOrder


def entry_coid(rec) -> str:
    return ids.client_order_id(rec.id, ids.decision_hash(rec), ids.ENTRY)


def filled_entry(rec, fill_price: float, quantity: float = 1.0) -> ManagedOrder:
    order = ManagedOrder(
        spec=OrderSpec(client_order_id=entry_coid(rec), symbol=rec.symbol,
                       venue_symbol="", side=rec.action.value,
                       quantity=quantity, reference_price=rec.entry_price),
        leg=ids.ENTRY,
    )
    order.state = OrderState.FILLED
    order.filled_quantity = quantity
    order.avg_fill_price = fill_price
    return order


class TestLiveFillTca:
    def test_async_live_fill_captures_tca_like_paper(self):
        service = make_service([130.0])
        snapshot = pipeline_snapshot()
        arrival = snapshot.bars[-1].close  # no quote in the fake snapshot
        rec = make_recommendation(action=TradeAction.BUY)

        # submission time: the live route returned "submitted"
        service._register_pending_live_tca(rec, snapshot)
        assert entry_coid(rec) in service._pending_live_tca
        assert service._pending_live_tca[entry_coid(rec)]["arrival"] == arrival

        # fill time: the venue reports FILLED through the live OMS book
        fill_price = arrival * 1.001  # paid 10bps worse than arrival
        service.router.live_oms = SimpleNamespace(
            orders={entry_coid(rec): filled_entry(rec, fill_price)})
        service._absorb_live_entry_fills()

        assert not service._pending_live_tca
        position = service.open_positions[rec.symbol]
        assert position.fill_price == fill_price
        tca = position.tca
        assert tca["arrival_mid"] == arrival
        # BUY filled above arrival: positive slippage (paid worse), ~10bps
        assert tca["entry_slippage_bps"] == pytest.approx(10.0, abs=0.5)
        assert "markouts_bps" in tca  # same shape as the paper capture

    def test_sell_side_slippage_sign_mirrors_paper(self):
        service = make_service([130.0])
        snapshot = pipeline_snapshot()
        arrival = snapshot.bars[-1].close
        rec = make_recommendation(action=TradeAction.SELL)
        service._register_pending_live_tca(rec, snapshot)
        # SELL filled ABOVE arrival = favorable = negative slippage
        service.router.live_oms = SimpleNamespace(
            orders={entry_coid(rec): filled_entry(rec, arrival * 1.001)})
        service._absorb_live_entry_fills()
        assert service.open_positions[rec.symbol].tca[
            "entry_slippage_bps"] == pytest.approx(-10.0, abs=0.5)

    def test_rejected_live_entry_clears_pending_without_position(self):
        service = make_service([130.0])
        rec = make_recommendation(action=TradeAction.BUY)
        service._register_pending_live_tca(rec, pipeline_snapshot())
        order = filled_entry(rec, 0.0, quantity=0.0)
        order.state = OrderState.REJECTED
        order.filled_quantity = 0.0
        service.router.live_oms = SimpleNamespace(
            orders={entry_coid(rec): order})
        service._absorb_live_entry_fills()
        assert not service._pending_live_tca
        assert rec.symbol not in service.open_positions

    def test_unfilled_entry_stays_pending(self):
        service = make_service([130.0])
        rec = make_recommendation(action=TradeAction.BUY)
        service._register_pending_live_tca(rec, pipeline_snapshot())
        order = filled_entry(rec, 0.0, quantity=0.0)
        order.state = OrderState.ACKED
        service.router.live_oms = SimpleNamespace(
            orders={entry_coid(rec): order})
        service._absorb_live_entry_fills()
        assert entry_coid(rec) in service._pending_live_tca  # still waiting

    def test_no_live_oms_is_a_noop(self):
        service = make_service([130.0])
        rec = make_recommendation(action=TradeAction.BUY)
        service._register_pending_live_tca(rec, pipeline_snapshot())
        service._absorb_live_entry_fills()  # router.live_oms is None
        assert entry_coid(rec) in service._pending_live_tca
