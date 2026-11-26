"""People: one account per person, taken from brainbot:agent command's own logins.

A token belongs to a person (their phone, their agent) or to nobody (the
simulator, tests). The service token brainbot:agent command calls with stands
for whoever its X-Indie-User header names, and only a token marked
trusts_identity may do that. A meeting belongs to the person whose phone
recorded it, and everyone sees only their own; admin tokens see everything; a
token without a person sees the meetings without one.

On an Indie Box the owner may invite a team: members sign in with their own
password and read the meetings the owner shares with the team; recording and
managing stays with the owner."""
from .util import new_id, now_iso


class Who:
    def __init__(self, token, user_id=None, admin=False, caller="user", team_owner=None):
        self.token = token
        self.user_id = user_id
        self.admin = admin
        self.caller = caller          # user, agent or service (brainbot:agent command's X-Indie-Caller)
        self.team_owner = team_owner  # a team member: whose shared meetings they read

    @property
    def member(self):
        return self.team_owner is not None

    @property
    def name(self):
        return self.token["name"]

    def sees(self, meeting):
        if not meeting:
            return False
        if self.member:
            return bool(meeting.get("shared")) and meeting.get("owner_id") == self.team_owner
        return self.admin or (meeting.get("owner_id") or None) == self.user_id


def resolve(db, token, headers):
    """Who a request speaks for, or None when it may not say (no token, or the
    service token without a person)."""
    if not token:
        return None
    if token["role"] == "admin":
        return Who(token, admin=True)
    if token.get("trusts_identity"):
        ext = (headers.get("x-indie-user") or "").strip()[:120]
        if not ext:
            return None
        caller = (headers.get("x-indie-caller") or "user").strip().lower()
        return Who(token, person_for(db, ext)["id"], caller=caller if caller in ("user", "agent", "service") else "user")
    return Who(token, token.get("user_id"), team_owner=token.get("team_owner"))


def person_for(db, ext_id):
    """The account of a brainbot:agent command user, made on first sight."""
    row = db.one("SELECT * FROM users WHERE ext_id = ?", ext_id)
    if not row:
        db.run("INSERT OR IGNORE INTO users (id, ext_id, created_at) VALUES (?, ?, ?)", new_id("usr"), ext_id, now_iso())
        row = db.one("SELECT * FROM users WHERE ext_id = ?", ext_id)
    if (row["last_seen_at"] or "")[:13] != now_iso()[:13]:
        db.run("UPDATE users SET last_seen_at = ? WHERE id = ?", now_iso(), row["id"])
    return row


def add_person(db, *, ext_id=None, email=None, name=None):
    uid = new_id("usr")
    db.run("INSERT INTO users (id, ext_id, email, name, created_at) VALUES (?, ?, ?, ?, ?)",
           uid, ext_id, email, name, now_iso())
    return db.one("SELECT * FROM users WHERE id = ?", uid)


def label(person):
    return (person or {}).get("name") or (person or {}).get("email") or "someone"
