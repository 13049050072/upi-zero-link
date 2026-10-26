#!/bin/sh
set -eu
TARGET=${1:?usage: ROLLBACK.sh <target-dir>}
BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$TARGET"
cp "$BASE_DIR/BASELINE_FILE" "$TARGET/docs-extraction-errors.md"
cp "$BASE_DIR/BASELINE_PROGRESS_FILE" "$TARGET/progress.md"
shasum -a 256 "$TARGET/docs-extraction-errors.md" "$BASE_DIR/BASELINE_FILE" "$TARGET/progress.md" "$BASE_DIR/BASELINE_PROGRESS_FILE"
