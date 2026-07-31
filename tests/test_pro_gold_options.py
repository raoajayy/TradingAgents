"""P3-09 gold options/IV context: GVZ rank/percentile, IV-RV spread proxy,
the deterministic gold_vol_context evidence agent, and intel exposure."""

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tests.test_pro_agents_base import make_snapshot
from tradingagents.contracts import (
    AssetClass,
    Direction,
    MetricReading,
    utc_now,
)
from tradingagents.pro.agents.gold_vol_context import (
    GoldVolContextAgent,
    attach_gold_vol_context,
)
from tradingagents.pro.dashboard.intel import (
    GOLD_VOL_CONTEXT_KEYS,
    METRIC_INFO,
    IntelService,
)
from tradingagents.pro.ingestion.gold_feeds import YFinanceDailyBarsFeed
from tradingagents.pro.ingestion.gold_options import (
    GoldVolContextFeed,
    iv_percentile,
    iv_rank,
    parkinson_vol_annualized,
)

BASE = datetime(2026, 1, 5, tzinfo=timezone.utc)


def frame_from_bars(bars: list[tuple[float, float, float]]) -> pd.DataFrame:
    """(high, low, close) triples -> the frame shape load_ohlcv returns."""
    return pd.DataFrame([
        {"Date": (BASE + timedelta(days=i)).date().isoformat(),
         "Open": c, "High": h, "Low": lo, "Close": c, "Volume": 1.0}
        for i, (h, lo, c) in enumerate(bars)
    ])


def frame_from_closes(closes: list[float]) -> pd.DataFrame:
    return frame_from_bars([(c + 1.0, c - 1.0, c) for c in closes])


def make_feed(frames: dict) -> YFinanceDailyBarsFeed:
    return YFinanceDailyBarsFeed(loader=lambda symbol, curr: frames[symbol])


# --- rank / percentile math ---------------------------------------------------

def test_iv_rank_and_percentile_hand_computed():
    closes = [10.0, 20.0, 30.0, 40.0, 25.0]
    # rank: (25 - 10) / (40 - 10) = 50%
    assert iv_rank(closes, window=5, min_obs=5) == pytest.approx(50.0)
    # percentile: 3 of 5 closes (10, 20, 25) are <= 25 -> 60%
    assert iv_percentile(closes, window=5, min_obs=5) == pytest.approx(60.0)


def test_iv_rank_extremes():
    rising = [float(v) for v in range(1, 61)]
    assert iv_rank(rising, window=60, min_obs=60) == pytest.approx(100.0)
    assert iv_percentile(rising, window=60, min_obs=60) == pytest.approx(100.0)
    falling = list(reversed(rising))
    assert iv_rank(falling, window=60, min_obs=60) == pytest.approx(0.0)
    # only the last value itself is <= the minimum -> 1/60
    assert iv_percentile(falling, window=60, min_obs=60) == pytest.approx(100.0 / 60)


def test_iv_rank_uses_only_trailing_window():
    # the 100.0 spike falls outside the 5-value window and must not count
    closes = [100.0, 10.0, 20.0, 30.0, 40.0, 25.0]
    assert iv_rank(closes, window=5, min_obs=5) == pytest.approx(50.0)
    assert iv_percentile(closes, window=5, min_obs=5) == pytest.approx(60.0)


def test_short_history_and_flat_window_return_none():
    assert iv_rank([20.0] * 10, window=252, min_obs=60) is None
    assert iv_percentile([20.0] * 10, window=252, min_obs=60) is None
    # flat window: rank is 0/0 -> undefined, never fabricated
    assert iv_rank([20.0] * 60, window=60, min_obs=60) is None
    # percentile IS defined on a flat window (everything <= last)
    assert iv_percentile([20.0] * 60, window=60, min_obs=60) == pytest.approx(100.0)


# --- Parkinson realized vol / spread -------------------------------------------

def _bars(frame: pd.DataFrame):
    feed = YFinanceDailyBarsFeed(loader=lambda s, c: frame)
    from tradingagents.contracts import Timeframe

    return feed.get_bars("GC=F", Timeframe.D1, limit=len(frame))


