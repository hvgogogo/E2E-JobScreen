"""European job-ad fetchers (v2.2).  Same ad dict as sources.py, plus "lang" (language of the original title).

    euraxess   EURAXESS (European Commission research-jobs portal), one source per country.  HTML list pages,
               10 ads per page, filtered to researcher profiles R2-R4 (postdoc and above; R1 = PhD students).
    eures      EURES (EU public employment services) - JSON search API, one source per country.  Title search is
               fuzzy, so hits are kept only if the title looks academic/research (ACADEMIC regex below).
    jobsac     jobs.ac.uk (UK academic jobs) - HTML search pages, 25 ads per page.

Every fetcher returns {"jobs": [...], "partial": bool}.  partial=True means a page cap was hit, so ads that were
not seen this time must not be marked as removed straight away (engine.run_update handles that).
"""
from __future__ import annotations

import datetime as dt
import html
import re
import time
import urllib.parse

from .core import http_get, http_post_json, parse_date, strip_html, today

# code: (name, EURAXESS country id, EURES location code, language of local ads)
COUNTRIES = {
    "SE": ("Sweden", 770, "se", "sv"),
    "UK": ("United Kingdom", 771, "", "en"),
    "FR": ("France", 793, "fr", "fr"),
    "ES": ("Spain", 788, "es", "es"),
    "FI": ("Finland", 760, "fi", "fi"),
    "NO": ("Norway", 768, "no", "no"),
    "DK": ("Denmark", 757, "dk", "da"),
    "IT": ("Italy", 781, "it", "it"),
    "DE": ("Germany", 794, "de", "de"),
    "NL": ("Netherlands", 798, "nl", "nl"),
    "IE": ("Ireland", 763, "ie", "en"),
    "AT": ("Austria", 791, "at", "de"),
    "BE": ("Belgium", 792, "be", "fr"),
    "PT": ("Portugal", 784, "pt", "pt"),
    "CH": ("Switzerland", 799, "ch", "de"),
}
PAUSE = 0.4          # seconds between page requests - be polite to public services
EUX_PAUSE = 2.5      # EURAXESS answers 429 (Too Many Requests) when paged quickly


def country_name(code: str) -> str:
    return COUNTRIES.get(code.upper(), (code,))[0]


def _txt(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(strip_html(s or ""))).strip()


# ---------------------------------------------------------------------------------------------- EURAXESS
EUX = "https://euraxess.ec.europa.eu"
EUX_TEASER = '<div id="job-teaser-content">'


def _eux_field(block: str, name: str) -> str:
    m = re.search(r'class="id-' + name + r'[^"]*".*?ecl-text-standard[^>]*>(.*?)</div>\s*</div>', block, re.S)
    return _txt(m.group(1)) if m else ""


def _eux_city(loc: str, country: str) -> str:
    """'Number of offers: 1, Norway, Olav Kyrres gate 9, Trondheim, 7491, ...' -> 'Trondheim'."""
    parts = [p.strip() for p in loc.split(",") if p.strip() and not p.strip().lower().startswith("number of offers")]
    for i, p in enumerate(parts):
        if re.fullmatch(r"[A-Z]{0,3}-?\s?\d[\d\s-]{2,8}[A-Z]{0,3}", p) and i > 0:
            return parts[i - 1]
    rest = [p for p in parts if p.lower() != country.lower()]
    return rest[1] if len(rest) > 1 else (rest[0] if rest else "")


