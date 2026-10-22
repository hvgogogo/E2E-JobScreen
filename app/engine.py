"""Shared ad pool, background updates, per-user scoring, to-dos and the state sent to the web page."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
import traceback
import urllib.parse

from . import accounts as A
from .core import (ADS_PATH, GSTATE_PATH, LOCK, VERSION, config, days_left, gstate, job_root, limits, load_json, now,
                   parse_date, presets, public_url, save_json, sources, today)
from .sources import fetch_employer, fetch_jobtech, fetch_varbi
from . import sources_eu as EU
from . import sources_boards as BOARDS
from . import translate as TR

ACTIVE = {"interested", "applying", "applied", "interview", "offer"}
STATUSES = ["new", "interested", "applying", "applied", "interview", "offer", "rejected", "not relevant", "closed"]
STATUS_LABEL = {"new": "New", "interested": "Saved", "applying": "Preparing", "applied": "Applied",
                "interview": "Interview", "offer": "Offer", "rejected": "Rejected", "not relevant": "Not for me",
                "closed": "Closed"}
FUND_STATUSES = ["idea", "planned", "in progress", "submitted", "skip", "not eligible?"]
SKIP_FUND = {"skip", "submitted", "not eligible", "not eligible?"}
AD_FIELDS = ("id", "source", "title", "title_alt", "title_en", "lang", "types", "employer", "department", "city",
             "country", "url", "ad_url", "deadline", "published", "description", "first_seen", "last_seen", "gone")
SNAP_FIELDS = ("title", "title_alt", "title_en", "employer", "department", "city", "country", "url", "deadline",
               "source")
COUNTRY_LANG = {v[0]: v[3] for v in EU.COUNTRIES.values()}

PROGRESS = {"running": False, "done": 0, "total": 0, "current": "", "log": [], "finished_at": None}
ADS: dict = {}
SCORE_CACHE: dict = {}


def load_ads():
    ADS.clear(); ADS.update(load_json(ADS_PATH, {}))
    for a in ADS.values():                      # re-tag on every start, so edits to presets.json apply at once
        a["types"] = detect_types(a)


# ================================================================ visitors (landing page counter)
VISITS_PATH = os.path.join(os.path.dirname(ADS_PATH), "visits.json")
_SALT = os.urandom(8).hex()


def record_visit(ip: str):
    """Counts distinct visitors per day.  Only a salted hash is kept for one day - no IP addresses are stored."""
    import hashlib
    d = str(today())
    h = hashlib.sha256(f"{_SALT}|{d}|{ip}".encode()).hexdigest()[:12]
    with LOCK:
        v = load_json(VISITS_PATH, {"total": 0, "days": {}, "seen": {}})
        seen = v.setdefault("seen", {})
        if seen.get("day") != d:
            seen.clear(); seen["day"] = d; seen["h"] = []
        if h in seen["h"]:
            return
        seen["h"].append(h)
        v["total"] = int(v.get("total", 0)) + 1
        v.setdefault("days", {})[d] = v["days"].get(d, 0) + 1
        for k in sorted(v["days"])[:-90]:
            v["days"].pop(k, None)
        save_json(VISITS_PATH, v)


def visit_stats() -> dict:
    v = load_json(VISITS_PATH, {"total": 0, "days": {}})
    cut = str(today() - dt.timedelta(days=30))
    return {"total": int(v.get("total", 0)), "last30": sum(n for k, n in v.get("days", {}).items() if k > cut),
            "today": v.get("days", {}).get(str(today()), 0)}


# ================================================================ funding catalog
def roll_deadline(d: str) -> str:
    """An estimated yearly deadline that has passed moves to the same day next year."""
    x = parse_date(d)
    if not x:
        return d or ""
    while x < today():
        try:
            x = x.replace(year=x.year + 1)
        except ValueError:                      # 29 Feb
            x = x.replace(year=x.year + 1, day=28)
    return str(x)


def catalog() -> list[dict]:
    out = []
    for c in presets().get("funding_catalog", []):
        c = dict(c)
        if c.get("estimated"):
            c["deadline"] = roll_deadline(c.get("deadline", ""))
        out.append(c)
    return out


def add_catalog(fl: list, max_n: int) -> int:
    """Adds catalog calls the user does not have yet (status 'idea').  Returns how many were added."""
    import secrets
    have = {f.get("name", "").lower() for f in fl}
    n = 0
    for c in catalog():
        if c["name"].lower() not in have and len(fl) < max_n:
            fl.append({**{k: c.get(k, "") for k in ("name", "funder", "deadline", "url", "notes")},
                       "id": "f_" + secrets.token_hex(5), "estimated": bool(c.get("estimated")), "status": "idea",
                       "from_catalog": True})
            n += 1
    return n


def roll_user_funding(fl: list) -> bool:
    changed = False
    for f in fl:
        if f.get("estimated") and f.get("deadline") and f.get("status") not in ("submitted",):
            nd = roll_deadline(f["deadline"])
            if nd != f["deadline"]:
                f["deadline"] = nd; changed = True
    return changed


# ================================================================ site settings (admin-editable, data/site.json)
SITE_PATH = os.path.join(os.path.dirname(ADS_PATH), "site.json")


def site() -> dict:
    s = load_json(SITE_PATH, {})
    s.setdefault("registration_open", bool(config().get("registration_open", True)))
    s.setdefault("links", [])
    s.setdefault("disabled_sources", [])
    s.setdefault("announcement", "")
    return s


def save_site(s: dict):
    save_json(SITE_PATH, s)


def registration_open() -> bool:
    return bool(site().get("registration_open"))


# ================================================================ profiles
def default_profile() -> dict:
    return {"mode": "simple", "field": "", "preset": "",
            "simple": {"roles": [], "topics": []},
            "follow_up_days": 21, "funding_warn_days": 90, "manual_check_days": 7}


def get_profile(user: str) -> dict:
    p = A.uload(user, "profile", None) or default_profile()
    p.setdefault("simple", {"roles": [], "topics": []})
    return p


def user_queries(prof: dict) -> list[str]:
    """Platsbanken search words contributed by one account (topics + specific roles)."""
    if prof.get("mode") == "advanced" and prof.get("queries"):
        return list(prof["queries"])
    s = prof.get("simple", {})
    labels = {p["label"].lower() for p in presets().get("positions", [])}
    generic = {"postdoc", "postdoktor", "researcher", "forskare", "research engineer", "forskningsingenjör"} | labels
    return [k for k in s.get("topics", []) + s.get("roles", []) if k.lower() not in generic]


def search_words(prof: dict, n: int = 3) -> str:
    s = prof.get("simple", {})
    words = (s.get("topics", [])[:2] + [r.split(" / ")[0] for r in s.get("roles", [])])[:n]
    if not words and prof.get("mode") == "advanced":
        words = (prof.get("queries") or [])[:n]
    return " ".join(words)


# ================================================================ position types (multilingual, presets.json)
_POS_CACHE: dict = {}


def position_rules():
    pr = presets()
    key = json.dumps([pr.get("positions"), pr.get("absorb")], sort_keys=True)
    if _POS_CACHE.get("key") != key:
        rules = []
        for p in pr.get("positions", []):
            try:
                rules.append((p["id"], p["label"], re.compile(p["pattern"], re.I)))
            except (re.error, KeyError):
                continue
        _POS_CACHE.update(key=key, rules=rules, absorb=pr.get("absorb", {}),
                          by_label={l.lower(): i for i, l, _ in rules})
    return _POS_CACHE


def detect_types(ad: dict) -> list[str]:
    """Position types named in an ad title (original language, English translation and the alternative title)."""
    pc = position_rules()
    t = " | ".join(x for x in (ad.get("title", ""), ad.get("title_en", ""), ad.get("title_alt", "")) if x)
    found = {i for i, _, rx in pc["rules"] if rx.search(t)}
    for i in list(found):
        for g in pc["absorb"].get(i, []):
            if g != i:
                found.discard(g)
    return sorted(found)


def lang_of(ad: dict) -> str:
    if ad.get("lang"):
        return ad["lang"]
    if ad.get("source", "").startswith(("EURAXESS", "jobs.ac.uk")) or ad.get("detail") == "wpjm":
        return ""                                          # mostly English; detected per batch
    return COUNTRY_LANG.get(ad.get("country", ""), "")


# ================================================================ scoring
def kw_regex(k: str) -> str:
    parts = [re.escape(p) for p in re.split(r"[\s\-_]+", k.lower().strip()) if p]
    return r"[\s\-]?".join(parts)


def build_scoring(profile: dict, owner: bool) -> dict:
    """Compiled scoring spec.  Only the owner may use raw regex ('advanced'); everybody else gets plain keywords."""
    flags_cfg = config().get("flags", {})
    if profile.get("mode") == "advanced" and owner and profile.get("scoring"):
        sc = profile["scoring"]
        spec = {"role": [(re.compile(p), w, p.split("|")[0]) for p, w in sc.get("role", {}).items()],
                "topic": [(re.compile(p), w, p.split("|")[0]) for p, w in sc.get("topic", {}).items()],
                "negative": [(re.compile(p), w, p.split("|")[0]) for p, w in sc.get("negative", {}).items()],
                "gate": re.compile(sc["life_science_gate"]) if sc.get("life_science_gate") else None,
                "gate_penalty": sc.get("gate_penalty", -25), "topic_cap": sc.get("topic_cap", 60),
                "min_score": sc.get("min_score", 50), "flags": {**flags_cfg, **sc.get("flags", {})}}
    else:
        s = profile.get("simple", {})
        mk = lambda lst, w: [(re.compile(kw_regex(k)), w, k) for k in lst if k.strip()]  # noqa: E731
        by_label = position_rules()["by_label"]
        types = {by_label[r.lower()] for r in s.get("roles", []) if r.lower() in by_label}
        custom = [r for r in s.get("roles", []) if r.lower() not in by_label]
        spec = {"role": mk(custom, 40), "topic": mk(s.get("topics", []), 12),
                "negative": [], "gate": None, "gate_penalty": 0, "topic_cap": 60,
                "types": types, "strict": bool(types or custom),
                "min_score": int(config().get("default_min_score", 40)), "flags": dict(flags_cfg)}
    spec["flags"] = [(re.compile(p), lbl) for p, lbl in spec["flags"].items()]
    return spec


def score_job(job: dict, spec: dict) -> tuple[int, list[str], list[str]]:
    head = " ".join([job.get("title", ""), job.get("title_alt", ""), job.get("title_en", "")]).lower()
    body = " ".join([job.get("department", ""), job.get("description", "")]).lower()
    score, hits, flags, role_score, topic = 0, [], [], 0, 0
    if spec.get("types") is not None:
        ad_types = set(job.get("types") or [])
        pc = position_rules()
        if ad_types & spec["types"]:
            role_score = 40
            hits += [l for i, l, _ in pc["rules"] if i in ad_types & spec["types"]]
    for rx, w, lbl in spec["role"]:
        if rx.search(head):
            role_score = max(role_score, w); hits.append(lbl)
        elif rx.search(body):
            role_score = max(role_score, w // 3)
    score += role_score
    for rx, w, lbl in spec["topic"]:
        if rx.search(head):
            topic += w * 2; hits.append(lbl)
        elif rx.search(body):
            topic += w; hits.append(lbl)
    score += min(topic, spec["topic_cap"])
    for rx, w, lbl in spec["negative"]:
        if rx.search(head):
            score += w; flags.append(lbl)
    text = head + " \n " + body
    if spec["gate"] is not None and not spec["gate"].search(text):
        score += spec["gate_penalty"]; flags.append("off-field?")
    for rx, lbl in spec["flags"]:
        if rx.search(text):
            flags.append(lbl)
    if spec.get("strict") and job.get("types") and not (set(job["types"]) & spec["types"]) \
            and not any(rx.search(head) for rx, _, _ in spec["role"]):
        # the ad is a position type the user did not tick (e.g. a postdoc when only "Researcher" is ticked):
        # it stays in All ads but never counts as a match
        score = min(score, spec["min_score"] - 1)
        flags.append("not your position type")
    hits = list(dict.fromkeys(h.replace("\\b", "").replace("(?<!", "")[:24] for h in hits))
    return score, hits, list(dict.fromkeys(flags))


def user_scores(user: str, profile: dict) -> tuple[dict, int]:
    owner = A.is_owner(user)
    key = (gstate().get("ads_version", 0), json.dumps(profile.get("simple")), json.dumps(profile.get("scoring")),
           profile.get("mode"), owner, config().get("default_min_score", 40), hash(position_rules()["key"]))
    c = SCORE_CACHE.get(user)
    if c and c[0] == key:
        return c[1], c[2]
    spec = build_scoring(profile, owner)
    scores = {jid: score_job(ad, spec) for jid, ad in ADS.items()}
    SCORE_CACHE[user] = (key, scores, spec["min_score"])
    return scores, spec["min_score"]


# ================================================================ update (shared pool)
def log(msg):
    PROGRESS["log"].append(f"{now():%H:%M:%S} {msg}")
    PROGRESS["log"] = PROGRESS["log"][-80:]
    print(f"[{now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def all_queries() -> list[str]:
    lim = limits()
    qs = list(sources().get("platsbanken", {}).get("queries", []))
    users = A.load_users()
    for u in sorted(users, key=lambda x: A.role_of(x, users) != "owner"):  # owner first
        try:
            uq = user_queries(get_profile(u))
        except AssertionError:
            continue
        qs += uq if A.role_of(u, users) == "owner" else uq[:lim["max_queries_per_user"]]
    out = []
    for q in qs:
        q = q.strip()
        if q and q.lower() not in [o.lower() for o in out]:
            out.append(q)
    return out[:lim["max_total_queries"]]


def source_list() -> list[dict]:
    """Flat list of every configured source with name/kind/enabled - used by the update and the Admin page."""
    src, off = sources(), set(site().get("disabled_sources", []))
    out = []
    for s in src.get("varbi", []):
        out.append({"kind": "varbi", "name": s["label"], "enabled": s.get("enabled", True) and s["label"] not in off,
                    "spec": s, "site": f"https://{s['tenant']}.varbi.com/en/"})
    for s in src.get("employers", []):
        out.append({"kind": "employer", "name": s["label"],
                    "enabled": s.get("enabled", True) and s["label"] not in off, "spec": s,
                    "site": "https://arbetsformedlingen.se/platsbanken/annonser?q=" + urllib.parse.quote(s["label"])})
    pb = src.get("platsbanken", {})
    out.append({"kind": "platsbanken", "name": "Platsbanken", "enabled": pb.get("enabled", True) and
                "Platsbanken" not in off, "spec": pb, "site": "https://arbetsformedlingen.se/platsbanken/",
                "country": "Sweden"})
    for s in out:
        s.setdefault("country", "Sweden")
    eu = src.get("eu", {})
    for code in eu.get("countries", []):
        if code not in EU.COUNTRIES:
            continue
        cname = EU.country_name(code)
        ex, er = eu.get("euraxess", {}), eu.get("eures", {})
        if ex:
            nm = f"EURAXESS {cname}"
            out.append({"kind": "euraxess", "name": nm, "enabled": ex.get("enabled", True) and nm not in off,
                        "spec": {"code": code, **ex}, "country": cname,
                        "site": f"https://euraxess.ec.europa.eu/jobs/search?f%5B0%5D=job_country%3A"
                                f"{EU.COUNTRIES[code][1]}"})
        kws = (er.get("keywords") or {}).get(code)
        if er and kws and EU.COUNTRIES[code][2]:
            nm = f"EURES {cname}"
            out.append({"kind": "eures", "name": nm, "enabled": er.get("enabled", True) and nm not in off,
                        "spec": {"code": code, "keywords": kws, "pages": er.get("pages", 2)}, "country": cname,
                        "site": "https://europa.eu/eures/portal/jv-se/search?lang=en"})
    ja = eu.get("jobsac")
    if ja and "UK" in eu.get("countries", []):
        out.append({"kind": "jobsac", "name": "jobs.ac.uk", "enabled": ja.get("enabled", True) and
                    "jobs.ac.uk" not in off, "spec": ja, "country": "United Kingdom",
                    "site": "https://www.jobs.ac.uk/"})
    for b in src.get("boards", []):                   # v2.3: WordPress job boards (WP Job Manager)
        out.append({"kind": b.get("kind", "wpjm"), "name": b["label"],
                    "enabled": b.get("enabled", True) and b["label"] not in off, "spec": b, "country": "Europe",
                    "site": b.get("site") or b["base"].rstrip("/") + "/jobs/"})
    return out


def countries() -> list[str]:
    """Countries with at least one ad, Sweden first."""
    cs = {a.get("country") or "" for a in ADS.values() if not a.get("gone")}
    cs.discard("")
    return sorted(cs, key=lambda c: (c != "Sweden", c))


def topic_queries(max_n: int) -> list[str]:
    """Users' topic words (for keyword-search sources such as jobs.ac.uk)."""
    out = []
    for u in A.load_users():
        try:
            prof = get_profile(u)
        except AssertionError:
            continue
        for t in prof.get("simple", {}).get("topics", []):
            if t.lower() not in [o.lower() for o in out]:
                out.append(t)
    return out[:max_n]


