#!/usr/bin/env bash
# Uso: scripts/revisar.sh <numero-PR> ok|cambios "comentario"
# Para el revisor (Claude o un compañero): etiqueta el PR con "revisión: ok" o "revisión: cambios" y comenta.
# Con "ok" además saca el PR de borrador. El autor hace el merge: gh pr merge <PR> --squash --delete-branch
source "$(dirname "$0")/_common.sh"

PR="${1:?Uso: scripts/revisar.sh <PR> ok|cambios \"comentario\"}"
VEREDICTO="${2:?Indica ok o cambios}"
MSG="${3:-}"

case "$VEREDICTO" in
  ok)
    gh pr edit "$PR" --add-label "revisión: ok" --remove-label "revisión: cambios" >/dev/null
    gh pr comment "$PR" --body "✅ revisión: ok · $(ahora) · @$YO/$AGENTE · $MSG" >/dev/null
    gh pr ready "$PR" >/dev/null 2>&1 || true
    echo "✅ PR #$PR aprobado. El autor puede hacer: gh pr merge $PR --squash --delete-branch"
    ;;
  cambios)
    gh pr edit "$PR" --add-label "revisión: cambios" --remove-label "revisión: ok" >/dev/null
    gh pr comment "$PR" --body "🔁 revisión: cambios · $(ahora) · @$YO/$AGENTE · $MSG" >/dev/null
    echo "🔁 PR #$PR con cambios pedidos. Si el revisor corrige, lo hace en la rama claude/issue-N con PR aparte."
    ;;
  *)
    echo "Veredicto inválido: $VEREDICTO (usa ok o cambios)" >&2
    exit 1
    ;;
esac
