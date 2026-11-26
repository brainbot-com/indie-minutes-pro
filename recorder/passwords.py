"""Passwords of team members (the owner's comes from the install form): scrypt
from the standard library, with a salt each, compared in constant time."""
import base64
import hashlib
import hmac
import os

N, R, P = 2 ** 14, 8, 1          # about 16 MB and a few dozen ms per check
MIN_LENGTH = 10


def make(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return "scrypt${}${}${}${}${}".format(N, R, P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def check(password, stored):
    try:
        kind, n, r, p, salt, digest = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def problem(password):
    """Why a new password will not do, or None."""
    if len(password) < MIN_LENGTH:
        return f"at least {MIN_LENGTH} characters"
    if len(password) > 200:
        return "at most 200 characters"
    return None