def euraxess_pick(tasks: list[dict], prev: dict) -> set[str]:
    """Names of the EURAXESS countries to fetch now.  EURAXESS answers HTTP 429 after ~80 quick page loads, so a
    run takes the countries refreshed longest ago (never / failed first) until about `page_budget` pages are used
    (estimated from each country's last count, 10 ads per page), at most `countries_per_run`, at least one."""
    ex = sources().get("eu", {}).get("euraxess", {})
    per_run, budget = int(ex.get("countries_per_run", 3)), int(ex.get("page_budget", 70))
    eux = sorted((s for s in tasks if s["kind"] == "euraxess"),
                 key=lambda s: prev.get(s["name"], {}).get("fetched_at", ""))
    pick, used = set(), 0
    for s in eux:
        st = prev.get(s["name"], {})
        est = min(int(s["spec"].get("max_pages", 30)), (st.get("n", 0) if st.get("ok") else 0) // 10 + 1)
        if pick and (len(pick) >= per_run or used + est > budget):
            break
        pick.add(s["name"]); used += est
    return pick


def run_update(kinds: set | None = None, reason: str = "") -> dict:
    """kinds=None: every enabled source.  kinds={"euraxess"}: the EURAXESS top-up run (see scheduler)."""
    cfg = config()
    t0 = time.time()
    tasks = [s for s in source_list() if s["enabled"] and (kinds is None or s["kind"] in kinds)]
    prev_g = gstate()
    prev = prev_g.get("source_status", {})
    pick = euraxess_pick(tasks, prev)
    skip = {s["name"] for s in tasks if s["kind"] == "euraxess" and s["name"] not in pick}
    tasks = [s for s in tasks if s["name"] not in skip]
    PROGRESS.update(running=True, done=0, total=len(tasks), current="", log=[], finished_at=None)
    log(f"Update started{(' (' + reason + ')') if reason else ''}: {len(tasks)} sources"
        + (f"; EURAXESS now: {', '.join(sorted(n.replace('EURAXESS ', '') for n in pick))}" if pick else ""))
    fetched, status, partial_sources = {}, {}, set()
    if kinds is not None:                             # top-up run: every other source keeps its last status
        status.update({n: v for n, v in prev.items() if n not in {s["name"] for s in tasks}})
    for nm in skip:                                   # keep the last result; their ads are left untouched
        status[nm] = dict(prev.get(nm, {"ok": True, "n": 0, "kind": "euraxess"}), skipped=True)
    for s in tasks:
        name, spec = s["name"], s["spec"]
        PROGRESS["current"] = name
        try:
            if s["kind"] == "varbi":
                jobs = fetch_varbi(spec["tenant"], spec["label"], spec.get("city", ""))
            elif s["kind"] == "employer":
                jobs = fetch_employer(spec["orgnr"], spec["label"], spec.get("city", ""))
            elif s["kind"] == "euraxess":
                r = EU.fetch_euraxess(spec["code"], spec.get("max_pages", 30), tuple(spec.get("profiles") or ()))
                jobs = r["jobs"]
            elif s["kind"] == "eures":
                r = EU.fetch_eures(spec["code"], spec["keywords"], spec.get("pages", 2))
                jobs = r["jobs"]
            elif s["kind"] == "jobsac":
                r = EU.fetch_jobsac(spec.get("queries", []), spec.get("pages", 4))
                t = EU.fetch_jobsac([q for q in topic_queries(spec.get("max_topics", 25))
                                     if q.lower() not in [x.lower() for x in spec.get("queries", [])]],
                                    spec.get("topic_pages", 1))
                jobs = r["jobs"] + [j for j in t["jobs"] if j["id"] not in {x["id"] for x in r["jobs"]}]
                r = {"partial": True}                  # keyword search: an ad may simply not match this time
            elif s["kind"] == "wpjm":
                r = BOARDS.fetch_wpjm(spec["base"], spec["label"], spec.get("countries", "europe"),
                                      spec.get("max_pages", 15), spec.get("max_age_days", 60))
                jobs = drop_reposts(r["jobs"], fetched)
                if len(jobs) < len(r["jobs"]):
                    log(f"{name}: {len(r['jobs']) - len(jobs)} ads skipped - same ad already listed from another source")
            else:
                r = {"partial": True}               # keyword search: an ad may simply not match this time
                jobs = fetch_jobtech(all_queries(), spec.get("limit", 100))
            if s["kind"] in ("euraxess", "eures", "jobsac", "platsbanken", "wpjm") and r.get("partial"):
                partial_sources.add(name)
            for j in jobs:
                if j["id"] in fetched and s["kind"] in ("platsbanken", "wpjm"):
                    continue                       # a direct university copy wins over a search / job-board copy
                if j["id"] in fetched and not j.get("description"):
                    j["description"] = fetched[j["id"]].get("description", "")
                fetched[j["id"]] = j
            status[name] = {"ok": True, "n": len(jobs), "at": str(now()), "fetched_at": str(now()),
                            "kind": s["kind"], "country": s.get("country", "")}
            log(f"{name}: {len(jobs)} ads" + (" (page limit reached - more ads may exist)"
                                              if s["kind"] == "euraxess" and r.get("partial") else ""))
        except Exception as e:
            status[name] = {"ok": False, "error": str(e)[:200], "at": str(now()), "kind": s["kind"],
                            "country": s.get("country", "")}
            log(f"{name}: FAILED - {e}")
        PROGRESS["done"] += 1
    PROGRESS["current"] = "English titles"
    try:
        old = {jid: ADS[jid] for jid in fetched if jid in ADS}
        for jid, j in fetched.items():               # keep translations we already have
            o = old.get(jid)
            if o and o.get("title_en") and o.get("title_en_src") == j.get("title"):
                j["title_en"], j["title_en_src"] = o["title_en"], o["title_en_src"]
        n_tr = TR.add_english_titles(list(fetched.values()), lang_of)
        log(f"English titles: {n_tr} translated")
    except Exception as e:
        log(f"English titles: skipped - {e}")
    for j in fetched.values():
        j["types"] = detect_types(j)
    with LOCK:
        run_ts = str(now())
        new_ids = []
        for jid, j in fetched.items():
            j.update(last_seen=run_ts, gone=False)
            if jid in ADS:
                j["first_seen"] = ADS[jid].get("first_seen", run_ts)
                if ADS[jid].get("detail_done"):          # keep the full text fetched when the ad was opened
                    for k in ("description", "deadline", "city", "employer"):
                        if ADS[jid].get(k) and (k == "description" or not j.get(k)):
                            j.pop(k, None)
                ADS[jid].update(j)
            else:
                j["first_seen"] = run_ts
                ADS[jid] = j
                new_ids.append(jid)
        ran = {s["name"] for s in tasks}             # only sources fetched in THIS run can mark ads gone
        ok_sources = {n for n, v in status.items() if n in ran and v["ok"] and not v.get("skipped")}
        cutoff = str(today() - dt.timedelta(days=cfg.get("prune_after_days", 60)))
        for jid in list(ADS):
            a = ADS[jid]
            if jid not in fetched and a.get("source") in ok_sources:
                if a.get("source") in partial_sources:
                    # capped / keyword sources: only call an ad gone when unseen for 3 days or past its deadline
                    dl = parse_date(a.get("deadline"))
                    if not ((dl and dl < today()) or (a.get("last_seen") or "")[:10] <
                            str(today() - dt.timedelta(days=3))):
                        continue
                a["gone"] = True
            if a.get("gone") and (a.get("last_seen") or "")[:10] < cutoff:
                del ADS[jid]  # users keep a snapshot of any ad they acted on
        g = gstate()
        if kinds is None:
            g.update(last_update=run_ts, last_new=new_ids)
        else:                                         # top-up: add to the "new" list of the last full update
            g.update(last_topup=run_ts, last_new=(g.get("last_new", []) + new_ids)[-3000:])
        g.update(source_status=status, ads_version=g.get("ads_version", 0) + 1)
        save_json(ADS_PATH, ADS)
        save_json(GSTATE_PATH, g)
        SCORE_CACHE.clear()
    PROGRESS.update(running=False, current="", finished_at=str(now()))
    log(f"Done in {(time.time() - t0) / 60:.1f} min. {len(fetched)} ads fetched, {len(new_ids)} new ads in the "
        f"shared pool.")
    return {"fetched": len(fetched), "new": len(new_ids)}


_STOP = {"university", "universitet", "of", "the", "and", "for", "de", "di", "institute", "college", "school",
         "research", "centre", "center", "national", "sciences", "science", "technology", "medical"}


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _org_words(s: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower()) if w not in _STOP}


