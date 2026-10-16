# Bitácora de IA

Cada uso de IA se anota en el momento: hora, quién, herramienta, qué se pidió y qué se cambió a mano. Las filas no se borran.

| Hora (2026-10-16) | Quién | Herramienta | Qué se pidió | Qué se cambió a mano |
| --- | --- | --- | --- | --- |
| Antes de la hora 0 | Jesus | Claude (Claude Code, app de escritorio) | Plan del proyecto a partir de los retos y los talleres: arquitectura, agentes, evals, plan de 24 h; verificación de fuentes de datos de Panamá | Elección del reto 3, del framework (LangGraph) y reparto de roles |
| 14:00 | Jesus | Claude (Claude Code) | Crear etiquetas, hitos y los 17 issues del plan en GitHub | Revisión de asignaciones por integrante |
| 14:15 | Jesus | Claude (Claude Code) | Esqueleto del repo (issue #1): `pyproject.toml`, carpetas con módulos vacíos documentados, `.env.example`, plantillas de issue, prueba de importación | Pendiente de revisión en el PR |
| 2026-10-16 14:31 | Cornelio-Guerra | Claude Code (Claude Fable 5.1) | #21 · Protocolo AGENTS.md para Claude Code, Codex y Antigravity: tablero con reclamo en proceso, latidos, un worktree por issue, scripts con gh, hook pre-commit y merge=union para la bitácora; apuntadores CLAUDE.md, GEMINI.md y .agents/rules | Cornelio pidió cubrir tres herramientas y llenar la bitácora sobre la marcha; se crearon 3 etiquetas de estado en GitHub; pendiente de revisión del equipo |
| 2026-10-16 15:13 | Migue0714 | Antigravity (Gemini) | #11 · Implementar tablero web del orientador con servidor ASGI/REST (`app/server.py`), frontend `HTML/CSS/JS` limpio (`app/static/`) con las 5 vistas y acciones HITL (`interrupt`), y pruebas (`tests/test_tablero.py`) | Decisión arquitectónica: reemplazo de Streamlit por servidor ASGI/FastAPI (`uvicorn app.server:app`) + HTML/CSS/JS limpio sin dependencias pesadas y validación del flujo de Yaritza |
| 2026-10-16 15:14 | Cornelio-Guerra | Claude Code (nube) | #2 · contratos Pydantic en puente/schemas.py y pruebas de validación y compatibilidad con #22 y #24 | qué se cambió a mano: nada todavía |
| 2026-10-16 15:14 | Cornelio-Guerra | Claude Code (nube) | #2 · grafo LangGraph con nodos vacíos que pasan el estado | qué se cambió a mano: nada todavía |
| 2026-10-16 15:24 | Migue0714 | Antigravity (Gemini) | #11 · Agregar selector interactivo de archivos en `data/`, guardrail anti-inyección en ofertas y generación de CV en PDF sin PII (`fpdf2`) al aprobar en `app/server.py` | Verificación de los 3 artefactos en `outbox/` (`cv_*.pdf`, `telegram_*.txt`, `entrevista_*.ics`) |
| 2026-10-16 15:29 | Cornelio-Guerra | Claude Code (nube) | #2 · desglose de Match como dict validado para que el auditor (#24) lea viable; tests reales de #22 y #24 corridos contra schemas.py (9 passed) | qué se cambió a mano: nada todavía |
