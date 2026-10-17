# Grabaciones del LLM para los evals

Respuestas del modelo para los casos con LLM de `evals/casos.yaml`: extracción de ofertas (EXT) y perfil sin
CV (PER). Con ellas, `uv run python -m evals.run` mide esas categorías sin red ni claves. El formato y los
modos son los de `puente/llm.py` (#45); ver `data/grabaciones/README.md`.

Viven aquí y no en `data/grabaciones` para que los evals no dependan de los datos de la demo.

## Cómo se usan

`python -m evals.run` usa por defecto `LLM_MODO=reproducir` y `LLM_DIR_GRABACIONES=evals/grabaciones`.
Si el entorno o el `.env` ya definen esas variables, se respetan.

Si falta la grabación de un caso, ese caso sale **omitido (sin grabación)** en `evals/resultados.md`.
No sale como fallo: no se midió.

## Cómo grabarlas (requiere clave, lo hace una persona)

```bash
LLM_MODO=grabar uv run python -m evals.run
```

Con `ANTHROPIC_API_KEY` en `.env` (o `GOOGLE_API_KEY` con `LLM_PROVEEDOR=gemini`). Después:

1. Revisar que los JSON nuevos solo tengan datos sintéticos.
2. Commitear los `<hash>.json` junto con el `evals/resultados.md` regenerado.
3. Volver a correr sin variables para comprobar que todo se reproduce.

La clave de cada grabación es un hash del prompt. Si cambia el prompt de un agente, su grabación deja de
servir y el caso vuelve a quedar omitido hasta grabarlo de nuevo. Pasa, por ejemplo, con PER-04 cuando se
arregle #63.