def drop_reposts(jobs: list[dict], fetched: dict) -> list[dict]:
    """Job-board ads that repeat an ad we already have (same title, country and employer) are dropped."""
    idx: dict = {}
    for a in list(fetched.values()) + [a for a in ADS.values() if not a.get("gone") and a.get("detail") != "wpjm"]:
        idx.setdefault((_norm_title(a.get("title")), a.get("country", "")), []).append(a)
    out = []
    for j in jobs:
        same = idx.get((_norm_title(j.get("title")), j.get("country", "")), [])
        have = fetched.get(j["id"]) or ADS.get(j["id"])
        if (have and have.get("source") != j.get("source")) or any(_org_words(j.get("employer")) & (_org_words(a.get("employer")) |
                                     _org_words(a.get("department"))) for a in same):
            continue
        out.append(j)
    return out


def start_update_thread(kinds: set | None = None, reason: str = "") -> bool:
    with LOCK:
        if PROGRESS["running"]:
            return False
        PROGRESS["running"] = True

    def _run():
        try:
            run_update(kinds, reason)
        except Exception:
            log("Update crashed:\n" + traceback.format_exc())
            PROGRESS["running"] = False
    threading.Thread(target=_run, daemon=True).start()
    return True


def hours_since(ts) -> float:
    try:
        return (now() - dt.datetime.fromisoformat(str(ts))).total_seconds() / 3600
    except (TypeError, ValueError):
        return 1e9


