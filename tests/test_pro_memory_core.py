"""Memory building blocks: embedder, index, store, knowledge graph."""

import math

import pytest

from tradingagents.pro.memory import (
    HashingEmbedder,
    InMemoryVectorIndex,
    JsonlStore,
    KnowledgeGraph,
    MemoryKind,
    MemoryRecord,
    Relation,
    cosine,
    default_bitcoin_graph,
    default_gold_graph,
)


class TestHashingEmbedder:
    def test_deterministic(self):
        embed = HashingEmbedder()
        assert embed("gold trending up") == embed("gold trending up")

    def test_normalized(self):
        vec = HashingEmbedder()("gold trending up rsi oversold")
        assert math.fsum(v * v for v in vec) == pytest.approx(1.0)

    def test_similar_texts_score_higher_than_unrelated(self):
        embed = HashingEmbedder()
        base = embed("gold trending up dollar weak real yields falling")
        similar = embed("gold trending up dollar weak")
        unrelated = embed("bitcoin funding rate spike liquidations cascade")
        assert cosine(base, similar) > cosine(base, unrelated)

    def test_empty_text_is_zero_vector(self):
        assert all(v == 0.0 for v in HashingEmbedder()("!!!"))

    def test_dim_lower_bound(self):
        with pytest.raises(ValueError):
            HashingEmbedder(dim=4)


def record(kind=MemoryKind.TRADE, text="BUY XAUUSD in regime trending_up", **kw):
    return MemoryRecord(kind=kind, text=text, **kw)


class TestInMemoryVectorIndex:
    def test_ranked_search_with_filters(self):
        embed = HashingEmbedder()
        index = InMemoryVectorIndex()
        r1 = record(text="BUY XAUUSD trending up dollar weak", symbol="XAUUSD")
        r2 = record(text="SELL BTC-USD funding spike", symbol="BTC-USD")
        r3 = record(kind=MemoryKind.REFLECTION, text="XAUUSD thesis weak on yields",
                    symbol="XAUUSD")
        for r in (r1, r2, r3):
            index.add(r, embed(r.text))

        hits = index.search(embed("XAUUSD trending dollar"), k=3)
        assert hits[0].record.id == r1.id

        trade_hits = index.search(embed("XAUUSD"), kinds=(MemoryKind.TRADE,))
        assert {h.record.id for h in trade_hits} <= {r1.id, r2.id}

        symbol_hits = index.search(embed("anything"), symbol="BTC-USD")
        assert [h.record.id for h in symbol_hits] == [r2.id]


class TestJsonlStore:
    def test_round_trip(self, tmp_path):
        store = JsonlStore(tmp_path / "memory.jsonl")
        r = record(symbol="XAUUSD", payload={"pnl": 1.5})
        store.append(r)
        store.append(record(kind=MemoryKind.MISTAKE, text="chased entry"))
        loaded = store.load()
        assert len(loaded) == 2
        assert loaded[0] == r

    def test_corrupt_lines_skipped(self, tmp_path):
        path = tmp_path / "memory.jsonl"
        store = JsonlStore(path)
        store.append(record())
        with path.open("a") as f:
            f.write("{not json\n")
        store.append(record(text="second valid"))
        assert len(store.load()) == 2

    def test_missing_file_loads_empty(self, tmp_path):
        assert JsonlStore(tmp_path / "nope.jsonl").load() == []


class TestKnowledgeGraph:
    def test_seed_graphs_cover_key_drivers(self):
        gold = default_gold_graph()
        assert any(e.source == "US10Y_REAL" for e in gold.neighbors("XAUUSD"))
        assert gold.between("DXY", "XAUUSD")[0].relation == "inverse"
        btc = default_bitcoin_graph()
        assert any(e.source == "FUNDING_RATE" for e in btc.neighbors("BTC-USD"))

    def test_prompt_block_orders_by_weight(self):
        graph = KnowledgeGraph([
            Relation("A", "inverse", "X", 0.3),
            Relation("B", "positive", "X", 0.9),
        ])
        block = graph.to_prompt_block("X")
        assert block.index("B") < block.index("A")
        assert "structural priors" in block

    def test_empty_block_for_unknown_node(self):
        assert default_gold_graph().to_prompt_block("TSLA") == ""


class TestSemanticEmbedder:
    """P2-04: semantic default with hashing fallback."""

    def test_fallback_to_hashing_when_model_unavailable(self, monkeypatch):
        from tradingagents.pro.memory import embedding as emb

        def boom(self):
            raise RuntimeError("no model")

        monkeypatch.setattr(emb.SemanticEmbedder, "_load", boom)
        monkeypatch.delenv("TRADINGAGENTS_EMBEDDER", raising=False)
        assert isinstance(emb.make_default_embedder(), emb.HashingEmbedder)

    def test_env_forces_hashing(self, monkeypatch):
        from tradingagents.pro.memory import embedding as emb

        monkeypatch.setenv("TRADINGAGENTS_EMBEDDER", "hashing")
        assert isinstance(emb.make_default_embedder(), emb.HashingEmbedder)

    def test_semantic_vector_shape_and_latency(self):
        import time

        from tradingagents.pro.memory.embedding import SemanticEmbedder

        embedder = SemanticEmbedder()
        try:
            embedder("warmup")  # may download on first ever call
        except Exception:
            pytest.skip("model2vec model unavailable (offline CI)")
        t0 = time.perf_counter()
        vec = embedder("gold ranging regime, fade the breakout")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert len(vec) == 256
        assert abs(sum(v * v for v in vec) - 1.0) < 1e-3  # unit norm
        assert elapsed_ms < 100  # AC latency bound

    def test_semantic_beats_hashing_on_paraphrase(self):
        from tradingagents.pro.memory.embedding import (
            HashingEmbedder,
            SemanticEmbedder,
        )

        sem = SemanticEmbedder()
        try:
            sem("warmup")
        except Exception:
            pytest.skip("model2vec model unavailable (offline CI)")

        def cos(a, b):
            return sum(x * y for x, y in zip(a, b, strict=True))

        query = "central bank buying supports bullion"
        match = "gold demand from official-sector purchases"
        distractor = "solana network outage halts transactions"
        s_match, s_dist = cos(sem(query), sem(match)), cos(sem(query), sem(distractor))
        assert s_match > s_dist  # semantic model ranks the paraphrase higher
        h = HashingEmbedder()
        assert cos(h(query), h(match)) == 0.0  # zero token overlap => hashing blind
