"""Production entrypoint: paper-trading loop + dashboard in one process.

    python -m tradingagents.pro.main

Runs the full PaperTradingService loop (real models from env, all safety
rails: kill switch, circuit breaker, hash-chained audit, reconciliation)
in a worker thread while uvicorn serves the dashboard. Without an LLM
API key for the configured provider the loop is skipped and the process
serves the dashboard in monitor mode — stated in the logs, not silent.

Env:
    TRADINGAGENTS_LLM_PROVIDER / _QUICK_THINK_LLM / _DEEP_THINK_LLM
    PRO_LOOP_INTERVAL_SECONDS   (default 3600 — one decision per hour)
    PRO_LOOP_DISABLED=1         skip the automatic hourly loop only — the
                                service still builds and on-demand runs
                                (POST /api/pipeline/run) keep working as
                                long as an LLM key is present
    PRO_EVENT_TRIGGERS=1        enable P2-06 event-driven runs (calendar
                                release T+5min / vol spike / price gap)
                                beside the hourly rotation; off by default
    PRO_MAX_PORTFOLIO_VAR_PCT / PRO_MAX_CORRELATED_GROSS_PCT
                                P2-05 portfolio caps (float, percent of
                                equity); unset = disabled
    PRO_MAX_VOL_INTERVAL_WIDTH_PCT
                                P3-04 conformal vol-uncertainty gate: cap on
                                the adaptive-conformal interval width around
                                the HAR vol forecast, percent of price
                                (float); unset = gate disabled
    PRO_VOL_INTERVAL_SIZE_SCALE=1
                                P3-04: a width breach scales the position by
                                cap/width (floor 0.25) instead of blocking
    PRO_MAX_RUNS                recorder retention / boot-RAM knob
                                (int, default 500)
    PRO_TWAP_SLICES / PRO_TWAP_WINDOW_MIN
                                P3-10 TWAP entry slicing (int slices /
                                float minutes); unset = single orders
    PRO_RERUN_UNCHANGED_BARS=1  loop re-runs a symbol even when its driving
                                bar is unchanged (restores the pre-skip
                                behavior; see service._skip_unchanged_bar)
    TRADINGAGENTS_PRO_DATA      audit/prefs dir (volume in Docker)
    PRO_DASHBOARD_TOKEN         dashboard auth
    PORT                        uvicorn bind port (default 8600; Cloud Run
                                injects its own value)
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from tradingagents.contracts import utc_now

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 3600.0


class _MappedBars:
    """Presents a feed under dashboard symbols (XAUUSD → XAUTUSD, ...)."""

    def __init__(self, feed, mapping: dict[str, str]):
        self._feed = feed
        self._mapping = mapping
        self.name = getattr(feed, "name", "mapped")

    def get_bars(self, symbol, timeframe, *, limit=250, end=None):
        return self._feed.get_bars(self._mapping.get(symbol, symbol),
                                   timeframe, limit=limit, end=end)


class _DeltaPerpMetrics:
    """MetricsFeed adapter: Delta funding/OI/mark for a crypto roster."""

    name = "delta_exchange"

    def __init__(self, feed, vendor_symbol: str = "BTCUSD"):
        self._feed = feed
        self._vendor = vendor_symbol

    def get_metrics(self):
        return self._feed.get_metrics(self._vendor)


# crypto universe wiring: dashboard symbol -> (Delta perp, CoinMetrics asset)
CRYPTO_WIRING: dict[str, tuple[str, str]] = {
    "BTC-USD": ("BTCUSD", "btc"),
    "ETH-USD": ("ETHUSD", "eth"),
    "SOL-USD": ("SOLUSD", "sol"),
}

# FX majors wiring (P2-10): dashboard symbol -> (OANDA instrument, yfinance
# ticker). One AssetClass.FX spans the pairs — symbol picks the instrument,
# exactly how CRYPTO_WIRING parameterizes the shared crypto build.
FX_WIRING: dict[str, tuple[str, str]] = {
    "EURUSD": ("EUR_USD", "EURUSD=X"),
    "USDJPY": ("USD_JPY", "USDJPY=X"),
}


def _crypto_snapshot_builder(symbol: str, vintage_sink=None):
    """One SnapshotBuilder per crypto symbol: Delta bars/derivatives +
    CoinMetrics on-chain + Fear & Greed + Yahoo news — the BTC wiring,
    parameterized (Phase 2 of the score plan: ETH/SOL are config, not code)."""
    from tradingagents.pro.ingestion.builder import SnapshotBuilder
    from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed
    from tradingagents.pro.ingestion.deribit import DVOL_CURRENCIES, DeribitVolFeed
    from tradingagents.pro.ingestion.fred_macro import FredMacroFeed
    from tradingagents.pro.ingestion.news import YahooFinanceNewsFeed
    from tradingagents.pro.ingestion.onchain import CoinMetricsFeed, FearGreedFeed
    from tradingagents.pro.ingestion.sessions import current_session
    from tradingagents.pro.ingestion.token_terminal import (
        KEY_ENV as TT_KEY_ENV,
        TokenTerminalFeed,
    )

    vendor, cm_asset = CRYPTO_WIRING[symbol]
    delta = DeltaExchangeFeed()
    onchain = [CoinMetricsFeed(asset=cm_asset), FearGreedFeed(),
               _DeltaPerpMetrics(delta, vendor)]
    if cm_asset.upper() in DVOL_CURRENCIES:  # Deribit has no SOL DVOL
        onchain.append(DeribitVolFeed(currency=cm_asset.upper()))
    if os.environ.get(TT_KEY_ENV):  # optional keyed feed (P1-05d)
        onchain.append(TokenTerminalFeed(asset=cm_asset))
    if symbol == "BTC-USD":
        # P2-11 sampled liquidations + OI deltas reach the agents: the
        # process-wide shared stream — the SAME singleton the Intel panel
        # reads, so exactly one websocket per process. BTC only:
        # LiquidationStream is BTCUSDT-hardcoded, so ETH/SOL snapshots
        # deliberately go without rather than fake coverage. No socket
        # opens here — autostart defers to the first get_metrics call
        # inside a real snapshot build.
        from tradingagents.pro.ingestion.liquidations import shared_stream

        onchain.append(shared_stream("BTCUSDT"))
    return SnapshotBuilder(
        bars_feed=_MappedBars(delta, {symbol: vendor}),
        macro_feeds=(FredMacroFeed(vintage_sink=vintage_sink),),
        onchain_feeds=tuple(onchain),
        news_feed=YahooFinanceNewsFeed(symbol),
        session_fn=current_session,
    )


def _fx_snapshot_builder(symbol: str, vintage_sink=None):
    """One SnapshotBuilder per FX pair: OANDA intraday bars when the token
    is configured (else yfinance daily), FRED rates + dollar/yield context,
    Yahoo news, FX session awareness. Deliberately composed WITHOUT crypto
    on-chain feeds and without the gold-only GoldHub/COT feeds — an FX
    snapshot never fakes coverage it doesn't have."""
    from tradingagents.pro.ingestion.builder import SnapshotBuilder
    from tradingagents.pro.ingestion.fred_macro import FredMacroFeed
    from tradingagents.pro.ingestion.gold_feeds import (
        GoldCrossAssetFeed,
        YFinanceDailyBarsFeed,
    )
    from tradingagents.pro.ingestion.news import YahooFinanceNewsFeed
    from tradingagents.pro.ingestion.oanda_gold import OandaFeed
    from tradingagents.pro.ingestion.sessions import current_session

    oanda_sym, yf_sym = FX_WIRING[symbol]
    if OandaFeed.configured():
        bars_feed = _MappedBars(OandaFeed(instrument=oanda_sym),
                                {symbol: oanda_sym})
    else:
        bars_feed = _MappedBars(YFinanceDailyBarsFeed(), {symbol: yf_sym})
    # GoldCrossAssetFeed doubles as the DXY / US10Y provider (the macro
    # rates/dollar agents read those metrics); its gold-correlation extras
    # are simply unused by FX-relevant agents
    return SnapshotBuilder(
        bars_feed=bars_feed,
        macro_feeds=(FredMacroFeed(vintage_sink=vintage_sink),
                     GoldCrossAssetFeed(YFinanceDailyBarsFeed())),
        news_feed=YahooFinanceNewsFeed(yf_sym),
        session_fn=current_session,
    )


