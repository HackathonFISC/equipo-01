# Decisiones de diseño (ADR cortos)

Una entrada por decisión que cambie stack, arquitectura, alcance o forma de trabajo. Lo que no está aquí o en un issue no se decidió. Formato: fecha, título, decisión, contexto, consecuencias, quién.

## 2026-10-16 · Protocolo de trabajo en AGENTS.md

- **Decisión:** GitHub Issues como única fuente de verdad; reclamo con `estado: en proceso` y comentario 🔒; latidos cada hora y regla de abandono a los 90 min; una rama `issue-N-descripcion` y un worktree por issue; bitácora de IA escrita en el momento con `scripts/bitacora.sh` y exigida por un hook pre-commit; `docs/bitacora_ia.md` con `merge=union` para que varias ramas añadan filas sin conflictos.
- **Contexto:** tres personas con agentes de desarrollo de tres proveedores (Claude Code, Codex, Antigravity) en paralelo; riesgo de duplicar trabajo, de conflictos de merge en archivos compartidos y de dejar la bitácora para el final.
- **Consecuencias:** tres etiquetas nuevas (`estado: en proceso`, `estado: en revisión`, `estado: bloqueado`); scripts en `scripts/` y hook en `.githooks/`; `CLAUDE.md`, `GEMINI.md` y `.agents/rules/` apuntan a `AGENTS.md`.
- **Quién:** @Cornelio-Guerra

## Plantilla

## AAAA-MM-DD · Título

- **Decisión:**
- **Contexto:**
- **Consecuencias:**
- **Quién:**
