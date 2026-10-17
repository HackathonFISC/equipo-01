# Metodología de Puente

Reto 3 del Hackathon FISC 2026: empleabilidad juvenil. Puente ayuda a un orientador a conectar jóvenes de 18 a 29 años con vacantes que de verdad pueden tomar. Puede tomar una vacante quien llega en 60 minutos o menos, paga el pasaje y cumple el horario sin perder sus clases. Este documento cubre:

1. [Arquitectura](#1-arquitectura): qué piezas hay y cómo se hablan.
2. [Funcionamiento](#2-funcionamiento): el recorrido de un caso, nodo por nodo.
3. [Despliegue](#3-despliegue): cómo se instala y se corre, también sin red y sin claves.
4. [Decisiones de diseño](#4-decisiones-de-diseño): qué decidimos y por qué.
5. [Límites](#5-límites): qué no hace, o hace a medias.

Todo lo que se describe está en `main`. Las cifras salen de archivos del repositorio, que se citan, o de una fuente externa con enlace. Si una pieza existe pero no está conectada a la demo, se dice.

---

## 1. Arquitectura

### Principio: el LLM en los bordes y el código en el centro

**El LLM** se usa para:
- leer texto desordenado: ofertas copiadas de portales y respuestas de una entrevista;
- leer la foto o el PDF de la tirilla de matrícula;
- extraer campos;
- redactar la versión legal de una oferta bloqueada;
- explicar las rutas de Formación, solo si se activa `usar_llm_formacion`, que está apagado por defecto.

**Todo lo que tiene que poder verificarse lo decide el código**, con la misma salida para la misma entrada:
- si una oferta es legal;
- el tiempo de traslado, el pasaje y el horario;
- la asignación de jóvenes a vacantes;
- la auditoría de sesgo.

Por eso `puente/solver/` y `puente/rules/` no importan ningún modelo de lenguaje. El LLM nunca ordena candidatos, nunca calcula traslados y nunca decide si una oferta es legal.

### Grafo de agentes

El flujo es un grafo de **LangGraph** (`puente/graph.py`, #10) con 14 nodos. Por defecto `construir_grafo()` usa los agentes reales (`nodos_fase_2()`). Los stubs de la fase 1 (`NODOS_FASE_1`) sirven para probar la mecánica del grafo, y el tablero los usa para Mercado y Perfil (ver [Límites](#5-límites)).

El diagrama sale del grafo real, sin red:

```bash
uv run python -c "from puente.graph import construir_grafo; print(construir_grafo().get_graph().draw_mermaid())"
```

A mano solo se marcó en rojo el nodo `aprobacion`, que es el `interrupt()` del orientador; el resto sale tal cual. Las flechas punteadas son aristas condicionales.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	guardrails(guardrails)
	router(router)
	consulta(consulta)
	mercado(mercado)
	alerta_oferta(alerta_oferta)
	perfil(perfil)
	matching(matching)
	sin_matches(sin_matches)
	auditor(auditor)
	formacion(formacion)
	aprobacion{{"aprobacion · interrupt() del orientador"}}:::humano
	postulacion(postulacion)
	evento_empresa(evento_empresa)
	avisos(avisos)
	__end__([<p>__end__</p>]):::last
	__start__ -.-> __end__;
	__start__ -.-> guardrails;
	__start__ -.-> router;
	aprobacion -.-> __end__;
	aprobacion -.-> matching;
	aprobacion -.-> postulacion;
	auditor -. &nbsp;continuar&nbsp; .-> formacion;
	auditor -.-> matching;
	auditor -.-> perfil;
	avisos --> matching;
	evento_empresa -.-> aprobacion;
	evento_empresa -.-> avisos;
	formacion --> aprobacion;
	guardrails -.-> __end__;
	guardrails -.-> router;
	matching -.-> auditor;
	matching -.-> sin_matches;
	mercado -.-> alerta_oferta;
	mercado -.-> matching;
	perfil --> matching;
	postulacion -.-> __end__;
	postulacion -.-> aprobacion;
	router -.-> consulta;
	router -.-> evento_empresa;
	router -.-> mercado;
	router -.-> perfil;
	alerta_oferta --> __end__;
	consulta --> __end__;
	sin_matches --> __end__;
	aprobacion -.-> aprobacion;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef humano fill:#fde2e2,stroke:#c0282d,stroke-width:3px
```

Cómo se lee cada arista condicional:

- **`__start__`.** Una entrada nueva va a `guardrails`. Un evento de la empresa entra directo al `router`, sin Guardrails: es una acción estructurada, como la respuesta al `interrupt()`, y su nota ya llega limpia de datos personales. Una invocación sin nada que revisar termina sin efectos.
- **`auditor`.**
  - Vuelve a `perfil` solo si la diferencia viene de cómo se leyó el perfil (`accion_auditor="perfil"`).
  - Si la diferencia está en la asignación, vuelve a `matching`.
  - Las dos vueltas comparten el tope de un recálculo. Si no hay diferencia, o se llegó al tope, sigue a `formacion` («continuar»).
- **`aprobacion`.**
  - «Quitar» vuelve a `matching`.
  - «Aprobar» y «editar» pasan a `postulacion`.
  - «Rechazar» pasa a la siguiente vacante pendiente, que es el lazo de `aprobacion` sobre sí misma, o termina si no queda ninguna. Una respuesta inválida también repite la pausa.
- **`postulacion`.** Vuelve a `aprobacion` mientras queden vacantes por decidir.
- **`evento_empresa`.** Pasa a `avisos` y de ahí a `matching`. Si el evento no cambia nada, vuelve a `aprobacion`.

### Módulos

| Módulo | Issues | Qué hace |
|---|---|---|
| `puente/schemas.py` | #2, #42 | Contratos Pydantic: `Oferta`, `Perfil`, `Match`, `Decision`, `EstadoPuente` y `EstadoPostulacion`, más `OfertaExtraida` y `PerfilExtraido`, que es lo que produce el LLM en el flujo principal. Las extensiones tienen su propia salida del LLM, que el código también valida y que se define en el módulo de cada una, no aquí: `TirillaExtraida` (`horario.py`), `TextoRedactado` (`redactor.py`) y `RespuestaExtraida` (`micromision.py`). |
| `puente/graph.py` | #10, #12, #54 | Grafo, router, `interrupt()` del orientador, checkpointer SQLite, reacomodo por evento de la empresa y demo por terminal. |
| `puente/guardrails.py` | #9, #56, #128 | Validación de entradas y limpieza de datos personales, sin LLM. |
| `puente/llm.py` | #45, #125, #137 | Capa única de acceso al LLM: Claude por defecto, modos vivo, grabar y reproducir, tope de gasto y esquema simplificado para la salida estructurada. |
| `puente/agents/mercado.py`, `puente/rules/discriminacion.py`, `puente/rules/taxonomia.py` | #5, #43, #73, #75, #94, #117 | Extracción de ofertas, reglas de la Ley 11 de 2005, deduplicación y taxonomía. |
| `puente/agents/perfil.py` | #6, #44, #63 | Formulario fijo, CV o entrevista de 5 preguntas, y habilidades con evidencia literal. |
| `puente/solver/asignacion.py`, `puente/rules/traslados.py`, `puente/agents/matching.py` | #4, #48, #127 | Asignación con OR-Tools CP-SAT, matriz de traslados y explicación con plantilla. |
| `puente/agents/auditor.py` | #8, #50 | Variantes contrafactuales, línea base y tope de un recálculo. |
| `puente/agents/formacion.py` | #14, #53 | Cursos del INADEH para cerrar brechas: cobertura voraz de conjuntos (*set cover*). |
| `puente/agents/postulacion.py`, `puente/tools/` | #7, #12, #84, #212 | SQLite, CV ciego y carta de presentación en PDF, Telegram con respaldo en `outbox/`, `.ics` y agenda de la entrevista. |
| `puente/cargar.py` | #46 | Carga inicial de `data/` en `puente.db`. |
| `puente/metricas.py` | #47, #198 | Indicadores ilustrativos con datos de la demo, rotulados como tales, y tendencias del mercado sobre las ofertas de `puente.db`. |
| `app/server.py`, `app/empresa.py`, `app/joven_fase2.py`, `app/static/` | #11, #33, #49, #52, #166, #167 | Starlette y Uvicorn, con HTML, CSS y JS sin framework. Sirve tres vistas: el tablero del orientador (`/`); la vista del joven (`/joven`), con formulario, entrevista de una vez o por chat, tirilla de matrícula y «Mis postulaciones»; y la lista corta ciega de la empresa (`/empresa/lista-corta`), con aceptar, rechazar o vacante cubierta, más `/empresa` para publicar una vacante, que responde solo con el resultado de su oferta. |
| `evals/run.py`, `evals/casos.yaml` | #13, #129 | Evals propios, con el resultado esperado escrito antes de correr. |
| `puente/agents/horario.py`, `redactor.py`, `micromision.py` | #164, #157, #158 | Fase 1 de tres extensiones. Horario ya lee la tirilla desde `/joven`. Redactor y Micromisión se usan desde su propia CLI y desde los evals, sin conexión al grafo ni al tablero (ver [Límites](#5-límites)). |

### Contratos y estado

- **Contratos.** Los agentes se hablan solo a través de los modelos de `puente/schemas.py`. Ese archivo está congelado: cambiarlo exige un issue propio y actualizar todos los usos en el mismo PR, como se hizo con los contratos v2 (#42).
- **Estado del caso.** Cada nodo recibe el estado y devuelve solo las claves que cambia.
- **Trazabilidad.** Cada decisión de un agente queda como un `Decision` (agente, resumen de entrada y de salida, razón y hora). Los agentes devuelven sus `Decision` y los nodos del grafo las suman al estado con `agregar_decision()`.

---

## 2. Funcionamiento

### Caso de la demo: Yaritza

**Yaritza** es un perfil sintético (`tests/fixtures/caso_yaritza.json`): 20 años, vive en La Chorrera (el caso usa «Barrio Colón», un alias que la tabla de alias resuelve a La Chorrera), estudia de 18:00 a 22:00 de lunes a viernes y atiende la fonda de su mamá por las mañanas. No tiene CV y pide un salario mínimo de 400 con un pasaje diario de hasta 3.00.

**Su recorrido**, nodo por nodo:

1. **Guardrails** (#9, #56, #128). Es el primer nodo y no usa LLM. Hace cuatro cosas:
   - **Rechaza con un mensaje claro:**
     - archivos que no son PDF, JPG o PNG, comprobando también la firma de los bytes;
     - archivos de más de 5 MB;
     - textos vacíos o de más de 10 000 caracteres;
     - edades fuera de 18 a 29;
     - notas de voz, con «Todavía no aceptamos notas de voz; escribe tu mensaje o sube un pdf, jpg o png».
   - **Responde «Solo atiendo empleo y formación»** a lo que está claramente fuera de alcance.
   - **Solo marca la inyección de instrucciones**: el texto sigue como dato, no como orden.
   - **Quita cédula, teléfono y dirección exacta** antes de que la lea cualquier agente (`limpiar_entrada`). Deja el corregimiento.

2. **Router.** Elige el camino según `tipo_entrada`:
   - una oferta va a Mercado;
   - un perfil va a Perfil;
   - un evento de la empresa va al reacomodo;
   - lo demás recibe la respuesta fija de consulta y termina.

3. **Mercado** (#5, #43). Convierte una oferta desordenada en una `Oferta` validada.
   - **El LLM solo extrae campos** a una `OfertaExtraida` (`extractor_claude`, vía `puente.llm`).
   - **El código decide todo lo demás:**
     - **Bloquean la oferta** el límite de edad (Ley 11 de 2005, art. 2) y la exigencia de sexo (art. 1). Una oferta bloqueada no entra al Matching.
     - **Solo alertan**, con la frase y la regla que las disparó, para que el orientador decida:
       - las demás causales del art. 1: raza, nacimiento, discapacidad, clase social, religión e ideas políticas;
       - el puesto o el estado civil escrito solo en femenino, como «cajera» o «soltera»;
       - «buena presencia», foto y estado civil.
     - **El salario** se acepta solo si la cifra aparece en el texto. Si no aparece, queda vacío, la oferta se marca «por confirmar» y nunca se inventa. Con el horario pasa lo mismo.
     - **Dos ofertas se unen como duplicadas** solo si cumplen todo esto:
       - el mismo puesto;
       - textos al menos 85 % parecidos;
       - ninguna empresa conocida distinta;
       - las dos bloqueadas o las dos no bloqueadas;
       - el mismo corregimiento. Si a una le falta, se exige 95 % de parecido.

       Gana la que llegó primero.
   - **Las habilidades** que no están en la taxonomía se descartan y quedan anotadas.

4. **Perfil** (#6, #44). Tiene cuatro partes:
   - **Formulario fijo validado en código:** nombre, género opcional, edad, corregimiento, horario de clases, horario libre, salario mínimo y presupuesto diario de pasaje.
   - **Entrevista de 5 preguntas** para quien no tiene CV, solo sobre lo que sabe hacer: el negocio de la familia, otros trabajos, voluntariado, qué sabe usar y en qué le piden ayuda.
   - **Limpieza antes del LLM:** se quitan cédula, teléfono y dirección y se conserva el corregimiento.
   - **Habilidades con evidencia:** el LLM devuelve un `PerfilExtraido`. Cada habilidad necesita un `skill_id` de la taxonomía y una frase **literal** del joven que la pruebe. Lo que no cumple se descarta con su motivo.

5. **Matching** (#4, #48). OR-Tools CP-SAT asigna sin LLM.
   - **Restricciones duras** por pareja de joven y vacante:
     - traslado de 60 minutos o menos;
     - pasaje mensual de ida y vuelta, por 22 días laborables, de hasta el 15 % del salario;
     - pasaje diario dentro del presupuesto del joven;
     - horario sin cruce con las clases y dentro del horario libre;
     - salario igual o mayor al mínimo que pide el joven;
     - al menos una habilidad en común, si la oferta pide habilidades;
     - hasta 3 candidatos por plaza (una vacante con 2 plazas admite 6) y hasta 2 vacantes por joven.
   - **Si falta un dato** de la oferta, como el salario, el horario o las habilidades, no se descarta por ese dato. El par baja 10 puntos por cada dato que falta y queda «por confirmar». La excepción es el traslado: un par sin fila en la matriz de traslados no es viable.
   - **El puntaje** es la cobertura de habilidades. Los minutos de traslado no restan puntaje: filtran por el máximo y desempatan entre las vacantes de un mismo joven.
   - **El orden de la lista corta** de cada vacante es por puntaje, de mayor a menor, y a igual puntaje por id del perfil. Los minutos de traslado no cuentan: así ve primero a los más aptos el orientador y así se presentan a la empresa (#238, #269). En la vista de la empresa, en cambio, «Candidato N» se numera por la hora de entrevista propuesta, no por puntaje.
   - **La explicación** de cada match es una plantilla determinística, sin LLM.
   - **Ejemplo con Yaritza**, desde La Chorrera (`data/traslados.csv`):
     - Vista Alegre: 20 minutos y 0.75 por viaje, o sea 1.50 al día ida y vuelta: entra.
     - Calidonia: 55 minutos, dentro del tope de 60, pero 1.85 por viaje son 3.70 al día ida y vuelta, más que sus 3.00: queda fuera.
     - Tocumen: 95 minutos: queda fuera.

6. **Auditor** (#8, #50). Repite la evaluación con variantes contrafactuales:
   - 7 pares de nombre y género, con la concordancia del texto ajustada;
   - otro corregimiento que **conserva la misma fila de traslados**. Así se mide si el resultado cambia solo por el nombre del lugar, sin tocar el traslado real, que sí es una restricción legítima.

   El Auditor admite una línea base de 3 corridas que descuenta la variación propia del LLM de Perfil. **En el grafo esa línea base no descuenta nada:** `nodo_auditor_grafo` no le pasa un `fn_perfil`, así que el perfil se reprocesa con la identidad y las corridas dan siempre lo mismo. **Si una variante cambia el resultado, el grafo recalcula una sola vez. Si la diferencia persiste, no hay bucle:** pasa al orientador como alerta, con el texto «Tope de 1 recálculo alcanzado».

7. **Formación** (#14, #53). Para las brechas de habilidades, propone cursos de `data/cursos.csv`: 9 cursos armados sobre 5 títulos reales del INADEH, con horarios y cupos de prueba (`data/FUENTES.md` los marca como dato mixto). Cada curso tiene que cumplir:
   - tener cupo;
   - traslado de 60 minutos o menos;
   - pasaje dentro del presupuesto;
   - horario sin cruce con las clases.

   El algoritmo es una cobertura voraz de conjuntos: elige primero el curso que cierra más brechas y abre más vacantes.

8. **Aprobación del orientador** (`interrupt()`, #10, #11). El grafo se detiene en cada vacante con su lista corta.
   - **El orientador ve:**
     - la oferta, con sus alertas y lo que falta confirmar;
     - cada candidato, con su puntaje, sus brechas, la explicación y las rutas de formación;
     - las alertas del Auditor y el mensaje sugerido para la empresa.
   - **Tiene cuatro acciones:**
     - **Aprobar.**
     - **Editar:** cambia el mensaje a la empresa y su nota, sin recalcular.
     - **Quitar** a un candidato: el grafo vuelve a Matching y recalcula sin él.
     - **Rechazar** la vacante.
   - **Consentimiento:** solo se postula a quien tiene el consentimiento confirmado. Si el joven dijo «no», eso pesa más que la confirmación del orientador.

9. **Postulación** (#7, #12, #84). Solo corre después de la aprobación:
   - guarda perfil, oferta y postulación en SQLite y la avanza por sus estados, cada paso con su `Decision` en la tabla `decisiones`;
   - genera el CV ciego en PDF, solo con las habilidades que tienen evidencia;
   - genera la carta de presentación (#212, `puente/tools/carta.py`) por plantilla y sin LLM: saludo, puesto, hasta 3 habilidades que la oferta pide con su frase literal (si son menos de 2, completa con otras que tengan evidencia) y la disponibilidad. Solo dice que el horario del puesto «cabe» si es fijo y no cruza la medianoche. La evidencia y el nombre de cada habilidad pasan por `cegar()`; el texto de la oferta no, porque tapar ahí el corregimiento del joven se lo revelaría a la empresa (#226). Todo pasa por `limpiar_pii()` y el nombre va tal como lo da la persona (#123);
   - elige una franja de entrevista sin choques y crea el `.ics` de 45 minutos;
   - envía la lista corta a la empresa por Telegram o, sin token, la escribe en `outbox/`.

10. **Reacomodo** (#54). La respuesta de la empresa entra como `evento_empresa`:
    - **Si rechaza a un candidato:** su postulación pasa a `no_seleccionado`, la pareja queda excluida, la vacante vuelve a estar pendiente y el Matching recalcula. El orientador aprueba la lista nueva en la misma pausa de siempre.
    - **Si la vacante se cubre:** sale del Matching.

    Los avisos al joven salen por Telegram solo en la terminal con `--telegram`; si no, y siempre en el tablero, se escriben en el outbox (ver [Estado persistente](#estado-persistente)). El joven los ve en «Mis postulaciones» de `/joven`. El reacomodo funciona en el grafo, en la terminal (con `--auto` o escribiendo la respuesta de la empresa) y en la web: en `/empresa/lista-corta`, rechazar o dar la vacante por cubierta llama a `evento_empresa` y recalcula (#167). El tablero del orientador no tiene un botón propio para esto.

**Estados de una postulación:** `perfil_listo`, `preseleccionado`, `aprobado`, `consentido`, `enviado`, `entrevista`, `contratado` y `no_seleccionado`.

### Trazabilidad

- **Decisiones.** Cada nodo agrega un `Decision` al estado y el checkpointer las guarda con el caso. Al llegar a Postulación se copian a la tabla `decisiones` de SQLite. Al final, la demo por terminal imprime la cadena de agentes que decidieron.
- **LangSmith.** Se activa solo con `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY`. La demo por terminal pone `LANGSMITH_HIDE_INPUTS` y `LANGSMITH_HIDE_OUTPUTS` en `true` para no enviar datos personales.

### Datos

Toda persona en los datos es sintética. `data/FUENTES.md` documenta la fuente de cada archivo y separa tres tipos de dato:

- **Dato medido:**
  - **Traslados** (`data/traslados.csv`): 13 nodos (10 corregimientos y 3 distritos: La Chorrera, Arraiján y San Miguelito) y 91 filas, medidos a las 07:00 en Google Maps Transit y Moovit con las tarifas de MiBus, el Metro y la ATTT.
  - **Ofertas** (`data/ofertas/`): 20 anuncios reales de Computrabajo (14) y Konzerta (6), con empresas renombradas como empresas de prueba.
- **Dato de prueba:**
  - los 10 perfiles de `data/perfiles/`;
  - los pares de duplicados;
  - los cupos y horarios de los cursos.
- **Supuesto del equipo:**
  - los umbrales de 60 minutos y 15 %;
  - los 22 días laborables;
  - la simetría de ida y vuelta.

La taxonomía (`data/taxonomia.csv`) tiene 51 habilidades:
- 47 están enlazadas a una URI de ESCO verificada.
- 4 son identificadores propios sin URI (`cocina`, `comunicacion_efectiva`, `excel` y `ventas`), declarados como supuesto del equipo (#76).

---

## 3. Despliegue

Todo corre en una sola máquina, sin Docker ni servidor externo.

**Requisitos.** [uv](https://docs.astral.sh/uv/) y acceso a internet para instalar dependencias. `uv` descarga Python 3.12 si hace falta.

```bash
uv sync --locked              # exactamente las dependencias fijadas en uv.lock
cp .env.example .env          # llenar solo lo que se tenga; .env nunca se sube
uv run pytest -q              # pruebas sin red ni claves
```

**Demo del grafo con el caso Yaritza, por terminal:**

```bash
uv run python -m puente.graph
```

- **`--llm`**:
  - `grabado`, el valor por defecto, usa las respuestas guardadas en el caso, así que no necesita red ni clave.
  - `claude` llama a Claude a través de `puente.llm`.
- **`--auto`** decide sin preguntar, por ejemplo `--auto "aprobar,aprobar,empresa_rechaza:yaritza@of-cajero-vista-alegre,aprobar"`. Sin esta opción, la demo pregunta en cada vacante.
- **`--telegram`**: en la demo por terminal es la única forma de que algo salga por Telegram. Sin esta opción, todo se escribe en `outbox/`, aunque `.env` traiga token.
  - **El tablero no tiene esa opción y tiene dos caminos de salida:**
    - **Postulación.** La lista corta a la empresa (CV ciego, carta, `.ics` y mensaje) la arma `_nodo_postulacion_tablero` en `app/server.py`. Llama a `postular()` (`puente/agents/postulacion.py`) con la carpeta de `PUENTE_OUTBOX` (o `outbox/` de la raíz si no está definida, #207), y `postular()` la manda con `enviar_lista_corta()` (`puente/tools/telegram.py`). Sale por Telegram si el entorno del proceso trae `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID_EMPRESA`; si no, queda en esa carpeta.
    - **Avisos del reacomodo.** Los avisos al joven los escribe el nodo `avisos` del grafo, que el tablero no reemplaza: `crear_nodo_avisos()` de `nodos_fase_2()` (`puente/graph.py`), con el transporte `sin_telegram`. Nunca salen por Telegram y van a `PUENTE_OUTBOX` o, si falta, a `outbox/` del directorio desde donde se arrancó el servidor (`outbox_por_entorno()`).

**Otras piezas:**

```bash
uv run python -m puente.cargar                 # carga data/ en puente.db (#46)
uv run python -m puente.metricas --tendencias  # tendencias del mercado sobre puente.db (#198)
uv run python -m puente.agents.perfil --entrevista   # entrevista de Perfil por terminal
uv run python -m app.server                    # tablero en http://127.0.0.1:8000 (vista del joven en /joven)
uv run python -m evals.run                     # evals (escribe evals/resultados.md)
```

La vista de la empresa (`/empresa/lista-corta`) se abre con un código por empresa. El servidor imprime cada código en la consola al arrancar y en cada `/api/reset`, con el enlace `/empresa/lista-corta#codigo=<empresa>.<token>`. Lo mismo pasa con los códigos de `/joven`, y una empresa nueva recibe el suyo cuando publica su primera vacante.

### Modo sin red y sin claves

- **Grabaciones.** La capa común de LLM (`puente/llm.py`) tiene tres modos, que se eligen con `LLM_MODO`:
  - `vivo` llama al modelo;
  - `grabar` llama y guarda la respuesta en `data/grabaciones/`;
  - `reproducir` solo lee las grabaciones, así que no usa red ni clave.

  La clave de cada grabación es un hash (sha256) de proveedor, modelo, sistema, prompt, esquema y bytes de los archivos adjuntos. Cambiar cualquiera de ellos invalida la grabación.
  - **Carga sin red:** `LLM_MODO=reproducir uv run python -m puente.cargar` carga las 20 ofertas grabadas (7 bloqueadas) y los 10 perfiles (`data/grabaciones/README.md`).
  - **Evals y CI:** los evals reproducen sus grabaciones desde `evals/grabaciones/`, y el CI corre con `LLM_MODO=reproducir`.
- **Sin clave en modo vivo,** el error es claro: `ErrorLLM` con «Falta ANTHROPIC_API_KEY… use LLM_MODO=reproducir». No se cae el proceso.
- **Sin `TELEGRAM_BOT_TOKEN`,** los mensajes a la empresa y al joven se escriben en `outbox/` y el flujo no falla.
- **Tope de gasto.** `PUENTE_LLM_TOPE_USD` fija un gasto estimado máximo por proceso, que se comprueba antes de cada llamada real.
- **Las pruebas** fijan `LLM_MODO=reproducir`, no leen `.env`, borran las variables de Telegram y escriben la base y `outbox/` en carpetas temporales (`tests/conftest.py`).

### Estado persistente

| Archivo | Terminal y carga | Tablero | Variable | Contenido |
|---|---|---|---|---|
| Base de Puente | `puente.db` | `%TEMP%/puente_tablero.db` | `PUENTE_DB` | Perfiles, ofertas, postulaciones con su historial, `entrevistas`, `decisiones`, `descartes`, y las tablas de la carga `cargas` y `cargas_ofertas`; la del tablero guarda además su estado en `tablero_estado` (#122) |
| Checkpoints del grafo | `checkpoints.db` | `<base>_checkpoints.db`, junto a la base | `PUENTE_CHECKPOINTS` | Cada caso por `thread_id`, para retomarlo después del `interrupt()` |
| Salida de mensajes | `outbox/` | `PUENTE_OUTBOX`; sin ella, lista corta en `outbox/` de la raíz y avisos del reacomodo en `outbox/` del directorio de trabajo | `PUENTE_OUTBOX` (terminal y tablero, #207) | CV ciego, carta, `.ics` y mensajes a la empresa y al joven cuando no salen por Telegram |

Desde el #207, el tablero respeta `PUENTE_OUTBOX` en la lista corta, el `.ics` que confirma la empresa y la cobertura por turnos, igual que los avisos del reacomodo. Para repetir la demo desde cero se usa otra `PUENTE_DB` o se borran esos archivos.

---

## 4. Decisiones de diseño

El registro completo está en [`decisiones.md`](decisiones.md). El horario y las decisiones del 16 de octubre están en el issue #41. Qué se hizo con IA, con qué herramienta y qué se cambió a mano está en la [bitácora de IA](bitacora_ia.md). Las decisiones que dan forma al producto son estas:

1. **LLM en los bordes, código en el centro.**
   - **Por qué:** un modelo de lenguaje no da la misma respuesta dos veces, ni explica de forma verificable por qué descartó a alguien. La legalidad, el traslado, el pasaje, el horario, la asignación y la auditoría tienen que dar la misma salida con la misma entrada.
   - **Cómo se aplica:** en el flujo principal el LLM solo produce `OfertaExtraida` y `PerfilExtraido`, salvo la explicación opcional de las rutas de Formación (`usar_llm_formacion`, apagada por defecto); todo lo demás es código. Hasta la explicación del match es una plantilla determinística (#48). Las extensiones de fase 1 tienen sus propios esquemas de salida: Horario, Redactor y Micro-misión.
2. **El traslado filtra y desempata, pero no pone a competir a unos jóvenes contra otros.**
   - **Por qué:** vivir lejos no debe dejar a nadie detrás de otro joven igual de apto para la misma vacante.
   - **Cómo se aplica:** los minutos descartan lo que el joven no puede alcanzar y desempatan entre las opciones de ese mismo joven. No restan puntaje: el peso por minuto es 0. El Auditor comprueba que cambiar solo el corregimiento, con el mismo traslado, no cambie el resultado.
3. **El humano decide al final y el sistema no entra en bucles.**
   - **Por qué:** nada sale hacia una empresa sin que una persona lo apruebe, y un sistema que recalcula sin fin no es confiable.
   - **Cómo se aplica:**
     - el grafo se detiene con `interrupt()` en cada vacante;
     - el Auditor recalcula una sola vez y, si el sesgo persiste, se lo muestra al orientador;
     - «editar» cambia el mensaje y la nota, no el cálculo; «quitar» sí recalcula.
4. **Consentimiento del joven y CV ciego.**
   - **Por qué:** el joven decide si su CV llega a una empresa, y la empresa no debe recibir datos que permitan discriminar antes de la entrevista.
   - **Cómo se aplica:**
     - Solo se postula a quien consintió, y el «no» del joven pesa más que la confirmación del orientador (#54).
     - El CV (`puente/tools/pdf.py`) lleva el nombre, el puesto, las habilidades con su evidencia y la disponibilidad.
     - Cada línea que sale hacia la empresa, del CV y del mensaje, pasa por `cegar()` (#148, #163). Esa función cambia por «[dato omitido]»:
       - el corregimiento del perfil;
       - la edad exacta en cifras («20 años», «edad 20») y la edad en palabras de 18 a 29;
       - el año de nacimiento y las fechas que corresponden a su edad;
       - el estado civil («soltera», «está unida», «separada»);
       - el embarazo;
       - la pareja y los hijos del joven;
       - los términos de género;
       - en el texto del orientador, además, palabras como «chica» o «muchacha».
     - Después pasa `limpiar_pii()`, que quita cédula, teléfono y dirección.
     - **El nombre es decisión de Jesus:** va tal como lo da la persona en su perfil, y Puente no lo agrega ni lo quita. En la demo todos los nombres son ficticios.
5. **Datos de prueba rotulados.**
   - **Por qué:** ninguna cifra de prueba puede presentarse como medición real.
   - **Cómo se aplica:** las personas son sintéticas y las empresas llevan nombres de prueba. Los cupos y horarios de los cursos son datos de prueba. Los traslados están medidos, con fuente y fecha. Los umbrales se declaran como supuestos del equipo (`data/FUENTES.md`). Las métricas del tablero dicen «Ilustrativo, datos de la demo» (`puente/metricas.py`).
6. **Robustez antes que adorno.**
   - **Por qué:** en una demo en vivo, un dato inventado o una caída cuestan más que una función menos.
   - **Cómo se aplica:**
     - un salario que no aparece en la oferta queda vacío y marcado;
     - un par sin dato de traslado no es viable;
     - Telegram sin token escribe en `outbox/`;
     - el modo reproducir permite correr sin red;
     - la inyección se marca, pero la defensa real es la salida estructurada y que decida el código.
7. **Menores de 18 años fuera de la demo, como trabajo futuro.**
   - **Por qué:** contratar a menores de edad tiene reglas propias que el sistema todavía no modela.
   - **Cómo se aplica:** el guardrail rechaza edades fuera de 18 a 29.
8. **Un solo modelo detrás de una sola puerta.**
   - **Por qué:** cambiar de proveedor o de modelo no debe tocar a los agentes, y la demo necesita poder correr sin red.
   - **Cómo se aplica:** el modelo es Claude Opus 5.5 (`claude-opus-5-5`) con el crédito del equipo, detrás de `puente/llm.py` (#45, #41), con Gemini como respaldo opcional (`LLM_PROVEEDOR=gemini`). Ningún agente crea su propio cliente.
9. **Tablero en Starlette y Uvicorn, no en Streamlit.**
   - **Por qué:** Streamlit vuelve a ejecutar todo el programa en cada clic, y eso complica controlar la pausa del orientador (`interrupt()`) y probar el tablero de punta a punta por HTTP.
   - **Cómo se aplica:** el tablero es un servidor web (Starlette y Uvicorn) con páginas en HTML, CSS y JavaScript sin framework (#11, #33). `app/streamlit_app.py` quedó vacío, con solo su docstring, y la dependencia `streamlit` salió de `pyproject.toml` para que el despliegue de Vercel quepa en 500 MB (#252).

---

## 5. Límites

- **Entrevista simulada: fuera de alcance.** Puente no ensaya entrevistas de trabajo con el joven. La entrevista de Perfil solo sirve para conocer sus habilidades.
- **Tendencias del mercado: solo por terminal y sin series en el tiempo.** `uv run python -m puente.metricas --tendencias` (#198) resume las ofertas que dejó `puente.cargar` en `puente.db`: habilidades más pedidas, mediana del salario publicado por corregimiento (las ofertas sin cifra se cuentan aparte) y ofertas bloqueadas por la Ley 11 con su regla. Es código sin LLM y cuenta también las bloqueadas. El tablero no lo muestra, y con 20 ofertas de un solo día no hay evolución que medir.
- **El determinismo vale a la escala de la demo.** El solver usa 1 hilo, semilla fija (42) y orden por id, así que con la misma entrada da el mismo resultado. Pero la búsqueda se corta a los 1.8 segundos (`max_time_in_seconds`). Con los datos de la demo llega al resultado antes de ese corte. Con muchos más jóvenes y vacantes, el corte podría dejar una asignación distinta según la máquina.
- **El grafo del tablero usa stubs de Mercado y Perfil.** Lo construye con `stub_mercado` y `stub_perfil` (`app/server.py`, en la función que arma el grafo del tablero), así que lo que ya está en el tablero no se vuelve a extraer. Las ofertas y perfiles nuevos entran de tres formas:
  - **«Cargar desde data/»** (#205) lee el archivo elegido. Un anuncio de `data/ofertas/` pasa por los guardrails y por el agente Mercado real (`puente.llm`; en modo reproducir, con las grabaciones de las 20 ofertas), y la Ley 11 se aplica sobre el texto del anuncio. El mismo archivo no se carga dos veces. Un perfil de `data/perfiles/` entra tal como está en el archivo, validado como `Perfil` y con guardrails sobre sus evidencias.
  - **Los ejemplos escritos en el código** salen rotulados «ejemplo sintético (no es un archivo)» y no pasan por el LLM.
  - **El formulario** tampoco usa el LLM, pero pasa por las reglas de la Ley 11 y por los guardrails.

  El Matching, el Auditor, Formación y Postulación del tablero sí son reales. La vista `/joven` usa el agente Perfil real.
- **La demo por terminal no pasa por Mercado.** El caso Yaritza entra como perfil, con las ofertas ya cargadas, y por defecto usa respuestas grabadas (`--llm grabado`). La extracción real de ofertas se ve con `puente.cargar`.
- **El Auditor dentro del grafo** recalcula el Matching con las variantes, pero no vuelve a correr el LLM de Perfil. Por eso mide que el solver no dependa del nombre, el género o el corregimiento. Hoy nada mide si el nombre cambia lo que extrae el LLM de Perfil: los evals de sesgo tampoco usan LLM.
- **Dos extensiones sin conectar.** El redactor de una versión legal de la oferta (#157) y la micro-misión con *scorecard* ciego (#158) existen como módulos con su CLI y sus evals, pero no están en el grafo ni en el tablero. Horario (#164) sí está conectado: `/joven` lee la tirilla, la confirma y recalcula.
- **La foto de la tirilla va completa a Claude**, con lo que traiga: nombre, cédula o número de carnet. `limpiar_entrada` solo limpia lo que vuelve del modelo, y la imagen no se guarda.
- **`cegar()` es heurístico:** son expresiones regulares, y un dato escrito de una forma que no prevén puede pasar.
- **Los evals y la CI corren con grabaciones**, no con el LLM en vivo. En modo reproducir cada caso con LLM usa una sola grabación, así que no se mide la variación entre corridas. La corrida de la entrega queda en [`evals/resultados.md`](../evals/resultados.md), que se regenera sobre el commit final antes del tag (#17); las metas por categoría son propuestas del equipo.
- **Publicar una vacante no pide código.** En la demo, `/empresa` y `POST /api/empresa/ofertas` no piden código y el nombre de empresa es libre, así que cualquiera puede publicar a nombre de cualquiera. Por eso esa ruta pasa por la misma Ley 11 y responde solo con el resultado de su oferta, nunca con datos de los jóvenes. En Vercel todo POST da 403.
- **Notas de voz: fuera de alcance.** Se rechazan con un mensaje claro; #55 se cerró sin implementar.
- **Cobertura geográfica:** la matriz de traslados tiene 13 nodos (10 corregimientos y 3 distritos) del corredor Panamá Oeste, Centro, San Miguelito y Este. Diez de las 20 ofertas no indican un lugar de esa matriz, así que el Matching no las puede asignar (`data/FUENTES.md`).
