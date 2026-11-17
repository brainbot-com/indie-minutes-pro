"""Tests without GPU or LLM: upload resume, the pipeline with the fake engine,
speaker renames, follow-up idempotency, window merging and MCP.

    docker compose run --rm -T --no-deps recorder python -m pytest -q tests"""
import hashlib
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from recorder import followup as fu
from recorder import worker as wk
from recorder.api import create_app, create_token
from recorder.config import Settings
from recorder.engines.base import merge_windows

SUMMARY = {"title": "Release planning", "tldr": "Speaker 2 sends the deck.", "decisions": ["Ship the beta next week"],
           "action_items": [{"text": "Send the updated deck", "owner": "Speaker 2", "due": "Friday", "at": "00:00:10"},
                            {"text": "Check the open security issue", "owner": "Speaker 1", "due": "", "at": ""}],
           "open_questions": [], "language": "en", "model": "stub", "llm_stats": []}


@pytest.fixture
def app(tmp_path, monkeypatch):
    s = Settings(data_dir=tmp_path / "data", worker="0", engine="fake", secure_cookies="0",
                 public_url="https://box.test", github_token_file=tmp_path / "no-token", followup_on_done="0")
    monkeypatch.setattr(wk, "summarize", lambda transcript, settings, log=print: dict(SUMMARY))
    return create_app(s)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def tone(path, seconds):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=300:duration={seconds}", "-ac", "1", "-ar", "16000", str(path)], check=True)
    return path.read_bytes()


def make_meeting(app, client, tmp_path, seconds=20):
    h = {"Authorization": f"Bearer {create_token(app.state.db, 'phone', 'device')}"}
    data = tone(tmp_path / "m.wav", seconds)
    rid = client.post("/api/v1/recordings", json={"client_id": "client-meeting", "mime": "audio/wav",
                                                 "title": "Test meeting"}, headers=h).json()["recording_id"]
    client.put(f"/api/v1/recordings/{rid}/chunks/0", content=data, headers=h)
    mid = client.post(f"/api/v1/recordings/{rid}/finalize", json={"total_chunks": 1}, headers=h).json()["meeting_id"]
    app.state.worker.run_job(app.state.db.one("SELECT * FROM jobs WHERE meeting_id = ?", mid))
    return mid, h


def test_upload_resume_and_finalize(app, client, tmp_path):
    h = {"Authorization": f"Bearer {create_token(app.state.db, 'phone', 'device')}"}
    data = tone(tmp_path / "t.wav", 3)
    parts = [data[i:i + 20000] for i in range(0, len(data), 20000)]
    body = {"client_id": "client-0001", "mime": "audio/wav"}
    rid = client.post("/api/v1/recordings", json=body, headers=h).json()["recording_id"]
    assert client.post("/api/v1/recordings", json=body, headers=h).json()["recording_id"] == rid
    bad = client.put(f"/api/v1/recordings/{rid}/chunks/0", content=parts[0], headers={**h, "X-Chunk-SHA256": "0" * 64})
    assert bad.status_code == 422
    for i in reversed(range(1, len(parts))):
        assert client.put(f"/api/v1/recordings/{rid}/chunks/{i}", content=parts[i], headers=h).status_code == 200
    r = client.post(f"/api/v1/recordings/{rid}/finalize", json={"total_chunks": len(parts)}, headers=h)
    assert r.status_code == 409 and r.json()["missing"] == [0]
    sha = hashlib.sha256(parts[0]).hexdigest()
    assert client.put(f"/api/v1/recordings/{rid}/chunks/0", content=parts[0], headers={**h, "X-Chunk-SHA256": sha}).status_code == 200
    assert client.put(f"/api/v1/recordings/{rid}/chunks/0", content=parts[0], headers=h).json()["duplicate"]
    mid = client.post(f"/api/v1/recordings/{rid}/finalize", json={"total_chunks": len(parts)}, headers=h).json()["meeting_id"]
    assert app.state.store.original(mid).read_bytes() == data
    assert client.post(f"/api/v1/recordings/{rid}/finalize", json={"total_chunks": len(parts)}, headers=h).json()["meeting_id"] == mid
    other = {"Authorization": f"Bearer {create_token(app.state.db, 'other', 'device')}"}
    assert client.get(f"/api/v1/recordings/{rid}", headers=other).status_code == 404
    assert client.get(f"/api/v1/recordings/{rid}").status_code == 401


