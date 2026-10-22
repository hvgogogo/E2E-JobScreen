"""Data migrations, backups and health checks.  Safe to run many times (idempotent)."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import zipfile

from . import accounts as A
from .core import (ADS_PATH, BACKUP_DIR, DATA_DIR, GSTATE_PATH, USERS_DIR, VERSION, config, gstate, limits, load_json,
                   now, save_json, today)

SCHEMA = 4          # 1 = v2.0, 3 = v2.1 roles + site.json + simple profiles, 4 = v2.2 EU sources + position types


# ---------------------------------------------------------------- backups
def backup(tag: str = "") -> str:
    """Zip users.json, site.json, global_state.json and every users/<name>/ folder (ads.json is re-fetchable)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = f"backup_{now():%Y%m%d_%H%M%S}{('_' + tag) if tag else ''}.zip"
    path = os.path.join(BACKUP_DIR, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in ("users.json", "site.json", "global_state.json"):
            p = os.path.join(DATA_DIR, fn)
            if os.path.exists(p):
                z.write(p, fn)
        for root, _, files in os.walk(USERS_DIR):
            for fn in files:
                p = os.path.join(root, fn)
                z.write(p, os.path.relpath(p, DATA_DIR))
    keep = int(config().get("backups_keep", 14))
    zips = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".zip"))
    for old in zips[:-keep]:
        os.remove(os.path.join(BACKUP_DIR, old))
    return path


def daily_backup():
    g = gstate()
    if g.get("last_backup", "")[:10] != str(today()):
        backup()
        g = gstate(); g["last_backup"] = str(now()); save_json(GSTATE_PATH, g)


# ---------------------------------------------------------------- v2.0 -> v2.1
def upgrade(verbose: bool = True) -> list[str]:
    """Bring data/ written by an older v2 up to the current schema."""
    from .engine import site, save_site
    msgs = []
    g = gstate()
    users = A.load_users()
    have = g.get("schema", 1)
    if have >= SCHEMA:
        return msgs
    if users:
        msgs.append("backup: " + os.path.basename(backup("before_v" + VERSION)))
    if have < 3:
        msgs += _upgrade_to_3(g, users)
    if have < 4:
        msgs += _upgrade_to_4(users)
    g = gstate()
    g["schema"] = SCHEMA
    save_json(GSTATE_PATH, g)
    msgs.append(f"schema -> {SCHEMA}")
    if verbose:
        for m in msgs:
            print("upgrade:", m)
    return msgs


def _upgrade_to_3(g: dict, users: dict) -> list[str]:
    from .engine import site, save_site
    msgs = []
    # 1. roles: is_admin -> role; the owner is the configured owner_account (or the oldest admin)
    owner = config().get("owner_account") or ""
    if owner not in users:
        admins = sorted((u for u, r in users.items() if r.get("is_admin") or r.get("role") in ("owner", "admin")),
                        key=lambda u: users[u].get("created", ""))
        owner = admins[0] if admins else ""
    for u, r in users.items():
        if r.get("role") not in A.ROLES:
            r["role"] = "owner" if u == owner else ("admin" if r.get("is_admin") else "user")
        if u == owner:
            r["role"] = "owner"
        r.pop("is_admin", None)
    if users:
        A.save_users(users)
        msgs.append(f"roles: owner={owner or '-'}, {len(users)} accounts")
    # 2. site.json: registration switch moves out of global_state; saved-search links become site-wide
    s = site()
    if "registration_open" in g:
        if g["registration_open"] is not None:
            s["registration_open"] = bool(g["registration_open"])
        g.pop("registration_open", None)
    if not s.get("links"):
        s["links"] = list(config().get("default_links", []))
    save_site(s)
    # 3. profiles: drop per-user min_score (now one site-wide cutoff); non-admin saved links are removed
    for u in users:
        try:
            p = A.uload(u, "profile", None)
        except AssertionError:
            continue
        if not p:
            continue
        p.setdefault("simple", {}).pop("min_score", None)
        if A.role_of(u, users) not in ("owner", "admin"):
            p.pop("manual_sources", None)
            p.pop("queries", None)
        p.setdefault("preset", "")
        A.usave(u, "profile", p)
    save_json(GSTATE_PATH, g)
    return msgs


