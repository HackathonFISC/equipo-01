#!/usr/bin/env bash
# Uso: scripts/nube.sh <numero-issue> [descripcion-corta] [--simular]
#
# Manda un issue a una sesión de Claude Code en la nube siguiendo AGENTS.md (sección 12):
#   1. reclama el issue y crea la rama issue-N-descripcion desde origin/main con su worktree (scripts/reclamar.sh);
#   2. empuja la rama, porque `claude --cloud` parte de la rama actual;
#   3. desde ese worktree lanza `claude --cloud` con el texto del issue y las reglas para la nube;
#   4. deja el enlace de la sesión en el issue como latido.
#
# Requisito único por persona: `gh auth login` y luego `/web-setup` dentro de `claude`.
# --simular: muestra el prompt y los pasos sin reclamar, empujar ni lanzar nada.
source "$(dirname "$0")/_common.sh"

N=""; DESC=""; SIMULAR=0
for arg in "$@"; do
  case "$arg" in
    --simular) SIMULAR=1 ;;
    --*) echo "Opción desconocida: $arg" >&2; exit 1 ;;
    *) if [ -z "$N" ]; then N="$arg"; elif [ -z "$DESC" ]; then DESC="$arg"; fi ;;
  esac
done
[ -n "$N" ] || { echo "Uso: scripts/nube.sh <issue> [descripcion-corta] [--simular]" >&2; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "Falta la CLI de Claude Code (claude)." >&2; exit 1; }

REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
TITULO="$(gh issue view "$N" --json title --jq .title)"
CUERPO="$(gh issue view "$N" --json body --jq .body)"

if [ -z "$DESC" ]; then
  DESC="$(printf '%s' "$TITULO" \
    | LC_ALL=C tr '[:upper:]' '[:lower:]' \
    | LC_ALL=C tr -c 'a-z0-9\n' '-' \
    | sed -E 's/-+/-/g; s/^-//; s/-$//' | cut -c1-40 | sed -E 's/-$//')"
fi
RAMA="issue-$N-$DESC"

# Plantilla entre comillas simples: nada se ejecuta; los marcadores se reemplazan después.
read -r -d '' PLANTILLA <<'FIN' || true
Eres el agente de desarrollo de @__YO__ en el repo __REPO__. Trabaja SOLO el issue #__N__ "__TITULO__". El issue ya está reclamado a tu nombre en GitHub con la etiqueta "estado: en proceso"; no lo reclames de nuevo.

## Acceso a GitHub (hazlo primero)
La sesión puede llegar como copia del repo sin remoto, y dentro de la nube la API de GitHub puede responder 403 y no hay `gh`; git sí funciona. Antes de nada: `git remote add origin https://github.com/__REPO__.git` (o `git remote set-url origin …` si ya existe) y `git fetch origin`. Si el fetch falla, dime en una línea el error exacto y detente.

## Rama
Trabaja en la rama __RAMA__, que sale de main. Si el entorno no te deja empujar a esa rama, usa claude/__RAMA__. Nunca empujes a main ni hagas merge.

## Reglas
- Sigue AGENTS.md completo; en especial la sección 2 (el LLM nunca ordena candidatos, nunca calcula traslados y nunca decide si una oferta es legal) y la sección 9.
- Importa los contratos de `puente.schemas`; no los cambies sin un issue nuevo.
- `uv sync` y `uv run pytest -q` en verde. Los tests no dependen de red ni de claves: el LLM va detrás de una función inyectable y los tests usan un falso.
- Bitácora en el momento: corre `scripts/instalar-hooks.sh` una vez y, antes de cada commit, `AGENTE="Claude Code (nube)" scripts/bitacora.sh __N__ "qué hiciste con IA" "qué se cambió a mano: nada todavía"`.
- Toca solo los archivos del alcance del issue. Si algo es ambiguo, decide lo razonable, anótalo en el PR y sigue.

## Al terminar
Empuja tu rama y abre un PR contra main con "Closes #__N__", qué hace, decisiones tomadas, cómo probarlo y el resultado de pytest. Pide revisión a los otros dos integrantes del equipo (AGENTS.md, sección 3). Si no puedes crear el PR desde aquí, deja la rama empujada y el título y la descripción listos en tu último mensaje.

## Texto del issue #__N__
FIN

PROMPT="${PLANTILLA//__YO__/$YO}"
PROMPT="${PROMPT//__REPO__/$REPO}"
PROMPT="${PROMPT//__N__/$N}"
PROMPT="${PROMPT//__TITULO__/$TITULO}"
PROMPT="${PROMPT//__RAMA__/$RAMA}"
PROMPT="$PROMPT"$'\n'"$CUERPO"

if [ "$SIMULAR" = 1 ]; then
  echo "[simulado] reclamaría #$N y crearía la rama $RAMA desde origin/main con su worktree"
  echo "[simulado] empujaría $RAMA y lanzaría claude --cloud con este prompt:"
  echo "----------------------------------------------------------------"
  printf '%s\n' "$PROMPT"
  echo "----------------------------------------------------------------"
  exit 0
fi

# 1. Reclamo, rama y worktree. reclamar.sh se niega si el issue ya está en proceso.
AGENTE="${AGENTE_NUBE:-Claude Code (nube)}" "$(dirname "$0")/reclamar.sh" "$N" "$DESC"

# El lanzamiento sale del worktree del issue, o de aquí si ya estás en esa rama.
if [ "$(git rev-parse --abbrev-ref HEAD)" = "$RAMA" ]; then
  DIR_LANZAR="$ROOT"
else
  DIR_LANZAR="$(dirname "$MAIN_ROOT")/wt-$(basename "$MAIN_ROOT")-issue-$N"
fi
[ -d "$DIR_LANZAR" ] || { echo "No encuentro el worktree $DIR_LANZAR" >&2; exit 1; }

# 2. Push: la nube parte de la rama actual.
git -C "$DIR_LANZAR" push -u origin "$RAMA"

# 3. Lanzamiento. Si Claude Code pregunta si confías en la carpeta nueva, es el repo del equipo: "Yes, I trust this folder".
echo "Lanzando la sesión en la nube desde $DIR_LANZAR …"
LOG="$(mktemp -t nube-issue-"$N".XXXXXX)"
if [ "$(uname)" = "Darwin" ]; then
  # `script` conserva la terminal interactiva y guarda la salida para leer el enlace de la sesión.
  (cd "$DIR_LANZAR" && script -q "$LOG" claude --cloud "$PROMPT")
else
  (cd "$DIR_LANZAR" && claude --cloud "$PROMPT")
fi

# 4. Enlace en el issue.
SESION="$(grep -aoE 'session_[A-Za-z0-9]{10,}' "$LOG" 2>/dev/null | head -1 || true)"
if [ -n "$SESION" ]; then
  gh issue comment "$N" --body "⏱ $(ahora) · @$YO/Claude Code (nube) · sesión en la nube: https://claude.ai/code/$SESION · rama base \`$RAMA\` · abrirá PR con Closes #$N" >/dev/null
  echo "✅ #$N en la nube: https://claude.ai/code/$SESION"
else
  echo "Copia el enlace de la sesión y regístralo con: scripts/latido.sh $N \"sesión en la nube: <enlace>\""
fi
echo "Cuando la sesión abra su PR: gh issue edit $N --add-label \"estado: en revisión\" --remove-label \"estado: en proceso\""
rm -f "$LOG"
