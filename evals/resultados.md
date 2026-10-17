# Resultados de los evals de Puente

Generado por `uv run python -m evals.run` el 2026-10-17 02:07 (hora de Panamá, UTC-5). No lo edites a mano: se regenera en cada corrida.

Estados: 42 ok · 0 fallo · 0 pendiente · 0 omitido (de 42 casos).

Todos los casos se ejecutaron en esta corrida.

En modo reproducir cada caso con LLM usa una sola grabación; no se mide variación entre corridas. Las metas son propuestas provisionales hasta que el equipo las confirme con el plan. Todos los datos de los casos son sintéticos.

Taxonomía usada en esta corrida: `data/taxonomia.csv`, el mismo catálogo que usa la demo.

LLM de esta corrida: `puente.llm` en modo reproducir, con las grabaciones de `evals/grabaciones`.

## Tabla por categoría

| Categoría | Casos | Ejecutados | Aciertos | Tasa | Métrica | Meta | Cumple |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| extraccion_ofertas | 5 | 5 | 5/5 | 100 % | Fracción de corridas en que los campos esperados salen todos correctos (puesto, empresa, corregimiento, salario_mensual, habilidades) | 80 % | sí |
| requisitos_discriminatorios | 11 | 11 | 11/11 | 100 % | Bloqueada, regla (art. 1 / art. 2) y alertas iguales a lo esperado | 100 % | sí |
| perfil_sin_cv | 4 | 4 | 4/4 | 100 % | Habilidades esperadas encontradas con evidencia literal y 0 habilidades sin evidencia | 75 % | sí |
| matching_factible | 3 | 3 | 3/3 | 100 % | Asignación igual a la calculada a mano y 0 violaciones duras | 100 % | sí |
| sesgo_contrafactual | 3 | 3 | 3/3 | 100 % | sesgo_detectado igual a lo esperado | 100 % | sí |
| guardrails | 7 | 7 | 7/7 | 100 % | Veredicto (rechazar \| marcar \| fuera_de_alcance \| aceptar) igual a lo esperado y sin llamar al LLM cuando se rechaza | 100 % | sí |
| horario_tirilla | 1 | 1 | 1/1 | 100 % | La tirilla sintética da exactamente el horario_clases, las excepciones y la vigencia esperados, sin problemas | 100 % | sí |
| horario_validacion | 1 | 1 | 1/1 | 100 % | Los bloques inválidos se rechazan (índice y campo) y los válidos dan el horario_clases esperado | 100 % | sí |
| redactor_puente | 3 | 3 | 3/3 | 100 % | El texto propuesto pasa revisar_texto sin hallazgos, con los cambios esperados, y los viables antes y después son los calculados a mano con las restricciones duras del solver | 100 % | sí |
| micromision | 4 | 4 | 4/4 | 100 % | Resultado y puntaje del reto calculados en código iguales a lo esperado; mismo puntaje con nombre y género cambiados; scorecard sin datos personales | 100 % | sí |

## Detalle por caso

| Caso | Categoría | Estado | Aciertos | Detalle |
| --- | --- | --- | ---: | --- |
| EXT-01 | extraccion_ofertas | ok | 1/1 | cumple lo esperado |
| EXT-02 | extraccion_ofertas | ok | 1/1 | cumple lo esperado |
| EXT-03 | extraccion_ofertas | ok | 1/1 | cumple lo esperado |
| EXT-04 | extraccion_ofertas | ok | 1/1 | cumple lo esperado |
| EXT-05 | extraccion_ofertas | ok | 1/1 | cumple lo esperado |
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
| DIS-11 | requisitos_discriminatorios | ok | 1/1 | cumple lo esperado |
| PER-01 | perfil_sin_cv | ok | 1/1 | cumple lo esperado |
| PER-02 | perfil_sin_cv | ok | 1/1 | cumple lo esperado |
| PER-03 | perfil_sin_cv | ok | 1/1 | cumple lo esperado |
| PER-04 | perfil_sin_cv | ok | 1/1 | cumple lo esperado |
| MAT-01 | matching_factible | ok | 1/1 | cumple lo esperado |
| MAT-02 | matching_factible | ok | 1/1 | cumple lo esperado |
| MAT-03 | matching_factible | ok | 1/1 | cumple lo esperado |
| SES-01 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| SES-02 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| SES-03 | sesgo_contrafactual | ok | 1/1 | cumple lo esperado |
| GRD-01 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-02 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-03 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-04 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-05 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-06 | guardrails | ok | 1/1 | cumple lo esperado |
| GRD-07 | guardrails | ok | 1/1 | cumple lo esperado |
| HOR-01 | horario_tirilla | ok | 1/1 | cumple lo esperado |
| HOR-02 | horario_validacion | ok | 1/1 | cumple lo esperado |
| RED-01 | redactor_puente | ok | 1/1 | cumple lo esperado |
| RED-02 | redactor_puente | ok | 1/1 | cumple lo esperado |
| RED-03 | redactor_puente | ok | 1/1 | cumple lo esperado |
| MIC-01 | micromision | ok | 1/1 | cumple lo esperado |
| MIC-02 | micromision | ok | 1/1 | cumple lo esperado |
| MIC-03 | micromision | ok | 1/1 | cumple lo esperado |
| MIC-04 | micromision | ok | 1/1 | cumple lo esperado |