# ---------------------------------------------------------------- v2.1 -> v2.2
OLD_ROLE_MAP = {
    "postdoc": "Postdoc", "postdoktor": "Postdoc", "researcher": "Researcher / scientist",
    "forskare": "Researcher / scientist", "scientist": "Researcher / scientist",
    "staff scientist": "Researcher / scientist", "research engineer": "Research engineer / specialist",
    "forskningsingenjör": "Research engineer / specialist", "research specialist": "Research engineer / specialist",
    "data scientist": "Data scientist / analyst", "analyst": "Data scientist / analyst",
    "bioinformatician": "Bioinformatician", "assistant professor": "Lecturer / assistant professor",
    "biträdande lektor": "Lecturer / assistant professor", "lecturer": "Lecturer / assistant professor",
    "universitetslektor": "Lecturer / assistant professor", "associate professor": "Associate / full professor",
    "lab manager": "Lab manager / technician", "software engineer": "Software engineer / developer",
}


def map_roles(roles: list) -> list:
    out = []
    for r in roles:
        r2 = OLD_ROLE_MAP.get(str(r).lower().strip(), r)
        if r2 not in out:
            out.append(r2)
    return out


def _upgrade_to_4(users: dict) -> list[str]:
    """Position types become English pick-list labels, 'hide ads whose title contains' goes away, empty funding
    lists get the catalog, site links lose their Sweden-only filters."""
    from .engine import add_catalog, roll_user_funding, save_site, site
    msgs, n_prof, n_fund = [], 0, 0
    lim = limits()
    for u in users:
        try:
            p = A.uload(u, "profile", None)
            fl = A.uload(u, "funding", [])
        except AssertionError:
            continue
        if p:
            sp = p.setdefault("simple", {})
            old = list(sp.get("roles", []))
            sp["roles"] = map_roles(old)
            sp.pop("exclude", None)
            if sp["roles"] != old or "exclude" in sp:
                n_prof += 1
            A.usave(u, "profile", p)
        if not fl:
            if add_catalog(fl, lim["max_funding"]):
                n_fund += 1
        roll_user_funding(fl)
        A.usave(u, "funding", fl)
    msgs.append(f"profiles: position types updated for {n_prof} accounts")
    msgs.append(f"funding: catalog added for {n_fund} accounts with an empty list")
    s = site()
    names = {l.get("name") for l in s.get("links", [])}
    for l in s.get("links", []):
        if "linkedin.com" in l.get("url", ""):
            l["url"] = l["url"].replace("&location=Sweden", "&location=Europe")
            l["name"] = l["name"].replace("(Sweden)", "(Europe)")
        if "academicpositions.com" in l.get("url", ""):
            l["url"] = l["url"].replace("&country=sweden", "")
    for l in config().get("default_links", []):
        if l.get("name") not in names and l.get("name", "").replace("(Europe)", "(Sweden)") not in names:
            s["links"].append(l)
    save_site(s)
    msgs.append(f"site links: {len(s['links'])}")
    return msgs


