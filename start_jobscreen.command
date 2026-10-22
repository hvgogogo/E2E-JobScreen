#!/bin/bash
# Job Screen v2 launcher (multi-user website) - double-click in Finder or on the Desktop.
# 1 starts the server on this Mac   2 opens a public https address (Cloudflare quick tunnel)   3 opens the site in Edge.
# Close this window (or press Ctrl+C) to stop the website.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${JOBSCREEN_PORT:-8020}"
URL="http://localhost:$PORT"
title() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
die()   { printf '\033[31m%s\033[0m\n' "$1"; read -r -p "Press Enter to close..." _; exit 1; }
openb() { open -a "Microsoft Edge" "$1" 2>/dev/null || open -a "Google Chrome" "$1" 2>/dev/null || open "$1"; }

cd "$APP_DIR" 2>/dev/null || die "App folder not found: $APP_DIR"
mkdir -p data
PY=""
for c in /usr/bin/python3 "$HOME/miniforge3/envs/py2025/bin/python3" python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3,9))' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "python3 >= 3.9 not found"
export PYTHONDONTWRITEBYTECODE=1
VER=$(cat VERSION 2>/dev/null || echo "?")
SHARE=$("$PY" -c 'import json;print(int(json.load(open("config.json")).get("public_tunnel",True)))' 2>/dev/null || echo 1)

title "Job Screen v$VER"
RUNNING=$(curl -s "$URL/api/public_info" 2>/dev/null | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
if [ -n "$RUNNING" ] && [ "$RUNNING" = "$VER" ]; then
  echo "Already running."; [ -s data/public_url.txt ] && echo "Public address: $(cat data/public_url.txt)"
  openb "$URL"; sleep 2; exit 0
fi
if [ -n "$RUNNING" ]; then echo "An older version ($RUNNING) is running - restarting it as v$VER ..."; fi
OLD=$(lsof -ti tcp:"$PORT" 2>/dev/null); [ -n "$OLD" ] && kill $OLD 2>/dev/null && sleep 1
pkill -f "cloudflared tunnel --no-autoupdate --url $URL" 2>/dev/null
[ -f data/users.json ] || { [ -f seed_admin.json ] && "$PY" jobscreen_v2.py migrate; }
"$PY" jobscreen_v2.py upgrade | sed 's/^/  /'      # safe to run every time: backs up first, does nothing if up to date

rm -f data/public_url.txt
"$PY" jobscreen_v2.py serve & SRV=$!
TUN=""
cleanup() { [ -n "$TUN" ] && kill $TUN 2>/dev/null; kill $SRV 2>/dev/null; rm -f data/public_url.txt; }
trap cleanup EXIT INT TERM
for i in $(seq 1 40); do curl -s -o /dev/null "$URL/api/public_info" && break; sleep 0.3; done
kill -0 $SRV 2>/dev/null || die "The server did not start - see the messages above."

if [ "$SHARE" = "1" ] && command -v cloudflared >/dev/null 2>&1; then
  cloudflared tunnel --no-autoupdate --url "$URL" > data/tunnel.log 2>&1 & TUN=$!
  PUB=""
  for i in $(seq 1 40); do
    PUB=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' data/tunnel.log | head -1); [ -n "$PUB" ] && break; sleep 0.5
  done
  if [ -n "$PUB" ]; then
    echo "$PUB" > data/public_url.txt; printf '%s' "$PUB" | pbcopy
    printf '\n\033[1;32mPublic address (copied to clipboard): %s\033[0m\n' "$PUB"
    echo "Share it with others. It changes every time you restart this launcher (also shown on the Admin page)."
  else
    echo "Could not open the public tunnel (see data/tunnel.log) - running on this Mac only."
  fi
elif [ "$SHARE" = "1" ]; then
  echo "cloudflared not installed (brew install cloudflared) - running on this Mac only."
fi
echo "Local address : $URL"
echo "Close this window or press Ctrl+C to stop the website."
( sleep 0.5; openb "$URL" ) &
wait $SRV
