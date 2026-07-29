"""P2-03 anonymizer: deterministic identity+calendar masking at the
snapshot->prompt boundary, with named mode byte-identical to before."""

import re
from datetime import datetime, timezone

import pytest

from tests.test_pro_agents_base import make_snapshot
from tests.test_pro_pipeline_graph import CONFIG, pipeline_snapshot
from tradingagents.contracts import AgentTeam
from tradingagents.pro.agents import (
    AgentSpec,
    active_masker,
    anonymization_scope,
    render_context,
)
from tradingagents.pro.evals.anonymize import (
    Anonymizer,
    anonymize_llm,
    run_memorization_audit,
)
from tradingagents.pro.evals.scripted import FakePipelineLLM
from tradingagents.pro.pipeline import run_pipeline

REF = datetime(2026, 7, 6, 14, 30, tzinfo=timezone.utc)  # == fixture AS_OF

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
IDENTITY = re.compile(
    r"(?<![A-Za-z0-9])(?:xauusd|xau|gold|bullion|btc|bitcoin)(?![A-Za-z0-9])",
    re.IGNORECASE)  # boundary-aware: "golden cross" is TA vocabulary, not identity

FULL_SPEC = AgentSpec(
    agent_id="full_view", team=AgentTeam.NEWS_SENTIMENT,
    persona="Sees indicators, macro, bars and news.",
    indicators=("RSI_14",), metrics=("DXY",),
    include_bars=5, include_news=2,
)


def masker(**kwargs) -> Anonymizer:
    a = Anonymizer(reference=REF, **kwargs)
    a.register("XAUUSD")
    return a


# --- masking rules -----------------------------------------------------------


def test_masking_is_deterministic_and_stable():
    text = "Gold (XAUUSD) closed higher on 2026-07-03; bullion bid since July 1."
    a, b = masker(), masker()
    assert a.mask(text) == b.mask(text)          # same seed -> same masks
    assert a.mask(text) == a.mask(text)          # pure: no per-call state
    # idempotent: masking masked text changes nothing
    assert a.mask(a.mask(text)) == a.mask(text)


def test_ticker_and_asset_words_share_one_stable_token():
    a = masker()
    out = a.mask("gold XAUUSD XAU/USD bullion Gold GOLD")
    assert out == "ASSET_A ASSET_A ASSET_A ASSET_A ASSET_A ASSET_A"
    assert a.mapping == {"XAUUSD": "ASSET_A"}


def test_second_symbol_gets_the_next_token():
    a = masker()
    a.register("BTC-USD")
    out = a.mask("rotate gold into bitcoin: sell XAUUSD, buy BTC-USD (BTC)")
    assert "ASSET_A" in out and "ASSET_B" in out
    assert not IDENTITY.search(out)


def test_seed_rotates_labels_deterministically():
    a = Anonymizer(reference=REF, seed=3)
    assert a.register("XAUUSD") == "ASSET_D"
    assert a.register("BTC-USD") == "ASSET_E"
    b = Anonymizer(reference=REF, seed=3)
    assert b.register("XAUUSD") == "ASSET_D"


def test_absolute_dates_become_relative():
    a = masker()
    assert a.mask("as of 2026-07-06") == "as of T-0"
    assert a.mask("low printed 2026-07-03") == "low printed T-3d"
    assert a.mask("expiry 2026-07-08") == "expiry T+2d"
    # datetimes keep the clock (intraday ordering is data, not identity)
    assert a.mask("bar 2026-07-03 14:00 closed") == "bar T-3d 14:00 closed"
    # textual dates, with and without a year
    assert a.mask("since July 3, 2026") == "since T-3d"
    assert a.mask("since Jul 3") == "since T-3d"


def test_numbers_and_levels_are_preserved_verbatim():
    a = masker()
    text = ("RSI_14: value=27.4000 | DXY: 104.2 | support 2400.5, "
            "resistance 2450-2500, funding 0.0003")
    assert a.mask(text) == text  # not one digit touched
    # year-like numbers that are prices stay: only real dates are masked
    assert a.mask("target 2026.5 by 2026-07-08") == "target 2026.5 by T+2d"


