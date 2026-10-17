# Resultados de los evals de Puente

Generado por `uv run python -m evals.run` el 2026-10-16 19:27 (hora de Panamá, UTC-5). No lo edites a mano: se regenera en cada corrida.

Estados: 15 ok · 0 fallo · 0 pendiente · 9 omitido (de 24 casos).

**Esta corrida no mide todo el sistema:** 9 casos con LLM omitidos (sin grabación); no se miden hasta grabar sus respuestas con `LLM_MODO=grabar` y la clave del LLM (ver evals/grabaciones/README.md). Una categoría sin medir no es una categoría aprobada.

Los casos con LLM se corren 3 veces y la tasa es aciertos sobre corridas. Las metas son propuestas provisionales hasta que el equipo las confirme con el plan. Todos los datos de los casos son sintéticos.

Taxonomía usada en esta corrida: la de `casos.yaml` (en línea), copia literal de `data/taxonomia.csv` de #3 (rama issue-3-datos-puente, PR #27) con sus mismos sinónimos; no se agregaron sinónimos tomados de los textos de los casos. Cuando se fusione #27, medir con `data/taxonomia.csv`.

LLM de esta corrida: `puente.llm` en modo reproducir, con las grabaciones de `evals/grabaciones`.

## Tabla por categoría

| Categoría | Casos | Ejecutados | Aciertos | Tasa | Métrica | Meta | Cumple |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| extraccion_ofertas | 5 | 0 | — | — | Fracción de corridas en que los campos esperados salen todos correctos (puesto, empresa, corregimiento, salario_mensual, habilidades) | 80 % | sin medir |
| requisitos_discriminatorios | 6 | 6 | 6/6 | 100 % | Bloqueada, regla (art. 1 / art. 2) y alertas iguales a lo esperado | 100 % | sí |
| perfil_sin_cv | 4 | 0 | — | — | Habilidades esperadas encontradas con evidencia literal y 0 habilidades sin evidencia | 75 % | sin medir |
| matching_factible | 3 | 3 | 3/3 | 100 % | Asignación igual a la calculada a mano y 0 violaciones duras | 100 % | sí |
| sesgo_contrafactual | 3 | 3 | 3/3 | 100 % | sesgo_detectado igual a lo esperado | 100 % | sí |
| guardrails | 3 | 3 | 3/3 | 100 % | Veredicto (rechazar \| marcar \| fuera_de_alcance \| aceptar) igual a lo esperado y sin llamar al LLM cuando se rechaza | 100 % | sí |

## Detalle por caso

| Caso | Categoría | Estado | Aciertos | Detalle |
| --- | --- | --- | ---: | --- |
| EXT-01 | extraccion_ofertas | omitido | 0/0 | omitido (sin grabación) |
| EXT-02 | extraccion_ofertas | omitido | 0/0 | omitido (sin grabación) |
| EXT-03 | extraccion_ofertas | omitido | 0/0 | omitido (sin grabación) |
| EXT-04 | extraccion_ofertas | omitido | 0/0 | omitido (sin grabación) |
| EXT-05 | extraccion_ofertas | omitido | 0/0 | omitido (sin grabación) |
| DIS-01 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-02 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-03 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-04 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-05 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-06 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| PER-01 | perfil_sin_cv | omitido | 0/0 | omitido (sin grabación) |
| PER-02 | perfil_sin_cv | omitido | 0/0 | omitido (sin grabación) |
| PER-03 | perfil_sin_cv | omitido | 0/0 | omitido (sin grabación) |
| PER-04 | perfil_sin_cv | omitido | 0/0 | omitido (sin grabación) |
| MAT-01 | matching_factible | ok | 1/1 | cumple lo esperado |
| MAT-02 | matching_factible | ok | 1/1 | cumple lo esperado |
| MAT-03 | matching_factible | ok | 1/1 | cumple lo esperado |
| SES-01 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| SES-02 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| SES-03 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| GRD-01 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-02 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-03 | guardrails | ok | 1/1 | cumple lo esperado |

## Registro de fallos

### Fallos de esta corrida

Ningún caso falló en esta corrida.

### Historial anotado en casos.yaml

Sin fallos anotados todavía. Los casos nunca se borran: cada fallo se anota aquí con su cambio y su nueva ejecución.