class TriggerBusy(RuntimeError):
    pass


class TriggerUnsupported(ValueError):
    """Requested pair × timeframe cannot be served by the configured feeds.
    Typed so the API maps it to a 422 (mirrors TriggerBusy → 409) instead
    of the run crashing with a 500 in the builder."""


class PipelineTrigger:
    """On-demand full pipeline run for a chosen pair × timeframe, through
    the SAME service (router, memory, recorder, gates) as the hourly loop.
    One at a time: `busy()` backs the API's 409; the service's run_lock
    additionally serializes against the loop itself."""

    SYMBOLS = ("XAUUSD", "BTC-USD", "ETH-USD", "SOL-USD", "EURUSD", "USDJPY")
    TIMEFRAMES = ("1h", "4h", "1d")

    def __init__(self, service):
        self.service = service
        self._busy = threading.Lock()
        self.current: dict | None = None  # {"symbol","timeframe"} while running

    def busy(self) -> bool:
        return self._busy.locked()

    def validate_supported(self, symbol: str, timeframe: str) -> None:
        """Raise TriggerUnsupported when the run would only crash later in
        the builder: without an OANDA token the FX pairs fall back to
        yfinance, whose feed serves DAILY bars only (YFinanceDailyBarsFeed
        raises on any intraday timeframe, and SnapshotBuilder.build treats
        bar failures as fatal by contract)."""
        from tradingagents.pro.ingestion.oanda_gold import OandaFeed

        if (symbol in FX_WIRING and timeframe != "1d"
                and not OandaFeed.configured()):
            raise TriggerUnsupported(
                f"{symbol} intraday requires OANDA_API_TOKEN; "
                "only 1d available")

    def run(self, symbol: str, timeframe: str) -> dict:
        from tradingagents.contracts import (
            ASSET_BY_SYMBOL,
            ProConfig,
            Timeframe,
        )

        if symbol not in self.SYMBOLS:
            raise ValueError(f"symbol must be one of {self.SYMBOLS}")
        if timeframe not in self.TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {self.TIMEFRAMES}")
        self.validate_supported(symbol, timeframe)
        if not self._busy.acquire(blocking=False):
            raise TriggerBusy("a pipeline run is already in progress")
        try:
            self.current = {"symbol": symbol, "timeframe": timeframe}
            asset = ASSET_BY_SYMBOL[symbol]
            # symbol passed explicitly: AssetClass.FX spans multiple pairs,
            # so the per-asset default would mislabel a USDJPY run.
            # risk: the SAME limits object the service config carries, so
            # env-armed gates (P3-04 conformal cap, P2-05, TWAP) apply to
            # operator-triggered runs exactly as to loop runs
            config = ProConfig(asset=asset, symbol=symbol,
                               max_debate_rounds=1,
                               models=self.service.config.models,
                               risk=self.service.config.risk)
            tf = Timeframe(timeframe)
            snapshot = self._build_snapshot(symbol, asset, tf)
            return self.service.run_once(snapshot=snapshot, config=config,
                                         trigger="operator")
        finally:
            self.current = None
            self._busy.release()

    def _vintage_sink(self):
        """P3-02: operator-triggered builds record FRED vintages into the
        SAME event store the loop's builders write to (via the service's
        recorder). None when no store is wired (file-backed dev/tests) —
        the feeds treat a missing sink as a no-op."""
        store = getattr(
            getattr(self.service, "dashboard", None), "recorder", None)
        store = getattr(store, "store", None)
        if store is None:
            return None

        def sink(**kw):
            try:
                store.record_vintage(**kw)
            except Exception:
                logger.warning("vintage record failed", exc_info=True)
        return sink

    def _build_snapshot(self, symbol: str, asset, tf):
        from tradingagents.contracts import Timeframe
        from tradingagents.pro.dashboard.prefs import default_data_dir
        from tradingagents.pro.ingestion.builder import SnapshotBuilder
        from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed
        from tradingagents.pro.ingestion.fred_macro import FredMacroFeed
        from tradingagents.pro.ingestion.gold_feeds import (
            GoldCrossAssetFeed,
            YFinanceDailyBarsFeed,
        )
        from tradingagents.pro.ingestion.positioning import GoldCotFeed, GoldVolFeed
        from tradingagents.pro.ingestion.sessions import current_session

        vintage_sink = self._vintage_sink()
        if symbol == "XAUUSD":
            if tf is Timeframe.D1:
                # the loop's canonical daily gold path (GC=F futures)
                def gold_loader(sym: str, curr_date: str):
                    from tradingagents.dataflows.stockstats_utils import load_ohlcv

                    return load_ohlcv("GC=F" if sym == "XAUUSD" else sym, curr_date)

                from tradingagents.pro.ingestion.builder import build_gold_pipeline
                from tradingagents.pro.ingestion.goldhub import GOLDHUB_CSV_NAME

                builder = build_gold_pipeline(
                    loader=gold_loader,
                    cot_cache_path=default_data_dir() / "cot_cache.json",
                    goldhub_csv_path=default_data_dir() / GOLDHUB_CSV_NAME,
                    vintage_sink=vintage_sink,
                )
            else:
                # intraday gold: Delta XAUT (≈ spot) + the same macro context
                delta = DeltaExchangeFeed()
                yf = YFinanceDailyBarsFeed()
                builder = SnapshotBuilder(
                    bars_feed=_MappedBars(delta, {"XAUUSD": "XAUTUSD"}),
                    macro_feeds=(
                        GoldCrossAssetFeed(yf),
                        FredMacroFeed(vintage_sink=vintage_sink),
                        GoldCotFeed(cache_path=default_data_dir()
                                    / "cot_cache.json"),
                        GoldVolFeed(yf),
                    ),
                    session_fn=current_session,
                )
        elif symbol in FX_WIRING:
            builder = _fx_snapshot_builder(symbol, vintage_sink=vintage_sink)
        else:
            builder = _crypto_snapshot_builder(symbol,
                                               vintage_sink=vintage_sink)
        return builder.build(symbol, asset, timeframes=(tf,), bar_limit=250)


