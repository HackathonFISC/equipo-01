# Decisiones de diseño (ADR cortos)

Una entrada por decisión que cambie stack, arquitectura, alcance o forma de trabajo. Lo que no está aquí o en un issue no se decidió. Formato: fecha, título, decisión, contexto, consecuencias, quién.

## 2026-10-16 · Protocolo de trabajo en AGENTS.md

- **Decisión:** GitHub Issues como única fuente de verdad; reclamo con `estado: en proceso` y comentario 🔒; latidos cada hora y regla de abandono a los 90 min; una rama `issue-N-descripcion` y un worktree por issue; bitácora de IA escrita en el momento con `scripts/bitacora.sh` y exigida por un hook pre-commit; `docs/bitacora_ia.md` con `merge=union` para que varias ramas añadan filas sin conflictos.
- **Contexto:** tres personas con agentes de desarrollo de tres proveedores (Claude Code, Codex, Antigravity) en paralelo; riesgo de duplicar trabajo, de conflictos de merge en archivos compartidos y de dejar la bitácora para el final.
- **Consecuencias:** tres etiquetas nuevas (`estado: en proceso`, `estado: en revisión`, `estado: bloqueado`); scripts en `scripts/` y hook en `.githooks/`; `CLAUDE.md`, `GEMINI.md` y `.agents/rules/` apuntan a `AGENTS.md`.
- **Quién:** @Cornelio-Guerra

## 2026-10-16 · Sesiones en la nube con scripts/nube.sh

- **Decisión:** los issues que se mandan a sesiones de Claude Code en la nube se lanzan con `scripts/nube.sh`, que reclama, crea y empuja la rama desde `origin/main`, lanza la sesión desde ese worktree y deja el enlace en el issue. Cada persona corre `/web-setup` una vez. El prompt obliga a la sesión a agregar el remoto `origin` antes de trabajar.
- **Contexto:** las sesiones de #2, #5, #6 y #9 arrancaron sin remoto y sin acceso a la API de GitHub, porque la app de Claude no está instalada en la organización y la CLI sube una copia local del repo. Sin `/web-setup` no podían empujar; con `/web-setup` sí, después de agregar el remoto.
- **Consecuencias:** la sección 12 de `AGENTS.md`; el humano mueve las etiquetas de estado de los issues que trabaja la nube; si un dueño de la organización instala la app de Claude para GitHub, las sesiones clonarían directo y el paso del remoto dejaría de hacer falta.
- **Quién:** @Cornelio-Guerra

## Plantilla

## AAAA-MM-DD · Título

- **Decisión:**
- **Contexto:**
- **Consecuencias:**
- **Quién:**
