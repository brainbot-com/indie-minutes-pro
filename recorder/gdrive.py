"""Google Drive for the meetings a person keeps there, put there by the recorder
itself. Each person connects their own Google account once (OAuth with PKCE,
scope drive.file: the files this app makes, nothing else in their Drive); from
then on a meeting set to Drive goes there as soon as it is done, with no
language model in between. The refresh token is kept in the index (SQLCipher,
encrypted at rest), access tokens only in memory. The OAuth client is the one
brainbot:agent command signs in to Google with (secrets/google_oauth.json)."""
import base64
import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from markdown_it import MarkdownIt

from .signing import sign_value, verify_value
from .util import now_iso, read_bytes

DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
SCOPES = "openid email " + DRIVE_FILE
FOLDER = "application/vnd.google-apps.folder"
DOC = "application/vnd.google-apps.document"
FIELDS = "id,name,mimeType,webViewLink,modifiedTime,md5Checksum"
SMALL = 5 * 1024 * 1024          # above this Google wants a resumable upload
CHUNK = 8 * 1024 * 1024          # resumable parts: a multiple of 256 KiB
OLDER = "Older versions"
TYPES = {".md": "text/markdown", ".txt": "text/plain", ".json": "application/json", ".m4a": "audio/mp4",
         ".aac": "audio/aac", ".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg"}
KEYS = ("google_refresh", "google_email", "google_since", "google_problem", "google_pending")


class GoogleError(Exception):
    """code says what happened (the pages say it in the person's language);
    detail is what Google answered, if anything."""

    def __init__(self, code, detail="", **info):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.info = code, detail, info


