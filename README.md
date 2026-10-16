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

En Panamá, la Encuesta de Mercado Laboral del INEC de **agosto de 2026** estimó una tasa de desocupación nacional de **8,5 %**. Para las personas de **20 a 24 años** fue **22,6 %**, y para las de **25 a 29 años**, **14,4 %**. El mismo boletín reportó **45,8 %** de empleo informal no agrícola. Son indicadores de contexto nacional, no resultados ni línea base de Puente: [comentario oficial](https://www.inec.gob.pa/archivos/P053342420260930151945Comentarios.pdf), [cuadro de desocupación por edad](https://www.inec.gob.pa/archivos/P0579518620260930152328Cuadro%205.pdf) y [boletín oficial, cuadro 26](https://www.inec.gob.pa/archivos/P0862619320260930153357Bolet%C3%ADn%20de%20la%20Encuesta%20de%20Mercado%20Laboral%20-%20Agosto%202026.pdf).

El reto que aborda esta demo es concreto: una persona joven puede tener habilidades adquiridas en trabajo informal sin un certificado que las haga visibles, mientras una oferta aparentemente cercana puede exigir un viaje o pasaje incompatible con su horario y presupuesto. Puente toma evidencia literal de esas habilidades, evalúa restricciones de traslado, horario y salario, y presenta al orientador humano una lista corta con motivos verificables. La demo usa perfiles y vacantes sintéticos; todavía no demuestra colocaciones ni reducción real del desempleo.

El [plan original de Jesús del 16 de octubre](docs/plan.md) se conserva como documento histórico. Sus cifras de septiembre de 2025 y tecnologías previstas describen la propuesta inicial; para el horario y los recortes vigentes manda [el issue #41](https://github.com/HackathonFISC/equipo-01/issues/41), y esta sección usa los datos oficiales más recientes.

### Cómo mediremos el impacto en la demo

`puente/metricas.py` calcula desde el desglose de los matches: (1) número de ofertas descartadas por traslado o pasaje y motivo; (2) diferencia mensual de minutos de ida y vuelta y de costo frente a **cada** oferta descartada con ruta conocida, con 22 días laborables por defecto, igual que el valor por defecto del solver; y (3) habilidades pedidas por una oferta asignada que el perfil respalda con evidencia literal. El rótulo de salida es **«Ilustrativo, datos de la demo»**. Las comparaciones son alternativas hipotéticas: no se suman entre ofertas ni se presentan como ahorro observado. Si falta una ruta o un match viable, no se calcula una diferencia. La evidencia literal no prueba por sí sola que una habilidad provenga de trabajo informal. Los traslados de la demo se documentan en [`data/FUENTES.md`](data/FUENTES.md); la estimación mensual depende de la cantidad de días laborables elegida.

### Nivel alcanzado: autonomía acotada y verificable

El **documento oficial de retos, sección 7 («Rúbrica base»)** asigna 25 % al diseño agéntico y pide mostrar roles, herramientas, autonomía, componente determinístico y «nivel alcanzado», pero **no publica una escala de niveles**. El taller de preparación *Introducción al Paradigma Agéntico · Prompting avanzado con OpenCode* (Ing. Carlos Rodríguez-Chong, labVIA, 22 de septiembre de 2026, diapositivas 8, 9, 12 y 13) distingue un chatbot que responde, un flujo que sigue pasos programados y un agente que elige la siguiente acción según el objetivo y el resultado. También describe el ciclo de acción y observación, y exige que la autonomía dependa de permisos y condiciones de parada. Usamos esos criterios para describir el comportamiento observable de Puente, sin atribuirle un grado numérico oficial.

Presentamos Puente como un **sistema agéntico de autonomía acotada**. En el código actual, el [router](puente/graph.py) elige la ruta según el tipo de entrada; [Auditor](puente/agents/auditor.py) puede devolver el caso a Matching **una vez** al detectar una diferencia contrafactual y luego entrega el caso al orientador. El [solver](puente/solver/asignacion.py) calcula las restricciones y el puntaje de forma determinística. El grafo conserva estado con un checkpointer SQLite y se detiene con `interrupt()` para que el orientador pueda quitar un candidato y pedir el recálculo, rechazar o aprobar. La aprobación requiere consentimiento confirmado antes de Postulación; las herramientas de esta registran en SQLite y preparan PDF, mensaje y cita. Las rutas y los límites están definidos por código, y el envío depende de autorización humana.

El tablero conectado al grafo (#49) debe demostrar ese recálculo durante la demo. El reacomodo tras rechazo de la empresa o vacante cubierta pertenece a #54 y aún no cuenta como capacidad alcanzada. Perfil usa cinco preguntas fijas; la selección adaptativa de preguntas tampoco se presenta como implementada. Si la organización aclara qué entiende por «nivel alcanzado», se ajustará esta sección con ese criterio.

## Arquitectura de agentes

Agentes, herramientas, flujos y punto de intervención humana (incluir el diagrama).

## Tecnologías y fuentes de datos

## Instalación y ejecución

Requisitos: [uv](https://docs.astral.sh/uv/). `uv` descarga Python 3.12 si no está instalado.

```bash
uv sync
cp .env.example .env   # llenar con claves propias; .env nunca se sube
uv run pytest
```

Credenciales de demo sin claves reales: ver `.env.example`.

## Guardrails y datos personales

Las palabras clave de inyección ("ignora tus instrucciones", "eres ahora", "system prompt") solo **marcan** el texto, que sigue como dato; no bloquean nada, porque una lista de palabras se esquiva con facilidad. La defensa real es estructural: el LLM responde con salida estructurada que se valida con Pydantic, y las decisiones que importan (orden de candidatos, traslados, legalidad de una oferta) se toman en código. Antes de `graph.invoke`, `limpiar_entrada()` de `puente/guardrails.py` quita cédula, teléfono y dirección exacta. Así el dato crudo no llega al checkpoint, a `decisiones`, al LLM ni a LangSmith, que además corre con `LANGSMITH_HIDE_INPUTS` y `LANGSMITH_HIDE_OUTPUTS`. Las notas de voz se rechazan por ahora con un mensaje claro.

## Evals

Al menos 15 casos de prueba, métrica y resultados.

## Bitácora de IA

Qué partes se hicieron con IA, con qué herramienta y qué se modificó a mano. Registro completo en [`docs/bitacora_ia.md`](docs/bitacora_ia.md).

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