def euraxess_topup_due(g: dict) -> bool:
    """Between the daily updates, refresh EURAXESS countries not fetched for `refresh_hours` (a few per run,
    every `topup_every_hours`), so every country stays about a day old despite EURAXESS' rate limit."""
    ex = sources().get("eu", {}).get("euraxess", {})
    every, fresh = float(ex.get("topup_every_hours", 6)), float(ex.get("refresh_hours", 20))
    if every <= 0 or not ex.get("enabled", True):
        return False
    if min(hours_since(g.get("last_update")), hours_since(g.get("last_topup"))) < every:
        return False
    st = g.get("source_status", {})
    return any(hours_since(st.get(s["name"], {}).get("fetched_at")) >= fresh
               for s in source_list() if s["kind"] == "euraxess" and s["enabled"])


def scheduler():
    from .maintenance import daily_backup
    while True:
        time.sleep(60)
        try:
            hrs = float(config().get("auto_update_hours", 24))
            g = gstate()
            if hrs > 0 and hours_since(g.get("last_update")) >= hrs:
                start_update_thread(reason="scheduled")
            elif euraxess_topup_due(g):
                start_update_thread({"euraxess"}, "EURAXESS top-up")
            daily_backup()
        except Exception:
            traceback.print_exc()


# ================================================================ application folders (owner, this Mac)
ORG_SHORT = {"Karolinska Institutet": "KI", "Uppsala universitet": "UU", "Uppsala University": "UU",
             "Stockholms universitet": "SU", "Stockholm University": "SU", "Lunds universitet": "LU",
             "Lund University": "LU", "Umeå universitet": "Umea", "Umea University": "Umea",
             "Kungliga Tekniska högskolan": "KTH", "Göteborgs universitet": "GU", "University of Gothenburg": "GU",
             "Sveriges Lantbruksuniversitet": "SLU", "Swedish University of Agricultural": "SLU",
             "Linköpings universitet": "LiU", "Linköping University": "LiU", "Örebro": "ORU", "Malmö": "MAU",
             "Chalmers": "Chalmers"}


