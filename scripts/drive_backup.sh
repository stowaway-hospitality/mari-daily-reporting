#!/usr/bin/env bash
# Daily headless backup of the live pipeline data to Google Drive.
# Runs as the `stowaway` user via ops/com.stowaway.drivebackup.plist.
# Replaces the Drive-push step of the old weekly-sales-pull Claude task.
#
# ONE-TIME SETUP (must run as the stowaway user — an OAuth "Allow" click that
# automation cannot do for you):
#   /opt/homebrew/bin/rclone config create gdrive drive scope drive \
#     root_folder_id 1cUEsU0sdljyqwnXx7I6SCXpcrEHJettN
#   (a browser opens once — click Allow; this writes ~/.config/rclone/rclone.conf
#    for the stowaway user, so launchd can use it headlessly thereafter.)
set -uo pipefail
RCLONE=/opt/homebrew/bin/rclone
REPO="/Users/stowaway/Documents/STOW/Sales Reports/Daily Reporting"
LOG_TS() { date '+%F %T'; }
cd "$REPO" || { echo "$(LOG_TS) repo not found: $REPO"; exit 1; }

# Refresh from GitHub first so the backup reflects the latest committed data.
git pull --quiet --rebase origin main 2>/dev/null || true

if ! "$RCLONE" listremotes 2>/dev/null | grep -q '^gdrive:'; then
  echo "$(LOG_TS) gdrive remote not configured for $(whoami) — skipping."
  echo "  Enable once with: $RCLONE config create gdrive drive scope drive root_folder_id 1cUEsU0sdljyqwnXx7I6SCXpcrEHJettN"
  exit 0
fi

# copy (not sync) into a dedicated subfolder of the shared Daily Sales drive so
# nothing else in that folder is ever touched or deleted.
"$RCLONE" copy "$REPO/data" "gdrive:Daily Reporting Backup/data" \
  --update --checksum --fast-list --stats 15s
echo "$(LOG_TS) drive backup OK ($($RCLONE size "gdrive:Daily Reporting Backup/data" --json 2>/dev/null))"
