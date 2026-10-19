#!/bin/sh
set -eu

TARGET=${1:?usage: ROLLBACK.sh TARGET}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_NAME=$(basename -- "$TARGET")
if [ "$BASE_NAME" = "server-config.py" ] && [ -f "$SCRIPT_DIR/baseline/server.config.baseline.py" ]; then
    cp "$SCRIPT_DIR/baseline/server.config.baseline.py" "$TARGET"
    printf '%s\n' "restored $TARGET from $SCRIPT_DIR/baseline/server.config.baseline.py"
    exit 0
fi
if [ "$BASE_NAME" = "server.py" ] && [ -f "$SCRIPT_DIR/baseline/server.py.absent" ]; then
    rm -f "$TARGET"
    printf '%s\n' "removed $TARGET (baseline was absent)"
    exit 0
fi

BASELINE="${TARGET}.baseline"
if [ ! -f "$BASELINE" ]; then
    BASELINE="$SCRIPT_DIR/baseline/$(basename -- "$TARGET")"
fi
if [ ! -f "$BASELINE" ]; then
    printf '%s\n' "baseline not found for $TARGET" >&2
    exit 2
fi
cp "$BASELINE" "$TARGET"
printf '%s\n' "restored $TARGET from $BASELINE"
