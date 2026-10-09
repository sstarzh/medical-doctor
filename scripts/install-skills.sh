#!/usr/bin/env bash
# Copy the clinical skills into Claude Code or OpenClaw.
#   ./scripts/install-skills.sh openclaw [workspace-dir]
#   ./scripts/install-skills.sh claude
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)/skills"
case "${1:-}" in
  openclaw) dest="${2:-$HOME/.openclaw/workspace}/skills" ;;
  claude)   dest="$HOME/.claude/skills" ;;
  *) echo "usage: $0 openclaw [workspace-dir] | claude" >&2; exit 1 ;;
esac
mkdir -p "$dest"
for d in "$src"/*/; do
  name="$(basename "$d")"
  rm -rf "${dest:?}/$name"
  cp -R "$d" "$dest/$name"
  echo "installed $name -> $dest/$name"
done
