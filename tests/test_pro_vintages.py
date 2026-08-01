"""P3-02 point-in-time vintages: revised macro data never rewrites the past.

AC: NFP value v1 observed at T1, revised to v2 observed at T2 — a snapshot
built as_of between T1 and T2 sees v1, after T2 sees v2, both rows stay in
the store, re-recording is idempotent, and an existing P2-01 database file
upgrades in place when reopened.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from tests.pro_fakes import FakeBarsFeed, FakeTransport
from tradingagents.contracts import AssetClass, MetricReading
from tradingagents.pro.ingestion.builder import SnapshotBuilder
from tradingagents.pro.ingestion.fred_macro import FredMacroFeed
from tradingagents.pro.store import EventStore

T1 = datetime(2026, 7, 4, tzinfo=timezone.utc)   # first print of June NFP
T2 = datetime(2026, 8, 1, tzinfo=timezone.utc)   # revision published
JUNE = datetime(2026, 6, 1, tzinfo=timezone.utc)  # the period both describe
V1, V2 = 210.0, 150.0

BETWEEN = datetime(2026, 7, 10, tzinfo=timezone.utc)
AFTER = datetime(2026, 8, 5, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "pro.db")
    yield s
    s.close()


def record_nfp_revision_pair(store: EventStore) -> None:
    store.record_vintage("NFP_CHANGE", V1, observed_at=T1, as_of=JUNE,
                         source="fred:PAYEMS")
    store.record_vintage("NFP_CHANGE", V2, observed_at=T2, as_of=JUNE,
                         source="fred:PAYEMS")


class TestVintageStore:
    def test_as_known_between_and_after_revision(self, store):
        record_nfp_revision_pair(store)
        assert store.latest_as_known("NFP_CHANGE", BETWEEN)["value"] == V1
        assert store.latest_as_known("NFP_CHANGE", AFTER)["value"] == V2

    def test_nothing_knowable_before_first_print(self, store):
        record_nfp_revision_pair(store)
        assert store.latest_as_known(
            "NFP_CHANGE", datetime(2026, 7, 1, tzinfo=timezone.utc)) is None

    def test_boundary_observed_at_equals_at_is_knowable(self, store):
        record_nfp_revision_pair(store)
        assert store.latest_as_known("NFP_CHANGE", T1)["value"] == V1
        assert store.latest_as_known("NFP_CHANGE", T2)["value"] == V2

    def test_revision_retains_both_series(self, store):
        record_nfp_revision_pair(store)
        rows = store.load_vintages("NFP_CHANGE")
        assert [(r["value"], r["observed_at"]) for r in rows] == [
            (V1, T1), (V2, T2)]

    def test_rerecord_is_idempotent(self, store):
        record_nfp_revision_pair(store)
        record_nfp_revision_pair(store)  # backfill re-run
        # a changed value for the SAME (name, as_of, observed_at) is ignored
        # too — first write wins, history is append-only
        store.record_vintage("NFP_CHANGE", 999.0, observed_at=T1, as_of=JUNE)
        rows = store.load_vintages("NFP_CHANGE")
        assert len(rows) == 2
        assert rows[0]["value"] == V1

    def test_newest_period_wins_over_newest_observation(self, store):
        # July's first print (observed before June's revision) must win a
        # post-T2 read: "newest" is by period first, then revision
        record_nfp_revision_pair(store)
        july = datetime(2026, 7, 1, tzinfo=timezone.utc)
        store.record_vintage("NFP_CHANGE", 180.0,
                             observed_at=datetime(2026, 8, 7,
                                                  tzinfo=timezone.utc),
                             as_of=july)
        row = store.latest_as_known(
            "NFP_CHANGE", datetime(2026, 8, 10, tzinfo=timezone.utc))
        assert row["value"] == 180.0 and row["as_of"] == july
        # while the June-only window still resolves to June's revision
        assert store.latest_as_known("NFP_CHANGE", AFTER)["value"] == V2

    def test_has_vintages_and_accepts_iso_strings(self, store):
        store.record_vintage("CPI_YOY", 3.1, observed_at="2026-07-15",
                             as_of="2026-06-01")
        assert store.has_vintages("CPI_YOY")
        assert not store.has_vintages("NFP_CHANGE")
        row = store.latest_as_known("CPI_YOY", "2026-07-20")
        assert row["value"] == 3.1
        assert row["as_of"] == datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert row["observed_at"] == datetime(2026, 7, 15,
                                              tzinfo=timezone.utc)

    def test_existing_p2_01_db_upgrades_in_place(self, tmp_path):
        # hand-build a pre-P3-02 database: P2-01 tables only, with data
        path = tmp_path / "pro.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT "
            "NOT NULL, symbol TEXT, trigger TEXT, record TEXT NOT NULL);"
            "CREATE TABLE memory (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "symbol TEXT, created_at TEXT NOT NULL, ref_id TEXT, "
            "record TEXT NOT NULL);"
            "CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '');"
        )
        conn.execute("INSERT INTO runs VALUES ('r1','2026-07-01T00:00:00Z',"
                     "'XAUUSD','loop','{}')")
        conn.commit()
        conn.close()

        store = EventStore(path)  # additive migration on open
        try:
            record_nfp_revision_pair(store)
            assert store.latest_as_known("NFP_CHANGE", BETWEEN)["value"] == V1
            assert store.count_runs() == 1  # legacy data untouched
        finally:
            store.close()


# --- FRED/ALFRED feed -> sink ---------------------------------------------

def alfred_payload(value: str, realtime_start: str, date: str = "2026-06-01",
                   extra: list[dict] | None = None) -> dict:
    """ALFRED-shaped /series/observations payload: every observation
    carries realtime_start (the vintage date this value became public)."""
    obs = [{
        "realtime_start": realtime_start,
        "realtime_end": "9999-12-31",
        "date": date,
        "value": value,
    }] + (extra or [])
    return {"observations": obs, "sort_order": "desc", "units": "chg"}


NFP_SERIES = {"NFP_CHANGE": ("PAYEMS", "chg", "thousands")}


class RecordingSink:
    def __init__(self):
        self.rows = []

    def __call__(self, **kw):
        self.rows.append(kw)


class TestFredVintageCapture:
    def test_fetch_writes_observed_at_from_realtime_start(self):
        transport = FakeTransport(
            {"series/observations": alfred_payload("210.0", "2026-07-04")})
        sink = RecordingSink()
        readings = FredMacroFeed(transport=transport, series=NFP_SERIES,
                                 api_key="test",
                                 vintage_sink=sink).get_metrics()

        assert readings[0].value == V1  # the reading itself is unchanged
        assert sink.rows == [{
            "name": "NFP_CHANGE",
            "value": V1,
            "observed_at": T1,
            "as_of": JUNE,
            "source": "fred:PAYEMS",
        }]

    def test_all_usable_observations_reach_sink_placeholders_skipped(self):
        payload = alfred_payload("210.0", "2026-07-04", extra=[
            {"realtime_start": "2026-07-04", "date": "2026-05-01",
             "value": "."},  # FRED placeholder — never sinked
            {"realtime_start": "2026-06-05", "date": "2026-05-01",
             "value": "190.0"},
        ])
        transport = FakeTransport({"series/observations": payload})
        sink = RecordingSink()
        readings = FredMacroFeed(transport=transport, series=NFP_SERIES,
                                 api_key="test",
                                 vintage_sink=sink).get_metrics()

        assert len(readings) == 1 and readings[0].as_of == JUNE
        assert [(r["as_of"].month, r["value"]) for r in sink.rows] == [
            (6, 210.0), (5, 190.0)]

    def test_no_sink_means_current_behavior(self):
        transport = FakeTransport(
            {"series/observations": alfred_payload("210.0", "2026-07-04")})
        readings = FredMacroFeed(transport=transport, series=NFP_SERIES,
                                 api_key="test").get_metrics()
        assert [(r.name, r.value) for r in readings] == [("NFP_CHANGE", V1)]

    def test_sink_failure_never_degrades_the_feed(self):
        def exploding_sink(**kw):
            raise RuntimeError("db locked")

        transport = FakeTransport(
            {"series/observations": alfred_payload("210.0", "2026-07-04")})
        readings = FredMacroFeed(transport=transport, series=NFP_SERIES,
                                 api_key="test",
                                 vintage_sink=exploding_sink).get_metrics()
        assert readings[0].value == V1

    def test_store_record_vintage_is_a_valid_sink(self, store):
        transport = FakeTransport(
            {"series/observations": alfred_payload("210.0", "2026-07-04")})
        FredMacroFeed(transport=transport, series=NFP_SERIES, api_key="test",
                      vintage_sink=store.record_vintage).get_metrics()
        assert store.latest_as_known("NFP_CHANGE", BETWEEN)["value"] == V1


# --- builder read path ------------------------------------------------------

class LiveMacro:
    """A macro feed that always serves the LATEST (revised) values —
    exactly what a naive backtest would leak into the past."""

    name = "live_macro"

    def get_metrics(self):
        return [
            MetricReading(name="NFP_CHANGE", value=V2, unit="thousands",
                          as_of=JUNE, source="fred:PAYEMS"),
            MetricReading(name="GOLD_SILVER_RATIO", value=88.0,
                          as_of=AFTER, source="cross_asset"),
        ]


def make_builder(store: EventStore) -> SnapshotBuilder:
    return SnapshotBuilder(bars_feed=FakeBarsFeed(),
                           macro_feeds=(LiveMacro(),),
                           vintage_reader=store)


class TestSnapshotPitRead:
    def test_snapshot_between_prints_sees_v1_after_sees_v2(self, store):
        record_nfp_revision_pair(store)
        builder = make_builder(store)

        between = builder.build("XAUUSD", AssetClass.GOLD, as_of=BETWEEN)
        after = builder.build("XAUUSD", AssetClass.GOLD, as_of=AFTER)

        by_name = {m.name: m for m in between.macro}
        assert by_name["NFP_CHANGE"].value == V1  # revision did NOT leak back
        assert by_name["NFP_CHANGE"].as_of == JUNE
        assert by_name["NFP_CHANGE"].unit == "thousands"
        assert {m.name: m.value for m in after.macro}["NFP_CHANGE"] == V2

    def test_unvintaged_metric_passes_through(self, store):
        record_nfp_revision_pair(store)
        snapshot = make_builder(store).build("XAUUSD", AssetClass.GOLD,
                                             as_of=BETWEEN)
        by_name = {m.name: m for m in snapshot.macro}
        # cross-asset ratio has no vintage history -> best-effort passthrough
        assert by_name["GOLD_SILVER_RATIO"].value == 88.0

    def test_tracked_but_not_yet_knowable_is_dropped(self, store):
        record_nfp_revision_pair(store)
        snapshot = make_builder(store).build(
            "XAUUSD", AssetClass.GOLD,
            as_of=datetime(2026, 7, 1, tzinfo=timezone.utc))  # before T1
        names = {m.name for m in snapshot.macro}
        assert "NFP_CHANGE" not in names  # it was not knowable then
        assert "GOLD_SILVER_RATIO" in names

    def test_live_build_never_touches_the_reader(self, store):
        record_nfp_revision_pair(store)

        class ExplodingReader:
            def latest_as_known(self, name, at):
                raise AssertionError("live path consulted the vintage reader")

        builder = SnapshotBuilder(bars_feed=FakeBarsFeed(),
                                  macro_feeds=(LiveMacro(),),
                                  vintage_reader=ExplodingReader())
        snapshot = builder.build("XAUUSD", AssetClass.GOLD)  # as_of=None
        assert {m.name: m.value for m in snapshot.macro}["NFP_CHANGE"] == V2

    def test_no_reader_keeps_past_as_of_behavior(self, store):
        builder = SnapshotBuilder(bars_feed=FakeBarsFeed(),
                                  macro_feeds=(LiveMacro(),))
        snapshot = builder.build("XAUUSD", AssetClass.GOLD, as_of=BETWEEN)
        assert {m.name: m.value for m in snapshot.macro}["NFP_CHANGE"] == V2


# --- P3-02 wiring: the PIT-shaped callers actually receive the reader/sink --

class TestAblationVintageReader:
    def test_series_threads_the_reader_into_pit_builds(self, store, monkeypatch):
        """run_ablation_series builds every cut with an explicit as_of AND
        the caller's vintage_reader — the audited gap was the reader never
        reaching the one PIT-shaped SnapshotBuilder in the eval suite."""
        from tests.pro_fakes import make_bars
        from tradingagents.contracts import ProConfig, Timeframe
        from tradingagents.pro.evals import ablation as ablation_module
        from tradingagents.pro.ingestion import (
            builder as builder_module,
            delta_exchange as delta_module,
        )

        bars = make_bars(300, timeframe=Timeframe.H4)
        captured: dict = {}

        class FakeFeed:
            def get_bars(self, vendor, tf, limit=500):
                return bars

        class CapturingBuilder:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def build(self, symbol, asset, **kwargs):
                captured["as_of"] = kwargs.get("as_of")
                return "snapshot"

        monkeypatch.setattr(delta_module, "DeltaExchangeFeed", FakeFeed)
        monkeypatch.setattr(builder_module, "SnapshotBuilder",
                            CapturingBuilder)
        monkeypatch.setattr(ablation_module, "run_ablation",
                            lambda *a, **k: [])

        config = ProConfig(asset=AssetClass.BITCOIN)
        rows = ablation_module.run_ablation_series(
            llm=None, config=config, points=1, vintage_reader=store)
        assert captured["vintage_reader"] is store
        assert captured["as_of"] is not None  # every cut is a PIT build
        assert rows and "error" not in rows[0]


class TestOperatorTriggerVintageSink:
    def _trigger(self, store):
        from tradingagents.pro.main import PipelineTrigger

        class _Obj:
            pass

        service = _Obj()
        service.dashboard = _Obj()
        service.dashboard.recorder = _Obj()
        service.dashboard.recorder.store = store
        return PipelineTrigger(service)

    def test_trigger_builders_receive_a_recording_sink(self, store, monkeypatch):
        """Operator-triggered builds mirror the loop wiring: their FRED
        feeds get a sink that records into the service's event store."""
        import tradingagents.pro.main as main_module
        from tradingagents.contracts import Timeframe

        captured: dict = {}

        def fake_crypto_builder(symbol, vintage_sink=None):
            captured["sink"] = vintage_sink

            class B:
                def build(self, *a, **k):
                    return "snapshot"
            return B()

        monkeypatch.setattr(main_module, "_crypto_snapshot_builder",
                            fake_crypto_builder)
        trigger = self._trigger(store)
        assert trigger._build_snapshot("BTC-USD", AssetClass.BITCOIN,
                                       Timeframe.H1) == "snapshot"
        sink = captured["sink"]
        assert sink is not None
        sink(name="NFP_CHANGE", value=V1, observed_at=T1, as_of=JUNE,
             source="fred:PAYEMS")
        assert store.latest_as_known("NFP_CHANGE", BETWEEN)["value"] == V1
        # best-effort by contract: a store failure never raises out of
        # the sink (a vintage write must not fail a snapshot build)
        store.close()
        sink(name="CPI_YOY", value=3.1, observed_at=T1, as_of=JUNE)

    def test_trigger_without_a_store_passes_no_sink(self):
        trigger = self._trigger(None)
        assert trigger._vintage_sink() is None