def _build_alert_sinks(broadcaster, prefs=None):
    """Assemble alert sinks from the environment (go-live Phase 5). Log +
    dashboard broadcast always, plus the bell (NotificationSink) when a
    prefs store exists — the trader review caught the bell reading "All
    clear" through a run start, a completion, and two feed outages because
    nothing ever persisted notifications. Telegram and webhook are added
    only when their secrets are present, so paper/dev stays quiet by
    default."""
    from tradingagents.pro.alerting import (
        LogAlertSink,
        TelegramAlertSink,
        WebhookAlertSink,
    )
    from tradingagents.pro.dashboard.events import BroadcastAlertSink
    from tradingagents.pro.dashboard.prefs import NotificationSink
    from tradingagents.pro.secrets import get_secret

    sinks = [LogAlertSink(), BroadcastAlertSink(broadcaster)]
    if prefs is not None:
        sinks.append(NotificationSink(prefs))
    bot_token = get_secret("PRO_TELEGRAM_BOT_TOKEN")
    chat_id = get_secret("PRO_TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        sinks.append(TelegramAlertSink(bot_token, chat_id))
        logger.info("Telegram alert sink enabled")
    webhook_url = get_secret("PRO_ALERT_WEBHOOK_URL")
    if webhook_url:
        sinks.append(WebhookAlertSink(webhook_url))
        logger.info("webhook alert sink enabled")
    return sinks


def mirror_alert_feed(state, runs) -> int:
    """Mirror runs' operational alerts into the bell. Returns the number
    of NEW notifications written.

    The dashboard's Alerts panel derives its entries on the fly from run
    records (service.alert_feed) and persists nothing, while the bell reads
    the persisted notification ring — so the bell sat empty at "No
    notifications yet" while the Alerts panel showed a long list of the
    same events. Same events, two stores, no bridge.

    Reuses alert_feed's own severity mapping and consecutive-event
    coalescing rather than restating the rules. Idempotent via a per-alert
    key, so it is safe to call both per completed run and as a startup
    backfill over history.
    """
    from tradingagents.pro.dashboard import service as dashboard_service

    before = len(state.prefs.notifications())
    for alert in dashboard_service.alert_feed(list(runs))["alerts"]:
        count = alert.get("count", 1)
        suffix = f" (×{count})" if count > 1 else ""
        text = f"{alert['text']}{suffix}"
        state.prefs.add_notification(
            severity=alert["severity"], event="alert", text=text,
            time=alert["time"],
            key=f"alert:{alert.get('run_id')}:{alert['severity']}:{text}",
        )
    return len(state.prefs.notifications()) - before


def _bell_alerts_for_run(state, run_id: str) -> None:
    run = next((r for r in getattr(state, "runs", ()) if r.run_id == run_id), None)
    if run is not None:
        mirror_alert_feed(state, [run])


def _bell_on_event(state):
    """SSE publish + bell persistence for run outcomes (review P1.4).

    Alerts already reach the bell through NotificationSink; run completions
    are not alerts (they'd spam Telegram), yet a verdict landing while the
    trader was away is exactly what the bell exists to hold."""
    def on_event(type_: str, data: dict) -> None:
        state.broadcaster.publish(type_, data)
        if type_ != "run":
            return
        try:
            action = data.get("action")
            outcome = action or (
                f"rejected @ {data.get('rejected_at')}"
                if data.get("rejected_at") else "no decision"
            )
            state.prefs.add_notification(
                severity="info", event="run_complete",
                text=f"run complete — {data.get('symbol', '?')}: {outcome}",
                time=utc_now().isoformat(),
            )
        except Exception:
            logger.exception("bell notification for run event failed")
        try:
            run_id = data.get("run_id")
            if run_id:
                _bell_alerts_for_run(state, run_id)
        except Exception:
            logger.exception("bell alert mirror for run event failed")
    return on_event


def has_llm_key() -> bool:
    """True when an API key exists for the configured provider — independent
    of PRO_LOOP_DISABLED, which only gates the *periodic* background thread
    (on-demand runs still need the service/trigger wired up)."""
    from tradingagents.llm_clients.api_key_env import get_api_key_env

    provider = os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "deepseek")
    key_env = get_api_key_env(provider)
    return bool(key_env and os.environ.get(key_env))


