#!/usr/bin/env bash
# Uso: scripts/configurar-etiquetas.sh [--simular]
# Crea (o actualiza) las tres etiquetas de estado del tablero. Idempotente: se puede correr varias veces.
# Las etiquetas de área, mvp, stretch y revisión ya existen en el repo.
set -euo pipefail

SIMULAR=0
[ "${1:-}" = "--simular" ] && SIMULAR=1

run() {
  if [ "$SIMULAR" = 1 ]; then printf '[simulado]'; printf ' %q' "$@"; echo; else "$@"; fi
}

if [ "$SIMULAR" = 0 ]; then
  command -v gh >/dev/null 2>&1 || { echo "Falta gh (GitHub CLI)" >&2; exit 1; }
fi

run gh label create "estado: en proceso"  --color fbca04 --description "Alguien lo está trabajando ahora (ver comentario 🔒)" --force
run gh label create "estado: en revisión" --color 0075ca --description "PR abierto, esperando revisión" --force
run gh label create "estado: bloqueado"   --color d73a4a --description "Esperando algo de otra persona (ver comentario ⛔)" --force

echo "Etiquetas de estado listas."
