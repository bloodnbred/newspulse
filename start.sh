#!/usr/bin/env bash
# One command to set up and run NewsPulse Nifty on a fresh machine.
#
# Safe to run more than once: every step checks before it acts, so a second run
# skips the installs and goes straight to starting the servers.
#
#   ./start.sh                 set up, run
#   ./start.sh -s 50000        smaller warehouse, much faster (good for a first look)
#   ./start.sh -r              discard the warehouse and rebuild it
#   ./start.sh -n              do not open a browser

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SAMPLE=0
REBUILD=0
OPEN_BROWSER=1
while getopts ":s:rnh" opt; do
  case $opt in
    s) SAMPLE="$OPTARG" ;;
    r) REBUILD=1 ;;
    n) OPEN_BROWSER=0 ;;
    h) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option -$OPTARG" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    OK  %s\n' "$1"; }
warn() { printf '    !!  %s\n' "$1" >&2; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$1" >&2; exit 1; }

# --- 1. Python -------------------------------------------------------------
step "Checking Python"
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.11 or newer is required. Install it from https://www.python.org/downloads/"
ok "using $PY"

VENV_PY="$ROOT/.venv/bin/python"

# --- 2. Virtual environment ------------------------------------------------
step "Preparing the Python environment"
if [ ! -x "$VENV_PY" ]; then
  echo "    creating .venv (first run only, takes a minute)"
  "$PY" -m venv .venv || die "could not create .venv"
fi
ok ".venv present"

step "Installing Python packages"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt || die "pip install failed. Scroll up for the error."
ok "requirements installed"

# --- 3. Node, for the dashboard -------------------------------------------
step "Checking Node.js (needed for the dashboard)"
command -v node >/dev/null 2>&1 || {
  warn "Node.js 18+ was not found on PATH. The dashboard needs it."
  warn "Install from https://nodejs.org/ then re-run this script."
  die "Node.js is required for the dashboard but is not installed."
}
ok "node $(node --version)"

step "Installing dashboard packages"
cd "$ROOT/web"
if [ -f package-lock.json ]; then npm ci --silent; else npm install --silent; fi
cd "$ROOT"
ok "web/node_modules present"

# --- 4. The data ----------------------------------------------------------
WAREHOUSE="$ROOT/warehouse/dwm.duckdb"
step "Checking the data warehouse"
[ "$REBUILD" -eq 1 ] && { warn "-r given; deleting the existing warehouse"; rm -f "$WAREHOUSE"; }
if [ -f "$WAREHOUSE" ]; then
  ok "warehouse present ($(( $(wc -c < "$WAREHOUSE") / 1048576 )) MB)"
else
  warn "No warehouse yet. Building it now."
  warn "This downloads news and price data and can take 30-60 minutes."
  warn "  For a fast first look, stop and run:  ./start.sh -s 50000"
  echo
  if [ "$SAMPLE" -gt 0 ]; then
    "$VENV_PY" -m dwm all --sample "$SAMPLE" || die "the data build failed. Scroll up for the error."
  else
    "$VENV_PY" -m dwm all || die "the data build failed. Scroll up for the error."
  fi
  ok "warehouse built"
fi

# --- 5. Run ----------------------------------------------------------------
step "Starting the API and the dashboard"

# The API opens a snapshot copy so the tests can open the original while the
# server is holding it.
[ -f "$WAREHOUSE" ] && cp "$WAREHOUSE" "$ROOT/warehouse/dwm.serve.duckdb"

"$VENV_PY" -m dwm serve > /tmp/newspulse-api.log 2>&1 &
API_PID=$!
( cd "$ROOT/web" && npm run dev ) > /tmp/newspulse-web.log 2>&1 &
WEB_PID=$!

echo "    waiting for both to answer..."
api_up=0; web_up=0
for _ in $(seq 1 60); do
  sleep 1
  [ "$api_up" -eq 1 ] || curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && api_up=1
  [ "$web_up" -eq 1 ] || curl -fsS http://localhost:5173/ >/dev/null 2>&1 && web_up=1
  [ "$api_up" -eq 1 ] && [ "$web_up" -eq 1 ] && break
done

echo
[ "$api_up"  -eq 1 ] && ok "API      http://127.0.0.1:8000   (add /docs for the endpoint list)"
[ "$api_up"  -eq 1 ] || warn "The API did not come up. See /tmp/newspulse-api.log"
[ "$web_up"  -eq 1 ] && ok "Dashboard http://localhost:5173"
[ "$web_up"  -eq 1 ] || warn "The dashboard did not come up. See /tmp/newspulse-web.log"

if [ "$OPEN_BROWSER" -eq 1 ] && [ "$web_up" -eq 1 ]; then
  (command -v xdg-open >/dev/null 2>&1 && xdg-open http://localhost:5173/ >/dev/null 2>&1) || true
fi

echo
echo "  Press Ctrl+C to stop both."
trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT INT TERM
wait