def loop_enabled() -> bool:
    if os.environ.get("PRO_LOOP_DISABLED") == "1":
        return False
    return has_llm_key()


def build_service(llm=None, data_dir: str | Path | None = None):
    """Assemble the live service + dashboard state. ``llm`` is injectable
    for tests; production builds the env-configured bundle."""
    from tradingagents.contracts import AssetClass, ModelRouting, ProConfig, RiskLimits
    from tradingagents.pro.alerting import AlertManager
    from tradingagents.pro.dashboard.app import DashboardState
    from tradingagents.pro.dashboard.prefs import PrefsStore, default_data_dir
    from tradingagents.pro.execution import (
        VENUES,
        AuditLog,
        CircuitBreaker,
        ExecutionRouter,
        KillSwitch,
        PaperVenueAdapter,
    )
    from tradingagents.pro.ingestion.builder import build_gold_pipeline
    from tradingagents.pro.memory import ProMemory
    from tradingagents.pro.service import PaperTradingService

    routing = ModelRouting(
        llm_provider=os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "deepseek"),
        quick_think_llm=os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-chat"),
        deep_think_llm=os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "deepseek-chat"),
        # opt-in (AI-07): default-on would refuse DeepSeek, which publishes
        # no dated aliases. Live arming (go-live Phase 4) revisits this.
        require_pinned_models=os.environ.get("PRO_REQUIRE_PINNED_MODELS") == "1",
    )
    from tradingagents.contracts import EventTriggerConfig

    def _env_float(name: str) -> float | None:
        raw = os.environ.get(name)
        return float(raw) if raw else None

    # P2-05 portfolio caps, env-plumbed so an operator can arm them without
    # a code change; unset keeps the contract default (None = disabled)
    limits = RiskLimits(
        max_portfolio_var_pct=_env_float("PRO_MAX_PORTFOLIO_VAR_PCT"),
        max_correlated_gross_pct=_env_float("PRO_MAX_CORRELATED_GROSS_PCT"),
        # P3-04 conformal vol-uncertainty gate; unset keeps the contract
        # default (None = disabled, exactly the pre-P3-04 behavior)
        max_vol_interval_width_pct=_env_float(
            "PRO_MAX_VOL_INTERVAL_WIDTH_PCT"),
        vol_interval_size_scale=os.environ.get(
            "PRO_VOL_INTERVAL_SIZE_SCALE") == "1",
        # P3-10 TWAP entry slicing; unset keeps the contract default
        # (1 slice = single order, byte-identical to pre-P3-10 behavior)
        twap_slices=int(os.environ.get("PRO_TWAP_SLICES") or 1),
        twap_window_minutes=_env_float("PRO_TWAP_WINDOW_MIN"),
    )

    config = ProConfig(
        asset=AssetClass.GOLD, max_debate_rounds=1, models=routing,
        # P3-04: the env-armed limits must reach the PIPELINE config too —
        # the router enforcing caps at submit time is not the same thing as
        # the risk_gate node reading config.risk during the run
        risk=limits,
        # P2-06: opt-in — nothing changes for existing deployments
        event_triggers=EventTriggerConfig(
            enabled=os.environ.get("PRO_EVENT_TRIGGERS") == "1"),
    )

    from tradingagents.pro.observability import (
        CostTrackingLLM,
        MetricsRegistry,
        price_for,
    )

    # one registry, constructed BEFORE the bundle: build_service builds the
    # LLM bundle before PaperTradingService (which owns metrics) exists, so
    # the cost wrapper and the service must share this instance — the
    # service adopts it via metrics=, and state.metrics (the /metrics
    # scrape target) ends up pointing at the same object.
    metrics = MetricsRegistry()

    if llm is None:
        from tradingagents.pro.models import bundle_from_config

        bundle = bundle_from_config(config, temperature=0.2)
        # prod LLM observability: every quick/deep call emits
        # llm_calls_total / llm_est_cost_usd (same wrapper the backtest
        # cost meter uses; token counts are estimates — see observability)
        price = price_for(routing.llm_provider)
        quick = CostTrackingLLM(bundle.quick, price=price, metrics=metrics)
        # dedupe check BEFORE mutating: a single-model bundle keeps one
        # wrapper (one report) serving both tiers
        deep = (quick if bundle.deep is bundle.quick
                else CostTrackingLLM(bundle.deep, price=price,
                                     metrics=metrics))
        bundle.quick, bundle.deep = quick, deep
        llm = bundle

    data_path = Path(data_dir) if data_dir else default_data_dir()
    data_path.mkdir(parents=True, exist_ok=True)

    from tradingagents.pro.dashboard.recorder import PipelineRecorder

    # persistent memory + venue book: without both, service.rehydrate()
    # has nothing to read after a container restart (go-live Phase 0)
    # P2-01: SQLite event store is the source of truth; legacy JSON/JSONL
    # is imported once (idempotent) and kept as backup. The DB must live on
    # LOCAL disk in prod (TRADINGAGENTS_PRO_DB=/tmp/pro.db + Litestream);
    # the GCS FUSE mount cannot hold SQLite locks.
    from tradingagents.pro.store import (
        EventStore,
        SqliteMemoryStore,
        migrate_legacy,
        seed_users,
    )

    event_store = EventStore()
    migrate_legacy(event_store, data_path)
    # P3-05: first boot with an empty users table seeds every allowlisted
    # email as operator (pre-roles deployments were single-operator).
    # Guarded + idempotent like migrate_legacy; create_app re-runs the
    # same seed defensively for states wired outside this builder.
    seed_users(event_store,
               os.environ.get("PRO_ALLOWED_EMAILS", "").split(","))
    from tradingagents.pro.memory.embedding import make_default_embedder

    memory = ProMemory(store=SqliteMemoryStore(event_store),
                       embedder=make_default_embedder())
    state = DashboardState(memory=memory)
    # PRO_MAX_RUNS: boot-RAM knob — every retained run holds a full snapshot
    # (bars, news, debate transcript), so this bounds resident memory after
    # a restart reloads the store (default 500, the recorder's own default)
    state.recorder = PipelineRecorder(
        store=event_store,
        max_runs=int(os.environ.get("PRO_MAX_RUNS") or 500),
    )
    state.prefs = PrefsStore(store=event_store)
    from tradingagents.pro.dashboard.backtest_firestore import build_run_store
    from tradingagents.pro.dashboard.backtest_job import recover_interrupted
    from tradingagents.pro.dashboard.backtest_store import BacktestRunStore

    state.backtest_runs = build_run_store(data_path)
    state.backtest_optimizations = BacktestRunStore(
        data_path / "backtest_optimizations.json")
    state.backtest_bakeoffs = BacktestRunStore(
        data_path / "backtest_bakeoffs.json")
    try:
        # a leftover running checkpoint = the instance restarted mid-backtest;
        # surface it as a saved partial instead of losing the run
        recover_interrupted(state.backtest_runs)
    except Exception:
        logging.getLogger(__name__).exception("backtest recovery failed")
    router = ExecutionRouter(
        # "paper" venue spans the full tradeable universe; mt5's gold-only
        # map silently venue-rejected every approved BTC order (Phase 2)
        adapter=PaperVenueAdapter(VENUES["paper"], starting_cash=100_000.0,
                                  state_path=data_path / "paper_state.json"),
        limits=limits,
        kill_switch=KillSwitch(data_path / "KILL"),
        breaker=CircuitBreaker(limits, equity_base=100_000.0),
        audit=AuditLog(data_path / "audit.jsonl"),
    )
    state.router = router
    state.equity = router.adapter.account().equity  # reflects a reloaded book

    # per-pair arming state (go-live Phase 4). Every pair defaults to
    # paper; the tradingagents-pro arm-live ceremony flips it, and the
    # dashboard header + /api/flatten read it. Present but paper-only
    # until the live routing lands (Phase 6).
    from tradingagents.pro.arming import ArmingStore

    state.arming = ArmingStore(data_path / "arming.json",
                               audit=router.audit)

    # display symbol is XAUUSD (what the venue trades); GC=F is only the
    # yfinance ticker, mapped inside the loader. First container run
    # proved why: a GC=F-labeled order is refused at venue validation.
    def gold_loader(symbol: str, curr_date: str):
        from tradingagents.dataflows.stockstats_utils import load_ohlcv

        return load_ohlcv("GC=F" if symbol == "XAUUSD" else symbol, curr_date)

    from tradingagents.pro.ingestion.goldhub import GOLDHUB_CSV_NAME

    # P3-02: every FRED fetch also appends (name, value, observed_at, as_of)
    # to the event store so backtests can read metrics "as known at decision
    # time". Best-effort by contract — a vintage failure never fails a boot
    # or a snapshot build.
    def _vintage_sink(**kw):
        try:
            event_store.record_vintage(**kw)
        except Exception:
            logging.getLogger(__name__).warning("vintage record failed",
                                                exc_info=True)

    builder = build_gold_pipeline(loader=gold_loader,
                                  cot_cache_path=data_path / "cot_cache.json",
                                  goldhub_csv_path=data_path / GOLDHUB_CSV_NAME,
                                  vintage_sink=_vintage_sink)

    # multi-symbol rotation (Phase 2, P2-10): one symbol per hourly tick,
    # so LLM spend stays flat while the whole universe accrues decisions —
    # each of the 6 symbols every 6h. Builders are shared across ticks
    # (feed instances carry caches / respect rate limits).
    import itertools

    from tradingagents.contracts import ASSET_BY_SYMBOL, AssetClass as AC

    crypto_builders = {sym: _crypto_snapshot_builder(sym,
                                                     vintage_sink=_vintage_sink)
                       for sym in CRYPTO_WIRING}
    fx_builders = {sym: _fx_snapshot_builder(sym, vintage_sink=_vintage_sink)
                   for sym in FX_WIRING}
    rotation = itertools.cycle(("XAUUSD", "BTC-USD", "ETH-USD", "SOL-USD",
                                "EURUSD", "USDJPY"))

    def snapshot_source():
        symbol = next(rotation)
        # symbol passed explicitly: AssetClass.FX spans multiple pairs.
        # risk=limits: the SAME env-armed limits object as the service
        # config — a per-run config that dropped it would silently disarm
        # the P3-04 conformal gate on every rotation run
        run_config = ProConfig(asset=ASSET_BY_SYMBOL[symbol], symbol=symbol,
                               max_debate_rounds=1, models=routing,
                               risk=limits)
        if symbol == "XAUUSD":
            return builder.build("XAUUSD", AC.GOLD, bar_limit=250), run_config
        source = fx_builders if symbol in fx_builders else crypto_builders
        snapshot = source[symbol].build(
            symbol, ASSET_BY_SYMBOL[symbol], bar_limit=250)
        return snapshot, run_config

    def next_major_event():
        # fresh countdown per run for the pipeline's event gate (P1.2);
        # IntelService caches under its own TTL so this stays cheap
        return state.intel.calendar(days=7).get("next_major")

    service = PaperTradingService(
        llm, config, snapshot_source,
        router=router, memory=memory, dashboard_state=state,
        # the SAME registry the CostTrackingLLM wrappers above emit into —
        # llm_calls_total / llm_est_cost_usd land on the /metrics scrape
        metrics=metrics,
        alerts=AlertManager(
            sinks=_build_alert_sinks(state.broadcaster, state.prefs)),
        on_event=_bell_on_event(state),
        calendar_fn=next_major_event,
        # P3-03: mined-factor survivors registered in the event store's kv
        # join the QUANT roster on every run (empty/absent kv = no-op)
        factor_store=event_store,
    )
    # P2-06: the event-trigger check scans the same universe the loop
    # rotates through (bars come from the dashboard's cached market data)
    service.event_symbols = ("XAUUSD", "BTC-USD", "ETH-USD", "SOL-USD",
                             "EURUSD", "USDJPY")
    state.metrics = service.metrics  # /metrics scrape target
    service.alerts.metrics = service.metrics  # count deliveries + failures
    state.alerts = service.alerts    # emergency-flatten alerting
    # backfill the bell from runs already on disk: without this the bridge
    # only ever covers runs completed after this process started, and the
    # bell stays empty next to a full Alerts panel. Idempotent (keyed), so
    # restarts re-offer the same alerts as no-ops.
    try:
        added = mirror_alert_feed(state, state.runs)
        if added:
            logger.info("mirrored %d historical alerts into the bell", added)
    except Exception:
        logger.exception("bell backfill from the alert feed failed")
    _wire_staged_routing(state, router, service, data_path)
    return service, state


