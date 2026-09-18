#!/usr/bin/env sh
# Rebuild the public dashboard and push it to GitHub Pages.
set -e
cd "$(dirname "$0")/.."
.venv/bin/python tools/build_dashboard.py --out docs --strip-contacts
cp dashboard/index.html docs/index.html
git add docs
git commit -m "Refresh dashboard data" || { echo "nothing changed"; exit 0; }
git push
echo "pushed — Pages redeploys in about a minute"