def scan_folders() -> list[dict]:
    from .core import APP_DIR
    root, out = job_root(), []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    me = os.path.abspath(os.path.join(APP_DIR, ".."))
    for n in names:
        p = os.path.join(root, n)
        m = re.match(r"^(\d+)\.(.+)$", n)
        if not (m and os.path.isdir(p)) or os.path.abspath(p) == me:
            continue
        jid = ""
        idf = os.path.join(p, ".jobscreen_id")
        if os.path.exists(idf):
            jid = open(idf, encoding="utf-8").read().strip()
        out.append({"num": int(m.group(1)), "name": n, "path": p, "job_id": jid,
                    "modified": dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d")})
    return sorted(out, key=lambda x: -x["num"])


def job_view(user: str, jid: str) -> dict | None:
    e = A.uload(user, "jobs", {}).get(jid, {})
    if e.get("manual"):
        return dict(e)
    base = ADS.get(jid) or e.get("snap")
    if not base:
        return None
    j = dict(base); j["id"] = jid
    if e.get("deadline"):
        j["deadline"] = e["deadline"]
    return j


def create_folder(user: str, jid: str) -> str:
    from .core import ascii_slug, http_get, strip_html
    with LOCK:
        j = job_view(user, jid)
        if not j:
            raise ValueError("unknown job")
        folders = scan_folders()
        for f in folders:
            if f["job_id"] == jid:
                return f["path"]
        num = max([f["num"] for f in folders] + [0]) + 1
        org = j.get("employer", "")
        short = next((v for k, v in ORG_SHORT.items() if k.lower() in org.lower()), None) or ascii_slug(org, 2) or "Org"
        path = os.path.join(job_root(), f"{num}.{short}_{ascii_slug(j['title'])}")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, ".jobscreen_id"), "w", encoding="utf-8") as f:
            f.write(jid)
        desc = j.get("description", "")
        if jid.startswith("varbi:") and j.get("url"):
            try:
                page = http_get(j["url"], accept="text/html")
                i = page.find('<div class="job-desc')
                if i >= 0:
                    seg = page[i:]
                    cut = min([k for k in (seg.find("what:login"), seg.find("Share links"), seg.find("<footer")) if k > 0]
                              or [len(seg)])
                    seg = seg[:seg.rfind("<", 0, cut)] if cut < len(seg) else seg
                    seg = re.sub(r"</(td|th)>", " </\\1>", seg)
                    desc = strip_html(seg)[:20000]
            except Exception as e:
                desc += f"\n\n(could not fetch full ad: {e})"
        md = [f"# {j['title']}", "", f"- Employer: {j.get('employer','')} - {j.get('department','')}",
              f"- City: {j.get('city','')}", f"- Deadline: {j.get('deadline','') or 'n/a'}",
              f"- Published: {j.get('published','')}", f"- Apply: {j.get('url','')}",
              f"- Job-screen id: {jid}", f"- Folder created: {today()}", "", "## Ad text", "", desc]
        with open(os.path.join(path, "job_ad.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        jobs = A.uload(user, "jobs", {})
        e = jobs.setdefault(jid, {})
        e["folder"] = path
        if e.get("status") in (None, "new", "interested"):
            e["status"] = "applying"; e["status_changed"] = str(today())
        if not e.get("manual") and jid in ADS:
            e["snap"] = {k: ADS[jid].get(k, "") for k in SNAP_FIELDS}
        A.usave(user, "jobs", jobs)
    return path


# ================================================================ links shown on "Other job sites"
def user_links(user: str, prof: dict) -> list[dict]:
    q = urllib.parse.quote_plus(search_words(prof) or prof.get("field", "") or "research")
    out = []
    for l in site().get("links", []):
        out.append({"name": l["name"], "url": l["url"].replace("{q}", q), "note": l.get("note", ""), "kind": "site"})
    for l in prof.get("manual_sources", []) if A.is_owner(user) else []:
        out.append({"name": l["name"], "url": l["url"], "note": "your own link", "kind": "own"})
    return out


# ================================================================ to-dos + state
def auto_todos(jobs, st, prof, funding, links, min_s) -> list[dict]:
    items, dis = [], set(st.get("dismissed", []))
    s = prof.get("simple", {})
    if prof.get("mode") != "advanced" and not (s.get("topics") or s.get("roles")):
        items.append({"key": "setup-profile", "text": "Pick your field and keywords in Search profile so ads get scored",
                      "due": "", "kind": "setup", "prio": 0})
    new = [j for j in jobs if j.get("status") == "new" and j.get("score", 0) >= min_s and not j.get("gone")
           and (j["days_left"] is None or j["days_left"] >= 0)]
    if new:
        items.append({"key": f"screen:{len(new)}", "text": f"Screen {len(new)} new matching job ads",
                      "due": str(today()), "kind": "screen", "prio": 1})
    for j in jobs:
        st_, dl, jid = j.get("status"), j["days_left"], j["id"]
        label = f"{j['title']} ({j.get('employer','')})"
        if st_ in ("interested", "applying") and (dl is None or dl >= 0):
            verb = "Apply" if st_ == "applying" else "Decide / start application"
            items.append({"key": f"apply:{jid}", "text": f"{verb}: {label}", "due": j.get("deadline") or "",
                          "kind": "apply", "job_id": jid, "prio": 0 if dl is not None and dl <= 7 else 2})
        if st_ == "applied":
            changed = parse_date(j.get("status_changed")) or today()
            if (today() - changed).days >= int(prof.get("follow_up_days", 21)):
                items.append({"key": f"follow:{jid}:{changed}", "text": f"Follow up (applied {changed}): {label}",
                              "due": "", "kind": "follow", "job_id": jid, "prio": 3})
        if st_ == "interview":
            items.append({"key": f"interview:{jid}", "text": f"Prepare interview: {label}",
                          "due": "", "kind": "interview", "job_id": jid, "prio": 0})
    for f in funding:
        dl = days_left(f.get("deadline"))
        if f.get("status") in SKIP_FUND or dl is None or dl < 0:
            continue
        if dl <= int(prof.get("funding_warn_days", 90)):
            items.append({"key": f"fund:{f['id']}:{f['deadline']}", "text": f"Funding: {f['name']} ({f.get('status','')})",
                          "due": f["deadline"], "kind": "funding", "prio": 0 if dl <= 14 else 2})
    stale = []
    for link in links:
        last = parse_date(st["links_checked"].get(link["name"]))
        if not last or (today() - last).days >= int(prof.get("manual_check_days", 7)):
            stale.append(link["name"])
    if stale:
        items.append({"key": f"links:{today()}", "text": "Check other job sites: " + ", ".join(stale[:6]) +
                      (" ..." if len(stale) > 6 else ""), "due": str(today()), "kind": "links", "prio": 2})
    items = [i for i in items if i["key"] not in dis]
    return sorted(items, key=lambda i: (i["prio"], i["due"] or "9999"))


def _merge_entry(j: dict, e: dict) -> dict:
    j["status"] = e.get("status") or j.get("status") or "new"
    for k in ("notes", "starred", "folder", "status_changed"):
        if e.get(k):
            j[k] = e[k]
    if e.get("deadline"):
        j["deadline"] = e["deadline"]
    return j


def full_state(user: str, local: bool = False) -> dict:
    with LOCK:
        role = A.role_of(user)
        owner, admin = role == "owner", role in ("owner", "admin")
        prof = get_profile(user)
        entries = A.uload(user, "jobs", {})
        st = A.load_state(user)
        funding = A.uload(user, "funding", [])
        if roll_user_funding(funding):
            A.usave(user, "funding", funding)
        g = gstate()
        scores, min_s = user_scores(user, prof)
        folders = scan_folders() if owner else []
        by_id = {f["job_id"]: f["path"] for f in folders if f["job_id"]}
        jobs = []
        for jid, ad in ADS.items():
            e = entries.get(jid, {})
            j = {k: ad.get(k, "") for k in AD_FIELDS if k != "description"}
            j["score"], j["matches"], j["flags"] = scores.get(jid, (0, [], []))
            jobs.append(_merge_entry(j, e))
        for jid, e in entries.items():
            if jid in ADS:
                continue
            if e.get("manual"):
                j = {k: v for k, v in e.items() if k != "description"}
            elif e.get("snap"):
                j = dict(e["snap"]); j.update(id=jid, gone=True, score=e.get("score", 0), matches=[], flags=[])
            else:
                continue
            jobs.append(_merge_entry(j, e))
        for j in jobs:
            if j["id"] in by_id:
                j["folder"] = by_id[j["id"]]
            j["days_left"] = days_left(j.get("deadline"))
            if not owner:
                j.pop("folder", None)
        for f in funding:
            f["days_left"] = days_left(f.get("deadline"))
        links = user_links(user, prof)
        ss = site()
        return {"user": user, "role": role, "is_admin": admin, "is_owner": owner, "local": local and owner,
                "version": VERSION,
                "jobs": jobs, "todos": st["todos"], "auto_todos": auto_todos(jobs, st, prof, funding, links, min_s),
                "funding": funding, "folders": folders, "links": links,
                "links_checked": st["links_checked"], "last_update": g.get("last_update"),
                "last_new": g.get("last_new", []), "source_status": g.get("source_status", {}),
                "min_score": min_s, "profile": prof, "presets": presets(), "progress": PROGRESS,
                "today": str(today()), "announcement": ss.get("announcement", ""),
                "job_root": job_root() if owner else "", "public_url": public_url() if admin else "",
                "statuses": STATUSES, "fund_statuses": FUND_STATUSES, "countries": countries(),
                "n_catalog": len(presets().get("funding_catalog", [])), "status_labels": STATUS_LABEL,
                "auto_update_hours": config().get("auto_update_hours", 24)}


def export_markdown(user: str) -> str:
    """Dashboard as Markdown (download from 'My applications'). Nothing is written to disk."""
    s = full_state(user)
    min_s = s["min_score"]
    L = [f"# Job Screen - {user}", "", f"_Exported {now():%Y-%m-%d %H:%M}. Job list updated {s.get('last_update')}._",
         "", "## To-do", ""]
    manual = [t for t in s["todos"] if not t.get("done")]
    for t in s["auto_todos"]:
        L.append(f"- [ ] {t['text']}" + (f" - **due {t['due']}**" if t.get("due") else ""))
    for t in manual:
        L.append(f"- [ ] {t['text']}" + (f" - due {t['due']}" if t.get("due") else ""))
    if not s["auto_todos"] and not manual:
        L.append("- nothing due")
    pipe = [j for j in s["jobs"] if j.get("status") in ACTIVE]
    order = {"offer": 0, "interview": 1, "applying": 2, "interested": 3, "applied": 4}
    L += ["", "## My applications", "", "| Status | Position | Employer | Deadline | Notes |", "|---|---|---|---|---|"]
    for j in sorted(pipe, key=lambda j: (order.get(j["status"], 9), j.get("deadline") or "9999")):
        note = (j.get("notes") or "").replace("\n", " ").replace("|", "/")[:120]
        L.append(f"| {STATUS_LABEL.get(j['status'], j['status'])} | [{j.get('title_en') or j['title']}]({j['url']}) | "
                 f"{j.get('employer','')} | {j.get('deadline','')} | {note} |")
    if not pipe:
        L.append("| - | none yet | | | |")
    new = sorted([j for j in s["jobs"] if j.get("status") == "new" and j.get("score", 0) >= min_s and not j.get("gone")
                  and (j["days_left"] is None or j["days_left"] >= 0)], key=lambda j: -j["score"])
    L += ["", f"## New matches ({len(new)})", "", "| Score | Position | Employer | Place | Deadline |",
          "|---|---|---|---|---|"]
    for j in new[:40]:
        place = ", ".join(x for x in (j.get("city", ""), j.get("country", "")) if x)
        L.append(f"| {j['score']} | [{j.get('title_en') or j['title']}]({j['url']}) | {j.get('employer','')} | {place} | "
                 f"{j.get('deadline','')} |")
    fund = sorted([f for f in s["funding"] if f["days_left"] is not None and f["days_left"] >= 0
                   and f.get("status") not in SKIP_FUND], key=lambda f: f["deadline"])
    if fund:
        L += ["", "## Funding deadlines", ""]
        for f in fund[:15]:
            L.append(f"- **{f['deadline']}** - [{f['name']}]({f.get('url','')}) - {f.get('status','')}")
    return "\n".join(L) + "\n"