def _wire_staged_routing(state, router, service, data_path) -> None:
    """Phase 6: the router honors per-pair arming tiers. Shadow tracking
    is always wired (costs nothing until a pair is armed 'shadow'); the
    LIVE route is built only when venue credentials exist — an armed pair
    without a live route is REFUSED by the router, never paper-filled."""
    router.arming = state.arming

    from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed
    from tradingagents.pro.staging import ShadowFillTracker

    vendor_map = {"XAUUSD": "XAUTUSD", "BTC-USD": "BTCUSD"}
    market_data = DeltaExchangeFeed()
    router.shadow_tracker = ShadowFillTracker(
        lambda s: market_data.get_quote(vendor_map.get(s, s)),
        store_path=data_path / "shadow_fills.jsonl",
        metrics=service.metrics,
    )

    testnet = os.environ.get("PRO_LIVE_VENUE", "testnet") != "production"
    exchange = os.environ.get("PRO_LIVE_EXCHANGE", "delta").lower()
    try:
        from tradingagents.pro.execution import OrderManager

        if exchange == "binance":
            # P3-01 dust pilot: Binance FUTURES (testnet-first). Inert
            # unless a pair is armed at a live tier; hard notional cap
            # from live.yaml (or the conservative contract default).
            from tradingagents.pro.execution.adapters.binance_futures import (
                BinanceFuturesAdapter,
            )

            live_adapter = BinanceFuturesAdapter.from_env(
                testnet=testnet,
                armed_fn=lambda: any(
                    v["tier"] in ("canary", "live")
                    for v in state.arming.status().values()),
                max_order_notional=_live_max_notional(),
                alerts=service.alerts,
                audit=router.audit,
                kill_switch=router.kill_switch,
            )
        else:
            from tradingagents.pro.execution.adapters.delta import DeltaAdapter

            live_adapter = DeltaAdapter.from_env(testnet=testnet)
        live_oms = OrderManager(
            live_adapter,
            journal_path=data_path / "oms" / "live_journal.jsonl",
            audit=router.audit,
        )
        live_oms.recover()  # blocking: unresolved live orders = no boot
        router.live_oms = live_oms
        logger.info("live route wired (%s) — orders go live only for "
                    "canary/live-armed pairs",
                    "testnet" if testnet else "PRODUCTION")
    except Exception as exc:
        # no credentials (the common paper case) or venue unreachable:
        # stay paper-only; armed pairs will be refused, honestly
        logger.info("live route not wired (%s) — armed pairs would be "
                    "refused, paper/shadow unaffected", exc)


