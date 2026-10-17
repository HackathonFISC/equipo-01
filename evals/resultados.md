# Resultados de los evals de Puente

Generado por `uv run python -m evals.run` el 2026-10-16 23:24 (hora de Panamá, UTC-5). No lo edites a mano: se regenera en cada corrida.

Estados: 29 ok · 4 fallo · 0 pendiente · 0 omitido (de 33 casos).

Todos los casos se ejecutaron en esta corrida.

En modo reproducir cada caso con LLM usa una sola grabación; no se mide variación entre corridas. Las metas son propuestas provisionales hasta que el equipo las confirme con el plan. Todos los datos de los casos son sintéticos.

Taxonomía usada en esta corrida: `data/taxonomia.csv`, el mismo catálogo que usa la demo.

LLM de esta corrida: `puente.llm` en modo reproducir, con las grabaciones de `evals/grabaciones`.

## Tabla por categoría

| Categoría | Casos | Ejecutados | Aciertos | Tasa | Métrica | Meta | Cumple |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| extraccion_ofertas | 5 | 5 | 1/5 | 20 % | Fracción de corridas en que los campos esperados salen todos correctos (puesto, empresa, corregimiento, salario_mensual, habilidades) | 80 % | no |
| requisitos_discriminatorios | 11 | 11 | 11/11 | 100 % | Bloqueada, regla (art. 1 / art. 2) y alertas iguales a lo esperado | 100 % | sí |
| perfil_sin_cv | 4 | 4 | 4/4 | 100 % | Habilidades esperadas encontradas con evidencia literal y 0 habilidades sin evidencia | 75 % | sí |
| matching_factible | 3 | 3 | 3/3 | 100 % | Asignación igual a la calculada a mano y 0 violaciones duras | 100 % | sí |
| sesgo_contrafactual | 3 | 3 | 3/3 | 100 % | sesgo_detectado igual a lo esperado | 100 % | sí |
| guardrails | 7 | 7 | 7/7 | 100 % | Veredicto (rechazar \| marcar \| fuera_de_alcance \| aceptar) igual a lo esperado y sin llamar al LLM cuando se rechaza | 100 % | sí |

## Detalle por caso

| Caso | Categoría | Estado | Aciertos | Detalle |
| --- | --- | --- | ---: | --- |
| EXT-01 | extraccion_ofertas | fallo | 0/1 | 0/1 corridas acertaron; difiere en corregimiento; esperado {"puesto": "Cajero", "empresa": "Super Rio Ficticio", "corregimiento": "La Chorrera", "salario_mensual": 520, "habilidades": ["manejo_caja", "atencion_cliente"]}; observado {"puesto": "Cajero/a", "empresa": "Super Rio Ficticio S.A.", "corregimiento": "", "salario_mensual": 520.0, "habilidades": ["manejo_caja", "atencion_cliente"]} |
| EXT-02 | extraccion_ofertas | fallo | 0/1 | 0/1 corridas acertaron; difiere en habilidades; esperado {"puesto": "Reponedor", "empresa": "Minimarket Los Cuatro Vientos", "corregimiento": "Arraiján", "salario_mensual": 450, "habilidades": ["reposicion_productos", "recepcion_mercancia", "inventario"]}; observado {"puesto": "Reponedor", "empresa": "Minimarket Los Cuatro Vientos", "corregimiento": "Arraiján", "salario_mensual": 450.0, "habilidades": ["inventario"]} |
| EXT-03 | extraccion_ofertas | fallo | 0/1 | 0/1 corridas acertaron; difiere en corregimiento, habilidades; esperado {"puesto": "Auxiliar administrativo", "empresa": "Distribuidora Ficticia Istmo", "corregimiento": "San Miguelito", "salario_mensual": 550, "habilidades": ["excel_basico", "registro_datos", "archivo_documentos"]}; observado {"puesto": "Auxiliar Administrativo", "empresa": "Distribuidora Ficticia Istmo", "corregimiento": "", "salario_mensual": 550.0, "habilidades": ["excel_basico"]} |
| EXT-04 | extraccion_ofertas | fallo | 0/1 | 0/1 corridas acertaron; difiere en habilidades; esperado {"puesto": "Dependiente", "empresa": "Tienda Moda Ficticia", "corregimiento": "Bella Vista", "salario_mensual": 480, "habilidades": ["ventas_mostrador", "atencion_cliente"]}; observado {"puesto": "Dependiente", "empresa": "Tienda Moda Ficticia", "corregimiento": "Bella Vista", "salario_mensual": 480.0, "habilidades": ["atencion_cliente"]} |
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

## Registro de fallos

### Fallos de esta corrida

