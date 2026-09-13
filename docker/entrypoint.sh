#!/bin/sh
set -e

# First run: create the DAV user from EXCENSE_USERNAME/EXCENSE_PASSWORD env
# (purpose-built for unattended bootstrap). Later create users with:
#   docker compose exec excense excense user-add <name>
if [ ! -f "$EXCENSE_DATA_DIR/auth/htpasswd" ] && [ -n "$EXCENSE_PASSWORD" ]; then
  echo "$EXCENSE_PASSWORD" | excense user-add "$EXCENSE_USERNAME"
fi

# Serving implies syncing: run the Graph bridge alongside Radicale. The
# loop retries until `excense auth` has produced a token, so it is safe to
# start before the first sign-in.
if [ "$1" = "serve" ]; then
  (while true; do excense sync-loop || true; sleep 30; done) &
fi

exec excense "$@"