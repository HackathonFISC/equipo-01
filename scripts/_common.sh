#!/usr/bin/env bash
# Funciones compartidas por los scripts del tablero. No se ejecuta directo: los demás scripts hacen `source`.
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "Falta gh (GitHub CLI). Instálalo y corre: gh auth login" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh no está autenticado. Corre: gh auth login" >&2
  exit 1
fi

YO="$(gh api user --jq .login)"
AGENTE="${AGENTE:-humano}"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
# Raíz del checkout principal, aunque estemos dentro de un worktree.
MAIN_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"

# Activa los hooks versionados (pre-commit de la bitácora) la primera vez que se usa cualquier script.
if [ -d "$MAIN_ROOT/.githooks" ] && [ "$(git config core.hooksPath 2>/dev/null || true)" != ".githooks" ]; then
  git config core.hooksPath .githooks
fi

ahora() { date '+%Y-%m-%d %H:%M'; }
etiquetas_issue() { gh issue view "$1" --json labels --jq '.labels[].name'; }
asignados_issue() { gh issue view "$1" --json assignees --jq '.assignees[].login' | paste -sd, -; }
tiene_etiqueta() { etiquetas_issue "$1" | grep -qxF "$2"; }
