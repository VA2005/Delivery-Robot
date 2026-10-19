"""Run: pytest -q   (from the server/ folder)"""
import os
import tempfile

DB = os.path.join(tempfile.gettempdir(), "navbot_test.db")
os.environ["DB_PATH"] = DB

import pytest
from fastapi.testclient import TestClient

from app import main, security as sec
from seed import seed


@pytest.fixture
def c():
    if os.path.exists(DB):
        os.remove(DB)
    main._hits.clear()
    with TestClient(main.app) as client:
        seed()
        yield client


def login(c, uid, pw, qr=None):
    return c.post("/api/auth/login", json={"user_id": uid, "password": pw, "qr_token": qr})


def bearer(r):
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def new_qr(c):
    return c.get("/api/qr/token", headers={"X-Robot-Key": sec.ROBOT_KEY}).json()["token"]


def loaded_order(c, rid="10423005"):
    admin = bearer(login(c, "admin01", "admin123"))
    oid = c.post("/api/orders/create", json={"recipient_id": rid}, headers=admin).json()["order_id"]
    c.post(f"/api/orders/{oid}/loaded", headers=bearer(login(c, "staff01", "staff123")))
    return oid


def test_happy_path(c):
    loaded_order(c)
    r = login(c, "10423005", "pass001", new_qr(c))
    assert r.status_code == 200
    u = c.post("/api/unlock", headers=bearer(r))
    assert u.status_code == 200 and u.json()["compartment"] == 1


def test_qr_cannot_be_reused(c):
    loaded_order(c)
    qr = new_qr(c)
    assert c.post("/api/unlock", headers=bearer(login(c, "10423005", "pass001", qr))).status_code == 200
    assert login(c, "10423005", "pass001", qr).status_code == 401


def test_wrong_user_gets_nothing(c):
    loaded_order(c, "10423005")
    r = login(c, "10423114", "pass002", new_qr(c))
    assert c.post("/api/unlock", headers=bearer(r)).status_code == 404


def test_expired_qr_rejected(c):
    expired = sec._encode({"dp": sec.DELIVERY_POINT}, -10, "qr")
    assert login(c, "10423005", "pass001", expired).status_code == 401


def test_qr_required_for_recipient(c):
    assert login(c, "10423005", "pass001").status_code == 400


def test_lockout_after_failures(c):
    for _ in range(5):
        assert login(c, "10423005", "wrong").status_code == 401
    assert login(c, "10423005", "pass001", new_qr(c)).status_code == 423


def test_only_admin_creates_orders(c):
    r = login(c, "staff01", "staff123")
    assert c.post("/api/orders/create", json={"recipient_id": "10423005"}, headers=bearer(r)).status_code == 403

def test_each_recipient_gets_own_compartment(c):
    loaded_order(c, "10423005")   # compartment 1
    loaded_order(c, "10423076")   # compartment 2

    minh = login(c, "10423076", "pass003", new_qr(c))
    assert c.post("/api/unlock", headers=bearer(minh)).json()["compartment"] == 2

    viet = login(c, "10423005", "pass001", new_qr(c))
    assert c.post("/api/unlock", headers=bearer(viet)).json()["compartment"] == 1

def test_complete_frees_compartment(c):
    oid = loaded_order(c)
    r = login(c, "10423005", "pass001", new_qr(c))
    assert c.post("/api/unlock", headers=bearer(r)).status_code == 200

    staff = bearer(login(c, "staff01", "staff123"))
    assert c.post(f"/api/orders/{oid}/complete", headers=staff).status_code == 200

    # box 1 is free again, so the next order gets compartment 1
    admin = bearer(login(c, "admin01", "admin123"))
    new = c.post("/api/orders/create", json={"recipient_id": "10423114"}, headers=admin).json()
    assert new["compartment"] == 1


def test_cannot_complete_before_unlock(c):
    oid = loaded_order(c)
    staff = bearer(login(c, "staff01", "staff123"))
    assert c.post(f"/api/orders/{oid}/complete", headers=staff).status_code == 404

def test_same_secret_handles_non_ascii():
    assert main.same_secret("dev-robot-key", "dev-robot-key") is True
    assert main.same_secret("é", "dev-robot-key") is False


def test_third_order_gets_no_compartment(c):
    admin = bearer(login(c, "admin01", "admin123"))
    for rid in ("10423005", "10423114"):
        assert c.post("/api/orders/create", json={"recipient_id": rid}, headers=admin).is_success
    r = c.post("/api/orders/create", json={"recipient_id": "10423076"}, headers=admin)
    assert r.status_code == 409

def test_same_secret_handles_non_ascii():
    assert main.same_secret("dev-robot-key", "dev-robot-key") is True
    assert main.same_secret("é", "dev-robot-key") is False


def test_third_order_gets_no_compartment(c):
    admin = bearer(login(c, "admin01", "admin123"))
    for rid in ("10423005", "10423114"):
        assert c.post("/api/orders/create", json={"recipient_id": rid}, headers=admin).is_success
    r = c.post("/api/orders/create", json={"recipient_id": "10423076"}, headers=admin)
    assert r.status_code == 409


def test_production_refuses_default_secrets(monkeypatch):
    monkeypatch.setattr(sec, "ENV", "production")
    with pytest.raises(RuntimeError):
        sec.check_config()


def test_production_accepts_real_secrets(monkeypatch):
    monkeypatch.setattr(sec, "ENV", "production")
    monkeypatch.setattr(sec, "SECRET", "x" * 40)
    monkeypatch.setattr(sec, "ROBOT_KEY", "y" * 20)
    sec.check_config()          # must not raise


def test_dev_mode_only_warns(monkeypatch):
    monkeypatch.setattr(sec, "ENV", "dev")
    sec.check_config()          # defaults are fine in development


def test_production_seed_uses_env_or_random_passwords(c, monkeypatch):
    monkeypatch.setattr(sec, "ENV", "production")
    monkeypatch.setenv("SEED_PW_ADMIN01", "Correct-Horse-9")
    generated = seed()
    assert "admin01" not in generated and "staff01" in generated
    assert login(c, "admin01", "Correct-Horse-9").status_code == 200
    assert login(c, "admin01", "admin123").status_code == 401
    assert login(c, "staff01", "staff123").status_code == 401