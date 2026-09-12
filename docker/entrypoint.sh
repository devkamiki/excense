#!/bin/sh
set -e

# First run: create the DAV user from EXCENSE_USERNAME/EXCENSE_PASSWORD env
# (purpose-built for unattended bootstrap). Later create users with:
#   docker compose exec excense excense user-add <name>
if [ ! -f "$EXCENSE_DATA_DIR/auth/htpasswd" ] && [ -n "$EXCENSE_PASSWORD" ]; then
  echo "$EXCENSE_PASSWORD" | excense user-add "$EXCENSE_USERNAME"
fi

exec excense "$@"