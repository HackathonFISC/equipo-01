# Metodología de Puente

Reto 3, empleabilidad juvenil. Este documento explica:

- cómo se despliega Puente;
- cómo funciona de principio a fin;
- cómo está construido;
- qué decisiones de diseño lo sostienen.

Lo que aún no existe en `main` se marca como "pendiente de #N" y se actualiza en el ensayo de #17.

## Despliegue

**Requisitos.** [uv](https://docs.astral.sh/uv/) y acceso a internet para instalar dependencias. `uv` descarga Python 3.12 si hace falta. No se necesita Docker ni servidor externo: todo corre en una sola máquina.

**Pasos.**

1. Clonar el repositorio y entrar a la carpeta.
2. `uv sync` instala las dependencias fijadas en `uv.lock`.
3. `cp .env.example .env` y llenar solo las claves que se tengan. `.env` nunca se sube al repo. Las variables están descritas en la sección "Credenciales de demo" del [README](../README.md).
4. `uv run pytest -q` corre las pruebas; no usan red ni claves.
5. Demo del grafo con el caso Yaritza: `uv run python -m puente.graph`. Se detiene en cada vacante y pregunta qué hace el orientador; con `--auto aprobar,aprobar` decide sola. Hoy los seis nodos de agentes son los stubs de la fase 1 de #10, sin LLM.
6. Entrevista de Perfil por terminal: `uv run python -m puente.agents.perfil --entrevista`, que necesita la clave del LLM o una respuesta grabada con `--reproducir`.
7. Tablero del orientador: `uv run python -m app.server` y abrir http://127.0.0.1:8000. Hoy usa casos de ejemplo fijos; conectarlo al grafo está pendiente de #49.

**Modo sin conexión o sin claves.** La demo debe poder correr sin red (ensayo de #17):

- Sin `GOOGLE_API_KEY`, la demo del grafo, el solver, las reglas legales, el Auditor y las pruebas funcionan igual: son código determinístico, y el LLM va detrás de una función inyectable. Los textos de explicación usan la plantilla de respaldo. Para la demo con los agentes reales y sin red, el modo reproducir de la capa común de LLM está pendiente de #45.
- Sin `TELEGRAM_BOT_TOKEN`, el envío a la empresa se escribe en `outbox/` y el flujo no falla.
- Con `LANGSMITH_TRACING=false` no se envían trazas.

**Estado persistente.** Hay dos archivos SQLite:

- **Checkpoints del grafo.** Por defecto `checkpoints.db`, o el de `PUENTE_CHECKPOINTS`. Guarda cada caso por `thread_id` para reanudarlo tras el `interrupt()` del orientador.
- **Base de Puente.** Por defecto `puente.db`, o el de `PUENTE_DB`. Tiene las tablas de perfiles, ofertas, postulaciones con su historial, y `decisiones`.

Para reiniciar la demo desde cero se borran esos dos archivos y la carpeta `outbox/`.

## Funcionamiento

**Recorrido con el caso Yaritza.** Perfil sintético: 20 años, La Chorrera, estudia de noche, sin CV, atiende la fonda de su mamá. El caso de la demo está en `tests/fixtures/caso_yaritza.json`.

1. **Guardrails (#9, #56).** Es el primer nodo del grafo. Rechaza, con un mensaje claro:
   - archivos que no son PDF, JPG o PNG, o que pasan de 5 MB;
   - textos vacíos o de más de 10,000 caracteres;
   - edades fuera de 18 a 29 años;
   - notas de voz;
   - lo que está claramente fuera de alcance.

   La inyección de instrucciones solo se marca. Quita cédula, teléfono y dirección exacta de la entrada antes de que la lea cualquier agente.
2. **Router (#10).** Elige el camino según el tipo de entrada:
   - una oferta va a Mercado;
   - un perfil va a Perfil;
   - una consulta recibe una respuesta fija y termina.
3. **Mercado (#5, #43).** Cada oferta sucia pasa a una `Oferta` validada. El LLM solo extrae campos; las reglas son código:
   - Bloquean la oferta: un límite de edad (Ley 11 de 2005, art. 2) o la exigencia de sexo (art. 1). Una oferta bloqueada no entra al Matching; el grafo deja la alerta y termina.
   - Solo alertan, con la frase y la regla que las disparó:
     - las demás causales del art. 1 (raza, nacimiento, discapacidad, clase social, religión, ideas políticas);
     - un puesto escrito solo en femenino;
     - "buena presencia", foto y estado civil. La ley no menciona la apariencia; por eso estas no bloquean.
   - El salario solo se acepta si la cifra aparece en el texto. Si no aparece, queda vacío y marcado "por confirmar"; nunca se inventa.
   - Las ofertas repetidas se unen por similitud: mismo puesto y textos al menos 85 % parecidos. Gana la primera que llegó.
4. **Perfil (#6, #44).** Yaritza llena un formulario fijo que se valida en código: edad, corregimiento, horario de clases y horario libre, salario mínimo y presupuesto diario de pasaje. Como no tiene CV, responde una entrevista de 5 preguntas sobre lo que sabe hacer. La entrevista ocurre fuera del grafo, por terminal o en el tablero (#52), y entra completa como un solo dato.
   - Antes de llamar al LLM se quitan cédula, teléfono y dirección exacta; solo queda el corregimiento.
   - Cada habilidad lleva su `skill_id` y una frase literal de Yaritza que la evidencia. La taxonomía es `data/taxonomia.csv`: 21 habilidades enlazadas a ESCO (#51). Lo que no está en la taxonomía se descarta y se registra.
5. **Matching (#4, #48).** OR-Tools CP-SAT asigna sin LLM y con resultado reproducible (un solo hilo y semilla fija). Restricciones duras:
   - Traslado <= 60 minutos desde el corregimiento del joven. Si no hay dato de traslado para el par, no es viable.
   - Pasaje mensual (ida y vuelta por los días laborables; 22 por defecto en el solver) <= 15 % del salario, y pasaje diario <= el presupuesto del joven.
   - Horario sin cruce con sus clases y dentro de su horario libre.
   - Salario >= el mínimo que pide el joven.
   - Hasta 3 candidatos por vacante.

   Los minutos y pasajes salen de `data/traslados.csv`, medido a hora pico con Google Maps Transit y Moovit y las tarifas de MiBus, el Metro y la ATTT (#51). Para Yaritza, por ejemplo, La Chorrera a Tocumen son 95 minutos y queda fuera; a Calidonia son 55. Los umbrales de 60 minutos y 15 % son supuestos del equipo, rotulados así en `data/FUENTES.md`.

   El LLM solo redacta por qué cada match puntúa como puntúa; no cambia puntajes ni viabilidad.
6. **Auditor (#8, #50).** Vuelve a correr Perfil y Matching con variantes contrafactuales:
   - otro nombre y otro género, con la concordancia del texto ajustada;
   - otro corregimiento que conserva la misma fila de traslados. Así se mide si el resultado cambia solo por el nombre del lugar, sin tocar el traslado real, que es una restricción legítima.

   Una línea base de 3 corridas del perfil original descuenta la variación propia del LLM. Si una variante cambia el resultado, el grafo recalcula una sola vez. Si la diferencia persiste, no hay bucle: pasa al orientador como alerta.
7. **Formación (#14, #53).** Para las brechas de habilidades, propone cursos del INADEH con cupo, horario sin cruce con las clases, traslado <= 60 minutos y pasaje dentro del presupuesto. El algoritmo es un set cover voraz. Los cursos salen de `data/cursos.csv` (#51): 9 cursos con títulos reales del INADEH, cuyos cupos y horarios son datos de prueba rotulados como tales.
8. **Orientador, con `interrupt()` (#10, #11).** El grafo se detiene en cada vacante con su lista corta. El orientador:
   - ve los candidatos con su puntaje y sus brechas, la alerta del Auditor y el mensaje sugerido para la empresa;
   - confirma el consentimiento de cada joven;
   - elige una acción:
     - **aprobar**;
     - **editar**: cambia el mensaje a la empresa y su nota, sin recalcular;
     - **quitar** a un candidato, y el grafo vuelve a Matching;
     - **rechazar**.

   Solo los jóvenes con consentimiento confirmado se postulan. Hoy esto se hace por terminal; el tablero conectado al grafo está pendiente de #49.
9. **Postulación (#7, #12).** Solo tras la aprobación se ejecuta lo siguiente:
   - se guarda la postulación en SQLite;
   - se genera el CV en PDF con las habilidades que tienen evidencia;
   - se envía la lista corta a la empresa por Telegram, o a `outbox/`;
   - se crea el `.ics` de la entrevista.

   Las herramientas están en `main` y probadas. Conectarlas al nodo del grafo y escribir cada `Decision` en la tabla `decisiones` está pendiente de #12; hoy el grafo usa un stub que no escribe nada.

**Estados de una postulación.** `perfil_listo`, `preseleccionado`, `aprobado`, `enviado`, `entrevista`, `contratado`, `no_seleccionado`. El embudo del tablero cuenta cuántas hay en cada uno.

**Trazabilidad.**

- **Decisiones.** Cada nodo agrega una `Decision` (agente, resumen de entrada y salida, razón y hora) al estado del grafo, y el checkpointer las guarda con el caso. Al final de la demo, el grafo imprime la cadena de agentes que decidieron. La copia en la tabla `decisiones` de SQLite llega con #12.
- **LangSmith.** Se activa solo con `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY`, y oculta entradas y salidas con `LANGSMITH_HIDE_INPUTS` y `LANGSMITH_HIDE_OUTPUTS`.

**Datos.** Los datos de personas son siempre sintéticos. `data/FUENTES.md` documenta la fuente de cada archivo y separa dato medido (traslados, taxonomía), dato de prueba (cupos y horarios de cursos, perfiles) y supuesto del equipo (umbrales del solver). Si falta un CSV, el código usa una tabla de respaldo estimada que no debe presentarse como medición. Las ofertas y perfiles de `data/` están pendientes de #3.

## Arquitectura

Diagrama: [`arquitectura.png`](arquitectura.png). Se regenera con `uv run python docs/generar_arquitectura.py` cuando cambia el flujo.

![Arquitectura de agentes de Puente](arquitectura.png)

**Principio: LLM en los bordes, código en el centro.** El LLM lee texto sucio, entrevista, redacta y explica. El código decide todo lo que debe ser verificable:

- la legalidad de una oferta;
- el traslado, el pasaje y el horario;
- la asignación;
- la auditoría de sesgo.

El LLM nunca ordena candidatos, nunca calcula traslados y nunca decide si una oferta es legal. Por eso `puente/solver/` y `puente/rules/` no importan ningún modelo de lenguaje, y las pruebas inyectan un doble en lugar del LLM.

**Módulos y su issue.**

| Módulo | Issue | Contenido |
|---|---|---|
| `puente/schemas.py` | #2, #42 | Contratos Pydantic: `Oferta`, `Perfil`, `Match`, `Decision`, `EstadoPuente`, el enum de estados y `agregar_decision()` |
| `puente/graph.py` | #10 | Grafo LangGraph: guardrails, router, `interrupt()` del orientador, checkpointer SQLite y la demo por terminal. Fase 1 con stubs en los nodos de agentes; la fase 2 conecta los agentes reales |
| `puente/guardrails.py` | #9, #56 | Validación de entradas, edad de 18 a 29, audio, inyección, fuera de alcance y `limpiar_entrada()` |
| `puente/agents/mercado.py`, `puente/rules/discriminacion.py`, `puente/rules/taxonomia.py` | #5, #43 | Agente Mercado, reglas de la Ley 11 de 2005 y taxonomía |
| `puente/agents/perfil.py` | #6, #44 | Agente Perfil: formulario fijo, CV o entrevista, y el modo terminal |
| `puente/solver/asignacion.py`, `puente/rules/traslados.py`, `puente/agents/matching.py` | #4, #48 | CP-SAT, tabla de traslados y nodo Matching |
| `puente/agents/auditor.py` | #8, #50 | Variantes contrafactuales, línea base y tope de un recálculo |
| `puente/agents/formacion.py` | #14, #53 | Ruta de formación |
| `puente/tools/db.py`, `puente/tools/pdf.py`, `puente/tools/telegram.py`, `puente/tools/calendario.py`, `puente/agents/postulacion.py` | #7, #12 | SQLite con la tabla `decisiones`, CV en PDF, Telegram con respaldo, `.ics` y agente Postulación; su nodo en el grafo está pendiente de #12 |
| `app/server.py`, `app/static/` | #11, #33, #49 | Tablero del orientador (servidor ASGI con Starlette y HTML, CSS y JS) |
| `evals/run.py` | #13 | Runner de evals, hoy un esqueleto (pendiente de #13) |
| `README.md`, `docs/arquitectura.png`, `docs/generar_arquitectura.py`, `docs/metodologia.md`, `docs/bitacora_ia.md` | #15 | Entrega escrita |

**Contratos.** Los agentes se hablan solo a través de los modelos de `puente/schemas.py`. Ese archivo está congelado: cambiarlo exige un issue propio y actualizar todos los usos en el mismo PR (así se hizo con los contratos v2, #42). Cada nodo recibe el estado del grafo y devuelve un dict con las claves que cambia.

**Pruebas.** No usan red ni claves:

- la llamada al LLM vive detrás de una función o un objeto inyectable, y Telegram también;
- los archivos generados (SQLite, PDF, `outbox/`) se escriben en carpetas temporales.

Los evals propios, con resultado esperado escrito antes de correr, son de #13.

## Decisiones de diseño

El registro completo de decisiones de stack, arquitectura y forma de trabajo está en [`decisiones.md`](decisiones.md); el horario único y las decisiones del 16 de octubre, en #41. La [bitácora de IA](bitacora_ia.md) registra qué se hizo con IA, con qué herramienta y qué se cambió a mano. Las decisiones que dan forma al producto:

**1. LLM en los bordes, código en el centro.** Un modelo de lenguaje no da la misma respuesta dos veces ni explica de forma verificable por qué descartó a alguien. Por eso solo lee, entrevista, redacta y explica. El resto lo decide código que da la misma salida con la misma entrada: legalidad, traslado, pasaje, horario, asignación y auditoría. Cada decisión queda como una `Decision` con su motivo.

**2. El traslado filtra y desempata, pero no pone a competir a unos jóvenes contra otros.** Vivir lejos no debe dejar a nadie en peor lugar que otro joven igual de apto para la misma vacante. Los minutos sirven para descartar lo que el joven no puede alcanzar y para desempatar entre opciones del mismo joven. El Auditor comprueba que cambiar solo el corregimiento, con el mismo traslado, no cambie el resultado.

Hoy el solver usa un máximo fijo de 60 minutos y resta los minutos en el puntaje de cada par. El máximo por joven, que el puntaje no castigue la distancia y los umbrales con fuente son #48.

**3. Datos de prueba rotulados.** Las personas son sintéticas (Yaritza Ficticia, Carlos Ficticio) y las empresas llevan nombres de prueba (Minisúper Ficticio El Ahorro). Los cupos y horarios de los cursos del INADEH son datos de prueba y así se rotulan. Los traslados sí están medidos, con fuente y fecha, y los umbrales de 60 minutos y 15 % se declaran como supuestos del equipo (`data/FUENTES.md`, #51). Ninguna cifra de prueba se presenta como medición real.

**4. Menores de 18 fuera de la demo, como trabajo futuro.** La demo atiende de 18 a 29 años, y el guardrail rechaza otras edades. El grupo de 15 a 19 años tiene la tasa de desocupación más alta según el INEC, pero contratar a menores de edad tiene reglas propias que el sistema todavía no modela. Queda como trabajo futuro, no como un caso que se resuelve a medias.

**5. Consentimiento del joven y CV ciego.** Nada sale hacia una empresa sin la aprobación del orientador, y esa aprobación exige confirmar el consentimiento de cada joven. Quien no consintió se queda fuera de la postulación, aunque esté en la lista corta. Que el joven dé su consentimiento desde su propia vista es #52.

La empresa debe recibir un CV ciego: sin datos que permitan discriminar antes de la entrevista. Hoy el PDF de #7 ya omite la dirección exacta, pero todavía lleva el nombre (ficticio) del joven; cegarlo es parte de conectar Postulación (#12).

**6. Sin bucles y con el humano al final.** El Auditor recalcula una sola vez. Si el sesgo persiste, se lo muestra al orientador en vez de iterar sin fin. "Editar" cambia el mensaje y la nota, no el cálculo; "quitar" sí recalcula.

**7. Robustez antes que adorno.** El sistema nunca inventa un dato que falta: un salario no publicado queda vacío y marcado, y un par sin dato de traslado no es viable. Telegram sin token escribe en `outbox/`. La inyección se marca, pero la defensa real es la salida estructurada y que las decisiones las tome el código.