def _live_max_notional() -> float:
    """Hard per-order notional cap for the live adapter (P3-01). The
    operator-written live.yaml wins when PRO_LIVE_CONFIG points at one;
    otherwise the conservative LiveRiskLimits contract default applies —
    never a silent 'unlimited'."""
    path = os.environ.get("PRO_LIVE_CONFIG", "")
    if path:
        from tradingagents.pro.live_config import load_live_config

        return load_live_config(path).risk.max_notional_per_trade
    from tradingagents.contracts import LiveRiskLimits

    return LiveRiskLimits().max_notional_per_trade


def _start_live_safety_daemons(service, state) -> None:
    """When any pair is live-armed: warn if on a laptop host, and start the
    dead-man switch (layer c of the kill switch). No-op in paper (go-live
    Phase 5)."""
    import os
    import platform

    arming = getattr(state, "arming", None)
    if arming is None:
        return
    live_armed = any(v["tier"] in ("canary", "live")
                     for v in arming.status().values())
    if not live_armed:
        return

    on_docker_desktop = os.path.exists("/.dockerenv") and (
        "linuxkit" in platform.release().lower())
    if on_docker_desktop or platform.system() == "Darwin":
        msg = ("LIVE-ARMED on a laptop / Docker Desktop host — a sleep "
               "leaves positions unmanaged. Move to an always-on Linux "
               "host with NTP before unattended live trading.")
        logger.warning(msg)
        service.alerts.emit("warning", "armed_on_laptop_host", msg)

    from tradingagents.pro.deadman import DeadManSwitch, cancel_resting_orders
    from tradingagents.pro.health import live_health

    timeout = float(os.environ.get("PRO_DEADMAN_TIMEOUT_SECONDS", "600"))
    # heartbeat on EXECUTION health only — optional data-feed degradation
    # (a sentiment feed outage) must not trip the dead-man and engage the
    # kill switch while the venue path is perfectly healthy (2026-08-08:
    # the coinmetrics outage did exactly that, 600s after arming)
    from types import SimpleNamespace

    deadman = DeadManSwitch(
        health_fn=lambda: SimpleNamespace(
            ok=live_health(state, state.arming).execution_ok),
        on_trip=cancel_resting_orders(service.router),
        timeout_seconds=timeout, alerts=service.alerts,
    )
    deadman.start()
    state.deadman = deadman
    logger.info("dead-man switch armed (timeout %.0fs)", timeout)