def test_parkinson_vol_hand_computed():
    # constant ln(high/low) = 0.02 each bar:
    # var = 0.02^2 / (4 ln 2); ann. % = sqrt(var * 252) * 100
    ratio = math.exp(0.02)
    frame = frame_from_bars([(2000.0 * ratio, 2000.0, 2000.0)] * 3)
    expected = math.sqrt((0.02 ** 2) / (4 * math.log(2)) * 252) * 100.0
    assert parkinson_vol_annualized(_bars(frame), window=3) == pytest.approx(expected)


def test_parkinson_short_history_returns_none():
    frame = frame_from_bars([(101.0, 100.0, 100.5)] * 2)
    assert parkinson_vol_annualized(_bars(frame), window=3) is None


def test_feed_metrics_hand_computed():
    ratio = math.exp(0.02)
    frames = {
        "^GVZ": frame_from_closes([10.0, 20.0, 30.0, 40.0, 25.0]),
        "GC=F": frame_from_bars([(2000.0 * ratio, 2000.0, 2000.0)] * 3),
    }
    feed = GoldVolContextFeed(make_feed(frames), window=5, rv_window=3, min_obs=5)
    readings = {r.name: r for r in feed.get_metrics()}
    assert readings["GOLD_IV_RANK"].value == pytest.approx(50.0)
    assert readings["GOLD_IV_PERCENTILE"].value == pytest.approx(60.0)
    rv = math.sqrt((0.02 ** 2) / (4 * math.log(2)) * 252) * 100.0
    assert readings["GOLD_IV_RV_SPREAD"].value == pytest.approx(25.0 - rv)
    assert readings["GOLD_IV_RV_SPREAD"].unit == "vol_points"
    assert all(r.source == "gold_vol_context" for r in readings.values())
    assert readings["GOLD_IV_RANK"].as_of.tzinfo is not None


def test_feed_degrades_per_submetric():
    # short GVZ history: rank/percentile omitted, spread still serves
    ratio = math.exp(0.02)
    frames = {
        "^GVZ": frame_from_closes([20.0, 22.0, 24.0]),
        "GC=F": frame_from_bars([(2000.0 * ratio, 2000.0, 2000.0)] * 3),
    }
    feed = GoldVolContextFeed(make_feed(frames), window=252, rv_window=3, min_obs=60)
    names = {r.name for r in feed.get_metrics()}
    assert names == {"GOLD_IV_RV_SPREAD"}

    # failing gold-futures fetch: spread omitted alone, rank/percentile serve
    def loader(symbol, curr):
        if symbol == "^GVZ":
            return frame_from_closes([10.0, 20.0, 30.0, 40.0, 25.0])
        raise RuntimeError("GC=F unavailable")

    feed = GoldVolContextFeed(YFinanceDailyBarsFeed(loader=loader),
                              window=5, rv_window=3, min_obs=5)
    names = {r.name for r in feed.get_metrics()}
    assert names == {"GOLD_IV_RANK", "GOLD_IV_PERCENTILE"}


def test_feed_raises_without_gvz_bars():
    from tradingagents.dataflows.errors import NoMarketDataError

    feed = GoldVolContextFeed(
        YFinanceDailyBarsFeed(loader=lambda s, c: pd.DataFrame()))
    with pytest.raises(NoMarketDataError):
        feed.get_metrics()


# --- deterministic evidence agent ----------------------------------------------

def vol_reading(name, value):
    return MetricReading(name=name, value=value, unit="x",
                         as_of=utc_now(), source="gold_vol_context")


def vol_snapshot(rank=85.0, **overrides):
    macro = [vol_reading("GOLD_VOL_INDEX", 24.3),
             vol_reading("GOLD_IV_PERCENTILE", 92.0),
             vol_reading("GOLD_IV_RV_SPREAD", 3.2)]
    if rank is not None:
        macro.append(vol_reading("GOLD_IV_RANK", rank))
    return make_snapshot(macro=macro, **overrides)


