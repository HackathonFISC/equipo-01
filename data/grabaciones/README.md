# Grabaciones del LLM (modo grabar/reproducir)

Esta carpeta guarda las respuestas del modelo para que el CI, los evals, el video y la demo en CONTECS
corran sin red y sin claves. La capa que las lee y escribe es `puente/llm.py` (issue #45).

**Solo datos sintéticos.** Todo lo que se graba aquí entra a git: usar perfiles, ofertas y entrevistas
inventados y rotulados (por ejemplo "Yaritza (sintético)"), y prompts que ya pasaron por la limpieza de
cédula, teléfono y dirección. Nunca claves: la API key la lee el SDK del entorno y no se guarda.

## Modos

`LLM_MODO` en `.env`. `.env` se carga una sola vez por proceso: después de editarlo, reinicie el tablero o la CLI.

| Valor | Qué hace |
|---|---|
| `vivo` (por defecto) | Llama al modelo, no guarda nada. |
| `grabar` | Llama al modelo y escribe `<hash>.json` en esta carpeta. |
| `reproducir` | Solo lee `<hash>.json`. No usa red ni claves. Si falta, `ErrorLLM` con el hash y la ruta esperada (relativa al repo). |

`LLM_DIR_GRABACIONES` cambia la carpeta (por defecto `data/grabaciones`, relativa a la raíz del repo).

## Formato

Un archivo por llamada, `<hash>.json`:

```json
{
  "hash": "9f2c...",
  "proveedor": "anthropic",
  "modelo": "claude-opus-5-5",
  "tipo": "estructurado",
  "agente": "perfil",
  "esquema": "PerfilExtraido",
  "salida": {"...": "model_dump(mode='json') del esquema, o un texto si tipo es texto"},
  "uso": {"entrada": 1234, "salida": 210},
  "creado": "2026-10-16T22:00:00+00:00"
}
```

Si la respuesta vino del proveedor de respaldo tras un rechazo, se guarda bajo la clave de lo pedido y se agrega
`"atendido_por": {"proveedor": ..., "modelo": ...}`.

## Cómo se calcula el hash

`sha256` en hexadecimal de este JSON (`sort_keys=True`, `ensure_ascii=False`, separadores compactos):

```
{"proveedor", "modelo", "sistema", "prompt", "esquema", "archivos"}
```

- `sistema` y `prompt` con fin de línea normalizado a `\n` (igual en Windows y Linux).
- `esquema`: `esquema.model_json_schema()` o `null` en llamadas de texto.
- `archivos`: el `sha256` de los bytes de cada archivo adjunto, en orden.

Cambiar el modelo, el proveedor, una letra del prompt, el esquema o un byte de un archivo da otro hash: hay que
volver a grabar. Para calcularlo a mano: `puente.llm.clave_grabacion(...)`.

**Guardia de CI para Yaritza (#132):** antes de fusionar cambios en `data/taxonomia.csv`,
los prompts de Perfil o Mercado, o `tests/fixtures/caso_yaritza.json`, vuelve a grabar las
respuestas afectadas con `LLM_MODO=grabar` y ejecuta
`uv run pytest -q tests/test_grabacion_yaritza.py`. La prueba calcula el hash del prompt
real de Perfil y falla con «falta la grabación …» cuando la demo ya no puede reproducirse.

## Tirilla de matrícula sintética (#164)

`6f0c1977…a309101.json` es la lectura de `tests/fixtures/tirilla_sintetica.png` por Claude Opus 5.5 con visión
(agente `horario`, esquema `TirillaExtraida`). Hay una copia idéntica en `evals/grabaciones/` para el caso HOR-01.
El hash depende de los bytes del PNG, de `INSTRUCCIONES`, `PROMPT` y `TirillaExtraida` en `puente/agents/horario.py`:
si cambia cualquiera de ellos, `tests/test_horario.py` falla con «falta la grabación» y hay que volver a grabar:

    LLM_MODO=grabar uv run python -m puente.agents.horario tests/fixtures/tirilla_sintetica.png --hoy 2026-10-17

y copiar el `<hash>.json` nuevo a `evals/grabaciones/`. La grabación del 17 de octubre de 2026 costó US$0.026 (3,937 tokens de
entrada y 535 de salida).

## Video (#194)

Tres llamadas que el video muestra y que no estaban grabadas: el chat de Yaritza en `/joven` (agente Perfil), el
redactor de OF-BLOQ-EDAD-04 y la micro-misión CAJA-01. El guion sintético está en `data/demo/yaritza_chat.json`: el
formulario, 5 respuestas (una por pregunta, en el orden de `PREGUNTAS_ENTREVISTA`) y el texto de la micro-misión.
`tests/fixtures/caso_yaritza.json` no cambia: la demo por terminal y su grabación dependen de él.

Para grabarlas hace falta `ANTHROPIC_API_KEY` en `.env`; cuesta centavos y el script pone un tope de US$1:

```bash
scripts/grabar_video.sh
```

Graba con `LLM_MODO=grabar`, las repite con `LLM_MODO=reproducir` por el camino real (la vista por HTTP y las CLI),
muestra los archivos nuevos y el costo y anota los hashes en `data/demo/grabaciones_video.json`. No hace commit ni
push: al final imprime los comandos para copiar. Si una grabación ya existe y da lo esperado, no la repite
(`scripts/grabar_video.sh --forzar` la rehace). Sin clave falla de inmediato, sin llamar al modelo.

En el video, sin cambiar `LLM_PROVEEDOR`, `PUENTE_MODELO_LLM` ni `LLM_DIR_GRABACIONES` en `.env`:

```bash
LLM_MODO=reproducir uv run uvicorn app.server:app --port 8000
LLM_MODO=reproducir uv run python -m puente.agents.redactor --oferta OF-BLOQ-EDAD-04
LLM_MODO=reproducir uv run python -m puente.agents.micromision --reto CAJA-01 --texto "Primero sumo el almuerzo con la soda, eso se lo resto a los 10 del billete y le doy 5.45 de vuelto."
```

En `/joven` va el formulario del guion y cada respuesta pegada tal cual, en el chat o en el formulario de la fase 1: las
dos vistas arman el mismo prompt. Una letra distinta cambia el hash y la vista responde que no hay lectura grabada. Los
botones de ejemplo de Yaritza (`EJEMPLO_YARITZA` en `joven.js`, `EJEMPLO_CHAT_YARITZA` en `joven_fase2.js`) traen las
respuestas y el formulario del guion, sin horario libre; `tests/test_vista_joven_reproducir.py` revisa que sean
idénticas y que den el hash de `llamadas.chat`.

`tests/test_grabar_video.py` comprueba con un LLM espía que el script pide el mismo sistema, prompt, esquema y hash
que la vista (chat y formulario) y las CLI. Cuando las grabaciones existen, las reproduce por el camino real: perfil
listo con `manejo_caja` y `atencion_cliente`, «Propuesta (llm)» del redactor y CAJA-01 «correcto» leído por el LLM.
Mientras no existan, esa prueba se omite. Con `data/demo/grabaciones_video.json` en el repo, un cambio de prompt,
esquema o taxonomía que deje al video sin grabación hace fallar la prueba en lugar de omitirla.

## Ofertas de la carga inicial (#46)

Las 20 ofertas de `data/ofertas/` tienen grabaciones de Claude Opus 5.5 validadas con
`OfertaExtraida`. La API con `output_config.format=json_schema` agotó el tiempo de espera
en esta carga; se obtuvo el JSON con el mismo modelo y las instrucciones de Mercado,
añadiendo el esquema al mensaje de sistema. Cada grabación indica
`atendido_por.modo=json_libre_por_timeout_esquema` para hacer visible esta diferencia.
Su `hash` corresponde a la petición normal de Mercado, de modo que
`LLM_MODO=reproducir uv run python -m puente.cargar` funciona sin red. Si cambia el
prompt, el esquema o el modelo, hay que volver a validar y grabar las respuestas.

En la primera carga se guardaron 20 ofertas (7 bloqueadas) y 10 perfiles. Una segunda
corrida reutilizó las 20 extracciones y dejó los mismos conteos. Las reglas de
discriminación y la deduplicación se aplican al reproducir mediante `puente.cargar`.

## Cómo regrabar el caso de Yaritza (lo hace una persona, con clave)

**Requisito previo:** los PRs de adopción de `puente/llm.py` en `mercado.py`, `perfil.py` y `formacion.py` (#43, #44 y
formación) deben estar en `main`. Mientras esos agentes sigan instanciando su propio cliente, `puente.graph` no pasa por
`llm.py` y no graba nada.

Requiere `ANTHROPIC_API_KEY` en tu `.env` (la entrega @Cornelio-Guerra por privado) y que #44 (Perfil v2),
#10 (grafo) y `data/perfiles/yaritza.json` ya estén en `main`.

1. En `.env`: `LLM_MODO=grabar` (con `LLM_PROVEEDOR=anthropic` y `PUENTE_MODELO_LLM=claude-opus-5-5`).
2. Correr el caso completo: `uv run python -m puente.graph` con la entrada de Yaritza.
3. Revisar que `git status` muestre solo archivos nuevos en `data/grabaciones/` y que no contengan datos reales.
4. Verificar: `LLM_MODO=reproducir uv run pytest -q` y repetir el caso en `reproducir`, sin red.
5. Anotar la corrida en la bitácora con `scripts/bitacora.sh` y subir las grabaciones en un PR.

El contador de llamadas, tokens y costo estimado por agente está en `puente.llm.uso_por_agente()`.
