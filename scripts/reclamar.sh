#!/usr/bin/env bash
# Uso: scripts/reclamar.sh <numero-issue> [descripcion-corta] [--retomar]
#
# Reclama un issue para que nadie más haga el mismo trabajo:
#   1. verifica que no esté ya en "estado: en proceso" (salvo --retomar);
#   2. te asigna y pone la etiqueta "estado: en proceso";
#   3. comenta 🔒 con humano, agente, rama y hora;
#   4. verifica la carrera: si otro 🔒 es más antiguo, libera el tuyo;
#   5. crea la rama issue-N-descripcion desde origin/main y un worktree hermano.
#
# --retomar: para issues abandonados (más de 90 min sin latido). Comenta ♻️ y reutiliza la rama existente.
source "$(dirname "$0")/_common.sh"

N=""; DESC=""; RETOMAR=0
for arg in "$@"; do
  case "$arg" in
    --retomar) RETOMAR=1 ;;
    --*) echo "Opción desconocida: $arg" >&2; exit 1 ;;
    *) if [ -z "$N" ]; then N="$arg"; elif [ -z "$DESC" ]; then DESC="$arg"; fi ;;
  esac
done
[ -n "$N" ] || { echo "Uso: scripts/reclamar.sh <issue> [descripcion-corta] [--retomar]" >&2; exit 1; }

if [ "$(gh issue view "$N" --json state --jq .state)" != "OPEN" ]; then
  echo "⛔ El issue #$N no está abierto." >&2
  exit 2
fi

if [ "$RETOMAR" = 1 ]; then
  gh issue comment "$N" --body "♻️ Retomo #$N por inactividad (más de 90 min sin latido) · humano: @$YO · agente: $AGENTE · $(ahora)" >/dev/null
elif tiene_etiqueta "$N" "estado: en proceso"; then
  echo "⛔ El issue #$N ya está EN PROCESO (asignado a: $(asignados_issue "$N")). Elige otro o coordina en el issue." >&2
  echo "   Si lleva más de 90 min sin latido, retómalo con: scripts/reclamar.sh $N <descripcion> --retomar" >&2
  exit 2
fi

if [ -z "$DESC" ]; then
  DESC="$(gh issue view "$N" --json title --jq .title \
    | LC_ALL=C tr '[:upper:]' '[:lower:]' \
    | LC_ALL=C tr -c 'a-z0-9\n' '-' \
    | sed -E 's/-+/-/g; s/^-//; s/-$//' | cut -c1-40 | sed -E 's/-$//')"
fi
RAMA="issue-$N-$DESC"

gh issue edit "$N" --add-assignee "@me" --add-label "estado: en proceso" --remove-label "estado: bloqueado" >/dev/null
gh issue comment "$N" --body "🔒 EN PROCESO · humano: @$YO · agente: $AGENTE · rama: \`$RAMA\` · inicio: $(ahora)" >/dev/null

# Carrera: entre los 🔒 posteriores al último ↩️ (liberado), 🔍 (entregado) o ♻️ (retomado), gana el más antiguo.
PRIMERO="$(gh issue view "$N" --json comments --jq '
  (.comments | sort_by(.createdAt)) as $c
  | ([$c | to_entries[] | select(.value.body | test("^(↩️|🔍|♻️)")) | .key] | max // -1) as $i
  | [$c[($i + 1):][] | select(.body | startswith("🔒"))] | .[0].author.login')"
if [ "$PRIMERO" != "$YO" ]; then
  echo "⚠️  Carrera: @$PRIMERO reclamó #$N primero. Libero mi reclamo." >&2
  gh issue edit "$N" --remove-assignee "@me" >/dev/null
  gh issue comment "$N" --body "↩️ Libero #$N: @$PRIMERO reclamó primero · $(ahora)" >/dev/null
  exit 3
fi

# Rama y worktree.
git fetch origin main >/dev/null 2>&1 || true
ACTUAL="$(git rev-parse --abbrev-ref HEAD)"
if [ "$ACTUAL" = "$RAMA" ]; then
  echo "Ya estás en la rama $RAMA."
else
  if ! git show-ref --verify --quiet "refs/heads/$RAMA"; then
    if git show-ref --verify --quiet "refs/remotes/origin/$RAMA"; then
      git branch --track "$RAMA" "origin/$RAMA" >/dev/null
    else
      git branch "$RAMA" origin/main >/dev/null 2>&1 || git branch "$RAMA" >/dev/null
    fi
  fi
  WT="$(dirname "$MAIN_ROOT")/wt-$(basename "$MAIN_ROOT")-issue-$N"
  if [ -d "$WT" ]; then
    echo "El worktree ya existe: $WT"
  elif git worktree list --porcelain | grep -qx "branch refs/heads/$RAMA"; then
    echo "La rama $RAMA ya está abierta en otro worktree:"
    git worktree list | grep "\[$RAMA\]" || true
  else
    git worktree add "$WT" "$RAMA" >/dev/null
    echo "Worktree creado: $WT"
  fi
fi

echo "✅ Issue #$N reclamado por @$YO ($AGENTE). Rama: $RAMA"
echo "   Cada hora:   scripts/latido.sh $N \"avance: … · siguiente: … · bloqueo: …\""
echo "   Al terminar: scripts/entregar.sh $N"
