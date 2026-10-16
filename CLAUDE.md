# CLAUDE.md · Puente

Antes de cualquier acción, lee `AGENTS.md` completo y síguelo. Resumen de lo innegociable:

1. Nada sin issue reclamado: `scripts/reclamar.sh <N> <descripcion>`. Si el issue ya tiene `estado: en proceso`, no lo tomes.
2. Trabaja solo en tu rama `issue-N-descripcion` y en tu worktree. Nunca en `main`.
3. Latido cada hora: `scripts/latido.sh <N> "avance · siguiente · bloqueo"`.
4. Antes de cada commit: `scripts/bitacora.sh <N> "qué pediste a la IA" "qué cambiaste a mano"`; el hook pre-commit lo exige. Antes de entregar: `uv run pytest -q`, caso de eval si aplica, luego `scripts/entregar.sh <N>`.
5. No cambies `puente/schemas.py` sin issue nuevo. Sin LLM en `puente/solver/` ni `puente/rules/`. Solo datos sintéticos de personas. Claves solo en `.env`.
6. Al cerrar la sesión sin terminar: comenta el traspaso en el issue con estado exacto y rama.

Al empezar define `export AGENTE="Claude Code"` para que los comentarios en GitHub identifiquen al agente.

Contexto del producto: `AGENTS.md`, sección 2. Issues: `gh issue list`. Tablero: `scripts/tablero.sh`.
