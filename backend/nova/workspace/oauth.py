"""One-time OAuth sign-in state with PKCE, shared by Google and Microsoft.

The state sent to the provider is random; only its SHA-256 is stored, together with the user it belongs to, the
provider, an expiry and the (encrypted) PKCE code_verifier. A callback is accepted once, before expiry, and only for
the signed-in Nova user who started it, so nobody can attach their account to yours (or yours to theirs).
"""
import base64
import hashlib
import secrets

from sqlalchemy import delete, insert, select

from .. import db, security

STATE_MINUTES = 10


class StateError(Exception):
    pass


def _h(state):
    return hashlib.sha256((state or "").encode()).hexdigest()


def begin(uid, provider):
    """Returns (state, code_challenge) for the authorization URL."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = db.now_us()
    with db.engine.begin() as c:
        c.execute(delete(db.oauth_states).where(db.oauth_states.c.expires_at < now))
        c.execute(insert(db.oauth_states).values(id=_h(state), user_id=uid, provider=provider,
                                                 verifier=security.encrypt(verifier),
                                                 expires_at=now + STATE_MINUTES * 60 * 10**6))
    return state, challenge


def finish(state, uid, provider):
    """Use the state once. Returns the PKCE code_verifier or raises StateError."""
    with db.engine.begin() as c:
        row = c.execute(select(db.oauth_states).where(db.oauth_states.c.id == _h(state))).first()
        if row:
            c.execute(delete(db.oauth_states).where(db.oauth_states.c.id == row.id))  # single use, even on failure
    if not row or row.provider != provider:
        raise StateError("Invalid or already used sign-in response. Try connecting again.")
    if row.expires_at < db.now_us():
        raise StateError("The sign-in took too long. Try connecting again.")
    if row.user_id != uid:
        raise StateError("This sign-in was started by a different Nova account.")
    return security.decrypt(row.verifier)
