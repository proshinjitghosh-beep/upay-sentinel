import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.engine import ENGINE, trace_flows
from backend.app.main import app
from backend.app.scoring import HOLD, Sentinel


@pytest.fixture(scope="module")
def s():
    return Sentinel()


def test_engine_matches_python_fallback(s):
    t, n = s.t, len(s.a)
    args = (n, t.src.values, t.dst.values, t.amount.values, t.ts.values, (s.a.role == "agent").values, 1800)
    c, p = trace_flows(*args), trace_flows(*args, force_python=True)
    for k in c:
        assert np.allclose(c[k], p[k]), k


def test_dataset_shape(s):
    assert len(s.t) == 10_000
    assert 0.045 <= s.t.is_fraud.mean() <= 0.055


def test_known_mule_ring_is_traced():
    # victim 0 -> mule 1 (50k) -> mules 2,3 -> agent 4 (cash-out), all within minutes
    src, dst = [0, 1, 1, 2, 3], [1, 2, 3, 4, 4]
    amt, ts = [50000, 25000, 24000, 24000, 23000], [0, 60, 90, 300, 400]
    r = trace_flows(5, src, dst, amt, ts, [0, 0, 0, 0, 1], 1800)
    assert r["reach"][1] == 3 and r["depth"][1] == 2 and r["cash_amt"][1] == 47000 and r["dwell"][1] == 60


def test_holdout_quality(s):
    m = s.metrics
    assert m["sentinel"]["precision"] >= 0.9 and m["sentinel_watch_or_above"]["recall"] >= 0.9


def test_api_flow():
    with TestClient(app) as c:
        top = c.get("/api/alerts").json()[0]
        assert top["tier"] == "HOLD"
        r = c.post("/api/check-cashout", json={"account_id": top["id"], "amount": 30000}).json()
        assert r["decision"] == "HOLD_AND_VERIFY"
        c.post(f"/api/alerts/{top['id']}/decision", json={"action": "release"})
        assert c.post("/api/check-cashout", json={"account_id": top["id"], "amount": 30000}).json()["decision"] != "HOLD_AND_VERIFY"
        assert c.get("/api/alerts/999999").status_code == 404
        assert c.post("/api/check-cashout", json={"account_id": 1310, "amount": 1}).status_code == 400