| Caso | Esperado | Observado / detalle | Cambio hecho | Nueva ejecución |
| --- | --- | --- | --- | --- |
| EXT-01 | `{"puesto": "Cajero", "empresa": "Super Rio Ficticio", "corregimiento": "La Chorrera", "salario_mensual": 520, "habilidades": ["manejo_caja", "atencion_cliente"]}` | 0/1 corridas acertaron; difiere en corregimiento; esperado {"puesto": "Cajero", "empresa": "Super Rio Ficticio", "corregimiento": "La Chorrera", "salario_mensual": 520, "habilidades": ["manejo_caja", "atencion_cliente"]}; observado {"puesto": "Cajero/a", "empresa": "Super Rio Ficticio S.A.", "corregimiento": "", "salario_mensual": 520.0, "habilidades": ["manejo_caja", "atencion_cliente"]} | (pendiente: anotar en `fallos` de casos.yaml) | (pendiente) |
| EXT-02 | `{"puesto": "Reponedor", "empresa": "Minimarket Los Cuatro Vientos", "corregimiento": "Arraiján", "salario_mensual": 450, "habilidades": ["reposicion_productos", "recepcion_mercancia", "inventario"]}` | 0/1 corridas acertaron; difiere en habilidades; esperado {"puesto": "Reponedor", "empresa": "Minimarket Los Cuatro Vientos", "corregimiento": "Arraiján", "salario_mensual": 450, "habilidades": ["reposicion_productos", "recepcion_mercancia", "inventario"]}; observado {"puesto": "Reponedor", "empresa": "Minimarket Los Cuatro Vientos", "corregimiento": "Arraiján", "salario_mensual": 450.0, "habilidades": ["inventario"]} | (pendiente: anotar en `fallos` de casos.yaml) | (pendiente) |
| EXT-03 | `{"puesto": "Auxiliar administrativo", "empresa": "Distribuidora Ficticia Istmo", "corregimiento": "San Miguelito", "salario_mensual": 550, "habilidades": ["excel_basico", "registro_datos", "archivo_documentos"]}` | 0/1 corridas acertaron; difiere en corregimiento, habilidades; esperado {"puesto": "Auxiliar administrativo", "empresa": "Distribuidora Ficticia Istmo", "corregimiento": "San Miguelito", "salario_mensual": 550, "habilidades": ["excel_basico", "registro_datos", "archivo_documentos"]}; observado {"puesto": "Auxiliar Administrativo", "empresa": "Distribuidora Ficticia Istmo", "corregimiento": "", "salario_mensual": 550.0, "habilidades": ["excel_basico"]} | (pendiente: anotar en `fallos` de casos.yaml) | (pendiente) |
| EXT-04 | `{"puesto": "Dependiente", "empresa": "Tienda Moda Ficticia", "corregimiento": "Bella Vista", "salario_mensual": 480, "habilidades": ["ventas_mostrador", "atencion_cliente"]}` | 0/1 corridas acertaron; difiere en habilidades; esperado {"puesto": "Dependiente", "empresa": "Tienda Moda Ficticia", "corregimiento": "Bella Vista", "salario_mensual": 480, "habilidades": ["ventas_mostrador", "atencion_cliente"]}; observado {"puesto": "Dependiente", "empresa": "Tienda Moda Ficticia", "corregimiento": "Bella Vista", "salario_mensual": 480.0, "habilidades": ["atencion_cliente"]} | (pendiente: anotar en `fallos` de casos.yaml) | (pendiente) |

### Historial anotado en casos.yaml

| Caso | Fecha | Observado | Cambio hecho | Issue | Nueva ejecución |
| --- | --- | --- | --- | --- | --- |
| EXT-01 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #152 |
| EXT-01 | 2026-10-16 | Mercado dejó corregimiento vacío aunque la oferta dice La Chorrera. | Pendiente de corregir en #152; esperado original conservado | #152 | pendiente |
| EXT-02 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #153 |
| EXT-02 | 2026-10-16 | Mercado solo conservó inventario; faltan reposición y recepción de mercancía. | Pendiente de corregir en #153; esperado original conservado | #153 | pendiente |
| EXT-03 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #154 |
| EXT-03 | 2026-10-16 | Mercado dejó corregimiento vacío y solo conservó Excel; faltan registro y archivo. | Pendiente de corregir en #154; esperado original conservado | #154 | pendiente |
| EXT-04 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (HTTP 400 «Schema is too complex» al pedir OfertaExtraida) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ya responde tras #140; nuevo fallo #155 |
| EXT-04 | 2026-10-16 | Mercado omitió ventas de mostrador y solo conservó atención al cliente. | Pendiente de corregir en #155; esperado original conservado | #155 | pendiente |
| EXT-05 | 2026-10-16 | grabando con Claude en vivo: mercado no devolvió ninguna oferta (timeout del modelo) | pendiente: simplificar el esquema o el pedido de Mercado (#137) y volver a grabar | #137 | 2026-10-16 23:22: ok con grabación Opus tras #140 |
| DIS-07 | 2026-10-16 | bloqueada false y alertas [estado_civil]: falta sexo_implicito por «soltera» | «soltera», «casada» y sus plurales en un requisito de estado civil dan además sexo_implicito en puente/rules/discriminacion.py; «persona soltera», «soltero/a» y «soltero o soltera» siguen solo como estado_civil (#117, PR de #121) | #117 | 2026-10-16 22:27, evals.run en modo reproducir: ok, bloqueada false y alertas [estado_civil, sexo_implicito] |
