"""Shared paths, settings, JSON storage and small helpers (no app logic here)."""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import os
import re
import ssl
import subprocess
import threading
import unicodedata
import urllib.error
import urllib.request

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../0.job_screen/v2
DATA_DIR = os.environ.get("JOBSCREEN_DATA") or os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")          # site settings (yours, kept on update)
SOURCES_PATH = os.path.join(APP_DIR, "sources.json")        # where ads come from (kept on update)
PRESETS_PATH = os.path.join(APP_DIR, "presets.json")        # keyword pick-lists for the profile page
ADS_PATH = os.path.join(DATA_DIR, "ads.json")
GSTATE_PATH = os.path.join(DATA_DIR, "global_state.json")
USERS_PATH = os.path.join(DATA_DIR, "users.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
USERS_DIR = os.path.join(DATA_DIR, "users")
PUBLIC_URL_PATH = os.path.join(DATA_DIR, "public_url.txt")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")


def _version() -> str:
    try:
        return open(os.path.join(APP_DIR, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return "2.1"


VERSION = _version()
UA = f"JobScreen/{VERSION} (job-alert web app; low volume)"
LOCK = threading.RLock()

DEFAULT_LIMITS = {
    "max_users": 1000, "max_manual_jobs": 300, "max_todos": 300, "max_funding": 100, "max_text": 4000,
    "max_keywords": 40, "max_queries_per_user": 8, "max_total_queries": 80, "max_links": 30,
    "update_min_interval_min": 60, "register_per_ip_per_day": 5, "login_fail_limit": 10, "body_max_kb": 256,
}


# ---------------------------------------------------------------- time
def now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def today() -> dt.date:
    return dt.date.today()


# ---------------------------------------------------------------- json storage
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def config() -> dict:
    return load_json(CONFIG_PATH, {})


def sources() -> dict:
    return load_json(SOURCES_PATH, {})


def presets() -> dict:
    return load_json(PRESETS_PATH, {})


def gstate() -> dict:
    return load_json(GSTATE_PATH, {})


def limits() -> dict:
    d = dict(DEFAULT_LIMITS)
    d.update(config().get("limits", {}))
    return d


def job_root() -> str:
    return os.path.abspath(os.path.join(APP_DIR, config().get("job_hunting_root", "../..")))


def public_url() -> str:
    try:
        return open(PUBLIC_URL_PATH, encoding="utf-8").read().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- http
def _ssl_context():
    try:
        import certifi  # noqa: optional
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


def http_get(url: str, accept: str = "*/*", timeout: int = 30, retries: int = 3) -> str:
    """GET with a few retries: 429/503 (rate limit) waits 15 s, 30 s, ...; a dropped connection is retried at once."""
    import http.client, time
    for attempt in range(retries + 1):
        try:
            return _http_get_once(url, accept, timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == retries:
                raise
            ra = e.headers.get("Retry-After", "") if e.headers else ""
            time.sleep(min(int(ra), 90) if ra.isdigit() else 15 * (attempt + 1))
        except (http.client.IncompleteRead, ConnectionResetError, TimeoutError):
            if attempt == retries:
                raise
            time.sleep(2)


def _http_get_once(url: str, accept: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        # python.org builds on macOS sometimes lack root certificates -> fall back to curl
        if isinstance(getattr(e, "reason", None), ssl.SSLError) or "CERTIFICATE" in str(e).upper():
            out = subprocess.run(["curl", "-sSL", "-m", str(timeout), "-A", UA, "-H", f"Accept: {accept}", url],
                                 capture_output=True, text=True)
            if out.returncode == 0:
                return out.stdout
        raise


def http_post(url: str, body: bytes, ctype: str, accept: str = "*/*", timeout: int = 30) -> str:
    hdr = {"User-Agent": UA, "Accept": accept, "Content-Type": ctype}
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLError) or "CERTIFICATE" in str(e).upper():
            out = subprocess.run(["curl", "-sSL", "-m", str(timeout), "-A", UA, "-H", f"Content-Type: {ctype}",
                                  "-H", f"Accept: {accept}", "--data-binary", "@-", url],
                                 input=body.decode("utf-8"), capture_output=True, text=True)
            if out.returncode == 0:
                return out.stdout
        raise


def http_post_json(url: str, payload: dict, timeout: int = 30):
    """POST a JSON body and return the parsed JSON answer (used by EURES)."""
    body = json.dumps(payload).encode("utf-8")
    return json.loads(http_post(url, body, "application/json", "application/json", timeout))


# ---------------------------------------------------------------- text helpers
def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def ascii_slug(s: str, max_words: int = 5) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    stop = {"in", "of", "the", "and", "for", "to", "a", "an", "with", "on", "at", "inom", "i", "och", "till",
            "med", "av", "position", "tjanst", "tidsbegransad", "anstallning", "within", "focus"}
    words = [w for w in re.findall(r"[A-Za-z0-9]+", s) if w.lower() not in stop]
    return "_".join(words[:max_words]) or "position"


def parse_date(s) -> dt.date | None:
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            return None
    for fmt in ("%d.%b.%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except Exception:
        return None


def days_left(deadline) -> int | None:
    d = parse_date(deadline)
    return (d - today()).days if d else None


def clean_text(v, n=None) -> str:
    n = n or limits()["max_text"]
    return str(v if v is not None else "").replace("\x00", "")[:n].strip()


def clean_url(v) -> str:
    v = clean_text(v, 600)
    return v if re.match(r"^https?://[^\s\"'<>]+$", v, re.I) else ""


def clean_date(v) -> str:
    v = clean_text(v, 10)
    return v if parse_date(v) and re.match(r"^\d{4}-\d{2}-\d{2}$", v) else ""


def str_list(v, max_items, max_len=60) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[\n,;]+", v)
    out = []
    for x in v or []:
        x = clean_text(x, max_len)
        if x and x.lower() not in [o.lower() for o in out]:
            out.append(x)
    return out[:max_items]
