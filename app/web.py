"""HTTP server: static page + JSON API.  Every handler only touches the logged-in account's folder."""
from __future__ import annotations

import datetime as dt
import gzip
import hmac
import json
import mimetypes
import os
import secrets
import subprocess
import traceback
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import accounts as A
from . import engine as E
from . import sources_eu as EU
from . import translate as TR
from .core import (APP_DIR, LOCK, VERSION, clean_date, clean_text, clean_url, config, days_left, gstate, job_root,
                   limits, now, presets, public_url, str_list, today)

STATIC_DIR = os.path.join(APP_DIR, "static")


class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    server_version = "JobScreen/2"
    sys_version = ""

    def log_message(self, *a):
        pass

    # ------------------------------------------------------------ helpers
    def client(self) -> tuple[str, bool]:
        ip = self.client_address[0]
        fwd = self.headers.get("Cf-Connecting-IP") or (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return (fwd, False) if fwd else (ip, True)  # via the tunnel = remote; direct = this Mac
        if config().get("trust_forwarded_for") and fwd:
            return fwd, False
        return ip, False

    def is_https(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "") == "https" or '"https"' in self.headers.get("Cf-Visitor", "")

    def token(self) -> str | None:
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        return c[A.COOKIE].value if A.COOKIE in c else None

    def _send(self, code, body, ctype="application/json", cookies=(), extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        gz = len(data) > 2048 and not ctype.startswith("image/") and "gzip" in self.headers.get("Accept-Encoding", "")
        if gz:
            data = gzip.compress(data, 5)
        self.send_response(code)
        text = any(t in ctype for t in ("json", "html", "text", "javascript", "css", "svg"))
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if text else ""))
        self.send_header("Content-Length", str(len(data)))
        if gz:
            self.send_header("Content-Encoding", "gzip")
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        for ck in cookies:
            self.send_header("Set-Cookie", ck)
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the browser left (reload / closed tab) mid-answer
            self.close_connection = True

    def _cookie(self, token: str, max_age: int) -> str:
        return (f"{A.COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
                + ("; Secure" if self.is_https() else ""))

    def _static(self, rel: str):
        path = os.path.abspath(os.path.join(STATIC_DIR, rel))
        if not path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        if ctype.startswith("image/"):
            return self._send(200, data, ctype, extra={"Cache-Control": "public, max-age=604800"})
        return self._send(200, data, ctype)

    # ------------------------------------------------------------ GET
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ("/", "/index.html") or (not p.startswith("/api/") and not p.startswith("/static/")
                                          and "." not in p.rsplit("/", 1)[-1]):
            try:
                E.record_visit(self.client()[0])
            except Exception:
                pass
            return self._static("index.html")          # page routes (#/...) all serve the single-page app
        if p.startswith("/static/"):
            return self._static(p[len("/static/"):])
        if p == "/api/public_info":
            g = gstate()
            st = g.get("source_status", {})
            open_ads = [a for a in E.ADS.values() if not a.get("gone") and
                        (days_left(a.get("deadline")) is None or days_left(a.get("deadline")) >= 0)]
            by_c = {}
            for a in open_ads:
                by_c[a.get("country") or "?"] = by_c.get(a.get("country") or "?", 0) + 1
            return self._send(200, {"registration_open": E.registration_open(), "invite_required": bool(
                config().get("invite_code")), "app": config().get("app_name", "Job Screen"), "version": VERSION,
                "last_update": g.get("last_update"), "n_ads": len(open_ads), "n_open": len(open_ads),
                "n_sources": sum(1 for v in st.values() if v.get("ok")),
                "by_country": dict(sorted(by_c.items(), key=lambda kv: -kv[1])),
                "n_users": len(A.load_users()), "visits": E.visit_stats(),
                "announcement": E.site().get("announcement", "")})
        user = A.session_user(self.token())
        if p == "/api/me":
            return self._send(200, {"user": user, "role": A.role_of(user)} if user else {"user": None})
        if not user:
            return self._send(401, {"error": "login required"})
        if p == "/api/state":
            return self._send(200, E.full_state(user, self.client()[1]))
        if p == "/api/progress":
            return self._send(200, E.PROGRESS)
        if p == "/api/ad":
            jid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("id", [""])[0][:120]
            j = E.job_view(user, jid)
            if not j:
                return self._send(404, {"error": "ad not found"})
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ad = E.ADS.get(jid)
            if ad is not None and ad.get("detail") and not ad.get("detail_done"):
                try:                                    # EU list pages give only a teaser: fetch the full ad once
                    upd = EU.fetch_detail(ad)
                    with LOCK:
                        ad.update(upd); ad["detail_done"] = True
                    j.update(upd)
                except Exception as e:
                    j["detail_error"] = str(e)[:120]
            if not j.get("description") and ad is not None:
                j["description"] = ad.get("description", "")
            out = {"id": jid, "description": j.get("description", ""), "url": j.get("url", ""),
                   "ad_url": j.get("ad_url", ""), "title": j.get("title", ""), "title_en": j.get("title_en", ""),
                   "lang": E.lang_of(j) or ""}
            out["is_english"] = TR.text_is_english(out["description"])
            if qs.get("en", ["0"])[0] == "1" and out["description"] and not out["is_english"]:
                try:
                    en = TR.translate_text(out["description"], out["lang"] or "auto")
                    TR.save_cache()
                    if en.strip() and en.strip() != out["description"].strip():
                        out["description_en"] = en
                    else:
                        out["translate_error"] = "translation not available right now"
                except Exception as e:
                    out["translate_error"] = str(e)[:120]
            return self._send(200, out)
        if p == "/api/export.md":
            fn = f"jobscreen_{user}_{today()}.md"
            return self._send(200, E.export_markdown(user).encode("utf-8"), "text/markdown",
                              extra={"Content-Disposition": f'attachment; filename="{fn}"'})
        if p == "/api/admin/overview":
            if not A.is_admin(user):
                return self._send(403, {"error": "admin only"})
            return self._send(200, self.admin_overview(user))
        self._send(404, {"error": "not found"})

    def admin_overview(self, me: str) -> dict:
        users = A.load_users()
        rows = []
        for u, r in sorted(users.items(), key=lambda kv: (A.ROLES.index(A.role_of(kv[0], users)), kv[0])):
            rows.append({"username": u, "role": A.role_of(u, users), "created": r.get("created", ""),
                         "last_login": r.get("last_login", ""), "disabled": bool(r.get("disabled")), "me": u == me})
        st = gstate().get("source_status", {})
        srcs = [{"name": s["name"], "kind": s["kind"], "enabled": s["enabled"], "site": s["site"],
                 "country": s.get("country", ""), "status": st.get(s["name"], {})} for s in E.source_list()]
        s = E.site()
        return {"users": rows, "max_users": limits()["max_users"], "registration_open": s["registration_open"],
                "invite_required": bool(config().get("invite_code")), "announcement": s.get("announcement", ""),
                "links": s.get("links", []), "sources": srcs, "public_url": public_url(),
                "last_update": gstate().get("last_update"), "n_ads": len(E.ADS), "version": VERSION}

    # ------------------------------------------------------------ POST
    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if self.headers.get("X-JS") != "1":  # simple CSRF guard: custom header cannot be sent cross-site
            return self._send(403, {"error": "bad request"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > limits()["body_max_kb"] * 1024:
            return self._send(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except Exception:
            return self._send(400, {"error": "bad json"})
        ip, local = self.client()
        try:
            if p.startswith("/api/auth/"):
                return self.auth_route(p, body, ip)
            user = A.session_user(self.token())
            if not user:
                return self._send(401, {"error": "login required"})
            if A.rate_hit("api", user, 600, 600):
                return self._send(429, {"error": "too many requests - slow down"})
            if p.startswith("/api/admin/"):
                if not A.is_admin(user):
                    return self._send(403, {"error": "admin only"})
                res = self.admin_route(p, body, user)
            else:
                res = self.route(p, body, user, local)
            self._send(200, res if res is not None else {"ok": True})
        except PermissionError as e:
            self._send(403, {"error": str(e) or "not allowed"})
        except (ValueError, KeyError, AssertionError) as e:
            self._send(400, {"error": str(e) or "bad request"})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": "server error: " + str(e)[:200]})

    # ------------------------------------------------------------ auth
    def auth_route(self, p, b, ip):
        lim = limits()
        if p == "/api/auth/login":
            u = clean_text(b.get("username"), 40).lower()
            pw = str(b.get("password") or "")
            # per network: login_fail_limit; per username (any network): 3x, so others cannot easily lock you out
            if A.rate_count("loginfail-ip", ip, 900) >= lim["login_fail_limit"] or \
                    A.rate_count("loginfail-u", u, 900) >= 3 * lim["login_fail_limit"]:
                return self._send(429, {"error": "too many failed logins - wait 15 minutes"})
            rec = A.load_users().get(u)
            ok = A.check_pw(pw, rec["pw"]) if rec else (A.check_pw(pw, A.DUMMY_PW) and False)
            if not ok:
                A.rate_hit("loginfail-ip", ip, 10 ** 6, 900); A.rate_hit("loginfail-u", u, 10 ** 6, 900)
                return self._send(401, {"error": "wrong username or password"})
            if rec.get("disabled"):
                return self._send(403, {"error": "this account is blocked - contact the site admin"})
            A.touch_login(u)
            tok = A.new_session(u)
            return self._send(200, {"user": u, "role": A.role_of(u)}, cookies=[self._cookie(tok, A.SESSION_DAYS * 86400)])
        if p == "/api/auth/register":
            if b.get("website"):  # honeypot field, invisible to humans
                return self._send(400, {"error": "bad request"})
            if not E.registration_open():
                return self._send(403, {"error": "registration is closed at the moment"})
            inv = config().get("invite_code")
            if inv and not hmac.compare_digest(str(b.get("invite") or ""), str(inv)):
                return self._send(403, {"error": "invite code required / wrong"})
            u = clean_text(b.get("username"), 40).lower()
            pw = str(b.get("password") or "")
            if not A.USERNAME_RE.match(u) or u in A.RESERVED:
                return self._send(400, {"error": "username: 3-32 characters, a-z 0-9 . _ - (start with a letter/digit)"})
            if len(pw) < 8 or len(pw) > 200 or pw.lower() == u:
                return self._send(400, {"error": "password: at least 8 characters and not the username"})
            if A.rate_hit("register", ip, lim["register_per_ip_per_day"], 86400, record=False):
                return self._send(429, {"error": "too many registrations from this network today"})
            with LOCK:
                users = A.load_users()
                if u in users:
                    return self._send(400, {"error": "username taken"})
                if len(users) >= lim["max_users"]:
                    return self._send(403, {"error": "the site is full - no new accounts at the moment"})
                users[u] = {"pw": A.hash_pw(pw), "created": str(now()), "last_login": str(now()), "role": "user"}
                A.save_users(users)
                fl = []
                E.add_catalog(fl, lim["max_funding"])     # new accounts start with the funding catalog
                A.usave(u, "profile", E.default_profile()); A.usave(u, "jobs", {}); A.usave(u, "funding", fl)
                A.usave(u, "state", {"todos": [], "dismissed": [], "links_checked": {}})
            A.rate_hit("register", ip, lim["register_per_ip_per_day"], 86400)
            tok = A.new_session(u)
            return self._send(200, {"user": u, "role": "user"}, cookies=[self._cookie(tok, A.SESSION_DAYS * 86400)])
        if p == "/api/auth/logout":
            A.drop_sessions(token=self.token())
            return self._send(200, {"ok": True}, cookies=[self._cookie("", 0)])
        user = A.session_user(self.token())
        if not user:
            return self._send(401, {"error": "login required"})
        if p in ("/api/auth/password", "/api/auth/delete"):
            if A.rate_count("pwfail-u", user, 900) >= lim["login_fail_limit"]:
                return self._send(429, {"error": "too many attempts - wait 15 minutes"})
            users = A.load_users()
            if not A.check_pw(str(b.get("password") or ""), users[user]["pw"]):
                A.rate_hit("pwfail-u", user, 10 ** 6, 900)
                return self._send(401, {"error": "current password is wrong"})
            if p == "/api/auth/password":
                new = str(b.get("new_password") or "")
                if len(new) < 8 or len(new) > 200:
                    return self._send(400, {"error": "new password: at least 8 characters"})
                with LOCK:
                    users = A.load_users(); users[user]["pw"] = A.hash_pw(new); A.save_users(users)
                A.drop_sessions(user=user)
                tok = A.new_session(user)
                return self._send(200, {"ok": True}, cookies=[self._cookie(tok, A.SESSION_DAYS * 86400)])
            if A.role_of(user, users) == "owner":
                return self._send(400, {"error": "the owner account cannot be deleted from the web page"})
            A.delete_user(user)
            return self._send(200, {"ok": True}, cookies=[self._cookie("", 0)])
        return self._send(404, {"error": "unknown endpoint"})

    # ------------------------------------------------------------ every logged-in account
    def route(self, p, b, user, local):
        lim = limits()
        owner = A.is_owner(user)
        if p == "/api/update":
            last = gstate().get("last_update")
            age_min = (now() - dt.datetime.fromisoformat(last)).total_seconds() / 60 if last else 1e9
            if not A.is_admin(user) and age_min < lim["update_min_interval_min"]:
                return {"started": False, "message": f"The job list was updated {int(age_min)} min ago - it refreshes "
                        f"automatically every {config().get('auto_update_hours', 6)} h."}
            return {"started": E.start_update_thread()}
        if p == "/api/job":
            with LOCK:
                jobs = A.uload(user, "jobs", {})
                jid = clean_text(b.get("id"), 120)
                if jid not in E.ADS and jid not in jobs:
                    raise ValueError("unknown job")
                e = jobs.setdefault(jid, {})
                if "status" in b:
                    s = b["status"]
                    if s not in E.STATUSES:
                        raise ValueError("bad status")
                    if s != e.get("status", "new"):
                        e["status_changed"] = str(today())
                    e["status"] = s
                if "notes" in b:
                    e["notes"] = clean_text(b["notes"])
                if "starred" in b:
                    e["starred"] = bool(b["starred"])
                if "deadline" in b:
                    d = clean_date(b["deadline"])
                    if d and (jid not in E.ADS or d != E.ADS[jid].get("deadline")):
                        e["deadline"] = d
                    else:
                        e.pop("deadline", None)
                if jid in E.ADS and not e.get("manual"):
                    e["snap"] = {k: E.ADS[jid].get(k, "") for k in E.SNAP_FIELDS}
                    if e.get("status", "new") == "new" and not e.get("notes") and not e.get("deadline") \
                            and not e.get("starred"):
                        jobs.pop(jid, None)  # nothing user-specific left
                A.usave(user, "jobs", jobs)
            return
        if p == "/api/job/add":
            with LOCK:
                jobs = A.uload(user, "jobs", {})
                if sum(1 for e in jobs.values() if e.get("manual")) >= lim["max_manual_jobs"]:
                    raise ValueError("manual job limit reached")
                title = clean_text(b.get("title"), 300)
                if not title:
                    raise ValueError("title needed")
                jid = "manual:" + secrets.token_hex(6)
                st_ = b.get("status") if b.get("status") in E.STATUSES else "interested"
                j = {"id": jid, "source": clean_text(b.get("source"), 80) or "added by me", "title": title,
                     "title_alt": "", "employer": clean_text(b.get("employer"), 200), "department": "",
                     "city": clean_text(b.get("city"), 120), "country": "", "url": clean_url(b.get("url")),
                     "deadline": clean_date(b.get("deadline")), "published": str(today()),
                     "description": clean_text(b.get("description")), "status": st_,
                     "status_changed": str(today()), "notes": clean_text(b.get("notes")), "first_seen": str(now()),
                     "manual": True}
                j["score"], j["matches"], j["flags"] = E.score_job(j, E.build_scoring(E.get_profile(user), owner))
                jobs[jid] = j
                A.usave(user, "jobs", jobs)
            return {"id": jid}
        if p == "/api/job/delete":
            with LOCK:
                jobs = A.uload(user, "jobs", {})
                jid = clean_text(b.get("id"), 120)
                if jobs.get(jid, {}).get("manual"):
                    jobs.pop(jid); A.usave(user, "jobs", jobs)
            return
        if p == "/api/todo":
            with LOCK:
                st = A.load_state(user)
                act = b.get("action")
                if act == "add":
                    if len(st["todos"]) >= lim["max_todos"]:
                        raise ValueError("to-do limit reached - delete some done items")
                    txt = clean_text(b.get("text"), 500)
                    if txt:
                        st["todos"].append({"id": secrets.token_hex(6), "text": txt, "due": clean_date(b.get("due")),
                                            "done": False, "created": str(today())})
                elif act == "toggle":
                    for t in st["todos"]:
                        if t["id"] == b.get("id"):
                            t["done"] = not t.get("done"); t["done_on"] = str(today())
                elif act == "delete":
                    st["todos"] = [t for t in st["todos"] if t["id"] != b.get("id")]
                elif act == "clear_done":
                    st["todos"] = [t for t in st["todos"] if not t.get("done")]
                elif act == "dismiss":
                    st["dismissed"] = (st["dismissed"] + [clean_text(b.get("key"), 200)])[-500:]
                A.usave(user, "state", st)
            return
        if p == "/api/link_checked":
            with LOCK:
                st = A.load_state(user)
                names = [l["name"] for l in E.user_links(user, E.get_profile(user))]
                pick = names if b.get("all") else [n for n in names if n == b.get("name")]
                for nme in pick:
                    st["links_checked"][nme] = str(today())
                A.usave(user, "state", st)
            return
        if p in ("/api/funding", "/api/funding/add", "/api/funding/delete", "/api/funding/import"):
            with LOCK:
                fl = A.uload(user, "funding", [])
                if p == "/api/funding/import":
                    E.add_catalog(fl, lim["max_funding"])
                elif p == "/api/funding/add":
                    if len(fl) >= lim["max_funding"]:
                        raise ValueError("funding list limit reached")
                    name = clean_text(b.get("name"), 200)
                    if not name:
                        raise ValueError("name needed")
                    fl.append({"id": "f_" + secrets.token_hex(5), "name": name, "funder": clean_text(b.get("funder"), 120),
                               "deadline": clean_date(b.get("deadline")), "estimated": bool(b.get("estimated")),
                               "url": clean_url(b.get("url")), "status": b.get("status") if b.get("status") in
                               E.FUND_STATUSES else "idea", "notes": clean_text(b.get("notes"))})
                elif p == "/api/funding/delete":
                    fl = [f for f in fl if f["id"] != b.get("id")]
                else:
                    for f in fl:
                        if f["id"] == b.get("id"):
                            if "status" in b and b["status"] in E.FUND_STATUSES:
                                f["status"] = b["status"]
                            if "deadline" in b:
                                f["deadline"] = clean_date(b["deadline"])
                            if "notes" in b:
                                f["notes"] = clean_text(b["notes"])
                            if "estimated" in b:
                                f["estimated"] = bool(b["estimated"])
                for f in fl:
                    f.pop("days_left", None)
                A.usave(user, "funding", fl)
            return
        if p == "/api/profile":
            return self.save_profile(user, owner, b)
        # ---------- owner + this Mac only
        if p == "/api/job/folder":
            if not owner:
                raise PermissionError("application folders are only for the site owner")
            return {"path": E.create_folder(user, clean_text(b.get("id"), 120))}
        if p == "/api/open":
            if not owner:
                raise PermissionError("owner only")
            if not local:
                raise PermissionError("opening Finder only works on the Mac that runs the app")
            path = os.path.abspath(str(b.get("path") or ""))
            if not (path == job_root() or path.startswith(job_root() + os.sep)):
                raise PermissionError("outside job_hunting folder")
            subprocess.run(["open", path]); return
        raise ValueError("unknown endpoint " + p)

    def save_profile(self, user, owner, b):
        lim = limits()
        with LOCK:
            prof = E.get_profile(user)
            s = b.get("simple") or {}
            prof["field"] = clean_text(b.get("field", prof.get("field", "")), 200)
            prof["preset"] = clean_text(b.get("preset", prof.get("preset", "")), 60)
            prof["simple"] = {"roles": str_list(s.get("roles", []), lim["max_keywords"]),
                              "topics": str_list(s.get("topics", []), lim["max_keywords"])}
            for k, lo, hi in (("follow_up_days", 1, 365), ("funding_warn_days", 1, 730), ("manual_check_days", 1, 90)):
                if k in b:
                    prof[k] = max(lo, min(hi, int(b[k] or lo)))
            if owner:
                if "mode" in b:
                    prof["mode"] = "advanced" if b["mode"] == "advanced" else "simple"
                if b.get("scoring_json"):
                    sc = json.loads(b["scoring_json"])
                    E.build_scoring({"mode": "advanced", "scoring": sc}, True)  # raises if a regex is invalid
                    prof["scoring"] = sc
                if "queries" in b:
                    prof["queries"] = str_list(b.get("queries", []), 50, 60)
                if "manual_sources" in b:
                    links = []
                    for item in (b.get("manual_sources") or [])[:lim["max_links"]]:
                        nm, url = clean_text(item.get("name"), 60), clean_url(item.get("url"))
                        if nm and url:
                            links.append({"name": nm, "url": url})
                    prof["manual_sources"] = links
            else:
                prof["mode"] = "simple"
                prof.pop("queries", None); prof.pop("manual_sources", None); prof.pop("scoring", None)
            A.usave(user, "profile", prof)
            E.SCORE_CACHE.pop(user, None)
        st = E.full_state(user)
        n = sum(1 for j in st["jobs"] if j.get("status") == "new" and j.get("score", 0) >= st["min_score"]
                and not j.get("gone") and (j["days_left"] is None or j["days_left"] >= 0))
        return {"ok": True, "new_matches": n}

    # ------------------------------------------------------------ admin (owner + admins)
    def admin_route(self, p, b, me):
        if p == "/api/admin/role":
            A.set_role(me, clean_text(b.get("username"), 40), str(b.get("role")))
            return self.admin_overview(me)
        if p == "/api/admin/block":
            A.set_blocked(clean_text(b.get("username"), 40), bool(b.get("blocked")))
            return self.admin_overview(me)
        if p == "/api/admin/reset_password":
            return {"temp_password": A.reset_password(clean_text(b.get("username"), 40))}
        if p == "/api/admin/user/delete":
            u = clean_text(b.get("username"), 40)
            if u == me or A.role_of(u) in ("owner", "admin"):
                raise PermissionError("remove admin rights first (you cannot delete yourself here)")
            if u in A.load_users():
                A.delete_user(u)
                E.SCORE_CACHE.pop(u, None)
            return self.admin_overview(me)
        if p == "/api/admin/settings":
            with LOCK:
                s = E.site()
                if "registration_open" in b:
                    s["registration_open"] = bool(b["registration_open"])
                if "announcement" in b:
                    s["announcement"] = clean_text(b["announcement"], 300)
                E.save_site(s)
            return self.admin_overview(me)
        if p == "/api/admin/links":
            links = []
            for item in (b.get("links") or [])[:limits()["max_links"]]:
                nm, url = clean_text(item.get("name"), 60), clean_url(str(item.get("url") or "").replace("{q}", "QQQ"))
                if nm and url:
                    links.append({"name": nm, "url": url.replace("QQQ", "{q}"), "note": clean_text(item.get("note"), 120)})
            with LOCK:
                s = E.site(); s["links"] = links; E.save_site(s)
            return self.admin_overview(me)
        if p == "/api/admin/source":
            name = clean_text(b.get("name"), 80)
            if name not in [s["name"] for s in E.source_list()]:
                raise ValueError("unknown source")
            with LOCK:
                s = E.site()
                off = [x for x in s.get("disabled_sources", []) if x != name]
                if not b.get("enabled"):
                    off.append(name)
                s["disabled_sources"] = off; E.save_site(s)
            return self.admin_overview(me)
        if p == "/api/admin/update":
            return {"started": E.start_update_thread()}
        raise ValueError("unknown endpoint " + p)


def serve(port: int, host: str, update_on_start: bool = True):
    import threading
    from .core import USERS_DIR
    from .maintenance import upgrade
    os.makedirs(USERS_DIR, exist_ok=True)
    upgrade()
    E.load_ads()
    A.load_sessions()
    age = E.hours_since(E.gstate().get("last_update"))
    if update_on_start and age >= float(E.config().get("update_on_start_after_hours", 12)):
        E.start_update_thread(reason="start")
    elif update_on_start:
        print(f"Last update {age:.1f} h ago - next automatic update when it is "
              f"{E.config().get('auto_update_hours', 24)} h old (Admin page: Update now).", flush=True)
    threading.Thread(target=E.scheduler, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"Job Screen {VERSION} running at http://localhost:{port}  (bound to {host})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