def test_agent_renders_evidence_with_refs():
    evidence = GoldVolContextAgent().analyze(vol_snapshot())
    assert evidence is not None
    assert evidence.agent_id == "gold_vol_context"
    ref_names = {r.name for r in evidence.data_refs}
    assert {"GOLD_VOL_INDEX", "GOLD_IV_RANK", "GOLD_IV_PERCENTILE",
            "GOLD_IV_RV_SPREAD"} <= ref_names
    # computed numbers, not prose; refs resolve to the declared source
    assert all(isinstance(r.value, float) for r in evidence.data_refs)
    assert all(r.source == "gold_vol_context" for r in evidence.data_refs)
    assert evidence.sources[0].id == "gold_vol_context"
    # vol richness is a pricing statement, never a price-direction call
    assert evidence.direction is Direction.NEUTRAL
    # deterministic rule: rank > 80 -> options expensive vs history
    assert "expensive" in evidence.claim
    assert "proxy" in evidence.claim  # spread is quoted as a proxy
    assert evidence.confidence == 60


def test_agent_interpretation_rule_bands():
    cheap = GoldVolContextAgent().analyze(vol_snapshot(rank=12.0))
    assert "cheap" in cheap.claim and cheap.confidence == 60
    mid = GoldVolContextAgent().analyze(vol_snapshot(rank=50.0))
    assert "unremarkable" in mid.claim and mid.confidence == 45
    no_rank = GoldVolContextAgent().analyze(vol_snapshot(rank=None))
    assert no_rank is not None
    assert no_rank.confidence == 35
    assert "expensive" not in no_rank.claim and "cheap" not in no_rank.claim


def test_agent_reads_extra_metrics_too():
    snap = make_snapshot(macro=[])
    extras = {"GOLD_VOL_INDEX": vol_reading("GOLD_VOL_INDEX", 20.0),
              "GOLD_IV_RANK": vol_reading("GOLD_IV_RANK", 90.0)}
    evidence = GoldVolContextAgent().analyze(snap, extra_metrics=extras)
    assert evidence is not None and "expensive" in evidence.claim


def test_agent_abstains_without_gvz_level_or_off_gold():
    assert GoldVolContextAgent().analyze(make_snapshot(macro=[])) is None
    btc = vol_snapshot(asset=AssetClass.BITCOIN, symbol="BTC-USD")
    assert GoldVolContextAgent().analyze(btc) is None


def test_attach_is_gold_roster_only():
    roster = ["existing"]
    btc = make_snapshot(asset=AssetClass.BITCOIN, symbol="BTC-USD")
    assert attach_gold_vol_context(roster, btc) is roster  # same object
    gold = make_snapshot()
    attached = attach_gold_vol_context(roster, gold)
    assert attached[0] == "existing"
    assert isinstance(attached[-1], GoldVolContextAgent)


# --- intel exposure -------------------------------------------------------------

def test_metric_info_has_p3_09_keys_and_alert_vocabulary():
    for key in ("GOLD_IV_RANK", "GOLD_IV_PERCENTILE", "GOLD_IV_RV_SPREAD"):
        assert key in METRIC_INFO
        assert METRIC_INFO[key]["label"] and METRIC_INFO[key]["note"]
    # the spread note must disclose the proxy honestly
    assert "PROXY" in METRIC_INFO["GOLD_IV_RV_SPREAD"]["note"]


def test_intel_snapshot_carries_gold_vol_context_block():
    service = IntelService(feeds={
        "gold_vol": lambda: [vol_reading("GOLD_VOL_INDEX", 24.3)],
        "gold_vol_context": lambda: [vol_reading("GOLD_IV_RANK", 85.0),
                                     vol_reading("GOLD_IV_RV_SPREAD", 3.2)],
    }, news_fns={})
    view = service.snapshot()
    block = view["gold_vol_context"]
    assert set(block) == {"GOLD_VOL_INDEX", "GOLD_IV_RANK", "GOLD_IV_RV_SPREAD"}
    assert block["GOLD_IV_RANK"]["value"] == 85.0
    assert block["GOLD_IV_RANK"]["label"] == "Gold IV rank (1y)"
    # P2-07 condition alerts target metric_keys automatically
    for key in ("GOLD_IV_RANK", "GOLD_IV_PERCENTILE", "GOLD_IV_RV_SPREAD"):
        assert key in view["metric_keys"]
    assert set(GOLD_VOL_CONTEXT_KEYS) <= set(view["metric_keys"])


def test_intel_block_is_null_without_gold_vol_metrics():
    service = IntelService(feeds={
        "derivatives": lambda: [vol_reading("FUNDING_RATE", 0.0001)],
    }, news_fns={})
    assert service.snapshot()["gold_vol_context"] is None
