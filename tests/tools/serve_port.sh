#!/bin/bash
# (Re)start a seeded demo server: serve_port.sh PORT. Prints "serving PORT" when ready (about a minute).
# The app snapshots static/ at start, so run this again after every batch of CSS edits.
# State (pid, log, routes, the synthetic catalog) goes to $FACULTY_DEMO_SCRATCH, else a faculty-demo folder in the
# system temp directory (set TMPDIR to a /mnt drive on the Faculty server). PYTHON selects the interpreter.
set -u
PORT="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
SCRATCH="${FACULTY_DEMO_SCRATCH:-$("$PY" -c 'import tempfile; print(tempfile.gettempdir())')/faculty-demo}"
mkdir -p "$SCRATCH"
if [ -f "$SCRATCH/server-$PORT.pid" ]; then kill "$(cat "$SCRATCH/server-$PORT.pid")" 2>/dev/null; sleep 1; fi
rm -rf "$SCRATCH/data-$PORT"; mkdir -p "$SCRATCH/data-$PORT"
nohup "$PY" "$HERE/demo_serve.py" "$PORT" "$SCRATCH/data-$PORT" > "$SCRATCH/serve-$PORT.log" 2>&1 &
echo $! > "$SCRATCH/server-$PORT.pid"
for _ in $(seq 1 90); do sleep 2; grep -q serving "$SCRATCH/serve-$PORT.log" && break; done
cp "$SCRATCH/data-$PORT/routes-$PORT.txt" "$SCRATCH/routes-$PORT.txt" 2>/dev/null
tail -1 "$SCRATCH/serve-$PORT.log"
