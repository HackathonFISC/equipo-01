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

Hora límite de entrega: **13:00 del 17 de octubre** (hora 0 = 13:00; registrado por @Cornelio-Guerra a las 23:40; @jesus12ga había anotado 12:00 a las 19:26, pendiente de confirmación escrita de la organización). Para ser seguros con cualquiera de las dos horas: congelar funciones a las 09:00 (H20), congelar código a las 11:30 y tag `final-24h` a las 11:45, nunca después de las 12:00. La hora extra, si existe, es solo para documentación. Demo MVP de H12 a la 01:00; video a las 10:00.

## 2026-10-16 23:50 · Plan después de la demo: ideas nuevas como `stretch`

- **Decisión:** después de la demo de H12 y antes del congelamiento de código (11:30) se trabajan, en este orden y solo como `stretch`, el redactor de puente semántico (#157), la vista empresa (#167), la vista aplicante fase 2 (#166), la micro-misión con scorecard de evidencia (#158), el horario vivo desde la tirilla de matrícula (#164) y el chatbot de WhatsApp (#160); Miguel toma la cobertura por turnos (#165) y la vista admin con roles (#168) después del video; Jesús, la reserva tripartita con formación (#159) solo si la documentación (#15) va bien; Vercel (#161) queda como demo pública de solo lectura, nunca como ruta de la demo en vivo.
- **Contexto:** ideas propuestas por Cornelio a las 23:30 (micro-misión, escrow de talento, redactor anti-sesgo, modo semestre y job-sharing, tres vistas, WhatsApp, Vercel). Se juzgaron contra la rúbrica y el tiempo restante; cada issue trae las mejoras: validación en código y no «por IA», cifras calculadas por el solver sobre el pool real, nombres sin implicaciones laborales («carta de intención», «cobertura por turnos»), texto antes que voz, datos personales fuera.
- **Consecuencias:** nada de esto entra en la ruta de la demo de H12 ni se fusiona después de las 11:30; toda vista nueva va en archivos y rutas nuevas; lo que no esté fusionado se declara fuera de alcance en la tabla de cobertura del README. Los entregables obligatorios (#15, #16, #129, #17) van primero.
- **Quién:** @Cornelio-Guerra

## 2026-10-17 04:30 · WhatsApp: webhook en la laptop y única excepción del modo demo pública

- **Decisión:** el webhook `POST /api/whatsapp` (#160) corre en la laptop de la demo detrás de `cloudflared tunnel --url http://localhost:8000`, no en Vercel. En modo demo pública (`PUENTE_DEMO_PUBLICA=1` o Vercel) es la única ruta POST que pasa el middleware de solo lectura, y la ruta misma rechaza con 403 lo que no traiga una firma de Twilio válida. El consentimiento por WhatsApp se pide en cuanto la vacante entra en la lista corta, antes de que el orientador apruebe.
- **Contexto:** Vercel (#161) responde 403 a todo POST. Exponer la laptop por el túnel expone el tablero entero; con el modo público queda en solo lectura y el bot sigue recibiendo mensajes firmados. El tablero exige el consentimiento antes de aprobar (#49), así que pedirlo después de la aprobación no tendría efecto.
- **Consecuencias:** en modo público el LLM queda en `reproducir` y una entrevista nueva no arma el perfil. La demo en vivo corre sin modo público y la URL del túnel solo va a la consola de Twilio. El número del joven no se guarda: el bot no escribe por su cuenta salvo el «perfil listo» y el resultado de la aprobación sigue por Telegram u `outbox/`.
- **Quién:** @Cornelio-Guerra (Claude Code)

## Plantilla

## AAAA-MM-DD · Título

- **Decisión:**
- **Contexto:**
- **Consecuencias:**
- **Quién:**