class Google:
    def __init__(self, settings, db, transport=None):
        self.s, self.db = settings, db
        self.http = httpx.Client(transport=transport, timeout=60)
        self._tokens = {}                # user id -> (access token, when it runs out)
        self._lock = threading.Lock()

    def client(self):
        """The box's OAuth client, or None while it has none."""
        try:
            c = json.loads(self.s.google_oauth_file.read_text())
        except (OSError, ValueError):
            return None
        return c if c.get("client_id") and c.get("client_secret") else None

    def available(self):
        return self.client() is not None

    # -- a person's connection -------------------------------------------------

    def account(self, user_id):
        """What the app shows: whether the box can, and the person did, connect."""
        get = (lambda k: self.db.user_setting(user_id, k) or None) if user_id else (lambda k: None)
        return {"available": self.available(), "connected": bool(get("google_refresh")) and self.available(),
                "email": get("google_email"), "since": get("google_since"), "problem": get("google_problem")}

    def connected(self, user_id):
        return bool(user_id) and bool(self.db.user_setting(user_id, "google_refresh")) and self.available()

    def start_link(self, user_id):
        """Where the person's browser starts: a link of their own for ten minutes."""
        if not self.available():
            raise GoogleError("unavailable")
        return f"{self.s.browser_url}/oauth/google/start?s={sign_value(self.s, 'google-start', user_id, 600)}"

    def authorize_url(self, start):
        user_id, c = verify_value(self.s, "google-start", start), self.client()
        if not c:
            raise GoogleError("unavailable")
        if not user_id:
            raise GoogleError("expired")
        nonce, verifier = secrets.token_urlsafe(16), secrets.token_urlsafe(48)
        # one sign-in at a time: a newer start replaces an older one
        self.db.set_user_setting(user_id, "google_pending", json.dumps({"nonce": nonce, "verifier": verifier}))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": c["client_id"], "redirect_uri": self.s.google_redirect_uri, "response_type": "code",
                  "scope": SCOPES, "access_type": "offline", "prompt": "consent",
                  "state": sign_value(self.s, "google-state", [user_id, nonce], 600),
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        if self._email(user_id):
            params["login_hint"] = self._email(user_id)
        return self.s.google_auth_url + "?" + urlencode(params)

    def finish(self, state, code):
        """Google sent the person back: swap the code for tokens and keep the
        refresh token, if the account is the person's own."""
        got = verify_value(self.s, "google-state", state)
        if not got or not code:
            raise GoogleError("expired")
        user_id, nonce = got
        pending = json.loads(self.db.user_setting(user_id, "google_pending") or "{}")
        if pending.get("nonce") != nonce:
            raise GoogleError("replaced")
        self._clear(user_id, "google_pending")                  # a sign-in counts once
        c = self.client()
        if not c:
            raise GoogleError("unavailable")
        r = self.http.post(self.s.google_token_url, data={
            "code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
            "redirect_uri": self.s.google_redirect_uri, "grant_type": "authorization_code",
            "code_verifier": pending["verifier"]})
        if r.status_code != 200:
            raise GoogleError("google", _why(r))
        t = r.json()
        refresh = t.get("refresh_token")
        if DRIVE_FILE not in (t.get("scope") or "").split():
            self._revoke(refresh or t.get("access_token"))
            raise GoogleError("no_drive")
        email, known = (_claims(t.get("id_token")).get("email") or "").lower(), self._email(user_id)
        # A recorder account with an address takes that Google account only: a
        # start link that got around ends in nobody else's Drive.
        if known and email != known:
            self._revoke(refresh or t.get("access_token"))
            raise GoogleError("wrong_account", got=email, want=known)
        if not refresh:
            raise GoogleError("google", "no refresh token")
        for k, v in (("google_refresh", refresh), ("google_email", email), ("google_since", now_iso())):
            self.db.set_user_setting(user_id, k, v)
        self._clear(user_id, "google_problem")
        with self._lock:
            self._tokens[user_id] = (t["access_token"], time.time() + int(t.get("expires_in", 3600)) - 60)
        return {"user_id": user_id, "email": email}

    def disconnect(self, user_id):
        refresh = self.db.user_setting(user_id, "google_refresh")
        if refresh:
            self._revoke(refresh)
        self._clear(user_id, *KEYS)
        with self._lock:
            self._tokens.pop(user_id, None)
        return self.account(user_id)

    def token(self, user_id):
        with self._lock:
            have = self._tokens.get(user_id)
        if have and have[1] > time.time():
            return have[0]
        refresh, c = self.db.user_setting(user_id, "google_refresh"), self.client()
        if not refresh or not c:
            raise GoogleError("not_connected")
        r = self.http.post(self.s.google_token_url, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                                           "refresh_token": refresh, "grant_type": "refresh_token"})
        if r.status_code in (400, 401) and _code(r) in ("invalid_grant", "unauthorized_client"):
            # revoked at Google or run out: the person connects again, and until
            # then what is set to Drive waits
            self._clear(user_id, "google_refresh")
            self.db.set_user_setting(user_id, "google_problem", "expired")
            raise GoogleError("not_connected", _why(r))
        if r.status_code != 200:
            raise GoogleError("google", _why(r))
        t = r.json()
        with self._lock:
            self._tokens[user_id] = (t["access_token"], time.time() + int(t.get("expires_in", 3600)) - 60)
        return t["access_token"]

    def _email(self, user_id):
        return ((self.db.one("SELECT email FROM users WHERE id = ?", user_id) or {}).get("email") or "").lower()

    def _clear(self, user_id, *keys):
        for k in keys:
            self.db.run("DELETE FROM user_settings WHERE user_id = ? AND key = ?", user_id, k)

    def _revoke(self, token):
        if token:
            try:
                self.http.post(self.s.google_revoke_url, data={"token": token}, timeout=15)
            except httpx.HTTPError:
                pass                     # gone here either way; Google drops unused tokens itself

    # -- Drive -----------------------------------------------------------------

    def _call(self, user_id, method, url, headers=None, timeout=60, **kw):
        for again in (True, False):
            r = self.http.request(method, url, timeout=timeout,
                                  headers={**(headers or {}), "Authorization": "Bearer " + self.token(user_id)}, **kw)
            if r.status_code == 401 and again:          # Google stopped taking that access token: a new one, once
                with self._lock:
                    self._tokens.pop(user_id, None)
                continue
            if r.status_code >= 400:
                raise GoogleError("google", _why(r))
            return r

    def find(self, user_id, name, parent, kind=None):
        q = f"name = '{_q(name)}' and '{_q(parent)}' in parents and trashed = false"
        if kind:
            q += f" and mimeType = '{kind}'"
        r = self._call(user_id, "GET", self.s.google_api_url + "/drive/v3/files",
                       params={"q": q, "fields": f"files({FIELDS})", "pageSize": "10", "orderBy": "modifiedTime desc"})
        files = r.json().get("files", [])
        return files[0] if files else None

    def folder(self, user_id, name, parent="root"):
        """The folder of that name, made the first time. drive.file shows the
        recorder only what it made itself, so it keeps folders of its own."""
        got = self.find(user_id, name, parent, FOLDER)
        if got:
            return got
        return self._call(user_id, "POST", self.s.google_api_url + "/drive/v3/files", params={"fields": FIELDS},
                          json={"name": name, "mimeType": FOLDER, "parents": [parent]}).json()

    def upload(self, user_id, name, data, mime, parent=None, file_id=None, as_type=None):
        """Bytes into Drive: a new file in parent or, with file_id, that file's new
        content. as_type is what Google makes of them (a Google Doc from HTML)."""
        meta = {} if file_id else {"name": name, "parents": [parent], **({"mimeType": as_type} if as_type else {})}
        url = self.s.google_api_url + "/upload/drive/v3/files" + (f"/{file_id}" if file_id else "")
        method = "PATCH" if file_id else "POST"
        if len(data) <= SMALL:
            b = "indie_recorder_" + secrets.token_hex(8)
            body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                    f"--{b}\r\nContent-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{b}--".encode()
            return self._call(user_id, method, url, params={"uploadType": "multipart", "fields": FIELDS}, content=body,
                              headers={"Content-Type": f"multipart/related; boundary={b}"}, timeout=120).json()
        start = self._call(user_id, method, url, params={"uploadType": "resumable", "fields": FIELDS}, json=meta,
                           headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(len(data))})
        session = start.headers.get("location")
        if not session:
            raise GoogleError("google", "no upload address")
        sent = 0
        for _ in range(len(data) // CHUNK + 8):
            part = data[sent:sent + CHUNK]
            r = self.http.put(session, content=part, timeout=300,
                              headers={"Content-Range": f"bytes {sent}-{sent + len(part) - 1}/{len(data)}"})
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code != 308:
                raise GoogleError("google", _why(r))
            have = r.headers.get("range")             # "bytes=0-8388607": what Google has so far
            sent = int(have.rsplit("-", 1)[1]) + 1 if have else 0
        raise GoogleError("google", "upload did not finish")

    def move(self, user_id, file_id, to, frm, name=None):
        self._call(user_id, "PATCH", f"{self.s.google_api_url}/drive/v3/files/{file_id}",
                   params={"addParents": to, "removeParents": frm, "fields": FIELDS}, json={"name": name} if name else {})

    def store(self, user_id, folder, subfolder, files):
        """A meeting's files into folder/subfolder. A file of that name already
        there with other content moves into "Older versions" first; the same
        content is left as it is, so a store that broke off can run again.
        Returns the subfolder's link."""
        sub = self.folder(user_id, subfolder, self.folder(user_id, folder)["id"])
        older = None
        for name, path in files:
            data = read_bytes(path)
            old = self.find(user_id, name, sub["id"])
            if old and old.get("md5Checksum") == hashlib.md5(data).hexdigest():
                continue
            if old:
                older = older or self.folder(user_id, OLDER, sub["id"])
                stamp = (old.get("modifiedTime") or "")[:16].replace("T", " ").replace(":", "-")
                self.move(user_id, old["id"], older["id"], sub["id"], f"{stamp} {name}".strip())
            self.upload(user_id, name, data, TYPES.get(Path(name).suffix.lower(), "application/octet-stream"),
                        parent=sub["id"])
        return sub.get("webViewLink") or f"https://drive.google.com/drive/folders/{sub['id']}"

    def write_doc(self, user_id, folder, name, markdown):
        """The Google Doc of that name in folder, written whole from Markdown."""
        root = self.folder(user_id, folder)
        html = ("<html><head><meta charset=\"utf-8\"></head><body>"
                + MarkdownIt("commonmark", {"html": False}).render(markdown) + "</body></html>").encode()
        doc = self.find(user_id, name, root["id"], DOC)
        got = (self.upload(user_id, name, html, "text/html; charset=utf-8", file_id=doc["id"]) if doc else
               self.upload(user_id, name, html, "text/html; charset=utf-8", parent=root["id"], as_type=DOC))
        return got.get("webViewLink") or f"https://docs.google.com/document/d/{got['id']}/edit"


def _q(text):
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


def _code(r):
    try:
        e = r.json().get("error")
    except ValueError:
        return ""
    return e if isinstance(e, str) else (e or {}).get("status", "")


def _why(r):
    try:
        j = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    e = j.get("error")
    if isinstance(e, dict):
        return f"{e.get('code', r.status_code)} {e.get('message', '')}".strip()
    return f"{r.status_code} {e or ''} {j.get('error_description', '')}".strip()


def _claims(id_token):
    """The id token's claims. It came straight from Google's token endpoint over
    TLS, which is enough to trust it there (OpenID Connect Core 3.1.3.7)."""
    try:
        body = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}
