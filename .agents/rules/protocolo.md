# Regla de workspace para Antigravity

Lee `AGENTS.md` en la raíz del repositorio antes de cualquier acción y síguelo al pie de la letra. Las reglas de oro: nada sin issue reclamado con `scripts/reclamar.sh <N>`; una rama `issue-N-descripcion` y un worktree por issue; latido cada hora con `scripts/latido.sh`; fila en `docs/bitacora_ia.md` con `scripts/bitacora.sh <N> "qué se pidió" "qué se cambió a mano"` antes de cada commit; `uv run pytest -q` en verde antes de `scripts/entregar.sh <N>`; nunca cambiar `puente/schemas.py` sin issue nuevo; nunca push a `main`.

Define `export AGENTE="Antigravity"` al empezar la sesión.