def main() -> None:
    import uvicorn

    from tradingagents.pro.dashboard.app import create_app
    from tradingagents.pro.observability import configure_structured_logging
    from tradingagents.pro.secrets import get_secret

    configure_structured_logging()

    # Phase 3: live mode may never boot with an open dashboard. This
    # entrypoint only ever runs the paper loop (arming live is the Phase-4
    # CLI ceremony), but the guard is here too as defense in depth.
    if os.environ.get("LIVE_TRADING") == "true" and not get_secret(
            "PRO_DASHBOARD_TOKEN"):
        raise SystemExit(
            "refusing to start: LIVE_TRADING=true requires PRO_DASHBOARD_TOKEN "
            "(a control surface over real capital must be authenticated)"
        )

    interval = float(os.environ.get("PRO_LOOP_INTERVAL_SECONDS",
                                    DEFAULT_INTERVAL_SECONDS))
    if has_llm_key():
        service, state = build_service()
        state.trigger = PipelineTrigger(service)
        if loop_enabled():
            logger.info("paper-trading loop enabled: one decision every %.0fs "
                        "(real LLM calls — provider %s)", interval,
                        os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "deepseek"))
            thread = threading.Thread(
                target=service.run_forever,
                kwargs={"interval_seconds": interval},
                name="paper-loop", daemon=True,
            )
            thread.start()
            if service.start_event_trigger_daemon() is not None:
                logger.info("event-driven triggers enabled "
                            "(PRO_EVENT_TRIGGERS=1): calendar T+%.0fmin / "
                            "vol spike / gap checks every 60s",
                            service.config.event_triggers
                            .calendar_delay_minutes)
            _start_live_safety_daemons(service, state)
        else:
            logger.warning(
                "automatic paper-trading loop DISABLED (PRO_LOOP_DISABLED=1) "
                "— on-demand pipeline runs remain available"
            )
    else:
        from tradingagents.pro.dashboard.app import DashboardState

        state = DashboardState()
        logger.warning(
            "paper-trading DISABLED (no LLM key for the configured provider) "
            "— dashboard in monitor mode, on-demand runs unavailable"
        )

    port = int(os.environ.get("PORT", 8600))
    uvicorn.run(create_app(state), host="0.0.0.0", port=port,
                log_level="warning")


if __name__ == "__main__":
    main()
