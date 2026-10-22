# Job Screen

**Designed and built end-to-end (E2E) by [Wei Hang](https://github.com/hvgogogo)** — backend, data pipeline, scoring engine, accounts and security, web front end, operations and documentation.

Job Screen is a multi-user web application that collects research job adverts from across Europe every day, removes duplicates, translates non-English titles, and scores each advert against each user's own search profile. I built it to manage my own postdoc search, and it now runs as a small shared service for other researchers.

![Job Screen](static/img/hero-1920.jpg)

## What it does

- **Collects adverts daily** from Varbi and Platsbanken (Sweden), EURAXESS, EURES, jobs.ac.uk and Life Science Jobs, covering Sweden, the UK, France, Spain, Finland, Norway, Denmark and Italy.
- **Cleans and merges** them into one shared pool: duplicate detection across sources, deadline extraction, automatic translation of titles and ad text (cached).
- **Scores every advert per user** against a personal search profile (position types, fields, keywords, exclusions), with flags for things like "local language required" or "PhD ≤ 3 years rule".
- **Tracks applications**: saved jobs, notes, to-dos with reminders, a funding-call catalogue, and Markdown export.
- **Multi-user**: accounts with salted PBKDF2 password hashes, roles (owner / admin / user), strict per-user data isolation, rate limits on login and registration, and an admin page.

## Engineering

| Area | How |
|---|---|
| Backend | Python 3.9+, **standard library only** (no dependencies), threaded HTTP server with a JSON API |
| Data pipeline | One fetcher per source behind a common ad format; scheduled updates every 24 h with per-source status, page budgets and rotation for rate-limited sources |
| Front end | Single-page app in plain HTML, CSS and JavaScript, no build step |
| Storage | Atomic JSON writes; shared ad pool plus private per-user folders |
| Operations | Automatic daily backups and a backup before every upgrade, schema upgrades, a `doctor` health check, versioned releases |
| Deployment | One-click launcher; optional public HTTPS address through a Cloudflare tunnel |
| Docs | [MAINTAINING.md](MAINTAINING.md): code map, adding sources, releasing updates, restoring backups |

About 4,000 lines of code:

```
app/core.py            paths, atomic JSON storage, config, HTTP fetch
app/accounts.py        users, roles, password hashing, sessions, rate limits
app/sources*.py        source fetchers (Sweden, EU portals, job boards)
app/translate.py       batched, cached translation
app/engine.py          ad pool, update scheduler, per-user scoring, to-dos, export
app/maintenance.py     upgrade, backup, migration, doctor
app/web.py             HTTP server and every /api endpoint with permission checks
static/                the web page (index.html, app.js, app.css)
jobscreen_v2.py        command-line entry point
```

## Quick start

```bash
git clone https://github.com/hvgogogo/WeiHang-E2E-JobScreen.git
cd WeiHang-E2E-JobScreen
python3 jobscreen_v2.py serve          # http://localhost:8020
```

1. Open http://localhost:8020 and register an account.
2. To make it the owner account, put its username in `config.json` → `owner_account`, then run `python3 jobscreen_v2.py upgrade`.
3. On macOS you can instead double-click `start_jobscreen.command`, which also opens a public address if `cloudflared` is installed.

Other commands:

```bash
python3 jobscreen_v2.py update                 # fetch adverts once
python3 jobscreen_v2.py doctor                 # health report
python3 jobscreen_v2.py backup                 # zip the data folder
python3 jobscreen_v2.py set-password USER      # reset a password
```

All runtime data lives in `data/`, which is excluded from this repository.

## Author

**Wei Hang**, PhD — bioinformatician (single-cell and spatial omics, machine learning).
GitHub [@hvgogogo](https://github.com/hvgogogo) · ORCID [0009-0009-3954-1123](https://orcid.org/0009-0009-3954-1123)
