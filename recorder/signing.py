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


def sign(settings, meeting_id, name, ttl=7200):
    body = _b64(json.dumps({"m": meeting_id, "f": name, "e": int(time.time()) + ttl}).encode())
    mac = _b64(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify(settings, token):
    """Return (meeting_id, name) for a valid, unexpired token, else None."""
    try:
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
