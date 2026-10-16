# Bitácora de IA

Cada uso de IA se anota en el momento: hora, quién, herramienta, qué se pidió y qué se cambió a mano. Las filas no se borran.

| Hora (2026-10-16) | Quién | Herramienta | Qué se pidió | Qué se cambió a mano |
| --- | --- | --- | --- | --- |
| Antes de la hora 0 | Jesus | Claude (Claude Code, app de escritorio) | Plan del proyecto a partir de los retos y los talleres: arquitectura, agentes, evals, plan de 24 h; verificación de fuentes de datos de Panamá | Elección del reto 3, del framework (LangGraph) y reparto de roles |
| 14:00 | Jesus | Claude (Claude Code) | Crear etiquetas, hitos y los 17 issues del plan en GitHub | Revisión de asignaciones por integrante |
| 14:15 | Jesus | Claude (Claude Code) | Esqueleto del repo (issue #1): `pyproject.toml`, carpetas con módulos vacíos documentados, `.env.example`, plantillas de issue, prueba de importación | Pendiente de revisión en el PR |
| 2026-10-16 15:13 | Migue0714 | Antigravity (Gemini) | #11 · Implementar tablero web del orientador con servidor ASGI/REST (`app/server.py`), frontend `HTML/CSS/JS` limpio (`app/static/`) con las 5 vistas y acciones HITL (`interrupt`), y pruebas (`tests/test_tablero.py`) | Decisión arquitectónica: reemplazo de Streamlit por servidor ASGI/FastAPI (`uvicorn app.server:app`) + HTML/CSS/JS limpio sin dependencias pesadas y validación del flujo de Yaritza |
| 2026-10-16 15:24 | Migue0714 | Antigravity (Gemini) | #11 · Agregar selector interactivo de archivos en `data/`, guardrail anti-inyección en ofertas y generación de CV en PDF sin PII (`fpdf2`) al aprobar en `app/server.py` | Verificación de los 3 artefactos en `outbox/` (`cv_*.pdf`, `telegram_*.txt`, `entrevista_*.ics`) |
