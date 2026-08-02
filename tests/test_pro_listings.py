"""P4-03 slice 1: calibration-gated marketplace listings.

Store CRUD, the publish gate (listing_gate — no graded record, no
marketplace), operator-only mutations vs viewer reads, and the public
catalogue that never leaks config_json (the paid artifact)."""

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tradingagents.pro.dashboard import service  # noqa: E402
from tradingagents.pro.dashboard.app import (  # noqa: E402
    DashboardState,
    create_app,
)
from tradingagents.pro.dashboard.prefs import PrefsStore  # noqa: E402
from tradingagents.pro.memory import ProMemory  # noqa: E402
from tradingagents.pro.store import EventStore  # noqa: E402

OPERATOR = {"X-API-Key": "secret-token"}

GOOD_CALIBRATION = {
    "n_graded": 42,
    "win_rate": 0.57,
    "win_rate_n": 42,
    "avg_r": 0.31,
    "brier": 0.19,
    "versions": {"git_sha": "abc1234", "prompt_hash": "deadbeef"},
    "corpus": "paper decisions 2026-01..2026-07",
}

SECRET_CONFIG = {"strategy": "trend_follow", "atr_mult": 2.5}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _build(tmp_path):
    """Event-store-backed dashboard app with auth on."""
    store = EventStore(tmp_path / "pro.db")
    state = DashboardState(memory=ProMemory())
    state.prefs = PrefsStore(store=store)
    client = TestClient(create_app(state, api_token="secret-token"))
    return client, store


