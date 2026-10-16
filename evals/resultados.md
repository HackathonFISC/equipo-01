# Resultados de los evals de Puente

Generado por `uv run python -m evals.run` el 2026-10-16 16:20 (hora de Panamá, UTC-5). No lo edites a mano: se regenera en cada corrida.

Estados: 6 ok · 0 fallo · 9 pendiente · 9 omitido (de 24 casos).

**Esta corrida no mide todo el sistema:** 9 casos pendientes porque aún no están fusionadas sus dependencias (#5, #9); 9 casos con LLM omitidos por falta de GOOGLE_API_KEY (no se miden hasta correr con la clave real). Una categoría sin medir no es una categoría aprobada.

Los casos con LLM se corren 3 veces y la tasa es aciertos sobre corridas. Las metas son propuestas provisionales hasta que el equipo las confirme con el plan. Todos los datos de los casos son sintéticos.

## Tabla por categoría

| Categoría | Casos | Ejecutados | Aciertos | Tasa | Métrica | Meta | Cumple |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| extraccion_ofertas | 5 | 0 | — | — | Fracción de corridas en que los campos esperados salen todos correctos (puesto, empresa, corregimiento, salario_mensual, habilidades) | 80 % | sin medir |
| requisitos_discriminatorios | 6 | 0 | — | — | Bloqueada, regla (art. 1 / art. 2) y alertas iguales a lo esperado | 100 % | sin medir |
| perfil_sin_cv | 4 | 0 | — | — | Habilidades esperadas encontradas con evidencia literal y 0 habilidades sin evidencia | 75 % | sin medir |
| matching_factible | 3 | 3 | 3/3 | 100 % | Asignación igual a la calculada a mano y 0 violaciones duras | 100 % | sí |
| sesgo_contrafactual | 3 | 3 | 3/3 | 100 % | sesgo_detectado igual a lo esperado | 100 % | sí |
| guardrails | 3 | 0 | — | — | Veredicto (rechazar \| marcar \| fuera_de_alcance \| aceptar) igual a lo esperado y sin llamar al LLM cuando se rechaza | 100 % | sin medir |

## Detalle por caso

| Caso | Categoría | Estado | Aciertos | Detalle |
| --- | --- | --- | ---: | --- |
| EXT-01 | extraccion_ofertas | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| EXT-02 | extraccion_ofertas | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| EXT-03 | extraccion_ofertas | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| EXT-04 | extraccion_ofertas | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| EXT-05 | extraccion_ofertas | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| DIS-01 | requisitos_discriminatorios | pendiente | 0/0 | pendiente (#5: función evaluar_requisitos no existe en puente.rules.discriminacion (ajustar constante)) |
| DIS-02 | requisitos_discriminatorios | pendiente | 0/0 | pendiente (#5: función evaluar_requisitos no existe en puente.rules.discriminacion (ajustar constante)) |
| DIS-03 | requisitos_discriminatorios | pendiente | 0/0 | pendiente (#5: función evaluar_requisitos no existe en puente.rules.discriminacion (ajustar constante)) |
| DIS-04 | requisitos_discriminatorios | pendiente | 0/0 | pendiente (#5: función evaluar_requisitos no existe en puente.rules.discriminacion (ajustar constante)) |
| DIS-05 | requisitos_discriminatorios | pendiente | 0/0 | pendiente (#5: función evaluar_requisitos no existe en puente.rules.discriminacion (ajustar constante)) |
| DIS-06 | requisitos_discriminatorios | pendiente | 0/0 | pendiente (#5: función evaluar_requisitos no existe en puente.rules.discriminacion (ajustar constante)) |
| PER-01 | perfil_sin_cv | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| PER-02 | perfil_sin_cv | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| PER-03 | perfil_sin_cv | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| PER-04 | perfil_sin_cv | omitido | 0/0 | omitido: sin GOOGLE_API_KEY |
| MAT-01 | matching_factible | ok | 1/1 | cumple lo esperado |
| MAT-02 | matching_factible | ok | 1/1 | cumple lo esperado |
| MAT-03 | matching_factible | ok | 1/1 | cumple lo esperado |
| SES-01 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| SES-02 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| SES-03 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| GRD-01 | guardrails | pendiente | 0/0 | pendiente (#9: función validar_entrada no existe en puente.guardrails (ajustar constante)) |
| GRD-02 | guardrails | pendiente | 0/0 | pendiente (#9: función validar_entrada no existe en puente.guardrails (ajustar constante)) |
| GRD-03 | guardrails | pendiente | 0/0 | pendiente (#9: función validar_entrada no existe en puente.guardrails (ajustar constante)) |

## Registro de fallos

### Fallos de esta corrida

Ningún caso falló en esta corrida.

### Historial anotado en casos.yaml

Sin fallos anotados todavía. Los casos nunca se borran: cada fallo se anota aquí con su cambio y su nueva ejecución.
