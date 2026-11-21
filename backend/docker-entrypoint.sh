#!/bin/sh
set -eu

uv run --no-sync python -m app.db.migrate
uv run --no-sync python -m app.rules.seed

exec "$@"