def parse_euraxess(page: str, country: str) -> list[dict]:
    out = []
    page = re.sub(r"\s+", " ", page)
    page = re.sub(r"<(\w+) ", r"<\1 ", page).replace("<a href", "<a href")
    page = re.sub(r"\s*>", ">", re.sub(r"<\s+", "<", page))
    for b in page.split(EUX_TEASER)[1:]:
        m = re.search(r'<h3 class="ecl-content-block__title"><a href="/jobs/(\d+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        jid, title = m.group(1), _txt(m.group(2))
        emp = re.search(r'primary-meta-item"><a [^>]*>(.*?)</a>', b, re.S)
        posted = re.search(r"Posted on:\s*([^<]+)<", b)
        desc = re.search(r'ecl-content-block__description">(.*?)</div>', b, re.S)
        dl = _eux_field(b, "Application-Deadline")
        loc = _eux_field(b, "Work-Locations")
        prof = _eux_field(b, "Researcher-Profile")
        field = _eux_field(b, "Research-Field")
        dept = _eux_field(b, "Department")
        d_dl = parse_date(dl.split(" - ")[0]) if dl else None
        d_pub = parse_date(posted.group(1).strip()) if posted else None
        out.append({"id": f"euraxess:{jid}", "source": f"EURAXESS {country}", "title": title, "title_alt": "",
                    "employer": _txt(emp.group(1)) if emp else "", "department": dept,
                    "city": _eux_city(loc, country), "country": country, "lang": "",
                    "url": f"{EUX}/jobs/{jid}", "ad_url": "", "detail": "euraxess",
                    "deadline": str(d_dl) if d_dl else "", "published": str(d_pub) if d_pub else "",
                    "description": "\n".join(x for x in (f"Research field: {field}" if field else "",
                                                         f"Researcher profile: {prof}" if prof else "",
                                                         _txt(desc.group(1)) if desc else "") if x)})
    return out


def fetch_euraxess(code: str, max_pages: int = 20, profiles=(448, 449, 450), keywords: str = "") -> dict:
    name, cid = COUNTRIES[code][0], COUNTRIES[code][1]
    f = [f"job_country:{cid}", "offer_type:job_offer"] + [f"job_research_profile:{p}" for p in profiles]
    q = {f"f[{i}]": v for i, v in enumerate(f)}
    if keywords:
        q["keywords"] = keywords
    base = f"{EUX}/jobs/search?" + urllib.parse.urlencode(q)
    jobs, seen, partial = [], set(), False
    for pg in range(max_pages):
        try:
            page = http_get(base + (f"&page={pg}" if pg else ""), accept="text/html")
        except Exception:
            if pg == 0:
                raise
            partial = True                              # throttled half-way: keep what we have, mark nothing gone
            break
        if pg == 0 and "job-teaser" not in page and "No results" not in page and "ecl-content-item" not in page:
            raise RuntimeError("EURAXESS page format not recognised (site changed?)")
        got = [j for j in parse_euraxess(page, name) if j["id"] not in seen]
        for j in got:
            seen.add(j["id"])
        jobs += got
        last = max([int(x) for x in re.findall(r"(?:\?|&|&amp;)page=(\d+)", page)] or [0])
        if not got or pg >= last:
            break
        if pg == max_pages - 1:
            partial = True
        time.sleep(EUX_PAUSE)
    time.sleep(EUX_PAUSE * 3)                           # breathing room before the next country
    return {"jobs": jobs, "partial": partial}


def euraxess_detail(url: str) -> str:
    """Full ad text from an EURAXESS job page (offer description + requirements + additional information)."""
    t = http_get(url, accept="text/html")
    out = []
    for head, stop in (("Offer Description", ("Where to apply", "Requirements")),
                       ("Requirements", ("Additional Information", "Work Location(s)")),
                       ("Additional Information", ("Work Location(s)", "Contact"))):
        i = [m.end() for m in re.finditer(re.escape(head), t)]
        if len(i) < 2:
            continue
        st = i[-1]
        ends = [t.find(s, st) for s in stop if t.find(s, st) > 0]
        chunk = t[st:min(ends) if ends else st + 12000]
        txt = strip_html(chunk).strip()
        if txt:
            out.append((head + "\n" if head != "Offer Description" else "") + txt)
    return "\n\n".join(out)[:20000]


# ---------------------------------------------------------------------------------------------- EURES
EURES_SEARCH = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
EURES_DETAIL = "https://europa.eu/eures/api/jv-searchengine/public/jv/id/{id}?requestLang=en"
EURES_PAGE = "https://europa.eu/eures/portal/jv-se/jv-details/{id}?lang=en"
ACADEMIC = re.compile(
    r"post-?doc|postdoktor|post-?doctora|posdoctoral|research|researcher|forsk(er|are|ning)|chercheu|"
    r"enseignant|investigador|investigación|ricercat|ricerca|assegn|tutki|scien|lecturer|lektor|professor|"
    r"profesor|docent|bioinform|ph\.?d|doktor|doctora|stipendiat|universit|laborat|ingénieur de recherche", re.I)
NOT_ACADEMIC = re.compile(r"\b(farm|worker|driver|chauffeur|cleaner|nettoyage|cook|waiter|sales|butikk|vendeu)", re.I)


def _eures_body(cc: str, kw: str, page: int) -> dict:
    return {"resultsPerPage": 50, "page": page, "sortSearch": "MOST_RECENT",
            "keywords": [{"keyword": kw, "specificSearchCode": "TITLE"}], "publicationPeriod": None,
            "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [], "positionScheduleCodes": [],
            "sectorCodes": [], "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [],
            "locationCodes": [cc], "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [],
            "minNumberPost": None, "sessionId": "jobscreen", "requestLanguage": "en"}


def _ms_date(v) -> str:
    try:
        return str(dt.datetime.utcfromtimestamp(int(v) / 1000).date())
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _eures_hit(h: dict, country: str, lang: str) -> dict:
    emp = h.get("employer") or {}
    name = emp.get("name", "") if isinstance(emp, dict) else str(emp or "")
    langs = h.get("availableLanguages") or []
    return {"id": f"eures:{h['id']}", "source": f"EURES {country}", "title": _txt(h.get("title", "")),
            "title_alt": "", "employer": name, "department": "", "city": "", "country": country,
            "lang": (langs[0] if langs else lang) or lang, "url": EURES_PAGE.format(id=h["id"]), "ad_url": "",
            "detail": "eures", "deadline": _ms_date(h.get("lastApplicationDate")) if h.get("lastApplicationDate") else "",
            "published": _ms_date(h.get("creationDate")),
            "description": _txt(h.get("description", ""))[:3000]}


def fetch_eures(code: str, keywords: list[str], pages: int = 2) -> dict:
    name, _, cc, lang = COUNTRIES[code]
    jobs, seen, partial = [], set(), False
    for kw in keywords:
        for pg in range(1, pages + 1):
            d = http_post_json(EURES_SEARCH, _eures_body(cc, kw, pg))
            hits = d.get("jvs") or []
            for h in hits:
                t = h.get("title", "")
                if h.get("id") in seen or not ACADEMIC.search(t) or NOT_ACADEMIC.search(t):
                    continue
                seen.add(h["id"])
                jobs.append(_eures_hit(h, name, lang))
            time.sleep(PAUSE)
            if len(hits) < 50 or pg * 50 >= int(d.get("numberRecords") or 0):
                break
            if pg == pages:
                partial = True
    return {"jobs": jobs, "partial": partial}


def eures_detail(jid: str) -> dict:
    """{'description', 'deadline', 'city'} for one EURES ad (jid without the 'eures:' prefix)."""
    d = http_get(EURES_DETAIL.format(id=urllib.parse.quote(jid)), accept="application/json")
    import json as _json
    d = _json.loads(d)
    out = {"description": "", "deadline": "", "city": ""}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "description" and isinstance(v, str) and len(v) > len(out["description"]):
                    out["description"] = v
                elif k == "lastApplicationDate" and v and not out["deadline"]:
                    out["deadline"] = _ms_date(v)
                elif k == "cityName" and isinstance(v, str) and not out["city"]:
                    out["city"] = v.title()
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    out["description"] = strip_html(out["description"])[:20000]
    return out


# ---------------------------------------------------------------------------------------------- jobs.ac.uk
JAC = "https://www.jobs.ac.uk"
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}


