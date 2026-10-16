# Grabaciones del LLM (modo grabar/reproducir)

Esta carpeta guarda las respuestas del modelo para que el CI, los evals, el video y la demo en CONTECS
corran sin red y sin claves. La capa que las lee y escribe es `puente/llm.py` (issue #45).

**Solo datos sintéticos.** Todo lo que se graba aquí entra a git: usar perfiles, ofertas y entrevistas
inventados y rotulados (por ejemplo "Yaritza (sintético)"), y prompts que ya pasaron por la limpieza de
cédula, teléfono y dirección. Nunca claves: la API key la lee el SDK del entorno y no se guarda.

## Modos

`LLM_MODO` en `.env` (se lee en cada llamada):

| Valor | Qué hace |
|---|---|
| `vivo` (por defecto) | Llama al modelo, no guarda nada. |
| `grabar` | Llama al modelo y escribe `<hash>.json` en esta carpeta. |
| `reproducir` | Solo lee `<hash>.json`. No usa red ni claves. Si falta, `ErrorLLM` con el hash y la ruta esperada. |

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
