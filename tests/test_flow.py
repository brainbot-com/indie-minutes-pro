"""Tests without GPU or LLM: upload resume, the pipeline with the fake engine,
speaker renames, follow-up idempotency, window merging and MCP.

    docker compose run --rm -T --no-deps recorder python -m pytest -q tests"""
import base64
import hashlib
import json
import os
import re
import subprocess
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from recorder import followup as fu
from recorder import worker as wk
from recorder.api import create_app, create_token
from recorder.config import Settings
from recorder.util import read_bytes, read_text
from recorder import vault
from recorder.engines.base import merge_windows

SUMMARY = {"title": "Release planning", "tldr": "Speaker 2 sends the deck.", "decisions": ["Ship the beta next week"],
           "action_items": [{"text": "Send the updated deck", "owner": "Speaker 2", "due": "Friday", "at": "00:00:10"},
                            {"text": "Check the open security issue", "owner": "Speaker 1", "due": "", "at": ""}],
           "open_questions": [], "language": "en", "model": "stub", "llm_stats": []}


@pytest.fixture
def executor():
    return "direct"


def make_keys(tmp_path):
    """What deploy/make-keys.sh makes on the box."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    (tmp_path / "data.key").write_text(vault.b64u(os.urandom(32)))
    (tmp_path / "seal.pem").write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return {"data_key_file": tmp_path / "data.key", "seal_key_file": tmp_path / "seal.pem", "scratch_dir": tmp_path / "scratch"}


@pytest.fixture
def app(tmp_path, monkeypatch, executor):
    s = Settings(data_dir=tmp_path / "data", worker="0", engine="fake", secure_cookies="0",
                 public_url="https://box.test", github_token_file=tmp_path / "no-token", followup_on_done="0",
                 followup_executor=executor, drive_api_url="", followup_targets="drive,github",
                 storage_default="drive", files_base_url="https://box.test/api/v1/files", **make_keys(tmp_path))
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


def make_meeting(app, client, tmp_path, seconds=20, headers=None):
    h = headers or {"Authorization": f"Bearer {create_token(app.state.db, 'phone', 'device')}"}
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
    original = app.state.store.original(mid)
    assert original.read_bytes().startswith(vault.MAGIC) and read_bytes(original) == data
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
    summary_md = read_text(f / "summary.md")
    assert "Luis sends the deck" in summary_md and "Speaker 2" not in summary_md
    item = json.loads(read_text(f / "actions.json"))["items"][0]
    assert item["owner"] == "Luis" and item["owner_speaker"] == "Speaker 2"
    assert "**Luis**" in read_text(f / "transcript.md")
    page = client.get(f"/m/{mid}", headers=h)
    assert page.status_code == 200 and "Luis" in page.text
    home = client.get("/", headers=h)
    assert home.status_code == 200 and f'href="/m/{mid}"' in home.text and 'class="row mrow"' in home.text


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
    assert "version 2 (agent (agent))" in read_text(app.state.store.folder(mid) / "summary.md")
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


def redeem(client, url_or_code, device="Pixel 8"):
    """What a phone does with a connect code: look, then spend it."""
    code = url_or_code.split("#")[-1]
    return client.post("/api/v1/pair", json={"code": code, "device": device})


def test_devices_page_shows_one_connect_code(app, client, tmp_path):
    der = bytes(range(40))
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n" + base64.b64encode(der).decode() + "\n-----END CERTIFICATE-----\n")
    app.state.settings.ca_cert_file = ca
    app.state.settings.apk_file = tmp_path / "missing.apk"
    admin = create_token(app.state.db, "admin", "admin")
    client.post("/login", data={"token": admin, "next": "/devices"})
    page = client.post("/devices/connect", data={"user_id": ""}).text
    assert page.count("<svg") == 1 and "indierecorder://" not in page and "irt_" not in page
    assert re.search(r'<svg viewBox="0 0 \d+ \d+"', page)   # scales with the page and stays square
    url = re.search(r'href="(https://box\.test/connect#[A-Z0-9]{8})"', page).group(1)
    phone = redeem(client, url).json()
    assert phone["box_url"] == "https://box.test" and phone["device"] == "Pixel 8"
    assert base64.urlsafe_b64decode(phone["ca"] + "=" * (-len(phone["ca"]) % 4)) == der
    assert client.get("/api/v1/settings", headers={"Authorization": f"Bearer {phone['token']}"}).status_code == 200
    agent = client.post("/devices", data={"name": "Meeting agent"}).text
    assert re.search(r"irt_[A-Za-z0-9_-]{20,}", agent)
    assert client.get("/connect").status_code == 200


def test_connect_codes_work_once_and_expire(app, client):
    from recorder.connect import Codes
    from recorder.people import add_person
    db = app.state.db
    ana = add_person(db, ext_id="ext-ana-code", name="Ana")
    codes = Codes(db)
    first, _ = codes.create(ana["id"])
    code, _ = codes.create(ana["id"])
    assert client.get(f"/api/v1/pair/{first}").status_code == 404   # the newer code replaced it
    peek = client.get(f"/api/v1/pair/{code[:4]}-{code[4:].lower()}").json()
    assert peek["person"] == "Ana" and peek["box"] == "aibox1" and peek["webapp"] == "https://box.test/app/"
    phone = redeem(client, code, device="  Ana's   Pixel ").json()
    assert phone["person"] == "Ana" and phone["device"] == "Ana's Pixel"
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {phone['token']}"}).json()
    assert me == {"device": "Ana's Pixel", "role": "device", "person": "Ana"}
    assert redeem(client, code).status_code == 404 and client.get(f"/api/v1/pair/{code}").status_code == 404
    late, _ = codes.create(ana["id"])
    db.run("UPDATE pair_codes SET expires_at = '2000-01-01T00:00:00Z'")
    assert redeem(client, late).status_code == 404
    for _ in range(30):
        assert client.get("/api/v1/pair/ABCDEFGH").status_code in (404, 429)
    fresh, _ = codes.create(ana["id"])
    assert client.get(f"/api/v1/pair/{fresh}").status_code == 429   # too many wrong codes: nobody for a while


def upload_tone(client, tmp_path, headers, client_id, seconds=5):
    data = tone(tmp_path / f"{client_id}.wav", seconds)
    rid = client.post("/api/v1/recordings", json={"client_id": client_id, "mime": "audio/wav"},
                      headers=headers).json()["recording_id"]
    client.put(f"/api/v1/recordings/{rid}/chunks/0", content=data, headers=headers)
    return client.post(f"/api/v1/recordings/{rid}/finalize", json={"total_chunks": 1}, headers=headers).json()["meeting_id"]


def test_people_see_only_their_own_meetings(app, client, tmp_path):
    from recorder.people import add_person
    db = app.state.db
    ana, ben = add_person(db, ext_id="ext-ana"), add_person(db, ext_id="ext-ben")
    ana_phone = {"Authorization": f"Bearer {create_token(db, 'ana phone', 'device', user_id=ana['id'])}"}
    ben_phone = {"Authorization": f"Bearer {create_token(db, 'ben phone', 'device', user_id=ben['id'])}"}
    mid = upload_tone(client, tmp_path, ana_phone, "ana-meeting-1")
    assert db.one("SELECT owner_id FROM meetings WHERE id = ?", mid)["owner_id"] == ana["id"]
    assert [m["id"] for m in client.get("/api/v1/meetings", headers=ana_phone).json()] == [mid]
    assert client.get("/api/v1/meetings", headers=ben_phone).json() == []
    assert client.get(f"/api/v1/meetings/{mid}", headers=ben_phone).status_code == 404
    assert client.put(f"/api/v1/meetings/{mid}/storage", json={"storage": "local"}, headers=ben_phone).status_code == 404
    admin = {"Authorization": f"Bearer {create_token(db, 'admin', 'admin')}"}
    assert mid in [m["id"] for m in client.get("/api/v1/meetings", headers=admin).json()]
    shared = {"Authorization": f"Bearer {create_token(db, 'sim', 'device')}"}   # a token without a person
    assert mid not in [m["id"] for m in client.get("/api/v1/meetings", headers=shared).json()]


def test_brainbot_connector_speaks_for_the_person(app, client, tmp_path):
    db = app.state.db
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n" + base64.b64encode(bytes(range(40))).decode() + "\n-----END CERTIFICATE-----\n")
    app.state.settings.ca_cert_file = ca
    service = create_token(db, "brainbot:agent command", "agent", trusts_identity=True)

    def call(op, args=None, user="ext-ana", caller="user"):
        h = {"Authorization": f"Bearer {service}", "X-Indie-User": user, "X-Indie-Caller": caller}
        return client.post("/api/v1/indiechat", json={"op": op, "args": args or {}}, headers=h)

    assert call("whoami").json()["result"]["meetings"] == 0
    offer = call("pair_phone").json()["result"]
    assert offer["url"].startswith("https://box.test/connect#") and "<svg" in offer["qr"] and len(offer["code"]) == 9
    phone = {"Authorization": "Bearer " + redeem(client, offer["url"], device="Ana's Pixel").json()["token"]}
    mid = upload_tone(client, tmp_path, phone, "ana-meeting-2")
    assert [m["id"] for m in call("list_meetings").json()["result"]] == [mid]
    assert call("list_meetings", user="ext-ben").json()["result"] == []
    assert call("get_meeting", {"meeting_id": mid}, user="ext-ben").status_code == 400
    assert call("set_storage", {"meeting_id": mid, "storage": "local"}).json()["result"]["storage"] == "local"
    # Ana's agents read her meetings, but only she pairs or removes phones
    assert call("list_meetings", caller="agent").json()["result"][0]["id"] == mid
    assert call("pair_phone", caller="agent").status_code == 400
    phones = call("phones").json()["result"]
    assert [p["name"] for p in phones] == ["Ana's Pixel"]
    assert call("remove_phone", {"id": phones[0]["id"]}, user="ext-ben").status_code == 400
    assert call("remove_phone", {"id": phones[0]["id"]}).json()["result"]["removed"] == phones[0]["id"]
    assert client.get("/api/v1/meetings", headers=phone).status_code == 401
    # settings are per person
    assert call("update_settings", {"storage_default": "local"}).json()["result"]["storage_default"] == "local"
    assert call("get_settings", user="ext-ben").json()["result"]["storage_default"] == "drive"
    # without the service token, or without a person, nothing
    plain = create_token(db, "agent", "agent")
    assert client.post("/api/v1/indiechat", json={"op": "whoami"},
                       headers={"Authorization": f"Bearer {plain}", "X-Indie-User": "ext-ana"}).status_code == 401
    assert client.post("/api/v1/indiechat", json={"op": "whoami"},
                       headers={"Authorization": f"Bearer {service}"}).status_code == 401


def test_me_names_the_person_a_phone_records_for(app, client):
    from recorder.people import add_person
    db = app.state.db
    ana = add_person(db, ext_id="ext-ana-me", email="ana@example.com", name="Ana")
    raw = create_token(db, "Ana's phone", "device", user_id=ana["id"])
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).json()
    assert me == {"device": "Ana's phone", "role": "device", "person": "Ana"}
    loose = create_token(db, "box phone", "device")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {loose}"}).json()["person"] is None
    # a person who came in through the connector has only an id until they pick a name
    service = create_token(db, "brainbot:agent command", "agent", trusts_identity=True)
    hdr = {"Authorization": f"Bearer {service}", "X-Indie-User": "ext-ben-me", "X-Indie-Caller": "user"}
    offer = client.post("/api/v1/indiechat", json={"op": "pair_phone"}, headers=hdr).json()["result"]
    phone = {"Authorization": "Bearer " + redeem(client, offer["url"], device="Ben's phone").json()["token"]}
    assert client.get("/api/v1/me", headers=phone).json()["person"] is None
    agent = dict(hdr, **{"X-Indie-Caller": "agent"})
    assert client.post("/api/v1/indiechat", json={"op": "set_name", "args": {"name": "Ben"}}, headers=agent).status_code == 400
    assert client.post("/api/v1/indiechat", json={"op": "set_name", "args": {"name": " Ben "}}, headers=hdr).json()["result"] == {"name": "Ben"}
    assert client.get("/api/v1/me", headers=phone).json()["person"] == "Ben"
    assert client.post("/api/v1/indiechat", json={"op": "whoami"}, headers=hdr).json()["result"]["name"] == "Ben"


def test_meetings_are_encrypted_at_rest(app, client, tmp_path):
    mid, h = make_meeting(app, client, tmp_path)
    s = app.state.settings
    files = [f for f in (s.meetings_dir / mid).rglob("*") if f.is_file()]
    assert files and all(f.read_bytes().startswith(vault.MAGIC) for f in files), [f.name for f in files]
    assert not any(b"deck" in f.read_bytes() for f in files)                 # no summary text in the clear
    assert not s.db_path.read_bytes().startswith(b"SQLite format 3")          # the index is SQLCipher
    assert not list(s.scratch_dir.iterdir())                                 # no plain audio left behind
    wav = read_bytes(app.state.store.audio(mid))
    r = client.get(f"/m/{mid}/audio", headers={**h, "Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == wav[100:200] and r.headers["content-range"] == f"bytes 100-199/{len(wav)}"
    assert client.get(f"/m/{mid}/audio", headers=h).content == wav
    assert "deck" in client.get(f"/m/{mid}/files/summary.md", headers=h).text


def test_phones_seal_their_chunks(app, client, tmp_path):
    h = {"Authorization": f"Bearer {create_token(app.state.db, 'phone', 'device')}"}
    seal = client.get("/api/v1/health").json()["seal"]
    assert seal["alg"] == vault.SEAL_ALG
    data = tone(tmp_path / "s.wav", 3)
    parts = [data[i:i + 30000] for i in range(0, len(data), 30000)]
    cid = "sealed-0001"
    epk, key = vault.phone_side(seal["key"], cid)
    bad = {"client_id": "sealed-0002", "mime": "audio/wav", "seal": {"kid": "nope", "epk": epk}}
    assert client.post("/api/v1/recordings", json=bad, headers=h).status_code == 409
    body = {"client_id": cid, "mime": "audio/wav", "seal": {"kid": seal["kid"], "epk": epk}}
    rid = client.post("/api/v1/recordings", json=body, headers=h).json()["recording_id"]
    for i, part in enumerate(parts):
        sealed = vault.seal_chunk(key, cid, i, part)
        assert part[:200] not in sealed
        assert client.put(f"/api/v1/recordings/{rid}/chunks/{i}", content=sealed, headers=h).status_code == 200
    mid = client.post(f"/api/v1/recordings/{rid}/finalize", json={"total_chunks": len(parts)}, headers=h).json()["meeting_id"]
    assert read_bytes(app.state.store.original(mid)) == data
    # a chunk changed on the way does not open, and the recording waits for a good one
    cid2 = "sealed-0003"
    epk2, key2 = vault.phone_side(seal["key"], cid2)
    rid2 = client.post("/api/v1/recordings", json={"client_id": cid2, "mime": "audio/wav",
                                                   "seal": {"kid": seal["kid"], "epk": epk2}}, headers=h).json()["recording_id"]
    forged = bytearray(vault.seal_chunk(key2, cid2, 0, parts[0]))
    forged[5] ^= 1
    client.put(f"/api/v1/recordings/{rid2}/chunks/0", content=bytes(forged), headers=h)
    r = client.post(f"/api/v1/recordings/{rid2}/finalize", json={"total_chunks": 1}, headers=h)
    assert r.status_code == 422 and "does not open" in r.json()["detail"]
    assert client.get(f"/api/v1/recordings/{rid2}", headers=h).json()["state"] == "uploading"


def test_a_plain_index_is_encrypted_once(tmp_path):
    import sqlite3
    from recorder.db import open_index
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "index.sqlite")
    con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, ext_id TEXT UNIQUE, email TEXT, name TEXT, created_at TEXT NOT NULL, last_seen_at TEXT)")
    con.execute("INSERT INTO users (id, ext_id, email, name, created_at) VALUES ('usr_1', 'ext', 'a@b.c', 'Ana', 'now')")
    con.commit()
    con.close()
    s = Settings(data_dir=data, **make_keys(tmp_path))
    db = open_index(s)
    assert not (data / "index.sqlite").read_bytes().startswith(b"SQLite format 3")
    assert db.one("SELECT name FROM users WHERE id = 'usr_1'")["name"] == "Ana"
    assert open_index(s).one("SELECT COUNT(*) AS n FROM users")["n"] == 1     # a second open leaves it as it is


def test_encrypt_and_back(app, client, tmp_path, monkeypatch):
    """recorder.cli encrypt seals older plain files; decrypt is the way back."""
    from recorder import cli
    mid, h = make_meeting(app, client, tmp_path)
    s = app.state.settings
    summary = s.meetings_dir / mid / "summary.md"
    text = read_text(summary)
    keys = {k: str(v) for k, v in make_keys_like(s).items()}
    env = {"DATA_DIR": str(s.data_dir), "DATA_KEY_FILE": keys["data_key_file"], "SEAL_KEY_FILE": keys["seal_key_file"],
           "SCRATCH_DIR": str(s.scratch_dir)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    summary.write_text(text)                                   # as written before encryption at rest
    cli.main(["encrypt"])
    assert summary.read_bytes().startswith(vault.MAGIC) and read_text(summary) == text
    cli.main(["decrypt"])
    assert summary.read_text() == text and s.db_path.read_bytes().startswith(b"SQLite format 3")
    assert not (s.data_dir / ".vault").exists()


def make_keys_like(s):
    return {"data_key_file": s.data_key_file, "seal_key_file": s.seal_key_file}


def test_a_meeting_agent_gets_the_skill_and_the_files(app, client, tmp_path):
    """What a person's agent in brainbot:agent command does through the connector."""
    db = app.state.db
    service = create_token(db, "brainbot:agent command", "agent", trusts_identity=True)

    def call(op, args=None, user="ext-ana-agent", caller="agent"):
        h = {"Authorization": f"Bearer {service}", "X-Indie-User": user, "X-Indie-Caller": caller}
        return client.post("/api/v1/indiechat", json={"op": op, "args": args or {}}, headers=h)

    setup = call("agent_setup").json()["result"]
    assert "## Set up" in setup["skill"] and "routine_create" in setup["skill"]
    assert setup["each_run"].startswith("1.") and setup["routine"]["quiet"] == "[Nothing new]"
    # a meeting of Ana's, recorded with her phone
    offer = call("pair_phone", caller="user").json()["result"]
    phone = {"Authorization": "Bearer " + redeem(client, offer["url"]).json()["token"]}
    mid, _ = make_meeting(app, client, tmp_path, headers=phone)
    files = call("meeting_files", {"meeting_id": mid}).json()["result"]["files"]
    link = next(f for f in files if f["name"] == "Transcript.md")
    got = client.get(link["url"].replace("https://box.test", ""))
    assert got.status_code == 200 and got.content.decode() == read_text(app.state.store.folder(mid) / "transcript.md")
    assert link["bytes"] == len(got.content)                              # the plain size, not the encrypted one
    assert call("meeting_files", {"meeting_id": mid}, user="ext-ben-agent").status_code == 400
