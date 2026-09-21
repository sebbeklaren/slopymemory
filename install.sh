#!/usr/bin/env bash
# slopymemory installer — Linux, no root. Six announced, idempotent steps; a second run repairs whatever is
# missing and changes nothing that is already right. Nothing here edits your shell's rc files.
#
#   ./install.sh [--yes] [--postgres system|embedded] [--no-harness]
#
#   --yes         answer every question with yes (the download size is still printed first)
#   --postgres    which Postgres new stores go on; default: the system one when it can provision, else embedded
#   --no-harness  do not register the launcher in any coding harness (later: slopymem register <harness>)
#
# Everything lands under $SLOPYMEM_HOME (default ~/.slopymemory): the venv, the stores, the embedded Postgres.
set -euo pipefail
cd "$(dirname "$0")"
HOME_DIR="${SLOPYMEM_HOME:-$HOME/.slopymemory}"
VENV="$HOME_DIR/venv"
YES=0; PG=""; HARNESS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1;;
    --postgres=*) PG="${1#*=}";;
    --postgres) shift; PG="${1:-}";;
    --no-harness) HARNESS=0;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "unknown flag $1 (try --help)"; exit 2;;
  esac
  shift
done
case "$PG" in ""|system|embedded) ;; *) echo "--postgres takes system or embedded, not '$PG'"; exit 2;; esac
YESFLAG=""; [ "$YES" = 1 ] && YESFLAG="--yes"
say() { printf '\n== %s\n' "$*"; }
[ "$(uname -s)" = Linux ] || { echo "Linux only for now — see SETUP.md#python"; exit 1; }

say "1/6 uv + Python 3.13"
if command -v uv >/dev/null 2>&1; then
  echo "uv $(uv --version | awk '{print $2}') found: $(command -v uv)"
else
  echo "uv not found; installing it into ~/.local/bin (the official installer, no root, your shell rc files untouched)"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || { echo "uv did not land on PATH; add ~/.local/bin to PATH and re-run — see SETUP.md#python"; exit 1; }
fi
if PY="$(uv python find 3.13 2>/dev/null)"; then
  echo "Python 3.13: $PY"
else
  echo "Python 3.13 not found; uv will download one (about 30 MB, no root): uv python install 3.13"
  uv python install 3.13
  PY="$(uv python find 3.13)"
fi

say "2/6 the package (venv at $VENV)"
mkdir -p "$HOME_DIR"
# The preflight runs on the bare interpreter (standard library only): the real download size read from uv.lock
# minus what uv's cache already holds, the 4 GB free-space gate, and the question — before anything is fetched.
"$PY" -c "import sys; sys.path.insert(0, 'src'); from slopymemory.install_steps import preflight; sys.exit(preflight(sys.argv[1], yes=sys.argv[2] == '1'))" "$VENV" "$YES"
# The lock, exactly (--frozen); the CPU torch index is in pyproject.toml; no dev tools; the package copied into the
# venv (--no-editable), so this checkout can be deleted afterwards. A venv that is already complete is left alone.
UV_PROJECT_ENVIRONMENT="$VENV" uv sync --frozen --no-dev --no-editable --python "$PY"
"$VENV/bin/python" -c "import slopymemory, agent_memory; print('package ok')"

say "3/6 Postgres"
# probes the system Postgres and offers it; else the embedded one; verifies either with a scratch database
"$VENV/bin/slopymem" install-postgres ${PG:+--postgres "$PG"} $YESFLAG

say "4/6 the embedder model (once, about 0.5 GB, into the shared Hugging Face cache)"
"$VENV/bin/slopymem" install-model $YESFLAG

say "5/6 harnesses"
if [ "$HARNESS" = 1 ]; then
  "$VENV/bin/slopymem" register --detected $YESFLAG
else
  echo "skipped (--no-harness); register later: slopymem register <harness>"
fi

say "6/6 doctor"
if ! "$VENV/bin/slopymem" doctor; then
  echo "the doctor found something; each FAIL line names its section in SETUP.md"
  [ "$HARNESS" = 1 ] || echo "(a harnesses FAIL is expected with --no-harness: the launcher is registered nowhere yet)"
fi
printf '\nDone. Open any project and ask your agent to set up memory.\n'
printf 'The command is %s/bin/slopymem (a symlink into ~/.local/bin is yours to make).\n' "$VENV"
