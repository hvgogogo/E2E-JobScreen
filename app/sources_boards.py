"""Job boards built on WordPress + WP Job Manager (v2.3).

    wpjm   e.g. Life Science Jobs (lifesciencejobs.thebiologybro.com).  The WordPress REST API
           /wp-json/wp/v2/job-listings returns every open ad as JSON (100 per page) with the employer,
           location ("Institute, City, Country"), the employer's own application link and the full ad text.

The board is worldwide, so ads are kept only for the countries in sources.json ("region": "europe" or a list).
Many ads are re-posts of ads we already have from Varbi / jobs.ac.uk / EURAXESS; engine.run_update drops
those duplicates (same Varbi job id, or same title + employer).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
import urllib.parse

from .core import http_get, strip_html, today

EUROPE = {
    "Albania", "Andorra", "Austria", "Belgium", "Bosnia and Herzegovina", "Bulgaria", "Croatia", "Cyprus",
    "Czech Republic", "Czechia", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary",
    "Iceland", "Ireland", "Italy", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Monaco",
    "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland", "Portugal", "Romania", "Serbia",
    "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "United Kingdom"}
COUNTRY_FIX = {"UK": "United Kingdom", "England": "United Kingdom", "Scotland": "United Kingdom",
               "Wales": "United Kingdom", "Northern Ireland": "United Kingdom", "The Netherlands": "Netherlands",
               "Czechia": "Czech Republic", "USA": "United States", "US": "United States"}
FIELDS = "id,date,link,title,content,meta,job-types"
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"), 1)}
DL_HEAD = re.compile(r"(application deadline|deadline for applications?|closing date(?: for applications)?|"
                     r"last date(?: of receiving applications| for applications)?|apply by|apply before|"
                     r"applications? (?:will be )?(?:accepted|considered|open) until|bewerbungsfrist|"
                     r"deadline)\W{0,4}(?:is|:|-|on)?", re.I)
_D = r"(\d{1,2})(?:st|nd|rd|th)?"
DATE_RX = [  # (regex, order)  order: dmy / mdy / ymd
    (re.compile(_D + r"\.?\s+(?:of\s+)?([A-Za-z]{3,9})\.?,?\s+(\d{4})"), "dMy"),
    (re.compile(r"([A-Za-z]{3,9})\.?\s+" + _D + r",?\s+(\d{4})"), "Mdy"),
    (re.compile(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"), "dmy"),
]


def _country(loc: str) -> tuple[str, str]:
    """'University of Zurich, Zurich, Switzerland' -> ('Zurich', 'Switzerland')."""
    parts = [p.strip() for p in (loc or "").split(",") if p.strip()]
    if not parts:
        return "", ""
    c = COUNTRY_FIX.get(parts[-1], parts[-1])
    return (parts[-2] if len(parts) > 1 else ""), c


def _mk_date(y, m, d) -> dt.date | None:
    try:
        return dt.date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def find_deadline(text: str) -> str:
    """First plausible date written right after 'Application deadline', 'Closing date', 'Last date' ..."""
    t = re.sub(r"\s+", " ", text or "")
    lo, hi = today() - dt.timedelta(days=45), today() + dt.timedelta(days=400)
    for m in DL_HEAD.finditer(t):
        win = t[m.end():m.end() + 70]
        best = None
        for rx, order in DATE_RX:
            g = rx.search(win)
            if not g or (best and g.start() >= best[0]):
                continue
            a, b, c = g.groups()
            if order == "dMy":
                d = _mk_date(c, MONTHS.get(b[:3].lower()), a) if b[:3].lower() in MONTHS else None
            elif order == "Mdy":
                d = _mk_date(c, MONTHS.get(a[:3].lower()), b) if a[:3].lower() in MONTHS else None
            elif order == "ymd":
                d = _mk_date(a, b, c)
            else:
                d = _mk_date(c, b, a)
            if d:
                best = (g.start(), d)
        if best and lo <= best[1] <= hi:
            return str(best[1])
    return ""


def _ad(x: dict, label: str, types: dict) -> dict:
    meta = x.get("meta") or {}
    city, country = _country(meta.get("_job_location", ""))
    body = strip_html(x.get("content", {}).get("rendered", ""))
    cats = [types.get(i, "") for i in x.get("job-types") or [] if types.get(i)]
    app_url = (meta.get("_application") or "").strip()
    link = x.get("link") or ""
    if not app_url.lower().startswith(("http://", "https://")):
        app_url = ""                                    # WP Job Manager also allows an e-mail address here
    jid = f"wpjm:{urllib.parse.urlparse(link).netloc}:{x['id']}"
    m = re.search(r"//([a-z0-9-]+)\.varbi\.com/.*jobID:(\d+)", app_url)
    if m:                                               # same id as the Varbi copy -> no duplicate
        jid = f"varbi:{m.group(1)}:{m.group(2)}"
    head = "\n".join(s for s in (("Category: " + ", ".join(cats)) if cats else "",
                                 ("Salary: " + meta["_job_salary"]) if meta.get("_job_salary") else "") if s)
    return {"id": jid, "source": label, "title": html.unescape(strip_html(x.get("title", {}).get("rendered", ""))),
            "title_alt": "", "employer": html.unescape(meta.get("_company_name") or ""), "department": "",
            "city": city, "country": country, "lang": "", "url": app_url or link, "ad_url": link if app_url else "",
            "detail": "wpjm", "wp_api": x.get("_api", ""), "deadline": find_deadline(body),
            "published": (x.get("date") or "")[:10],
            "description": ((head + "\n\n") if head else "") + body[:3000]}


def fetch_wpjm(base: str, label: str, region="europe", max_pages: int = 15, max_age_days: int = 120,
               pause: float = 1.0) -> dict:
    base = base.rstrip("/")
    api = f"{base}/wp-json/wp/v2/job-listings"
    try:
        types = {t["id"]: html.unescape(t["name"]) for t in
                 json.loads(http_get(f"{base}/wp-json/wp/v2/job-types?per_page=100&_fields=id,name",
                                     accept="application/json"))}
    except Exception:
        types = {}
    keep = EUROPE if region == "europe" else (set(region) if isinstance(region, (list, tuple)) else None)
    oldest = str(today() - dt.timedelta(days=max_age_days))
    jobs, seen, partial, n_all = [], set(), False, 0
    for pg in range(1, max_pages + 1):
        url = f"{api}?per_page=100&page={pg}&orderby=date&order=desc&_fields={FIELDS}"
        try:
            page = json.loads(http_get(url, accept="application/json"))
        except Exception as e:
            if pg == 1:
                raise
            if "400" in str(e):                          # WordPress answers 400 for a page past the end
                break
            partial = True
            break
        if not isinstance(page, list):
            raise RuntimeError("job-listings API answer not recognised (site changed?)")
        n_all += len(page)
        for x in page:
            if (x.get("meta") or {}).get("_filled"):
                continue
            x["_api"] = f"{api}/{x['id']}"
            a = _ad(x, label, types)
            if a["id"] in seen or (keep is not None and a["country"] not in keep):
                continue
            if a["published"] and a["published"] < oldest and not a["deadline"]:
                continue                                 # very old ad without a deadline: probably closed
            if a["deadline"] and a["deadline"] < str(today()):
                continue
            seen.add(a["id"]); jobs.append(a)
        if len(page) < 100:
            break
        if pg == max_pages:
            partial = True
        time.sleep(pause)
    return {"jobs": jobs, "partial": partial, "n_all": n_all}


def wpjm_detail(ad: dict) -> str:
    api = ad.get("wp_api")
    if not api:
        return ""
    x = json.loads(http_get(api + "?_fields=content", accept="application/json"))
    return strip_html((x.get("content") or {}).get("rendered", ""))[:20000]
