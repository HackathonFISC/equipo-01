#!/usr/bin/env bash
# Uso: scripts/entregar.sh <numero-issue>
# Empuja tu rama, abre el PR en borrador si no existe (con "Closes #N") y pone el issue en "estado: en revisión".
source "$(dirname "$0")/_common.sh"

N="${1:?Uso: scripts/entregar.sh <issue>}"

RAMA="$(git rev-parse --abbrev-ref HEAD)"
case "$RAMA" in
  main|master) echo "⛔ Estás en $RAMA. Cámbiate a tu rama issue-$N-*." >&2; exit 2 ;;
esac

git push -u origin "$RAMA"

if [ "$(gh pr list --head "$RAMA" --state open --json number --jq length)" = "0" ]; then
  TITULO="$(gh issue view "$N" --json title --jq .title)"
  PLANTILLA="$ROOT/.github/PULL_REQUEST_TEMPLATE.md"
  CUERPO="Closes #$N"
  if [ -f "$PLANTILLA" ]; then
    CUERPO="$(cat "$PLANTILLA")"
    CUERPO="${CUERPO//Closes #N/Closes #$N}"
  fi
  gh pr create --draft --base main --head "$RAMA" --title "$TITULO (#$N)" --body "$CUERPO" >/dev/null
fi
URL="$(gh pr list --head "$RAMA" --state open --json url --jq '.[0].url')"

gh issue edit "$N" --add-label "estado: en revisión" --remove-label "estado: en proceso" --remove-label "estado: bloqueado" >/dev/null
gh issue comment "$N" --body "🔍 EN REVISIÓN · PR: $URL · $(ahora) · @$YO/$AGENTE" >/dev/null

echo "✅ PR en borrador: $URL"
echo "   Completa el checklist. Cuando esté listo para revisión: gh pr ready $(basename "$URL")"
echo "   El revisor usa: scripts/revisar.sh $(basename "$URL") ok|cambios \"comentario\""
