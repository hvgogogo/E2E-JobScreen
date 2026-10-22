"""Accounts, roles, password hashing, login sessions, rate limits and per-user storage.

Roles (users.json -> "role"):
    owner  the account of the person who runs this server. Everything an admin can do, plus
           application folders / Finder on this Mac and advanced (regex) scoring. Cannot be demoted,
           blocked or deleted from the web page.
    admin  manages accounts, registration, saved-search links, funding catalogue, sources on/off.
    user   normal account.
Nobody - not even the owner - can see another account's jobs, notes, to-dos or funding list.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import time

from .core import (LOCK, SESSIONS_PATH, USERS_DIR, USERS_PATH, load_json, now, save_json)

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
RESERVED = {"admin", "root", "system", "api", "static", "data", "null", "none", "guest", "test", "owner"}
ROLES = ("owner", "admin", "user")
PBKDF2_ITER = 200_000
SESSION_DAYS = 30
COOKIE = "js2_session"

SESSIONS: dict = {}
RATE: dict = {}


# ---------------------------------------------------------------- passwords
def hash_pw(pw: str, salt: str | None = None, it: int = PBKDF2_ITER) -> dict:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), it).hex()
    return {"algo": "pbkdf2_sha256", "iter": it, "salt": salt, "hash": h}


def check_pw(pw: str, rec: dict) -> bool:
    try:
        h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(rec["salt"]), int(rec["iter"])).hex()
        return hmac.compare_digest(h, rec["hash"])
    except Exception:
        return False


DUMMY_PW = hash_pw("dummy-password-for-timing")


def temp_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


# ---------------------------------------------------------------- users
def load_users() -> dict:
    return load_json(USERS_PATH, {})


def save_users(u):
    save_json(USERS_PATH, u)


def role_of(user: str | None, users: dict | None = None) -> str:
    if not user:
        return ""
    rec = (users if users is not None else load_users()).get(user, {})
    r = rec.get("role")
    if r in ROLES:
        return r
    return "admin" if rec.get("is_admin") else "user"   # accounts written by v2.0


def is_admin(user: str | None) -> bool:
    return role_of(user) in ("owner", "admin")


def is_owner(user: str | None) -> bool:
    return role_of(user) == "owner"


def set_role(actor: str, target: str, role: str):
    """Change `target` to role 'admin' or 'user'. The owner's role is fixed."""
    if role not in ("admin", "user"):
        raise ValueError("role must be admin or user")
    with LOCK:
        users = load_users()
        if target not in users:
            raise ValueError("no such account")
        if role_of(target, users) == "owner":
            raise PermissionError("the owner account's role cannot be changed")
        if target == actor and role == "user" and not any(
                role_of(u, users) in ("owner", "admin") for u in users if u != actor):
            raise PermissionError("you are the last admin")
        users[target]["role"] = role
        users[target].pop("is_admin", None)
        save_users(users)


def set_blocked(target: str, blocked: bool):
    with LOCK:
        users = load_users()
        if target not in users:
            raise ValueError("no such account")
        if role_of(target, users) in ("owner", "admin"):
            raise PermissionError("remove admin rights first")
        users[target]["disabled"] = bool(blocked)
        save_users(users)
    if blocked:
        drop_sessions(user=target)


def reset_password(target: str) -> str:
    with LOCK:
        users = load_users()
        if target not in users:
            raise ValueError("no such account")
        if role_of(target, users) == "owner":
            raise PermissionError("reset the owner password in Terminal (jobscreen_v2.py set-password)")
        pw = temp_password()
        users[target]["pw"] = hash_pw(pw)
        save_users(users)
    drop_sessions(user=target)
    return pw


def delete_user(u: str):
    with LOCK:
        users = load_users(); users.pop(u, None); save_users(users)
        drop_sessions(user=u)
        d = udir(u)
        if os.path.isdir(d):
            shutil.rmtree(d)


# ---------------------------------------------------------------- per-user storage
def udir(user: str) -> str:
    assert USERNAME_RE.match(user), "bad user"
    return os.path.join(USERS_DIR, user)


def uload(user: str, name: str, default):
    return load_json(os.path.join(udir(user), name + ".json"), default)


def usave(user: str, name: str, obj):
    save_json(os.path.join(udir(user), name + ".json"), obj)


def load_state(user):
    st = uload(user, "state", {})
    st.setdefault("todos", []); st.setdefault("dismissed", []); st.setdefault("links_checked", {})
    return st


# ---------------------------------------------------------------- sessions
def _tok_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_sessions():
    global SESSIONS
    t = time.time()
    SESSIONS.clear()
    SESSIONS.update({k: v for k, v in load_json(SESSIONS_PATH, {}).items() if v.get("expires", 0) > t})


def persist_sessions():
    save_json(SESSIONS_PATH, SESSIONS)


def new_session(user: str) -> str:
    token = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[_tok_key(token)] = {"user": user, "created": time.time(), "expires": time.time() + SESSION_DAYS * 86400}
        persist_sessions()
    return token


def drop_sessions(user: str | None = None, token: str | None = None):
    with LOCK:
        for k in list(SESSIONS):
            if (token and k == _tok_key(token)) or (user and SESSIONS[k]["user"] == user):
                SESSIONS.pop(k, None)
        persist_sessions()


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    s = SESSIONS.get(_tok_key(token))
    if not s or s["expires"] < time.time():
        return None
    rec = load_users().get(s["user"])
    return s["user"] if rec and not rec.get("disabled") else None


def touch_login(u: str):
    with LOCK:
        users = load_users()
        if u in users:
            users[u]["last_login"] = str(now()); save_users(users)


# ---------------------------------------------------------------- rate limits
def rate_hit(bucket: str, key: str, limit: int, window_s: int, record: bool = True) -> bool:
    """True if `key` already used `limit` events in the last `window_s` seconds."""
    with LOCK:
        t = time.time()
        lst = [x for x in RATE.get((bucket, key), []) if x > t - window_s]
        over = len(lst) >= limit
        if record and not over:
            lst.append(t)
        RATE[(bucket, key)] = lst
        if len(RATE) > 20000:
            RATE.clear()
        return over


def rate_count(bucket: str, key: str, window_s: int) -> int:
    t = time.time()
    return len([x for x in RATE.get((bucket, key), []) if x > t - window_s])