## Registro de fallos

### Fallos de esta corrida

Ningún caso falló en esta corrida.

### Historial anotado en casos.yaml

| Caso | Fecha | Observado | Cambio hecho | Issue | Nueva ejecución |
| --- | --- | --- | --- | --- | --- |
| EXT-01 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #152 |
| EXT-01 | 2026-10-16 | Mercado dejó corregimiento vacío aunque la oferta dice La Chorrera. | Causa: Claude deja null el corregimiento cuando la oferta da un distrito (La Chorrera) y lo copia en ubicacion_texto («La Chorrera, calle 5ta»); Mercado no lo resolvía. puente/agents/mercado.py (inferir_corregimiento) toma el corregimiento de ubicacion_texto si nombra un solo nodo o alias de traslados.csv, como palabra completa, y también aparece en el texto; si nombra varios no elige y no busca fuera de la ubicación. «Cajero/a» y «S.A.» no causaban el fallo (el comparador ya usa contención); evals/run.py los normaliza igual en los dos lados. Sin cambiar prompt, esquema, taxonomía ni grabación (#152); esperado original conservado | #152 | 2026-10-17 00:30, evals.run en modo reproducir con la grabación de Claude: ok, corregimiento La Chorrera |
| EXT-02 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #153 |
| EXT-02 | 2026-10-16 | Mercado solo conservó inventario; faltan reposición y recepción de mercancía. | Causa: la extracción sí traía las tres («acomodar productos en estantes», «recibir mercancía», «control de inventario»), pero la normalización a la taxonomía era solo exacta y descartó las dos primeras («acomodar productos», «recibir mercancías»). Mercado usa ahora, si no hay coincidencia exacta, buscar_habilidades de puente/rules/taxonomia.py: compara raíces (sin palabras vacías, plural, infinitivo ni -ción) y acepta una frase que contiene un nombre de dos o más palabras. Perfil sigue con la coincidencia exacta. Sin cambiar taxonomia.csv, prompt ni grabación (#153); esperado original conservado | #153 | 2026-10-17 00:30, evals.run en modo reproducir con la grabación de Claude: ok, habilidades [reposicion_productos, recepcion_mercancia, inventario] |
| EXT-03 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #154 |
| EXT-03 | 2026-10-16 | Mercado dejó corregimiento vacío y solo conservó Excel; faltan registro y archivo. | Causas: (1) como EXT-01, la oferta da el distrito San Miguelito, Claude deja null el corregimiento y lo copia en ubicacion_texto; Mercado lo toma ahora de ahí si es un nodo de traslados.csv (#152). (2) La extracción sí traía «registro de datos de facturas» y «archivo de documentos», pero la normalización exacta no las unía a «registro de datos» y «archivar documentos»; ahora Mercado usa buscar_habilidades por raíces (#153). Sin cambiar prompt, esquema, taxonomía ni grabación (#154); esperado original conservado | #154 | 2026-10-17 00:30, evals.run en modo reproducir con la grabación de Claude: ok, corregimiento San Miguelito y habilidades [excel_basico, registro_datos, archivo_documentos] |
| EXT-04 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #155 |
| EXT-04 | 2026-10-16 | Mercado omitió ventas de mostrador y solo conservó atención al cliente. | Causa: la extracción sí traía «venta de mostrador», pero la normalización exacta no la unía a «ventas de mostrador» (plural) y la descartó. Mercado usa ahora buscar_habilidades por raíces cuando no hay coincidencia exacta (#153). Sin cambiar prompt, esquema, taxonomía ni grabación (#155); esperado original conservado | #155 | 2026-10-17 00:30, evals.run en modo reproducir con la grabación de Claude: ok, habilidades [ventas_mostrador, atencion_cliente] |
| EXT-05 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (timeout del modelo) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ok con grabación Opus tras #140 |
| DIS-07 | 2026-10-16 | bloqueada false y alertas [estado_civil]: falta sexo_implicito por «soltera» | «soltera», «casada» y sus plurales en un requisito de estado civil dan además sexo_implicito en puente/rules/discriminacion.py; «persona soltera», «soltero/a» y «soltero o soltera» siguen solo como estado_civil (#117, PR de #121) | #117 | 2026-10-16 22:27, evals.run en modo reproducir: ok, bloqueada false y alertas [estado_civil, sexo_implicito] |