def test_pipeline_and_speaker_renames(app, client, tmp_path):
    mid, h = make_meeting(app, client, tmp_path)
    store = app.state.store
    meta = store.meta(mid)
    assert meta["state"] == "done" and meta["speakers"] == 2 and meta["summary_version"] == 1
    f = store.folder(mid)
    for name in ("audio.wav", "transcript.json", "transcript.md", "summary.md", "actions.json", "speakers.json"):
        assert (f / name).exists(), name
    assert client.put(f"/api/v1/meetings/{mid}/speakers", json={"names": {"Speaker 2": "Luis"}}, headers=h).status_code == 200
    summary_md = (f / "summary.md").read_text()
    assert "Luis sends the deck" in summary_md and "Speaker 2" not in summary_md
    item = json.loads((f / "actions.json").read_text())["items"][0]
    assert item["owner"] == "Luis" and item["owner_speaker"] == "Speaker 2"
    assert "**Luis**" in (f / "transcript.md").read_text()
    page = client.get(f"/m/{mid}", headers=h)
    assert page.status_code == 200 and "Luis" in page.text


class StubGitHub:
    def __init__(self):
        self.created, self.comments = [], []
        self._issues = [{"number": 7, "title": "Release deck for the beta", "body": "Prepare the updated deck",
                         "state": "open", "html_url": "https://gh.test/7"}]

    def available(self):
        return True

    def issues(self):
        return self._issues

    def find_marker(self, marker, issue_number=None):
        bodies = [b for n, b in self.comments if n == issue_number] if issue_number else [b for _, b in self.created]
        return "https://gh.test/found" if any(marker in b for b in bodies) else None

    def create_issue(self, title, body):
        self.created.append((title, body))
        return f"https://gh.test/{100 + len(self.created)}"

    def comment(self, number, body):
        self.comments.append((number, body))
        return f"https://gh.test/{number}#c{len(self.comments)}"


def test_followup_is_idempotent(app, client, tmp_path, monkeypatch):
    mid, _ = make_meeting(app, client, tmp_path)
    f = app.state.followup
    f.github = gh = StubGitHub()
    decisions = {"decisions": [
        {"item": 0, "action": "comment", "issue_number": 7, "title": "", "body": "Agreed: deck by Friday", "rationale": "same work"},
        {"item": 1, "action": "new_issue", "issue_number": 0, "title": "Check the open security issue", "body": "Look at it", "rationale": "new"}]}
    monkeypatch.setattr(fu, "chat_json", lambda *a, **k: (decisions, {}))
    f.propose()
    assert sorted(p["kind"] for p in f.list()) == ["drive_log", "github_comment", "github_issue"]
    assert all("transcript" not in json.dumps(p["payload"]).lower() for p in f.list())
    assert f.propose()["proposed"] == []
    for p in f.list(state="proposed"):
        f.decide(p["id"], approve=True)
    assert len(gh.created) == 1 and len(gh.comments) == 1
    assert "https://box.test/m/" in gh.created[0][1] and "indie-recorder meeting=" in gh.created[0][1]
    for p in f.list():
        f.execute(p["id"])
    assert len(gh.created) == 1 and len(gh.comments) == 1
    outbox = f.outbox()
    assert len(outbox) == 1 and outbox[0]["kind"] == "drive_log"
    assert f.ack(outbox[0]["id"], "https://docs.test/log")["state"] == "executed" and f.outbox() == []
    app.state.store.add_summary(mid, dict(SUMMARY), author="agent (test)")
    new = f.propose()["proposed"]
    assert [f.db.one("SELECT kind FROM proposals WHERE id = ?", pid)["kind"] for pid in new] == ["drive_log"]


def test_merge_windows_links_speakers():
    w1 = [{"w": f"x{i}", "s": float(i), "e": i + 0.5, "spk": "a" if i % 20 < 10 else "b"} for i in range(0, 110)]
    w2 = [{"w": f"x{i}", "s": i + 0.05, "e": i + 0.55, "spk": "q" if i % 20 < 10 else "p"} for i in range(90, 200)]
    words, links = merge_windows([(0, 110, w1), (90, 200, w2)])
    spk = {w["w"]: w["spk"] for w in words}
    assert spk["x5"] == spk["x125"] and spk["x15"] == spk["x135"] and spk["x5"] != spk["x15"]
    assert links[0]["linked"] == 2 and links[0]["new"] == 0
    assert len(words) == 200


def test_mcp_update_summary(app, client, tmp_path):
    mid, _ = make_meeting(app, client, tmp_path)
    h = {"Authorization": f"Bearer {create_token(app.state.db, 'agent', 'agent')}"}
    init = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18"}}, headers=h).json()
    assert init["result"]["serverInfo"]["name"] == "indie-recorder"
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "update_summary", "arguments": {
        "meeting_id": mid, "markdown": "## TL;DR\n\nBetter summary.", "note": "test"}}}
    res = client.post("/mcp", json=call, headers=h).json()["result"]
    assert not res["isError"] and json.loads(res["content"][0]["text"])["version"] == 2
    assert "version 2 (agent (agent))" in (app.state.store.folder(mid) / "summary.md").read_text()
    phone = {"Authorization": f"Bearer {create_token(app.state.db, 'phone2', 'device')}"}
    assert client.post("/mcp", json={}, headers=phone).status_code == 401