# ---------------------------------------------------------------- v1 -> owner account (first install only)
def migrate_v1(v1_dir: str, seed_path: str, force: bool = False):
    from .engine import AD_FIELDS, SNAP_FIELDS, default_profile
    if A.load_users() and not force:
        print("users.json already exists - nothing to migrate (use --force to redo)."); return
    seed = load_json(seed_path, None)
    if not seed:
        sys.exit(f"seed file not found: {seed_path}")
    u = seed["username"]
    v1jobs = load_json(os.path.join(v1_dir, "data", "jobs.json"), {})
    v1state = load_json(os.path.join(v1_dir, "data", "state.json"), {})
    v1cfg = load_json(os.path.join(v1_dir, "config.json"), {})
    v1fund = load_json(os.path.join(v1_dir, "funding.json"), [])
    ads, entries = {}, {}
    for jid, j in v1jobs.items():
        if j.get("manual"):
            entries[jid] = {k: v for k, v in j.items() if k not in ("days_left",)}
            continue
        ads[jid] = {k: j.get(k, "") for k in AD_FIELDS}
        ads[jid]["gone"] = bool(j.get("gone"))
        e = {k: j[k] for k in ("status", "notes", "starred", "folder", "status_changed") if j.get(k)}
        if e.get("status") == "new":
            e.pop("status")
        if e:
            e["snap"] = {k: j.get(k, "") for k in SNAP_FIELDS}
            entries[jid] = e
    prof = default_profile()
    prof.update(mode="advanced", field="Bioinformatics / single-cell & spatial omics (imported from v1)",
                scoring=v1cfg.get("scoring", {}), queries=v1cfg.get("jobtech", {}).get("queries", []),
                manual_sources=v1cfg.get("manual_sources", []))
    for k in ("follow_up_days", "funding_warn_days", "manual_check_days"):
        if k in v1cfg:
            prof[k] = v1cfg[k]
    os.makedirs(DATA_DIR, exist_ok=True)
    A.save_users({u: {"pw": seed["pw"], "created": str(now()), "last_login": "", "role": "owner"}})
    A.usave(u, "profile", prof)
    A.usave(u, "jobs", entries)
    A.usave(u, "state", {"todos": v1state.get("todos", []), "dismissed": v1state.get("dismissed", []),
                         "links_checked": v1state.get("links_checked", {})})
    A.usave(u, "funding", [{k: v for k, v in f.items() if k != "days_left"} for f in v1fund])
    save_json(ADS_PATH, ads)
    save_json(GSTATE_PATH, {"last_update": v1state.get("last_update"), "source_status": v1state.get("source_status", {}),
                            "last_new": v1state.get("last_new", []), "ads_version": 1, "schema": 1})
    upgrade(verbose=False)
    print(f"Migrated v1 -> account '{u}': {len(ads)} ads, {len(entries)} tracked/manual jobs, "
          f"{len(v1state.get('todos', []))} to-dos, {len(v1fund)} funding calls.")


# ---------------------------------------------------------------- health check
def doctor() -> int:
    """Print a short health report; exit code 0 = fine."""
    from .engine import site, source_list
    problems = 0
    users = A.load_users()
    owners = [u for u in users if A.role_of(u, users) == "owner"]
    admins = [u for u in users if A.role_of(u, users) == "admin"]
    print(f"Job Screen {VERSION} - data in {DATA_DIR}")
    print(f"accounts: {len(users)} (owner: {', '.join(owners) or 'NONE'}; admins: {', '.join(admins) or '-'})")
    if not owners:
        print("  ! no owner account"); problems += 1
    plain = [u for u, r in users.items() if not isinstance(r.get("pw"), dict) or "hash" not in r["pw"]]
    if plain:
        print("  ! accounts without a password hash:", plain); problems += 1
    missing = [u for u in users if not os.path.isdir(os.path.join(USERS_DIR, u))]
    if missing:
        print("  ! accounts without a data folder:", missing); problems += 1
    g = gstate()
    print(f"schema: {g.get('schema', 1)} (current {SCHEMA}); last update: {g.get('last_update')}; "
          f"ads in pool: {len(load_json(ADS_PATH, {}))}")
    if g.get("schema", 1) < SCHEMA:
        print("  ! data not upgraded - run: jobscreen_v2.py upgrade"); problems += 1
    st = g.get("source_status", {})
    for s in source_list():
        r = st.get(s["name"], {})
        mark = "off " if not s["enabled"] else ("ok  " if r.get("ok") else ("FAIL" if r else "new "))
        print(f"  {mark} {s['kind']:<11} {s['name']:<32} {r.get('n', '') if r.get('ok') else r.get('error', '')}")
        problems += 1 if s["enabled"] and r and not r.get("ok") else 0
    print(f"registration open: {site().get('registration_open')}; site links: {len(site().get('links', []))}")
    bk = sorted(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else []
    print(f"backups: {len(bk)} (latest: {bk[-1] if bk else '-'})")
    return 1 if problems else 0
