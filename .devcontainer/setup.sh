#!/usr/bin/env bash
# Runs once per container build.
#
# Pins the interpreter and reinstalls the workspace so a rebuild does not
# leave `import okwan_core` broken — which it did on every rebuild before
# this file existed. The default universal image also auto-detects the
# project and generates a poetry.lock we do not use.
set -euo pipefail

echo "python: $(python --version)"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev]"

# The universal image's auto-detection writes this; we use pip, not poetry.
rm -f poetry.lock

if [ -d apps/web ]; then
  (cd apps/web && npm install --silent --no-audit --no-fund) || true
fi

echo "okwan ready — $(python -c 'import okwan_core; print(\"sdk ok\")')"
