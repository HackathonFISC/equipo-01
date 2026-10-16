#!/usr/bin/env bash
# Uso: scripts/bitacora.sh <numero-issue> "qué se pidió a la IA" ["qué se cambió a mano"]
#
# Añade UNA FILA a docs/bitacora_ia.md en el momento, no al final del PR:
#   | fecha hora | quién | herramienta | #N · qué se pidió | qué se cambió a mano |
# Corre esto después de cada bloque de trabajo con IA y antes de cada commit (el hook pre-commit lo exige).
# Si gh está disponible, deja además un latido 📝 en el issue: cuenta como actividad para la regla de 90 min.
#
# Variables: AGENTE ("Claude Code", "Codex", "Antigravity"), MODELO (opcional, p. ej. "gemini-3-pro").
set -euo pipefail

N="${1:?Uso: scripts/bitacora.sh <issue> \"qué se pidió a la IA\" [\"qué se cambió a mano\"]}"
PEDIDO="${2:?Falta qué se pidió a la IA}"
MANO="${3:-nada todavía}"

ROOT="$(git rev-parse --show-toplevel)"
ARCHIVO="$ROOT/docs/bitacora_ia.md"
[ -f "$ARCHIVO" ] || { echo "No existe $ARCHIVO" >&2; exit 1; }

AGENTE="${AGENTE:-humano}"
MODELO="${MODELO:-}"
HERRAMIENTA="$AGENTE"
[ -n "$MODELO" ] && HERRAMIENTA="$AGENTE ($MODELO)"

# Quién: usuario de GitHub si gh está disponible (coincide con los comentarios del tablero); si no, el nombre de git.
QUIEN=""
if command -v gh >/dev/null 2>&1; then
  QUIEN="$(gh api user --jq .login 2>/dev/null || true)"
fi
[ -n "$QUIEN" ] || QUIEN="$(git config user.name 2>/dev/null || true)"
[ -n "$QUIEN" ] || QUIEN="desconocido"

limpiar() { printf '%s' "$1" | tr '\n' ' ' | sed 's/|/\//g'; }
AHORA="$(date '+%Y-%m-%d %H:%M')"
FILA="| $AHORA | $(limpiar "$QUIEN") | $(limpiar "$HERRAMIENTA") | #$N · $(limpiar "$PEDIDO") | $(limpiar "$MANO") |"

# Normaliza el final del archivo (sin líneas en blanco al final) para que la fila quede dentro de la tabla.
CONTENIDO="$(cat "$ARCHIVO")"
printf '%s\n%s\n' "$CONTENIDO" "$FILA" > "$ARCHIVO"
echo "📝 Fila añadida a docs/bitacora_ia.md:"
echo "   $FILA"

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  if gh issue comment "$N" --body "📝 bitácora · $AHORA · $QUIEN/$AGENTE · pedido: $(limpiar "$PEDIDO") · a mano: $(limpiar "$MANO")" >/dev/null 2>&1; then
    echo "⏱ Latido 📝 registrado en #$N"
  fi
fi
