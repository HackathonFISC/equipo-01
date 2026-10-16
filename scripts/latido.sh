#!/usr/bin/env bash
# Uso: scripts/latido.sh <numero-issue> "avance: … · siguiente: … · bloqueo: …"
# Deja un comentario de avance en el issue. Sin latido en 90 min, el issue se considera abandonado.
source "$(dirname "$0")/_common.sh"

N="${1:?Uso: scripts/latido.sh <issue> \"mensaje\"}"
MSG="${2:?Falta el mensaje del latido}"

gh issue comment "$N" --body "⏱ $(ahora) · @$YO/$AGENTE · $MSG" >/dev/null
echo "⏱ Latido registrado en #$N"
