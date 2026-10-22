# Maintaining Job Screen v2

Python 3.9+ standard library only. Nothing to pip-install. The front end uses no build step.

## Code map

| file | what it does | edit it when ... |
|---|---|---|
| `app/core.py` | paths, JSON load/save (atomic), config + limits, HTTP fetch, text helpers | a new global setting is needed |
| `app/accounts.py` | users, roles (owner/admin/user), password hashing, sessions, rate limits | changing login/registration rules |
| `app/sources.py` | Swedish fetchers: `fetch_varbi`, `fetch_employer`, `fetch_platsbanken`; all return the same ad dict | adding a Swedish source |
| `app/sources_eu.py` | European fetchers: `fetch_euraxess`, `fetch_eures`, `fetch_jobsac`, plus `fetch_detail` (full ad text, fetched once when a user opens the ad) | a European site changes its pages |
| `app/sources_boards.py` | job boards on WordPress + WP Job Manager (`fetch_wpjm`, e.g. Life Science Jobs): REST API, Europe filter, deadline from the ad text | a board changes, or to add another WPJM board (only `sources.json` -> `boards`) |
| `app/translate.py` | English titles and ad text (free Google gtx endpoint, one batch per language, cached in `data/translate_cache.json`) | translation breaks or is too slow |
| `app/engine.py` | shared ad pool, update run + scheduler, per-user scoring, to-dos, links, Markdown export | changing scoring or reminders |
| `app/maintenance.py` | `upgrade` (data schema), `backup`, `migrate_v1`, `doctor` | the data format changes |
| `app/web.py` | HTTP server and every `/api/...` endpoint, with permission checks | adding a button that needs the server |
| `static/app.js` | the whole page; sections 1-9 are marked in the file header (helpers, header, pages, drawer, profile, admin, router) | changing the page |
| `static/app.css` | styles; colours are variables at the top | restyling |

## Common tasks

**Add a university.**
- If it is on Varbi (`https://<x>.varbi.com/en/` opens), add `{"tenant": "<x>", "label": ..., "city": ...}` under `varbi` in `sources.json`.
- Otherwise find its organisation number in a Platsbanken ad (the JobTech API field `employer.organization_number`) and add it under `employers`.
- Restart the launcher. Doctor and the Admin page show whether it worked.

**Change limits.**
- Edit `limits` in `config.json`: max_users, queries, links and rate limits.
- The default "New matches" cutoff is `default_min_score`.

**Add keyword suggestions.** Edit `presets.json`.

**Position types (strict filter).** `positions` in `presets.json`: each type has an English label (shown in Search profile) and a regex with the words used in every supported language. An ad whose title is clearly a type the user did not select is kept out of New matches. `absorb` lists types that also count as another (a postdoc is also a researcher). Fields and topics are not strict.

**Add a European country.** Add its code to `eu.countries` in `sources.json`, add it to `COUNTRIES` in `app/sources_eu.py` (EURAXESS country id, language) and add local-language title words under `eu.eures.keywords`.

**Funding catalogue.** `funding_catalog` in `presets.json`. New accounts get it automatically; estimated deadlines that have passed move forward one year.

**Switch a broken source off.** Use the Admin page, Job sources.

## Releasing a new version (v2.2, ...)

1. Copy `v2/` to a test folder. Copy `data/` in as well, and test there on another port: `JOBSCREEN_PORT=8030 /usr/bin/python3 jobscreen_v2.py serve --no-update`.
2. If the data format changes, raise `SCHEMA` in `app/maintenance.py` and add a step to `upgrade()`. Every step must be safe to run twice.
3. Bump `VERSION`.
4. Install: replace everything **except `data/`**. The launcher runs `upgrade` on start, which makes a backup zip first.
5. Restart with the Desktop launcher. It notices the version change and restarts the old server.

## Restore a backup

Stop the app. Move `data/` aside. Unzip `data/backups/backup_....zip` into `v2/`; it contains `data/...`. Start the app again.

## Version history

- **2.3.0** (2026-10-03)
  - Full update every 24 h instead of 6 h; launcher start fetches only when the last update is > 12 h old.
  - EURAXESS: France was cut at the 30-page limit (292-298 ads every run) - limit now 60 pages, with a page
    budget per run (`page_budget` 70) and a 6-hourly EURAXESS-only top-up so each country is refreshed ~daily.
  - New source: Life Science Jobs (`sources.json` -> `boards`, kind `wpjm`, code `app/sources_boards.py`).
    Any other WordPress + WP Job Manager board can be added the same way (label, base URL).
  - Platsbanken free-text search counts as a partial source: an ad it stops returning is marked removed only
    after 3 days unseen or past its deadline (v2.2 marked ~275 still-open ads as removed).
  - Terminal log lines carry date and time, plus start reason and duration of each update.
  - No more BrokenPipeError tracebacks when a browser closes or reloads during a page load.
  - Admin source table names the EURAXESS / EURES / jobs.ac.uk / job-board kinds.

- **2.2.0** (2026-10-01)
  - Europe: EURAXESS (UK, France, Spain, Italy, Finland, Norway, Denmark; researcher profiles R2-R4), EURES (local-language title searches) and jobs.ac.uk. Every ad has a country.
  - English everywhere: Search profile options are English only; ad titles and text in other languages are machine-translated into English, with the original one click away.
  - Positions you want is strict (multilingual position types); "Hide ads whose title contains" was removed.
  - One filter bar (search, country, source, sort) shared by New matches and All ads.
  - Landing page: 4K photo hero, open ads, countries, visits (privacy-friendly daily counter, `data/visits.json`), users and last update.
  - Wording: My pipeline is now My applications; stages are Saved, Preparing, Applied, Interview, Offer, Rejected, Not for me.
  - Funding: every account gets the funding catalogue (before, it was only added after clicking Import); past estimated deadlines roll to next year.

- **2.1.0** (2026-10-01)
  - Code split into `app/` modules and `sources.json`/`presets.json`.
  - Roles: owner/admin/user, with Set as admin, block and reset password on the Admin page. Up to 1000 accounts.
  - The 404 sources (Linköping, Örebro, Malmö) now come from Platsbanken by employer, and Gothenburg, Chalmers, SLU, Luleå, Linnaeus, Mid Sweden, Karlstad and Mälardalen were added.
  - New interface: home dashboard behind the logo, chip-based search profile, a "profile saved, N new matches" dialog, a job details panel and a pipeline board.
  - JOB_HUNTING_v2.md replaced by a Markdown download.
  - Site-wide min score and links are managed by admins. Daily backups. `doctor` command.
- **2.0.0** (2026-10-01): first multi-user version.
