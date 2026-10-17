# Grabaciones del LLM para los evals

Respuestas del modelo para los casos con LLM de `evals/casos.yaml`: extracción de ofertas (EXT) y perfil sin
CV (PER). Con ellas, `uv run python -m evals.run` mide esas categorías sin red ni claves. El formato y los
modos son los de `puente/llm.py` (#45); ver `data/grabaciones/README.md`.

Viven aquí y no en `data/grabaciones` para que los evals no dependan de los datos de la demo.

## Cómo se usan

`uv run python -m evals.run` reproduce las grabaciones de `evals/grabaciones` sin llamar al modelo,
aunque el `.env` tenga `LLM_MODO=vivo` o `grabar`. Cada grabación cuenta como una muestra; repetir
el mismo hash no crea observaciones independientes.

Si falta la grabación de un caso, ese caso sale **omitido (sin grabación)** en `evals/resultados.md`.
No sale como fallo: no se midió.

## Cómo grabarlas (requiere clave, lo hace una persona)

```bash
uv run python -m evals.run --grabar --repeticiones 1
```

Con `ANTHROPIC_API_KEY` en `.env` (o `GOOGLE_API_KEY` con `LLM_PROVEEDOR=gemini`). Se puede
configurar `PUENTE_LLM_TOPE_USD=2` para acotar el gasto estimado del proceso. Después:

1. Revisar que los JSON nuevos solo tengan datos sintéticos.
2. Commitear los `<hash>.json` junto con el `evals/resultados.md` regenerado.
3. Volver a correr sin variables para comprobar que todo se reproduce.

La clave de cada grabación es un hash del proveedor, modelo, prompt y esquema. Si cambia el prompt
o la taxonomía de Perfil, la grabación deja de servir y el caso vuelve a quedar omitido hasta grabarlo
de nuevo. Los casos usan `data/taxonomia.csv`, el mismo catálogo de la demo.
