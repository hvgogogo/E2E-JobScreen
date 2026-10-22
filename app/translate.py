"""Free machine translation to English (Google's public 'gtx' endpoint - no key, no cost), with a disk cache.

Titles are translated in batches of one source language (mixed-language batches with sl=auto get mis-detected).
Lines are joined with newlines; the endpoint keeps one output line per input line.  Anything that fails is simply
left untranslated - translation is a convenience, never a reason for an update to fail.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse

from .core import DATA_DIR, http_post

GTX = "https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl=en&dt=t"
CACHE_PATH = os.path.join(DATA_DIR, "translate_cache.json")
CACHE_MAX = 30000
_lock = threading.Lock()
_cache: dict | None = None
ASCII_EN = re.compile(r"^[\x00-\x7f]*$")
EN_HINT = re.compile(r"\b(the|and|of|in|for|with|position|postdoc(toral)?|research(er)?|scientist|engineer|"
                     r"lecturer|professor|fellow|assistant|associate)\b", re.I)


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def save_cache():
    with _lock:
        c = _load()
        if len(c) > CACHE_MAX:
            for k in list(c)[: len(c) - CACHE_MAX]:
                c.pop(k, None)
        tmp = CACHE_PATH + ".tmp"
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)


def looks_english(s: str) -> bool:
    return bool(ASCII_EN.match(s or "")) and bool(EN_HINT.search(s or ""))


_COOL = {"until": 0.0}         # after a 429 (too many requests) stop asking for a while


def available() -> bool:
    import time
    return time.time() >= _COOL["until"]


def _gtx(text: str, sl: str, timeout: int = 30) -> str:
    import time, urllib.error
    if not available():
        raise RuntimeError("translation service busy - try again later")
    data = urllib.parse.urlencode({"q": text}).encode()
    try:
        d = json.loads(http_post(GTX.format(sl=sl), data, "application/x-www-form-urlencoded;charset=utf-8",
                                 "application/json", timeout))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _COOL["until"] = time.time() + 900
        raise
    time.sleep(0.3)
    return "".join(seg[0] for seg in (d[0] or []) if seg and seg[0])


def translate_lines(lines: list[str], sl: str = "auto", batch_chars: int = 3500) -> list[str]:
    """English versions of lines (same order).  Cached; untranslatable lines come back unchanged."""
    c = _load()
    sl = (sl or "auto").lower()
    key = lambda s: sl + "|" + s
    todo = [s for s in dict.fromkeys(x.strip() for x in lines) if s and key(s) not in c]
    batch, size = [], 0

    def flush(b):
        if not b:
            return
        try:
            out = _gtx("\n".join(b), sl).split("\n")
        except Exception:
            return
        if len(out) != len(b):                     # alignment lost - fall back to one at a time
            out = []
            for s in b:
                try:
                    out.append(_gtx(s, sl).replace("\n", " "))
                except Exception:
                    return                             # do not cache untranslated lines; retried next time
        with _lock:
            for s, t in zip(b, out):
                c[key(s)] = t.strip() or s

    for s in todo:
        s1 = s.replace("\n", " ")
        if size + len(s1) > batch_chars and batch:
            flush(batch); batch, size = [], 0
        batch.append(s1); size += len(s1) + 1
    flush(batch)
    return [c.get(key(x.strip()), x) for x in lines]


_EN_STOP = set("the and of to in for with is are be will we you our this that on as an at by from or have has".split())
_OTHER_STOP = set(("och att det som för med är på av till vi du kommer "            # sv
                   "le la les des et est une pour dans nous vous avec sur "         # fr
                   "el los las del y que una con por para es en se "                # es
                   "il di che per con una sono della nel "                          # it
                   "ja on ett tai joka ovat kanssa "                                # fi
                   "og er som til av med vil har ikke det en "                      # no/da
                   ).split())


def text_is_english(text: str) -> bool:
    """True when a longer text (an ad body) reads as English: English stop words clearly outnumber others."""
    w = re.findall(r"[a-zåäöæøéèàüñ]+", (text or "")[:3000].lower())
    if len(w) < 20:
        return looks_english(text)
    en = sum(1 for x in w if x in _EN_STOP)
    ot = sum(1 for x in w if x in _OTHER_STOP and x not in _EN_STOP)
    return en >= 2 * ot


def translate_text(text: str, sl: str = "auto", max_chars: int = 12000) -> str:
    """English version of a longer text (an ad description), paragraph by paragraph, cached per paragraph."""
    paras = (text or "")[:max_chars].split("\n")
    out = translate_lines(paras, sl)
    return "\n".join(out)


def add_english_titles(ads: list[dict], lang_of) -> int:
    """Sets ad['title_en'] for ads whose title is not English.  lang_of(ad) -> 'sv'/'fr'/... or ''.
    Returns how many titles were translated."""
    groups: dict[str, list[dict]] = {}
    for a in ads:
        t = a.get("title", "")
        if a.get("title_en") and a.get("title_en_src") == t:
            continue
        lang = (a.get("lang") or lang_of(a) or "auto").lower()
        if lang == "en" or looks_english(t):
            a.pop("title_en", None); a.pop("title_en_src", None)
            continue
        groups.setdefault(lang, []).append(a)
    n = 0
    for lang, grp in groups.items():
        res = translate_lines([a["title"] for a in grp], lang)
        for a, en in zip(grp, res):
            if en and en.strip().lower() != a["title"].strip().lower():
                a["title_en"], a["title_en_src"] = en.strip(), a["title"]
                n += 1
    if groups:
        save_cache()
    return n
