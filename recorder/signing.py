"""Short-lived signed links to one file of one meeting, so the Agent Command
Center agent can download it with curl (and upload it to Drive) without a
long-lived token and without the text passing through a language model."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import quote


def _key(settings):
    path = settings.data_dir / ".signing-key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return path.read_text().strip().encode()


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(settings, meeting_id, expires, name):
    msg = json.dumps(["file", meeting_id, expires, name]).encode()
    return _b64(hmac.new(_key(settings), msg, hashlib.sha256).digest()[:16])


def sign(settings, meeting_id, name, ttl=7200):
    """The path of one file's link: "<meeting>/<expiry>/<mac>/<name>". Meeting,
    expiry and name stay readable, so an agent that copies the link into curl
    has only the 22 characters of the MAC to get right (a 132-character base64
    link came back with a typo now and then)."""
    expires = int(time.time()) + ttl
    return f"{meeting_id}/{expires}/{_mac(settings, meeting_id, expires, name)}/{quote(name, safe='')}"


def verify(settings, token):
    """Return (meeting_id, name) for a valid, unexpired link path (decoded, as
    the server routes it), else None."""
    try:
        if "/" in token:
            meeting_id, expires, mac, name = token.split("/", 3)
            if not hmac.compare_digest(mac, _mac(settings, meeting_id, int(expires), name)) or int(expires) < time.time():
                return None
            return meeting_id, name
        # the older "<base64 json>.<mac>" form: links handed out before the readable
        # one, valid for their two hours
        body, mac = token.split(".", 1)
        good = _b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        data = json.loads(_unb64(body))
        if data["e"] < time.time():
            return None
        return data["m"], data["f"]
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def sign_value(settings, kind, value, ttl):
    """A short-lived signed token that carries a value (a person's id in a
    Google sign-in link, the state that comes back from Google)."""
    body = _b64(json.dumps([kind, value, int(time.time()) + ttl]).encode())
    return f"{body}.{_b64(hmac.new(_key(settings), b'value:' + body.encode(), hashlib.sha256).digest()[:16])}"


def verify_value(settings, kind, token):
    """The value of a valid, unexpired token of that kind, else None."""
    try:
        body, mac = str(token).split(".", 1)
        good = _b64(hmac.new(_key(settings), b"value:" + body.encode(), hashlib.sha256).digest()[:16])
        if not hmac.compare_digest(mac, good):
            return None
        k, value, expires = json.loads(_unb64(body))
        return value if k == kind and expires >= time.time() else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
