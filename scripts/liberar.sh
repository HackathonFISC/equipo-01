#!/usr/bin/env bash
# Uso: scripts/liberar.sh <numero-issue> "estado real y qué falta"
# Suelta el issue para que otro lo siga: te desasigna, quita el estado y empuja tu rama.
source "$(dirname "$0")/_common.sh"

N="${1:?Uso: scripts/liberar.sh <issue> \"estado real y qué falta\"}"
ESTADO="${2:?Describe el estado real y qué falta}"

RAMA="$(git rev-parse --abbrev-ref HEAD)"
case "$RAMA" in
  issue-"$N"-*) git push -u origin "$RAMA" >/dev/null 2>&1 && echo "Rama $RAMA empujada." || echo "No pude empujar $RAMA; hazlo a mano." ;;
  *) echo "No estás en una rama issue-$N-*; no empujo nada." ;;
esac

gh issue edit "$N" --remove-assignee "@me" --remove-label "estado: en proceso" --remove-label "estado: bloqueado" >/dev/null
gh issue comment "$N" --body "↩️ LIBERADO · $(ahora) · @$YO/$AGENTE · estado: $ESTADO · rama: \`$RAMA\`" >/dev/null
echo "↩️ #$N liberado. Queda pendiente para quien lo reclame."