def _create(client, **overrides) -> dict:
    body = {"kind": "strategy", "title": "Trend follower",
            "description": "ATR-trailed trend following",
            "config": SECRET_CONFIG,
            "calibration": GOOD_CALIBRATION}
    body.update(overrides)
    resp = client.post("/api/listings", headers=OPERATOR, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestListingStoreCrud:
    def test_create_get_list_roundtrip(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        created = store.create_listing(
            "Seller@Example.com", "prompt", "Debate pack",
            description="prompt bundle", config={"prompts": ["a", "b"]},
            calibration=GOOD_CALIBRATION)
        assert created["status"] == "draft"
        assert created["owner_email"] == "seller@example.com"  # folded
        assert created["kind"] == "prompt"
        assert created["config"] == {"prompts": ["a", "b"]}
        assert created["calibration"] == GOOD_CALIBRATION
        assert store.get_listing(created["id"]) == created
        assert store.list_listings() == [created]
        assert store.list_listings(status="published") == []
        assert store.get_listing("nope") is None

    def test_validation(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        with pytest.raises(ValueError, match="kind"):
            store.create_listing("a@b.co", "indicator", "t")
        with pytest.raises(ValueError, match="title"):
            store.create_listing("a@b.co", "strategy", "  ")
        with pytest.raises(ValueError, match="owner_email"):
            store.create_listing("", "strategy", "t")
        with pytest.raises(ValueError, match="status"):
            store.list_listings(status="archived")
        with pytest.raises(ValueError, match="status"):
            store.set_listing_status("x", "archived")

    def test_update_and_delete(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        row = store.create_listing("a@b.co", "strategy", "t",
                                   calibration=GOOD_CALIBRATION)
        updated = store.update_listing(row["id"], title="Better title",
                                       description="new")
        assert updated["title"] == "Better title"
        assert updated["description"] == "new"
        assert store.update_listing("nope", title="x") is None
        assert store.delete_listing(row["id"]) is True
        assert store.delete_listing(row["id"]) is False
        assert store.list_listings() == []

    def test_editing_published_artifact_demotes_to_draft(self, tmp_path):
        # the published record must be exactly what the gate approved —
        # touching config or calibration re-enters the gate
        store = EventStore(tmp_path / "pro.db")
        row = store.create_listing("a@b.co", "strategy", "t",
                                   calibration=GOOD_CALIBRATION)
        store.set_listing_status(row["id"], "published")
        titled = store.update_listing(row["id"], title="rename only")
        assert titled["status"] == "published"  # metadata edits are fine
        edited = store.update_listing(row["id"], config={"changed": True})
        assert edited["status"] == "draft"


class TestListingGate:
    def test_good_record_passes(self):
        assert service.listing_gate(GOOD_CALIBRATION) == []

    def test_no_calibration_at_all(self):
        for empty in (None, {}, "not-a-dict"):
            reasons = service.listing_gate(empty)
            assert len(reasons) == 1
            assert "no calibration record" in reasons[0]

    def test_n_below_threshold_named_specifically(self):
        reasons = service.listing_gate({**GOOD_CALIBRATION, "n_graded": 7})
        assert reasons == ["n_graded=7 is below the minimum of 30 "
                           "graded outcomes"]

    def test_missing_brier_is_not_a_zero(self):
        cal = dict(GOOD_CALIBRATION)
        del cal["brier"]
        reasons = service.listing_gate(cal)
        assert len(reasons) == 1 and "brier" in reasons[0]
        # a null brier is the same lie
        assert service.listing_gate(
            {**GOOD_CALIBRATION, "brier": None}) == reasons

    def test_win_rate_needs_its_sample_size(self):
        cal = dict(GOOD_CALIBRATION)
        del cal["win_rate_n"]
        reasons = service.listing_gate(cal)
        assert len(reasons) == 1 and "win_rate_n" in reasons[0]
        assert service.listing_gate(
            {**GOOD_CALIBRATION, "win_rate": None}) \
            == ["win_rate is missing or null"]
        assert service.listing_gate(
            {**GOOD_CALIBRATION, "win_rate": 57.0}) \
            == ["win_rate=57.0 is not a fraction in [0, 1]"]

    def test_multiple_failures_all_reported(self):
        reasons = service.listing_gate(
            {"n_graded": 3, "win_rate": 1.0, "win_rate_n": 3})
        assert len(reasons) == 2  # low n AND missing brier
        assert any("n_graded=3" in r for r in reasons)
        assert any("brier" in r for r in reasons)

    def test_env_threshold_honored(self, monkeypatch):
        monkeypatch.setenv("PRO_LISTING_MIN_GRADED", "5")
        assert service.listing_gate({**GOOD_CALIBRATION, "n_graded": 7}) == []
        assert "minimum of 5" in service.listing_gate(
            {**GOOD_CALIBRATION, "n_graded": 4})[0]
        # malformed / non-positive values never open the gate
        monkeypatch.setenv("PRO_LISTING_MIN_GRADED", "banana")
        assert service.listing_min_graded() == 30
        monkeypatch.setenv("PRO_LISTING_MIN_GRADED", "0")
        assert service.listing_min_graded() == 30


class TestListingEndpoints:
    def test_crud_roundtrip(self, tmp_path):
        client, store = _build(tmp_path)
        created = _create(client)
        assert created["status"] == "draft"
        assert created["owner_email"] == "operator@deployment"

        listed = client.get("/api/listings",
                            headers=OPERATOR).json()["listings"]
        assert [row["id"] for row in listed] == [created["id"]]

        fetched = client.get(f"/api/listings/{created['id']}",
                             headers=OPERATOR)
        assert fetched.status_code == 200
        assert fetched.json()["config"] == SECRET_CONFIG

        updated = client.put(f"/api/listings/{created['id']}",
                             headers=OPERATOR, json={"title": "Renamed"})
        assert updated.status_code == 200
        assert updated.json()["title"] == "Renamed"

        gone = client.delete(f"/api/listings/{created['id']}",
                             headers=OPERATOR)
        assert gone.status_code == 200
        assert gone.json()["status"] == "delisted"

        assert client.get("/api/listings/nope",
                          headers=OPERATOR).status_code == 404
        assert client.put("/api/listings/nope", headers=OPERATOR,
                          json={}).status_code == 404
        assert client.delete("/api/listings/nope",
                             headers=OPERATOR).status_code == 404
        assert client.post("/api/listings/nope/publish",
                           headers=OPERATOR).status_code == 404

    def test_creation_validation(self, tmp_path):
        client, _ = _build(tmp_path)
        bad_kind = client.post("/api/listings", headers=OPERATOR, json={
            "kind": "indicator", "title": "x"})
        assert bad_kind.status_code == 422
        assert "kind" in bad_kind.json()["detail"]
        assert client.post("/api/listings", headers=OPERATOR, json={
            "kind": "strategy", "title": ""}).status_code == 422
        assert client.get("/api/listings?status=archived",
                          headers=OPERATOR).status_code == 422

    def test_publish_gate_blocks_thin_records(self, tmp_path):
        client, _ = _build(tmp_path)
        thin = _create(client, calibration={**GOOD_CALIBRATION,
                                            "n_graded": 7})
        denied = client.post(f"/api/listings/{thin['id']}/publish",
                             headers=OPERATOR)
        assert denied.status_code == 422
        detail = denied.json()["detail"]
        assert detail["published"] is False
        assert detail["failures"] == [
            "n_graded=7 is below the minimum of 30 graded outcomes"]
        # still a draft — the gate is the only door
        assert client.get(f"/api/listings/{thin['id']}",
                          headers=OPERATOR).json()["status"] == "draft"

    def test_publish_gate_blocks_missing_brier(self, tmp_path):
        client, _ = _build(tmp_path)
        cal = dict(GOOD_CALIBRATION)
        del cal["brier"]
        row = _create(client, calibration=cal)
        denied = client.post(f"/api/listings/{row['id']}/publish",
                             headers=OPERATOR)
        assert denied.status_code == 422
        failures = denied.json()["detail"]["failures"]
        assert len(failures) == 1 and "brier" in failures[0]

    def test_publish_gate_admits_graded_record(self, tmp_path):
        client, _ = _build(tmp_path)
        row = _create(client)
        published = client.post(f"/api/listings/{row['id']}/publish",
                                headers=OPERATOR)
        assert published.status_code == 200
        assert published.json()["status"] == "published"

    def test_env_threshold_honored_end_to_end(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRO_LISTING_MIN_GRADED", "5")
        client, _ = _build(tmp_path)
        row = _create(client, calibration={**GOOD_CALIBRATION,
                                           "n_graded": 10,
                                           "win_rate_n": 10})
        assert client.post(f"/api/listings/{row['id']}/publish",
                           headers=OPERATOR).status_code == 200


class TestListingRoles:
    """Viewer sessions read the catalogue but cannot mutate it. Session
    minting mirrors TestRolesAndEntitlements in test_pro_dashboard_app:
    a monkeypatched Firebase verifier treats the bearer token as the
    signed-in email."""

    ENV = {
        "PRO_FIREBASE_PROJECT_ID": "demo-project",
        "PRO_ALLOWED_EMAILS": "op@example.com,eve@example.com",
        "PRO_FIREBASE_WEB_CONFIG": '{"apiKey": "public"}',
    }

    def _setup(self, tmp_path, monkeypatch):
        import tradingagents.pro.dashboard.app as app_module

        for key, value in self.ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            app_module, "_verify_firebase_token",
            lambda id_token, audience: {"email": id_token,
                                        "email_verified": True})
        store = EventStore(tmp_path / "pro.db")
        state = DashboardState(memory=ProMemory())
        state.prefs = PrefsStore(store=store)
        app = create_app(state, api_token="secret-token")
        store.put_user("eve@example.com", "viewer")
        client = TestClient(app)
        resp = client.post("/api/session",
                           headers={"Authorization": "Bearer eve@example.com"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "viewer"
        return client, store

    def test_viewer_reads_but_cannot_mutate(self, tmp_path, monkeypatch):
        viewer, store = self._setup(tmp_path, monkeypatch)
        row = store.create_listing("op@example.com", "strategy", "t",
                                   calibration=GOOD_CALIBRATION)

        assert viewer.get("/api/listings").status_code == 200
        assert viewer.get(f"/api/listings/{row['id']}").status_code == 200

        denied = viewer.post("/api/listings", json={
            "kind": "strategy", "title": "x"})
        assert denied.status_code == 403
        assert "viewer" in denied.json()["detail"]
        assert viewer.put(f"/api/listings/{row['id']}",
                          json={"title": "x"}).status_code == 403
        assert viewer.delete(f"/api/listings/{row['id']}").status_code == 403
        assert viewer.post(
            f"/api/listings/{row['id']}/publish").status_code == 403
        # nothing moved
        assert store.get_listing(row["id"])["status"] == "draft"

    def test_owner_defaults_to_signed_in_operator(self, tmp_path,
                                                  monkeypatch):
        client, store = self._setup(tmp_path, monkeypatch)
        op = TestClient(client.app)
        resp = op.post("/api/session",
                       headers={"Authorization": "Bearer op@example.com"})
        assert resp.json()["role"] == "operator"
        created = op.post("/api/listings", json={
            "kind": "prompt", "title": "Debate pack"})
        assert created.status_code == 200
        assert created.json()["owner_email"] == "op@example.com"


class TestPublicListings:
    def _published(self, tmp_path):
        client, store = _build(tmp_path)
        row = _create(client)
        assert client.post(f"/api/listings/{row['id']}/publish",
                           headers=OPERATOR).status_code == 200
        raw = client.post("/api/tokens", headers=OPERATOR, json={
            "label": "partner",
            "scopes": ["read:decisions"]}).json()["token"]
        return client, row, raw

    def test_requires_bearer_token_and_scope(self, tmp_path):
        client, row, raw = self._published(tmp_path)
        assert client.get("/public/v1/listings").status_code == 401
        assert client.get("/public/v1/listings",
                          headers=_bearer("wrong")).status_code == 401
        calibration_only = client.post(
            "/api/tokens", headers=OPERATOR,
            json={"label": "cal", "scopes": ["read:calibration"]}
        ).json()["token"]
        assert client.get("/public/v1/listings",
                          headers=_bearer(calibration_only)).status_code == 403

    def test_published_listing_never_leaks_config(self, tmp_path):
        client, row, raw = self._published(tmp_path)
        resp = client.get("/public/v1/listings", headers=_bearer(raw))
        assert resp.status_code == 200
        listings = resp.json()["listings"]
        assert len(listings) == 1
        public = listings[0]
        assert public["id"] == row["id"]
        assert public["title"] == "Trend follower"
        assert public["kind"] == "strategy"
        assert public["calibration"] == GOOD_CALIBRATION
        assert public["versions"] == GOOD_CALIBRATION["versions"]
        assert public["corpus"] == GOOD_CALIBRATION["corpus"]
        # the paid artifact and the seller's identity stay private
        assert "config" not in public
        assert "config_json" not in public
        assert "owner_email" not in public
        # the secret values appear nowhere in the payload at all
        assert "trend_follow" not in resp.text
        assert "atr_mult" not in resp.text

    def test_drafts_and_delisted_are_invisible(self, tmp_path):
        client, row, raw = self._published(tmp_path)
        _create(client, title="Still a draft")  # never published
        assert len(client.get("/public/v1/listings",
                              headers=_bearer(raw)).json()["listings"]) == 1
        assert client.delete(f"/api/listings/{row['id']}",
                             headers=OPERATOR).status_code == 200
        assert client.get("/public/v1/listings",
                          headers=_bearer(raw)).json()["listings"] == []
