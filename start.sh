#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up..."
  python3 -m venv .venv || exit 1
fi
# Install or update packages whenever requirements.txt has changed since the last run.
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing/updating packages..."
  .venv/bin/pip install -q --disable-pip-version-check -r requirements.txt && cp requirements.txt .venv/requirements.installed
fi
exec .venv/bin/python run.py "$@"
