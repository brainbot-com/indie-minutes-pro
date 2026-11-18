"""Connecting a phone with a short code instead of a token in a QR code.

A person asks for a code in the Meetings app (an admin also on the Devices
page). The code opens the connect page, which hands it to the Android app or
the web app; the phone redeems it once, within CODE_MINUTES, for a token of
its own. The code carries no token, address or certificate: the phone gets
those when it redeems it, so a screenshot of the code is useless after that.
"""
import hashlib
import secrets
import threading
import time
from datetime import timedelta

from .util import now_iso, utcnow

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"     # no 0 and O, no 1, I and L
CODE_LEN = 8                                      # 31^8: about 40 bits
CODE_MINUTES = 15


def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def normalize(text):
    """What someone typed or a link carried: case, spaces and dashes do not matter."""
    return "".join(c for c in str(text or "").upper() if c in ALPHABET)


def shown(code):
    return f"{code[:4]}-{code[4:]}"


def _hash(code):
    return hashlib.sha256(("pair:" + code).encode()).hexdigest()


class Guesses:
    """Wrong codes from anyone, per 10 minutes. The box sees every phone through
    one proxy, so this counts globally: 30 misses close the door for a while,
    against about 10^12 codes."""

    def __init__(self, limit=30, window=600):
        self.limit, self.window, self.misses, self.lock = limit, window, [], threading.Lock()

    def blocked(self):
        with self.lock:
            cut = time.monotonic() - self.window
            self.misses = [t for t in self.misses if t > cut]
            return len(self.misses) >= self.limit

    def miss(self):
        with self.lock:
            self.misses.append(time.monotonic())


class Codes:
    def __init__(self, db, minutes=CODE_MINUTES):
        self.db, self.minutes = db, minutes

    def create(self, user_id):
        """A new code for the person; their older unused ones stop working."""
        now = now_iso()
        self.db.run("UPDATE pair_codes SET expires_at = ? WHERE user_id IS ? AND used_at IS NULL AND expires_at > ?",
                    now, user_id, now)
        code = new_code()
        expires = (utcnow() + timedelta(minutes=self.minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.run("INSERT INTO pair_codes (code_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    _hash(code), user_id, now, expires)
        return code, expires

    def peek(self, code):
        code = normalize(code)
        if len(code) != CODE_LEN:
            return None
        return self.db.one("SELECT * FROM pair_codes WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                           _hash(code), now_iso())

    def redeem(self, code, make_token, device):
        """Spends the code once; make_token(name, user_id) makes the phone's token."""
        code = normalize(code)
        if len(code) != CODE_LEN:
            return None, None
        with self.db.tx() as con:
            row = con.execute("SELECT * FROM pair_codes WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                              (_hash(code), now_iso())).fetchone()
            if not row:
                return None, None
            con.execute("UPDATE pair_codes SET used_at = ? WHERE code_hash = ?", (now_iso(), _hash(code)))
        raw, token_id = make_token(device, row["user_id"])
        self.db.run("UPDATE pair_codes SET token_id = ? WHERE code_hash = ?", token_id, _hash(code))
        return raw, dict(row)
