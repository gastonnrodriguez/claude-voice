#!/bin/sh
# Lanzador de voice.py para Linux, macOS y Windows (Git Bash).
# Busca un Python 3 que funcione: en Windows "python3" suele ser el acceso
# directo a la Microsoft Store, así que probamos también "python" y "py -3".
script="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}/scripts/voice.py"
for py in python3 python "py -3"; do
  if $py -c "import sys; sys.exit(sys.version_info < (3, 9))" </dev/null >/dev/null 2>&1; then
    exec $py "$script" "$@"
  fi
done
echo "claude-voice: no encontré Python 3.9 o superior" >&2
exit 0
