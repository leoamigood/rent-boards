#!/usr/bin/env sh
# Rebuild every board and push the public copies to GitHub Pages.
set -e
cd "$(dirname "$0")/.."
.venv/bin/python tools/build_pages.py
git add docs
git commit -m "Refresh boards" || { echo "nothing changed"; exit 0; }
git push
echo "pushed — Pages redeploys in about a minute"