def test_word_boundaries_protect_lookalikes():
    a = masker()
    a.register("SOL-USD")
    out = a.mask("Marigold consols insolvency GOLDEN gold")
    assert out == "Marigold consols insolvency GOLDEN ASSET_A"
    # identifier segments DO leak identity and are masked
    assert a.mask("GOLD_VOL_INDEX: 18.2") == "ASSET_A_VOL_INDEX: 18.2"
    assert a.mask("XAU_XAG_CORR_30D") == "ASSET_A_XAG_CORR_30D"


# --- rendering-boundary flag ---------------------------------------------------


def test_render_context_anonymize_flag_masks_the_data_block():
    snapshot = make_snapshot()
    ctx = render_context(snapshot, FULL_SPEC, anonymize=masker())
    assert not ISO_DATE.search(ctx.text)
    assert not IDENTITY.search(ctx.text)
    assert "T-" in ctx.text or "T+" in ctx.text
    # honest masking: the numbers are still the real market data
    assert "value=27.4000" in ctx.text
    assert "DXY: 104.2" in ctx.text


def test_named_mode_is_byte_identical_and_unmasked():
    snapshot = make_snapshot()
    plain = render_context(snapshot, FULL_SPEC)
    assert render_context(snapshot, FULL_SPEC, anonymize=None).text == plain.text
    assert render_context(snapshot, FULL_SPEC, anonymize=False).text == plain.text
    assert ISO_DATE.search(plain.text)  # real dates still rendered
    assert "ASSET_" not in plain.text


def test_anonymize_true_requires_an_active_scope():
    with pytest.raises(ValueError, match="anonymization_scope"):
        render_context(make_snapshot(), FULL_SPEC, anonymize=True)


def test_scope_masks_renders_without_touching_agent_code():
    snapshot = make_snapshot()
    with anonymization_scope(masker()) as m:
        assert active_masker() is m
        ctx = render_context(snapshot, FULL_SPEC)  # no flag passed
        assert not ISO_DATE.search(ctx.text)
        # explicit opt-out wins over the scope
        assert ISO_DATE.search(render_context(snapshot, FULL_SPEC,
                                              anonymize=False).text)
    assert active_masker() is None
    assert ISO_DATE.search(render_context(snapshot, FULL_SPEC).text)


# --- full pipeline, prompts captured -------------------------------------------


def all_prompts(llm: FakePipelineLLM) -> list[str]:
    return [p for prompts in llm.prompts.values() for p in prompts]


def test_pipeline_prompts_fully_masked_under_scope_and_wrapper():
    fake = FakePipelineLLM()
    m = Anonymizer(reference=pipeline_snapshot().as_of)
    m.register("XAUUSD")
    with anonymization_scope(m):
        state = run_pipeline(anonymize_llm(fake, m), CONFIG, pipeline_snapshot())
    assert state["recommendation"] is not None  # masked run still decides
    prompts = all_prompts(fake)
    assert prompts
    for prompt in prompts:
        assert not IDENTITY.search(prompt), prompt[:200]
        assert not ISO_DATE.search(prompt), prompt[:200]
    assert any("ASSET_A" in p for p in prompts)


def test_pipeline_named_mode_prompts_still_carry_the_real_symbol():
    fake = FakePipelineLLM()
    run_pipeline(fake, CONFIG, pipeline_snapshot())
    prompts = all_prompts(fake)
    assert any("XAUUSD" in p for p in prompts)
    assert any(ISO_DATE.search(p) for p in prompts)


# --- memorization audit harness --------------------------------------------------


def test_memorization_audit_rows_agree_for_a_deterministic_model():
    import json

    rows = run_memorization_audit(
        FakePipelineLLM(), CONFIG, samples=2,
        case_names=("clean_uptrend_supportive_macro",))
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "XAUUSD" and row["token"] == "ASSET_A"
    assert row["named_actions"] == row["anon_actions"]
    assert row["action_agreement"] == 1.0
    assert row["flagged"] is False
    json.dumps(rows)  # artifact-writable as-is
