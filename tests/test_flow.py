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
def executor():
    return "direct"


@pytest.fixture
def app(tmp_path, monkeypatch, executor):
    s = Settings(data_dir=tmp_path / "data", worker="0", engine="fake", secure_cookies="0",
                 public_url="https://box.test", github_token_file=tmp_path / "no-token", followup_on_done="0",
                 followup_executor=executor, drive_api_url="", followup_targets="drive,github",
                 storage_default="drive", files_base_url="https://box.test/api/v1/files")
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
    assert sorted(p["kind"] for p in f.list()) == ["drive_log", "drive_store", "github_comment", "github_issue"]
    assert all("thanks for joining" not in json.dumps(p["payload"]).lower() for p in f.list())  # no transcript text
    assert f.propose()["proposed"] == []
    for p in f.list(state="proposed"):
        f.decide(p["id"], approve=True)
    assert len(gh.created) == 1 and len(gh.comments) == 1
    assert "https://box.test/m/" in gh.created[0][1] and "indie-recorder meeting=" in gh.created[0][1]
    for p in f.list():
        f.execute(p["id"])
    assert len(gh.created) == 1 and len(gh.comments) == 1
    outbox = f.outbox()
    assert sorted(p["kind"] for p in outbox) == ["drive_log", "drive_store"]
    for p in outbox:
        assert f.ack(p["id"], f"https://docs.test/{p['kind']}")["state"] == "executed"
    assert f.outbox() == [] and app.state.store.meta(mid)["drive_url"] == "https://docs.test/drive_store"
    app.state.store.add_summary(mid, dict(SUMMARY), author="agent (test)")
    new = f.propose()["proposed"]
    assert sorted(f.db.one("SELECT kind FROM proposals WHERE id = ?", pid)["kind"] for pid in new) == ["drive_log", "drive_store"]


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
    pending = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "pending_work", "arguments": {}}}
    assert client.post("/mcp", json=pending, headers=h).json()["result"]["content"][0]["text"] == ""


@pytest.mark.parametrize("executor", ["agent"])
def test_agent_mode_uses_outbox_and_synced_issues(app, client, tmp_path, monkeypatch):
    mid, _ = make_meeting(app, client, tmp_path)
    f = app.state.followup
    agent = {"Authorization": f"Bearer {create_token(app.state.db, 'cc-agent', 'agent')}"}
    seen = {}

    def decide(settings, system, user, schema, **kw):
        seen["candidates"] = [c["number"] for x in json.loads(user)["action_items"] for c in x["candidates"]]
        return {"decisions": [
            {"item": 0, "action": "comment", "issue_number": 7, "title": "", "body": "Deck by Friday", "rationale": "same"},
            {"item": 1, "action": "new_issue", "issue_number": 0, "title": "Security check", "body": "x", "rationale": "new"}]}, {}
    monkeypatch.setattr(fu, "chat_json", decide)
    issues = [{"number": 7, "title": "Release deck for the beta", "body": "Prepare the updated deck", "state": "open",
               "html_url": "https://github.test/7"}]
    r = client.put("/api/v1/issues-index", json={"repo": app.state.settings.github_repo, "issues": issues}, headers=agent)
    assert r.json()["issues"] == 1
    f.propose()
    assert seen["candidates"] == [7]
    for p in f.list(state="proposed"):
        assert f.decide(p["id"], approve=True)["outbox"]
    box = client.get("/api/v1/outbox", headers=agent).json()
    assert sorted(p["kind"] for p in box) == ["drive_log", "drive_store", "github_comment", "github_issue"]
    for p in box:
        assert client.post(f"/api/v1/outbox/{p['id']}/ack", json={"url": f"https://done.test/{p['kind']}"},
                           headers=agent).json()["state"] == "executed"
    assert client.get("/api/v1/outbox", headers=agent).json() == []
    assert f.propose()["proposed"] == []


def test_direct_mode_against_the_mock_server(app, client, tmp_path, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    monkeypatch.setenv("MOCK_STATE", str(tmp_path / "mock.json"))
    import importlib
    import mock.server as ms
    importlib.reload(ms)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ms.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    mid, _ = make_meeting(app, client, tmp_path)
    f = app.state.followup
    f.github.base, f.drive.base = base + "/github", base + "/drive"
    monkeypatch.setattr(fu, "chat_json", lambda *a, **k: ({"decisions": [
        {"item": 0, "action": "comment", "issue_number": 5, "title": "", "body": "Deck by Friday", "rationale": "same"},
        {"item": 1, "action": "new_issue", "issue_number": 0, "title": "Security check", "body": "x", "rationale": "new"}]}, {}))
    f.run()  # the Drive items are approved by the storage choice and run at once
    for p in f.list(state="proposed"):
        f.decide(p["id"], approve=True)
    assert sorted(p["state"] for p in f.list()) == ["executed"] * 4
    state = json.loads((tmp_path / "mock.json").read_text())
    folder = next(iter(state["folders"].values()))
    assert sorted(folder) == ["Action items.md", "Summary.md", "Transcript.md"]
    issues = state["repos"]["brainbot-com/indie-recorder-sandbox"]["issues"]
    assert issues[-1]["title"] == "Security check" and len(state["repos"]["brainbot-com/indie-recorder-sandbox"]["comments"]["5"]) == 1
    assert len(state["docs"]["indie-recorder test/Meeting log"]) == 1
    f.db.run("DELETE FROM mappings")  # a lost database must not cause duplicates
    f.db.run("UPDATE proposals SET state = 'approved'")
    for p in f.list():
        if not p["kind"].startswith("drive_"):
            f.execute(p["id"])
    state = json.loads((tmp_path / "mock.json").read_text())
    assert len(state["repos"]["brainbot-com/indie-recorder-sandbox"]["issues"]) == 9
    assert len(state["repos"]["brainbot-com/indie-recorder-sandbox"]["comments"]["5"]) == 1
    srv.shutdown()


def test_local_meetings_stay_local_until_switched(app, client, tmp_path, monkeypatch):
    agent = {"Authorization": f"Bearer {create_token(app.state.db, 'cc', 'agent')}"}
    assert client.put("/api/v1/settings", json={"storage_default": "local", "drive_folder": "Meetings"},
                      headers=agent).json()["storage_default"] == "local"
    mid, phone = make_meeting(app, client, tmp_path)
    f = app.state.followup
    monkeypatch.setattr(fu, "chat_json", lambda *a, **k: ({"decisions": []}, {}))
    f.run()
    assert app.state.store.meta(mid)["storage"] == "local" and f.list() == []
    # the person changes their mind: the meeting goes to Drive
    app.state.store.set_storage(mid, "drive")
    f.run()
    box = f.outbox()
    assert sorted(p["kind"] for p in box) == ["drive_log", "drive_store"]
    store_item = next(p for p in box if p["kind"] == "drive_store")
    assert store_item["payload"]["folder"] == "Meetings"
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "drive_files", "arguments": {"proposal_id": store_item["id"]}}}
    files = json.loads(client.post("/mcp", json=call, headers=agent).json()["result"]["content"][0]["text"])
    assert [x["name"] for x in files["files"]] == ["Summary.md", "Action items.md", "Transcript.md"]
    path = files["files"][2]["url"].replace("https://box.test", "")
    r = client.get(path)
    assert r.status_code == 200 and "Speaker" in r.text
    assert client.get(path[:-3] + "xyz").status_code == 403  # tampered link
