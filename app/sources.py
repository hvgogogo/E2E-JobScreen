"""Job-ad fetchers.  Every fetcher returns a list of ad dicts with the same keys.

    varbi       universities that publish on <tenant>.varbi.com (KI, UU, SU, KTH, LU, UmU ...)
    employer    every ad of one employer on Platsbanken (JobTech API), selected by organisation number.
                Used for universities that do NOT use Varbi (LiU, ORU, MAU, GU, Chalmers, SLU ... publish via
                ReachMee/own sites, but all of them are mirrored on Platsbanken).
    platsbanken free-text searches on Platsbanken (base queries + words from users' search profiles)
"""
from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from .core import http_get, parse_date, strip_html

JOBTECH = "https://jobsearch.api.jobtechdev.se/search?"
VARBI_ROW = re.compile(r"<tr>\s*<td[^>]*pos-title[^>]*>(.*?)</tr>", re.S)


def fetch_varbi(tenant: str, label: str, city_default: str = "") -> list[dict]:
    base = f"https://{tenant}.varbi.com"
    page = http_get(f"{base}/en/", accept="text/html")
    try:
        rss = http_get(f"{base}/en/what:rssfeed/", accept="application/rss+xml")
        root = ET.fromstring(rss.encode("utf-8"))
        rss_items = {}
        for it in root.iter("item"):
            m = re.search(r"jobID:(\d+)", it.findtext("link") or "")
            if m:
                rss_items[m.group(1)] = {"desc": strip_html(it.findtext("description") or ""),
                                         "pub": parse_date(it.findtext("pubDate")),
                                         "title_sv": (it.findtext("title") or "").strip()}
    except Exception:
        rss_items = {}
    jobs = []
    for row in VARBI_ROW.findall(page):
        m = re.search(r'href="([^"]*jobID:(\d+)[^"]*)"[^>]*>(.*?)</a>', row, re.S)
        if not m:
            continue
        url, jid, title = m.group(1), m.group(2), strip_html(m.group(3))
        cells = re.findall(r"<td[^>]*class=\"([^\"]*)\"[^>]*>(.*?)</td>", "<td class=\"pos-title\">" + row, re.S)
        cell = {c.split()[-1] if c else "": strip_html(v) for c, v in cells}
        get = lambda key: next((v for c, v in cell.items() if key in c), "")  # noqa: E731
        info = rss_items.get(jid, {})
        end = parse_date(get("ends"))
        jobs.append({
            "id": f"varbi:{tenant}:{jid}", "source": label, "title": title,
            "title_alt": info.get("title_sv", "") if info.get("title_sv", "") != title else "",
            "employer": label, "department": get("subcompany"), "city": get("town") or city_default,
            "country": "Sweden", "url": url, "deadline": str(end) if end else "",
            "published": str(info["pub"]) if info.get("pub") else "", "description": info.get("desc", "")[:3000],
        })
    if not jobs and "pos-title" not in page:
        raise RuntimeError("Varbi page format not recognised (site changed?)")
    return jobs


def _jobtech_hit(h: dict, source: str) -> dict:
    app_url = (h.get("application_details") or {}).get("url") or ""
    m = re.search(r"//([a-z0-9-]+)\.varbi\.com/.*jobID:(\d+)", app_url)
    jid = f"varbi:{m.group(1)}:{m.group(2)}" if m else f"jobtech:{h['id']}"
    emp = h.get("employer") or {}
    addr = h.get("workplace_address") or {}
    return {
        "id": jid, "source": source, "title": (h.get("headline") or "").strip(), "title_alt": "",
        "employer": emp.get("name") or emp.get("workplace") or "", "department": emp.get("workplace") or "",
        "orgnr": emp.get("organization_number") or "",
        "city": addr.get("municipality") or addr.get("city") or "", "country": "Sweden",
        "url": app_url or h.get("webpage_url") or "", "ad_url": h.get("webpage_url") or "",
        "deadline": (h.get("application_deadline") or "")[:10], "published": (h.get("publication_date") or "")[:10],
        "description": ((h.get("description") or {}).get("text") or "")[:3000],
    }


def _jobtech(params: dict) -> dict:
    return json.loads(http_get(JOBTECH + urllib.parse.urlencode(params, doseq=True), accept="application/json"))


def fetch_jobtech(queries: list[str], limit: int = 100) -> list[dict]:
    out = {}
    for q in queries:
        for h in _jobtech({"q": q, "limit": limit}).get("hits", []):
            j = _jobtech_hit(h, "Platsbanken")
            out[j["id"]] = j
    return list(out.values())


def fetch_employer(orgnr: str, label: str, city_default: str = "", max_ads: int = 400) -> list[dict]:
    """All current Platsbanken ads of one employer (organisation number), paged 100 at a time."""
    out, offset = {}, 0
    while offset < max_ads:
        d = _jobtech({"employer": orgnr, "limit": 100, "offset": offset})
        hits = d.get("hits", [])
        for h in hits:
            j = _jobtech_hit(h, label)
            j["employer"] = label  # one clean name instead of "LINKOPINGS UNIVERSITET" etc.
            j["city"] = j["city"] or city_default
            out[j["id"]] = j
        total = (d.get("total") or {}).get("value", 0)
        offset += 100
        if not hits or offset >= total:
            break
    return list(out.values())
