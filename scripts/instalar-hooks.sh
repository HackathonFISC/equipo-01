#!/usr/bin/env bash
# Uso: scripts/instalar-hooks.sh
# Activa los hooks versionados en .githooks/ para este clon (y todos sus worktrees). Idempotente.
set -euo pipefail

ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
chmod +x "$ROOT"/.githooks/* 2>/dev/null || true
git config core.hooksPath .githooks
echo "Hooks activados: $(git config core.hooksPath). El pre-commit exige una fila en docs/bitacora_ia.md en cada commit con código."
