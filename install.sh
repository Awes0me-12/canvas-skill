#!/bin/bash
# Make this folder available as the "canvas-coach" skill in Claude Code and in Codex.
#
#     bash install.sh
#
# It only creates links (~/.claude/skills/canvas-coach and ~/.codex/skills/canvas-coach) that point
# back to this folder, so both apps use one copy. It never replaces a real folder of the same name,
# and it reads nothing from Canvas. Run it again after moving this folder.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CODEX_SKILLS="${CODEX_HOME:-$HOME/.codex}/skills"
ok=1

link() {                                   # link <skills folder> <app name>
    local target="$1/canvas-coach"
    mkdir -p "$1"
    if [ -L "$target" ] || [ ! -e "$target" ]; then
        ln -sfn "$HERE" "$target"
        echo "$2: $target -> $HERE"
    else
        echo "$2: $target already exists and is not a link; left it alone. Move it away and run this again."
        ok=0
    fi
}

link "$HOME/.claude/skills" "Claude Code"
link "$CODEX_SKILLS" "Codex"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Note: Python 3.10 or newer is needed (python3 --version shows yours)."
    ok=0
fi
if ! command -v agent-browser >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/agent-browser" ]; then
    echo "Note: agent-browser is not installed yet. It opens the Canvas login window. Install it with:"
    echo "      npm i -g agent-browser && agent-browser install"
fi
[ "$ok" = 1 ] && echo "Done. Restart Claude Code or Codex, then ask: \"What's due this week?\""
