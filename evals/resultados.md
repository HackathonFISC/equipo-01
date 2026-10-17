# Resultados de los evals de Puente

Generado por `uv run python -m evals.run` el 2026-10-16 22:43 (hora de Panamá, UTC-5). No lo edites a mano: se regenera en cada corrida.

Estados: 23 ok · 0 fallo · 0 pendiente · 5 omitido (de 28 casos).

**Esta corrida no mide todo el sistema:** 5 casos con LLM omitidos (sin grabación); no se miden hasta grabar sus respuestas con `LLM_MODO=grabar` y la clave del LLM (ver evals/grabaciones/README.md). Una categoría sin medir no es una categoría aprobada.

Los casos con LLM se corren 3 veces y la tasa es aciertos sobre corridas. Las metas son propuestas provisionales hasta que el equipo las confirme con el plan. Todos los datos de los casos son sintéticos.

Taxonomía usada en esta corrida: la de `casos.yaml` (en línea), copia literal de `data/taxonomia.csv` de #3 (rama issue-3-datos-puente, PR #27) con sus mismos sinónimos; no se agregaron sinónimos tomados de los textos de los casos. Cuando se fusione #27, medir con `data/taxonomia.csv`.

LLM de esta corrida: `puente.llm` en modo reproducir, con las grabaciones de `evals/grabaciones`.

## Tabla por categoría

| Categoría | Casos | Ejecutados | Aciertos | Tasa | Métrica | Meta | Cumple |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| extraccion_ofertas | 5 | 0 | — | — | Fracción de corridas en que los campos esperados salen todos correctos (puesto, empresa, corregimiento, salario_mensual, habilidades) | 80 % | sin medir |
| requisitos_discriminatorios | 10 | 10 | 10/10 | 100 % | Bloqueada, regla (art. 1 / art. 2) y alertas iguales a lo esperado | 100 % | sí |
| perfil_sin_cv | 4 | 4 | 12/12 | 100 % | Habilidades esperadas encontradas con evidencia literal y 0 habilidades sin evidencia | 75 % | sí |
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
| DIS-07 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-08 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-09 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| DIS-10 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| PER-01 | perfil_sin_cv | ok | 3/3 | cumple lo esperado |
| PER-02 | perfil_sin_cv | ok | 3/3 | cumple lo esperado |
| PER-03 | perfil_sin_cv | ok | 3/3 | cumple lo esperado |
| PER-04 | perfil_sin_cv | ok | 3/3 | cumple lo esperado |
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

| Caso | Fecha | Observado | Cambio hecho | Issue | Nueva ejecución |
| --- | --- | --- | --- | --- | --- |
| EXT-01 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | pendiente |
| EXT-02 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | pendiente |
| EXT-03 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | pendiente |
| EXT-04 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | pendiente |
| EXT-05 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (timeout del modelo) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | pendiente |
| DIS-07 | 2026-10-16 | bloqueada false y alertas [estado_civil]: falta sexo_implicito por «soltera» | «soltera», «casada» y sus plurales en un requisito de estado civil dan además sexo_implicito en puente/rules/discriminacion.py; «persona soltera», «soltero/a» y «soltero o soltera» siguen solo como estado_civil (#117, PR de #121) | #117 | 2026-10-16 22:27, evals.run en modo reproducir: ok, bloqueada false y alertas [estado_civil, sexo_implicito] |