def _jac_date(s: str, future: bool) -> str:
    """'23 Nov' (no year) -> ISO date; closing dates lie ahead of today, placed dates behind it."""
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3})", s or "")
    if not m or m.group(2).lower() not in MONTHS:
        return ""
    t = today()
    try:
        d = dt.date(t.year, MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return ""
    if future and d < t - dt.timedelta(days=30):
        d = d.replace(year=t.year + 1)
    if not future and d > t + dt.timedelta(days=2):
        d = d.replace(year=t.year - 1)
    return str(d)


def parse_jac(page: str) -> list[dict]:
    out = []
    for b in page.split('<div class="j-search-result__result')[1:]:
        aid = re.search(r'data-advert-id="(\d+)"', b)
        a = re.search(r'<div class="j-search-result__text">\s*<a href="([^"]+)">(.*?)</a>', b, re.S)
        if not aid or not a:
            continue
        g = lambda rx: (lambda m: _txt(m.group(1)) if m else "")(re.search(rx, b, re.S))
        sal = g(r"<strong>Salary: </strong>(.*?)</div>")
        out.append({"id": f"jac:{aid.group(1)}", "source": "jobs.ac.uk", "title": _txt(a.group(2)), "title_alt": "",
                    "employer": g(r'j-search-result__employer">(.*?)</div>'),
                    "department": g(r'j-search-result__department">(.*?)</div>'),
                    "city": g(r"<div>Location:(.*?)</div>"), "country": "United Kingdom", "lang": "en",
                    "url": JAC + a.group(1), "ad_url": "", "detail": "jobsac",
                    "deadline": _jac_date(g(r'date--blue[^"]*">(.*?)</span>'), True),
                    "published": _jac_date(g(r"<strong>Date Placed: </strong>(.*?)</div>"), False),
                    "description": ("Salary: " + sal) if sal else ""})
    return out


def fetch_jobsac(queries: list[str], pages: int = 4) -> dict:
    jobs, seen, partial = [], set(), False
    for q in queries:
        for pg in range(pages):
            url = (f"{JAC}/search/?keywords={urllib.parse.quote_plus(q)}&sortOrder=1&pageSize=25"
                   f"&startIndex={1 + 25 * pg}")
            page = http_get(url, accept="text/html")
            got = parse_jac(page)
            if pg == 0 and not got and "j-search-result" not in page and "job-count" not in page:
                raise RuntimeError("jobs.ac.uk page format not recognised (site changed?)")
            for j in got:
                if j["id"] not in seen:
                    seen.add(j["id"]); jobs.append(j)
            time.sleep(PAUSE)
            if len(got) < 25:
                break
            if pg == pages - 1:
                partial = True
    return {"jobs": jobs, "partial": partial}


def jobsac_detail(url: str) -> str:
    t = http_get(url, accept="text/html")
    i = t.find('id="job-description"')
    if i < 0:
        i = t.find("job-description")
    if i < 0:
        return ""
    chunk = t[i:i + 40000]
    for stop in ('class="j-advert__apply', 'id="apply', "j-advert-details__footer", "<footer"):
        k = chunk.find(stop)
        if k > 500:
            chunk = chunk[:k]
    return strip_html("<" + chunk.split(">", 1)[-1]).strip()[:20000]


def fetch_detail(ad: dict) -> dict:
    """Full text for an ad whose list page gave only a teaser.  Returns fields to merge into the ad."""
    kind = ad.get("detail")
    if kind == "euraxess":
        return {"description": euraxess_detail(ad["url"])}
    if kind == "eures":
        d = eures_detail(ad["id"].split(":", 1)[1])
        return {k: v for k, v in d.items() if v and (k == "description" or not ad.get(k))}
    if kind == "jobsac":
        txt = jobsac_detail(ad["url"])
        return {"description": (ad.get("description", "") + "\n\n" + txt).strip()} if txt else {}
    if kind == "wpjm":
        from .sources_boards import wpjm_detail
        txt = wpjm_detail(ad)
        head = ad.get("description", "").split("\n\n", 1)[0] if ad.get("description", "").startswith("Category") else ""
        return {"description": ((head + "\n\n") if head else "") + txt} if txt else {}
    return {}
