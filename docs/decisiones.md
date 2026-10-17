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

## 2026-10-16 · Tablero del orientador en Starlette/Uvicorn + HTML/CSS/JS limpio (#11, #33)

- **Decisión:** Implementar el tablero del orientador (#11) como un servidor ASGI con `Starlette` + `Uvicorn` (`app/server.py`) y frontend estático en HTML5/CSS3/JS vanilla (`app/static/`), en lugar de `app/streamlit_app.py`.
- **Contexto:** Streamlit recarga todo el script en cada clic, dificulta controlar de forma limpia el ciclo de aprobación humana (`interrupt()`) y las pruebas de extremo a extremo por HTTP (`httpx.ASGITransport`). `starlette`, `uvicorn` y `httpx` ya formaban parte del entorno bloqueado en `uv.lock`.
- **Consecuencias:** El tablero se ejecuta con `uv run uvicorn app.server:app --port 8000`, expone endpoints JSON validados con Pydantic (`415`/`422`), invoca directamente los módulos reales de `puente` (`schemas`, `matching`, `auditor`, `formacion`, `postulacion`) y se prueba de punta a punta con `httpx` en `tests/test_tablero.py`.
- **Quién:** @Migue0714

## Decisiones del 16 de octubre (issue #41)

Copiadas del [issue #41](https://github.com/HackathonFISC/equipo-01/issues/41), que tiene además el horario único de las 24 horas.

- Modelo: **Claude Opus 5.5** (`claude-opus-5-5`), con el crédito de US$100 del equipo, detrás de `puente/llm.py` (#45); Gemini 4 Argon queda de respaldo opcional. La variable es `PUENTE_MODELO_LLM`, la que ya leen los PRs #35 y #36. @Cornelio-Guerra crea y reparte las claves por privado.
- Entradas del grafo: `ofertas_crudas` y `entradas_perfil` (las que ya leen los PRs #35 y #36; #37), más `tipo_entrada` para el router.
- Aprobación: el orientador aprueba la lista corta de cada vacante afectada, que es lo que se envía a la empresa, y confirma el consentimiento de cada joven. El Matching usa todos los perfiles y ofertas de `puente.db` (#46), y las parejas ya enviadas quedan fijas.
- `puente/agents/matching.py` es de @Migue0714 y la explicación es la plantilla determinística, sin LLM (#48).
- "Editar" en la aprobación cambia el texto del mensaje a la empresa y la nota del orientador; no recalcula.
- Cupos y horarios de los cursos del INADEH: datos de prueba, rotulados como tales.
- Ofertas duplicadas: pares de prueba con variación realista; se unen por similitud, no por hash exacto.
- Los minutos de traslado filtran (según el máximo del joven) y desempatan, pero no ponen a competir a unos jóvenes contra otros.
- Empresas con nombres de prueba en datos, tablero y video.
- Demo con jóvenes de 18 a 29 años; el joven consiente antes de que su CV llegue a una empresa; la empresa recibe un CV ciego.
- Formación deja de ser stretch: es el tercer lado del mercado (#53).
- El helper de estado se llama `agregar_decision()` (#42); `db.registrar_decision()` (PR #34) es el que escribe en SQLite.

### Cortes de alcance

- H4: si #42 o los datos mínimos no están, la demo usa 8 corregimientos y 12 ofertas.
- H6: #5 solo con texto (sin imagen ni PDF) y #6 solo con entrevista (sin CV).
- H8: si el Auditor (#8, con el tope de #50 fase 1) no está integrado al grafo, se muestra su reporte actual, corrido aparte sobre la lista corta de Yaritza y guardado en un archivo.
- H10: si el tablero no está conectado al grafo, la demo de H12 se hace por terminal.
- Ningún stretch antes de H12.

### Cierre

Congelar funciones a las 08:00 del 17 de octubre (H20), congelar código a las 10:30 (H22:30) y tag `final-24h` a las 11:00 (H23), nunca después de las 11:30. La hora 0 fue a las 12:00 del 16 de octubre (confirmado por @jesus12ga).

## Plantilla

## AAAA-MM-DD · Título

- **Decisión:**
- **Contexto:**
- **Consecuencias:**
- **Quién:**
