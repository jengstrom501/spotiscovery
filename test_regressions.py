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


def issue_007_standard_export_imports():
    # Regression: ISSUE-007 - StreamingHistory_music_*.json (account data export) imported 0 plays
    import io
    import json

    def upload(name, records):
        body = {"files": (io.BytesIO(json.dumps(records).encode()), name)}
        return client.post("/api/upload", data=body, content_type="multipart/form-data")

    standard = [
        {"endTime": "2024-03-01 14:15", "artistName": "Low", "trackName": "Words", "msPlayed": 215000},
        {"endTime": "2024-03-01 14:20", "artistName": "Low", "trackName": "Skip", "msPlayed": 4000},  # under 30s
        {"endTime": "not a time", "artistName": "Low", "trackName": "Bad row", "msPlayed": 215000},
    ]
    extended = [
        {"ts": "2024-04-02T20:15:00Z", "master_metadata_album_artist_name": "Duster", "ms_played": 240000},
        {"ts": "2024-04-02T21:00:00Z", "master_metadata_album_artist_name": None, "ms_played": 900000},  # podcast
        {"ts": "2024-04-04T24:15:00Z", "master_metadata_album_artist_name": "Duster", "ms_played": 240000},  # hour 24
    ]
    assert upload("StreamingHistory_music_0.json", standard).get_json() == {"added": 1}
    assert upload("Streaming_History_Audio_2024.json", extended).get_json() == {"added": 1}
    assert deep_cuts.store.parse_plays(standard)[0][0] == "2024-03-01T14:15:00Z"


if __name__ == "__main__":
    for name, check in list(globals().items()):
        if name.startswith("issue_"):
            check()
    print("ok")
