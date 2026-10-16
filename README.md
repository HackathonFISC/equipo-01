# Puente

<!-- Cambia el 0 por el número de su reto y deja la línea tal cual ("Reto: N"):
       1 = Agua · 2 = Educación · 3 = Empleabilidad
     No cambien de reto sin autorización de los organizadores. -->
Reto: 3

Sistema agéntico de inserción laboral juvenil: conecta jóvenes, vacantes y cursos con un solver que respeta traslado, horario y salario, y un orientador humano que aprueba antes de cualquier envío.

## Equipo

- Jesus Guerra (@jesus12ga)
- Cornelio Guerra (@Cornelio-Guerra)
- Miguel Vasquez (@Migue0714)

## Problema

En Panamá, conseguir el primer empleo formal es más difícil para quien tiene menos de 30 años, y no solo por falta de vacantes. Las ofertas llegan sucias (anuncios que piden edad o sexo, requisitos vagos), los jóvenes no tienen CV y casi nadie calcula si el traslado y el pasaje hacen viable el trabajo.

Cifras con su fuente (consultadas el 2026-10-16; cada una debe volver a verificarse antes de la entrega):

- Desempleo nacional de 10.4 %, desempleo de 15 a 19 años de 24.3 % e informalidad no agrícola de 47.1 %. Fuente: INEC, Encuesta de Mercado Laboral, septiembre de 2025 ([comentario del INEC](https://www.inec.gob.pa/archivos/P053342420260304095947Comentario%20Sept%202025.pdf)).
- Desempleo de 15 a 29 años de 19.9 % (113,347 jóvenes). Fuente: La Estrella de Panamá, 18 de agosto de 2026, que cita al INEC ([nota](https://www.laestrella.com.pa/economia/oit-alerta-desempleo-juvenil-mundial-sube-a-124-cual-es-la-situacion-en-panama-DI24976621)).
- La Ley 11 de 2005 (Gaceta Oficial 25287) prohíbe en el art. 2 los anuncios de empleo que exijan una edad; el art. 1 cubre sexo, raza, discapacidad y otros motivos. No menciona la apariencia ([texto de la ley](https://panama.justia.com/federales/leyes/11-de-2005-apr-27-2005/gdoc/)).
- El INADEH abrió 1,663 cursos gratuitos desde el 5 de octubre de 2026 ([TVN](https://www.tvn-2.com/nacionales/inadeh-abre-1-600-cursos_1_2264127.html)).
- La Línea 3 del Metro (Panamá Oeste) empezó pruebas en abril de 2026 y se espera que opere en 2028 ([Prensa](https://www.prensa.com/sociedad/metro-inicia-las-primeras-pruebas-del-monorriel-de-la-linea-3/)). Mientras tanto, el traslado desde La Chorrera y Arraiján sigue siendo una barrera práctica.

Cualquier otra cifra va como [CIFRA PENDIENTE · fuente]: no se publican números sin fuente.

**Caso guía: Yaritza.** 20 años, vive en La Chorrera, estudia de noche, no tiene CV y atiende la fonda de su mamá. Es un perfil sintético. Puente le arma un CV con lo que sí sabe hacer, le propone tres vacantes que puede alcanzar en menos de 60 minutos sin gastar más del 15 % del sueldo en pasaje y le sugiere un curso del INADEH para cerrar la brecha que le falta. Un orientador humano aprueba antes de que algo salga hacia una empresa.

## Arquitectura de agentes

LangGraph orquesta seis agentes. Principio de diseño: **el LLM trabaja en los bordes y el código decide en el centro**. El LLM lee ofertas y CVs, entrevista, redacta y explica; el código normaliza, aplica las reglas legales, calcula traslados y horarios, asigna con CP-SAT y audita sesgo. El LLM nunca ordena candidatos, nunca calcula traslados y nunca decide si una oferta es legal.

![Arquitectura de agentes de Puente](docs/arquitectura.png)

| # | Agente | Issue | Responsabilidad | LLM |
|---|---|---|---|---|
| 1 | Mercado | #5 | Ofertas sucias a `Oferta` validada. Bloquea límites de edad (art. 2) y exigencia de sexo (art. 1) de la Ley 11 de 2005; alerta por "buena presencia", foto o estado civil; deduplica por hash | Extrae |
| 2 | Perfil | #6 | CV o entrevista corta a habilidades (`skill_id` de la taxonomía) con la frase que las evidencia; quita cédula, teléfono y dirección antes del LLM | Extrae |
| 3 | Matching | #4 | OR-Tools CP-SAT con restricciones duras: traslado <= 60 min, pasaje mensual <= 15 % del salario y <= presupuesto, horario compatible, salario >= mínimo, k = 3 por vacante | Solo explica |
| 4 | Ruta de formación (opcional) | #14 | Brechas a cursos INADEH con cupo, horario sin cruce y traslado factible (set cover voraz) | Solo explica |
| 5 | Auditor | #8 | Contrafactuales de nombre y género; si el resultado cambia, el grafo vuelve a Matching | No |
| 6 | Postulación | #7, #12 | Tras la aprobación: SQLite, CV en PDF (fpdf2), Telegram con respaldo en `outbox/`, archivo `.ics` | No |

Flujo del grafo (`puente/graph.py`):

```text
entrada -> guardrails (#9) -> router por tipo
   oferta -> Mercado (#5) --+
   perfil -> Perfil  (#6) --+-> Matching (#4) -> Auditor (#8) --sesgo--> Matching
                                      | sin sesgo
                              Formación (#14, opcional)
                                      |
                  interrupt(): tablero del orientador (#11)
              aprobar, editar, quitar candidato o rechazar
                                      |
                    Postulación (#7, #12): SQLite, PDF, Telegram, .ics
```

El `interrupt()` del orientador va antes de Postulación: sin su aprobación no sale nada hacia terceros. Cada nodo registra una `Decision` en la tabla `decisiones` y las trazas van a LangSmith. Contratos de datos en [`puente/schemas.py`](puente/schemas.py); detalle en [`docs/metodologia.md`](docs/metodologia.md).

Estado de la construcción: el esqueleto del grafo con nodos vacíos y los esquemas (#2), el solver (#4), el Auditor (#8), la Formación (#14) y el tablero (#11) están en `main`. El grafo completo con router, `interrupt()` y checkpointer está pendiente de #10; Mercado y Perfil, pendientes de #5 y #6; los guardrails, pendientes de #9; la acción real de Postulación, pendiente de #7 y #12.

## Tecnologías

- Python 3.12 y [uv](https://docs.astral.sh/uv/) para dependencias y ejecución.
- LangGraph para el grafo, con checkpointer SQLite (`langgraph-checkpoint-sqlite`).
- Gemini Flash vía `langchain-google-genai` para extraer, entrevistar, redactar y explicar.
- OR-Tools CP-SAT para la asignación, sin LLM.
- Pydantic para los contratos entre agentes.
- fpdf2 para el CV en PDF, `ics` para el archivo de calendario y SQLite para el embudo y las decisiones.
- Tablero del orientador: servidor ASGI con Starlette y uvicorn, y HTML, CSS y JS en `app/static/`.
- LangSmith para las trazas.
- pytest para las pruebas.

## Fuentes de datos

Los datos de personas son siempre sintéticos; las ofertas no llevan datos personales. Cada archivo de datos con su fuente se documenta en `data/FUENTES.md` (pendiente de #3).

- Traslados (minutos y pasaje entre corregimientos): `data/traslados.csv` (pendiente de #3).
- Cursos con cupo y sede: `data/cursos.csv` (pendiente de #3).
- Taxonomía de habilidades con `skill_id`: `data/taxonomia.csv` (pendiente de #3).
- Ofertas y perfiles sintéticos, entre ellos Yaritza: carpetas `data/ofertas/` y `data/perfiles/` (pendiente de #3).

## Instalación y ejecución

Requisitos: [uv](https://docs.astral.sh/uv/). `uv` descarga Python 3.12 si no está instalado. No hace falta ninguna clave para instalar y probar.

```bash
git clone https://github.com/HackathonFISC/equipo-01.git
cd equipo-01
uv sync
cp .env.example .env   # opcional: llenar con claves propias; .env nunca se sube
uv run pytest -q
```

Tablero del orientador (abrir http://127.0.0.1:8000 en el navegador):

```bash
uv run python -m app.server
```

Recorrer el grafo (hoy con nodos vacíos; el flujo completo de Yaritza queda pendiente de #10 y #12):

```bash
uv run python -m puente.graph
```

Evals (el runner está pendiente de #13):

```bash
uv run python -m evals.run
```

**Modo sin claves.** Sin `TELEGRAM_BOT_TOKEN`, los mensajes para la empresa se escriben en `outbox/` y nada falla. Con `LANGSMITH_TRACING=false` no se envían trazas. Sin `GOOGLE_API_KEY`, el código determinístico (solver, reglas, auditor) y las pruebas funcionan igual, porque el LLM siempre va detrás de una función inyectable que las pruebas reemplazan por un doble. Más detalle en [`docs/metodologia.md`](docs/metodologia.md).

## Credenciales de demo

Solo se usa [`.env.example`](.env.example): se copia a `.env` y cada quien llena sus claves. Nunca se suben claves ni datos personales reales. Variables:

| Variable | Para qué sirve |
|---|---|
| `GOOGLE_API_KEY` | Gemini (Google AI Studio). Sin ella no se llama al LLM |
| `LANGSMITH_API_KEY` | Trazas en LangSmith |
| `LANGSMITH_TRACING` | `true` o `false`; en `false` no hay trazas |
| `LANGSMITH_PROJECT` | Nombre del proyecto de trazas (`puente`) |
| `TELEGRAM_BOT_TOKEN` | Bot de la demo. Sin token, los mensajes van a `outbox/` |
| `TELEGRAM_CHAT_ID_EMPRESA` | Chat de la empresa de demo que recibe la lista corta |
| `PUENTE_DB` | Ruta de la base SQLite (variable de #7; por defecto `puente.db`) |
| `PUENTE_ENTREVISTA_INICIO` | Fecha ISO 8601 fija de la entrevista en el `.ics` (variable de #7) |
| `PUENTE_CHEQUEO_ENTREGA` | Con valor `1`, las pruebas verifican que cada módulo con código tenga fila en la bitácora (se usa en el ensayo de #17) |

## Evals

Al menos 15 casos de prueba con resultado esperado escrito antes de correr, métrica y resultados por categoría. Casos: `evals/casos.yaml` (pendiente de #13). Runner: `evals/run.py`. Resultados: `evals/resultados.md` (pendiente de #13). No se citan métricas aquí hasta que existan; no se inventan.

Las pruebas de la propia entrega, `tests/test_docs_entrega.py`, verifican que este README tenga las secciones, que cada comando y ruta citados existan, que cada variable de `.env.example` esté documentada y que no haya claves. Con `PUENTE_CHEQUEO_ENTREGA=1` verifican además que la bitácora cubra cada módulo con código.

## Bitácora de IA

Qué partes se hicieron con IA, con qué herramienta y qué se modificó a mano. El registro completo, una fila por bloque de trabajo, está en [`docs/bitacora_ia.md`](docs/bitacora_ia.md). Resumen por componente; "nada registrado" o "pendiente" significa que la bitácora aún no dice qué se cambió a mano y el autor debe completarlo:

| Componente | Issue | Herramienta | Qué se cambió a mano |
|---|---|---|---|
| Esquemas y grafo | #2 | Claude Code (nube) | Nada registrado todavía |
| Mercado | #5 | Claude Code (nube) | Pendiente: el PR de #5 aún no se fusiona |
| Perfil | #6 | Claude Code (nube) | Pendiente: el PR de #6 aún no se fusiona |
| Matching/solver | #4 | Antigravity (Gemini) | Calibración de tiempos y pasajes de Panamá Oeste a Este; el redactor opcional solo toca el texto, nunca el puntaje |
| Auditor | #8 | Antigravity (Gemini) | Invariante de corregimiento fijo por ser dependencia legítima del traslado; arista de retorno a Matching |
| Formación | #14 | Antigravity (Gemini) | Validación de cupo, horario sin cruce con clases nocturnas y traslado <= 60 min |
| Postulación/herramientas | #7, #12 | Claude Code (flujo implementar-issues) | Pendiente de revisión humana en el PR de #7; #12 sin empezar |
| Guardrails | #9 | Claude Code (nube) | Pendiente: el PR de #9 aún no se fusiona |
| Tablero | #11 | Antigravity (Gemini) | Reemplazo de Streamlit por un servidor ASGI con HTML, CSS y JS limpios; verificación de los artefactos de `outbox/` |
| Evals | #13 | Claude Code (flujo implementar-issues) | Pendiente de revisión humana en el PR de #13 |
| Datos | #3 | Claude Code (ver bitácora) | Pendiente: el PR de #3 aún no se fusiona |

## Flujo de trabajo

Protocolo completo para humanos y agentes de IA (Claude Code, Codex, Antigravity): [`AGENTS.md`](AGENTS.md). Resumen:

1. Cada tarea es un issue (plantilla *Tarea*); cada fallo, un issue con la plantilla *Fallo*.
2. Antes de trabajar se reclama el issue con `scripts/reclamar.sh N descripcion`, que lo marca `estado: en proceso` y crea la rama `issue-N-descripcion` con su worktree. Si ya está en proceso, se toma otro. Nadie empuja directo a `main`.
3. PR con `Closes #N` (`scripts/entregar.sh N`); otro integrante o Claude lo revisa antes de fusionar.
4. Claude revisa issues y PRs; sus correcciones van en su propia rama `claude/issue-N-...` con un PR aparte, sin tocar ramas ajenas.
5. Todo uso de IA se anota en `docs/bitacora_ia.md` en el momento con `scripts/bitacora.sh N "qué se pidió" "qué se cambió a mano"`; el hook pre-commit no deja commitear código sin su fila.
6. Hora 23:30: tag `final-24h`. Después de eso no se empuja nada.

---

Entrega: tag de git `final-24h` sobre el último commit válido, al cumplirse la hora 24.
