"""Regression checks for bugs found in QA on 2026-09-24.
Run: python test_regressions.py  (offline, uses a throwaway database)"""
import os
import tempfile

os.environ["DEEP_CUTS_DB"] = os.path.join(tempfile.mkdtemp(), "qa.db")

import app as deep_cuts  # noqa: E402

client = deep_cuts.app.test_client()


def issue_001_cross_site_writes_blocked():
    # Regression: ISSUE-001 - any web page could rewrite .env keys and trigger paid Claude calls
    before = os.environ.get("ANTHROPIC_API_KEY")
    r = client.post("/api/settings", json={"anthropic_api_key": "evil"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403, r.status_code
    assert client.post("/api/claude", headers={"Origin": "http://127.0.0.1:8899"}).status_code == 403  # other local port
    assert client.get("/api/status", headers={"Host": "evil.example:8888"}).status_code == 403  # DNS rebinding
    # A no-cors text/plain post (no preflight) must not be parsed as settings
    r = client.post("/api/settings", data='{"anthropic_api_key": "evil"}', content_type="text/plain")
    assert r.status_code == 200 and r.get_json()["saved"] == [], r.get_json()
    assert os.environ.get("ANTHROPIC_API_KEY") == before
    # The app's own page still works
    assert client.post("/api/settings", json={}, headers={"Origin": "http://localhost"}).status_code == 200


if __name__ == "__main__":
    for name, check in list(globals().items()):
        if name.startswith("issue_"):
            check()
    print("ok")
