#!/bin/bash
# Rollback script for React UI migration
# Usage: ./rollback.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_FILE="$SCRIPT_DIR/index.vanilla-backup.html"
TARGET_FILE="$SCRIPT_DIR/index.html"

if [ -f "$BACKUP_FILE" ]; then
    cp "$BACKUP_FILE" "$TARGET_FILE"
    echo "Successfully rolled back to vanilla index.html"
else
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi
