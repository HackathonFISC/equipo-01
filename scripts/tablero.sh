#!/usr/bin/env bash
# Uso: scripts/tablero.sh
# Muestra el tablero desde GitHub Issues: en proceso (con minutos desde la última actividad),
# bloqueados, en revisión y pendientes (primero mvp).
source "$(dirname "$0")/_common.sh"

# Funciones jq compartidas por todas las consultas.
DEFS='
def asig: if (.assignees | length) > 0 then "@" + ([.assignees[].login] | join(", @")) else "sin asignar" end;
def mins: ((now - (.updatedAt | fromdateiso8601)) / 60) | floor;
def areas: [.labels[].name | select(startswith("area:") or . == "stretch")] | join(", ");
def sin_estado: ([.labels[].name] | map(startswith("estado:")) | any | not);
def es_mvp: ([.labels[].name] | index("mvp")) != null;
'

echo "== EN PROCESO =="
gh issue list --label "estado: en proceso" --limit 100 --json number,title,assignees,updatedAt \
  --jq "$DEFS"'.[] | "#\(.number) \(.title) · \(asig) · última actividad hace \(mins) min" + (if mins > 90 then " ⚠️ posible abandono: se puede retomar con --retomar" else "" end)'

echo; echo "== BLOQUEADOS =="
gh issue list --label "estado: bloqueado" --limit 100 --json number,title,assignees,updatedAt \
  --jq "$DEFS"'.[] | "#\(.number) \(.title) · \(asig) · hace \(mins) min"'

echo; echo "== EN REVISIÓN =="
gh issue list --label "estado: en revisión" --limit 100 --json number,title,assignees,updatedAt \
  --jq "$DEFS"'.[] | "#\(.number) \(.title) · \(asig) · hace \(mins) min"'

echo; echo "== PENDIENTES mvp =="
gh issue list --limit 100 --json number,title,assignees,labels \
  --jq "$DEFS"'.[] | select(sin_estado and es_mvp) | "#\(.number) \(.title) · \(asig) · \(areas)"'

echo; echo "== PENDIENTES resto =="
gh issue list --limit 100 --json number,title,assignees,labels \
  --jq "$DEFS"'.[] | select(sin_estado and (es_mvp | not)) | "#\(.number) \(.title) · \(asig) · \(areas)"'
