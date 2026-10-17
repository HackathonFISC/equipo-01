#!/usr/bin/env bash
set -eu
set -o pipefail

uso() {
  cat <<'TEXTO'
Uso: scripts/grabar_video.sh [--forzar]

Graba las tres llamadas al LLM que necesita el video (#194):
  1. el chat de Yaritza en /joven (agente Perfil), con el guion de data/demo/yaritza_chat.json;
  2. el redactor de OF-BLOQ-EDAD-04, como python -m puente.agents.redactor;
  3. la micro-misión CAJA-01, como python -m puente.agents.micromision.

Lee ANTHROPIC_API_KEY de .env sin mostrarla. Graba con LLM_MODO=grabar y un tope de US$1, las verifica
sin red con LLM_MODO=reproducir por el camino real (la vista por HTTP y las CLI) y muestra los archivos
nuevos y el costo. Si una grabación ya existe y da lo esperado, no la repite.
No hace commit ni push: al final imprime los comandos para copiarlos.

  --forzar   vuelve a grabar aunque ya exista una grabación que sirve
TEXTO
}

FORZAR=""
for arg in "$@"; do
  case "$arg" in
    --forzar) FORZAR="--forzar" ;;
    -h|--help|--ayuda) uso; exit 0 ;;
    *) echo "⛔ Opción desconocida: $arg" >&2; uso >&2; exit 2 ;;
  esac
done

RAIZ="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$RAIZ" ]; then
  echo "⛔ Corre el script dentro del repo de Puente." >&2
  exit 1
fi
cd "$RAIZ"

if ! command -v uv >/dev/null 2>&1; then
  echo "⛔ Falta uv (https://docs.astral.sh/uv/). Instálalo y vuelve a correr scripts/grabar_video.sh" >&2
  exit 1
fi

CLAVE=""
if [ -f .env ]; then
  CLAVE="$(sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?ANTHROPIC_API_KEY[[:space:]]*=[[:space:]]*//p' .env \
    | tail -n 1 | tr -d '\r' \
    | sed -E -e 's/[[:space:]]+#.*$//' -e 's/^"(.*)"$/\1/' -e "s/^'(.*)'\$/\1/" -e 's/[[:space:]]+$//')"
fi
if [ -z "$CLAVE" ]; then
  echo "⛔ Falta ANTHROPIC_API_KEY en .env: no se llamó al modelo ni se gastó nada." >&2
  echo "   Copia .env.example a .env si no lo tienes, pon la clave en ANTHROPIC_API_KEY= y vuelve a correr:" >&2
  echo "   scripts/grabar_video.sh" >&2
  exit 1
fi
export ANTHROPIC_API_KEY="$CLAVE"
CLAVE=""

export LLM_PROVEEDOR=anthropic
export PUENTE_MODELO_LLM=claude-opus-5-5
export LLM_DIR_GRABACIONES="$RAIZ/data/grabaciones"
export PUENTE_LLM_TOPE_USD=1
export LANGSMITH_TRACING=false
unset PUENTE_TAXONOMIA

TEMPORAL="$(mktemp -d "${TMPDIR:-/tmp}/puente_video.XXXXXX")"
trap 'rm -rf "$TEMPORAL"' EXIT
ls -1 data/grabaciones | LC_ALL=C sort > "$TEMPORAL/antes.txt"

echo "▶ 1/2 · Grabando con Claude (LLM_MODO=grabar, tope US\$1)"
GRABO=1
LLM_MODO=grabar uv run --quiet python scripts/grabar_video.py grabar $FORZAR --lista "$TEMPORAL/lista.txt" || GRABO=0

VERIFICO=0
if [ "$GRABO" = 1 ]; then
  echo ""
  echo "▶ 2/2 · Verificando sin red (LLM_MODO=reproducir) por la vista y las CLI"
  VERIFICO=1
  LLM_MODO=reproducir uv run --quiet python scripts/grabar_video.py verificar || VERIFICO=0
fi

ls -1 data/grabaciones | LC_ALL=C sort > "$TEMPORAL/despues.txt"
echo ""
echo "Archivos nuevos en data/grabaciones/:"
NUEVOS="$(LC_ALL=C comm -13 "$TEMPORAL/antes.txt" "$TEMPORAL/despues.txt")"
if [ -n "$NUEVOS" ]; then
  printf '%s\n' "$NUEVOS" | sed 's|^|  data/grabaciones/|'
else
  echo "  ninguno"
fi
CAMBIADOS="$(git status --porcelain -- data/grabaciones | sed -n 's/^ M //p')"
if [ -n "$CAMBIADOS" ]; then
  echo "Grabaciones que ya estaban en git y se reescribieron:"
  printf '%s\n' "$CAMBIADOS" | sed 's|^|  |'
fi

if [ "$GRABO" != 1 ]; then
  echo ""
  echo "⛔ La grabación no quedó completa: el motivo está arriba. No se hizo commit; corrige y vuelve a correr scripts/grabar_video.sh" >&2
  exit 1
fi
if [ "$VERIFICO" != 1 ]; then
  echo ""
  echo "⛔ Se grabó, pero la verificación en reproducir falló: el motivo está arriba. No subas estas grabaciones todavía." >&2
  exit 1
fi

ARCHIVOS=""
while IFS= read -r ruta; do
  if [ -n "$ruta" ]; then
    ARCHIVOS="$ARCHIVOS $ruta"
  fi
done < "$TEMPORAL/lista.txt"
RAMA="$(git rev-parse --abbrev-ref HEAD)"

echo ""
echo "✅ Grabadas y verificadas. El script no hace commit ni push: copia y pega estas líneas."
if [ "$RAMA" = "main" ] || [ "$RAMA" = "master" ] || [ "$RAMA" = "HEAD" ]; then
  echo "⚠ Estás en $RAMA: antes cámbiate a la rama del issue con git checkout issue-194-grabaciones-video"
  RAMA="issue-194-grabaciones-video"
fi
echo ""
echo "git add$ARCHIVOS"
echo "AGENTE=\"Claude Code\" scripts/bitacora.sh 194 \"Claude Opus 5.5 respondió las 3 llamadas del video que grabó scripts/grabar_video.sh: chat de Yaritza en /joven (Perfil), redactor de OF-BLOQ-EDAD-04 y micro-misión CAJA-01\" \"corrí el script con mi clave y revisé que las grabaciones sean sintéticas y se reproduzcan sin red\""
echo "git add docs/bitacora_ia.md"
echo "git commit -m \"data(grabaciones): chat de Yaritza, redactor y micro-misión para el video (#194)\""
echo "git push origin $RAMA"
echo ""
echo "Después de commitear la grabación: cambiar EJEMPLO_YARITZA de joven.js al guion nuevo (lo hace el manager en un PR aparte)."
