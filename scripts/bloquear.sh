#!/usr/bin/env bash
# Uso: scripts/bloquear.sh <numero-issue> "necesito X de @persona"
# Marca el issue como bloqueado y menciona a quien puede desbloquearlo. Sigues asignado.
# Al desbloquearte, vuelve a correr scripts/reclamar.sh <N>.
source "$(dirname "$0")/_common.sh"

N="${1:?Uso: scripts/bloquear.sh <issue> \"motivo con @persona\"}"
MOTIVO="${2:?Falta el motivo del bloqueo}"

gh issue edit "$N" --add-label "estado: bloqueado" --remove-label "estado: en proceso" >/dev/null
gh issue comment "$N" --body "⛔ BLOQUEADO · $(ahora) · @$YO/$AGENTE · $MOTIVO" >/dev/null
echo "⛔ #$N marcado como bloqueado. Toma otro issue mientras tanto."
