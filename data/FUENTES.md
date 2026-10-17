# Fuentes, Metodología de Medición y Clasificación de Datos (`data/`)

Fecha de consulta y consolidación: **2026-10-16** (Panamá).

Este documento distingue explícitamente entre **Dato medido**, **Dato de prueba** y **Supuesto del equipo** para garantizar trazabilidad ante el jurado del Hackathon FISC AI Agents 2026 (Reto 3: Empleabilidad).

---

## 1. Clasificación de fuentes

| Categoría | Archivos / Parámetros | Naturaleza | Fuente y metodología |
| :--- | :--- | :---: | :--- |
| **Traslados y tarifas** | `data/traslados.csv` (`minutos_hora_pico`, `pasaje_ida_usd`, `modo`) | **Dato medido** | Tiempos en transporte público consultados para hora pico de entrada laboral (07:00 a.m.) en Google Maps Transit / Moovit (2026-10-16) y tarifas vigentes de [MiBus](https://www.mibus.com.pa/guia-del-usuario/) (US$0.25 troncal), [Metro de Panamá](https://www.elmetrodepanama.com/) (US$0.35 Línea 1, US$0.50 Línea 2, US$0.85 trasbordo L1+L2) y rutas interurbanas ATTT Panamá Oeste–Albrook (US$1.00 Arraiján, US$1.25 Vista Alegre, US$1.50 Expreso La Chorrera, US$0.50 rutas internas). |
| **Taxonomía de habilidades** | `data/taxonomia.csv` (`skill_id`, `nombre_es`, `sinonimos`, `uri_esco`) | **Estándar oficial ESCO + Supuestos del equipo** | 47 identificadores de habilidades laborales enlazados a URIs reales verificadas en la API de la [Clasificación Europea ESCO v1.2.1](https://esco.ec.europa.eu/es) (`language=es`, consultado el 2026-10-16) con su etiqueta oficial en español; y 4 identificadores generales/alias operativos (`cocina`, `comunicacion_efectiva`, `excel`, `ventas`) con `uri_esco` vacía documentados como **supuesto del equipo** (#76) para no duplicar ni inventar URIs. |
| **Ofertas reales anonimizadas** | `data/ofertas/CT*.txt`, `data/ofertas/K*.txt` | **Dato medido (con empresas de prueba)** | Anuncios consultados el 2026-10-16 en Computrabajo Panamá y Konzerta Panamá conservando condiciones reales publicadas (incluyendo casos de salario no publicado y frases sujetas a la Ley 11 de 2005), con nombres de empresas de prueba según acuerdo de equipo (#41). URL y fecha de cada anuncio en la sección 3. |
| **Catálogo de cursos INADEH** | `data/cursos.csv` (`curso`, `area`, `horas` vs. `cupos`, `horario`) | **Mixto: títulos reales + Dato de prueba** | Nombres de cursos y áreas tomados de la oferta formativa pública del [INADEH](https://www.inadeh.edu.pa/) (`Hoja de Cálculo Básica`, `Operaciones de Caja Comercial`, `Auxiliar de Almacén, Bodega e Inventario`, `Manipulación Higiénica de Alimentos`, `Inglés Básico`). **Los cupos disponibles y horarios específicos son datos de prueba** rotulados como tales para evaluar las restricciones de horario nocturno y cupo del agente Formación (#14, #51, #53). |
| **Perfiles de jóvenes** | `data/perfiles/*.json`, `tests/fixtures/caso_yaritza.json` | **Dato de prueba (100% sintético)** | Diez perfiles sintéticos de 18 a 29 años, incluida Yaritza, sin datos personales reales (sin cédula, teléfono ni dirección exacta de personas reales). |
| **Duplicados entre portales** | `data/ofertas_prueba/duplicados.json` | **Dato de prueba (simulado)** | Dos pares de una misma vacante en formatos de portal distintos y un caso negativo (misma cadena, dos sucursales); ver sección 4. |
| **Umbrales y simetría de traslado** | `ConfigSolver` (`max_minutos_traslado=60`, `max_porcentaje_salario_pasaje=0.15`, `dias_laborables=22`) | **Supuesto del equipo** | Parámetros configurables: (1) tope de **60 minutos** por trayecto para evitar deserción temprana por fatiga de traslado; (2) tope del **15 % del salario mensual** destinado a transporte público; (3) **22 días laborables al mes por defecto**, equivalentes a `44 × pasaje_ida_usd` para ida y vuelta; (4) **supuesto de simetría**: se asume que ida y vuelta tienen duración y tarifa equivalentes en hora pico. Los días laborables se pueden cambiar mediante `dias_laborables` (alias `dias_laborables_mes`); no son una medición de campo. |

---

## 2. Nodos geográficos (distritos y corregimientos) y Tabla de Alias

En `data/traslados.csv` y `puente/rules/traslados.py` se utilizan 13 nodos canónicos que cubren el corredor Panamá Oeste – Centro – San Miguelito – Panamá Este:

- **Nodos a nivel de distrito (usados en anuncios y perfiles del área metropolitana):**
  - `La Chorrera` (Distrito de La Chorrera, Panamá Oeste)
  - `Arraiján` (Distrito de Arraiján cabecera, Panamá Oeste)
  - `San Miguelito` (Distrito de San Miguelito)
- **Nodos a nivel de corregimiento:**
  - `Vista Alegre` (Distrito de Arraiján)
  - `Ancón`, `Calidonia`, `Bella Vista`, `Betania`, `San Francisco`, `Juan Díaz`, `Pedregal`, `Tocumen`, `24 de Diciembre` (Distrito de Panamá)

### Tabla de alias (`TABLA_ALIAS_LUGARES` en `puente/rules/traslados.py`)
Cuando una oferta o perfil menciona un barrio, terminal, zona especial o corregimiento específico dentro de uno de los nodos anteriores, `traslados.py` lo resuelve de forma determinística:

| Ubicación en texto / formulario | Nodo canónico en `traslados.csv` | Justificación geográfica |
| :--- | :--- | :--- |
| `Barrio Colón`, `Barrio Balboa`, `El Coco`, `Guadalupe` | `La Chorrera` | Corregimientos céntricos del distrito de La Chorrera con la misma terminal de salida hacia Panamá. |
| `Juan Demóstenes Arosemena`, `Vacamonte` | `Vista Alegre` | Corregimientos contiguos del eje interurbano de Arraiján Oeste. |
| `Panamá Pacífico`, `Howard`, `Burunga`, `Veracruz` | `Arraiján` | Área económica especial y corregimientos conectados al nodo de Arraiján / Puente de las Américas. |
| `Albrook`, `Terminal de Albrook` | `Ancón` | Ubicada en el corregimiento de Ancón (punto de integración Bus Oeste + Metro L1 + MiBus). |
| `Curundú`, `Santa Ana`, `El Chorrillo` | `Calidonia` | Corregimientos céntricos contiguos al eje 5 de Mayo / Calidonia (Metro L1). |
| `Pueblo Nuevo` | `Betania` | Corregimiento contiguo sobre el eje Transístmica / Metro L1. |
| `Parque Lefevre`, `Río Abajo`, `Costa del Este` | `Juan Díaz` | Corregimientos contiguos del eje Vía España / Corredor Sur hacia Panamá Este. |
| `Rufina Alfaro`, `Belisario Porras`, `Amelia Denis de Icaza`, `José Domingo Espinar`, `Las Cumbres` | `San Miguelito` | Corregimientos del distrito de San Miguelito y eje Norte conectados a Metro L1/L2. |
| `Mañanitas` | `Tocumen` | Corregimiento contiguo sobre el eje de la Línea 2 del Metro en Panamá Este. |

---

## 3. Ofertas reales consultadas: 20 anuncios

La muestra sirve para desarrollo y evaluación. No confirma que una vacante siga abierta ni autoriza postular a nadie.

Las fichas `ofertas/CT*.txt` provienen de anuncios individuales de Computrabajo Panamá; `ofertas/K*.txt`, de Konzerta Panamá. Conservan el puesto, la zona indicada, el salario cuando se publicó y requisitos observados, con un resumen propio de tareas. Cada ficha contiene una **empresa de prueba** para que el sistema pueda procesarla sin tomar el nombre comercial como dato de demostración. El nombre publicado puede identificarse al abrir el enlace original que figura aquí.

El filtro «sin experiencia» de Computrabajo también devuelve anuncios que piden experiencia; por eso se transcribió el requisito visible. El salario, los horarios y la vigencia no publicados permanecen desconocidos. La muestra incluye cuatro anuncios de Panamá Oeste y al menos cinco de Panamá Este. Contiene frases de edad, sexo y «Buena presencia» como entradas para que el agente Mercado aplique sus reglas; no representan ofertas aptas para postulación.

| ID | Portal | URL de la publicación | Consulta |
| --- | --- | --- | --- |
| CT02 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-promotora-recaudacion-de-fondos-para-ong-mundial-en-panama-B9934D53C6A85EA661373E686DCF3405) | 2026-10-16 |
| CT03 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-operaria-de-limpieza-para-oficinas-y-planta-en-panama-26FA32F7F511B39761373E686DCF3405) | 2026-10-16 |
| CT06 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-ejecutivo-de-ventas-residencial-telecomunicaciones-en-panama-30E510DD9C03E53061373E686DCF3405) | 2026-10-16 |
| CT11 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-vendedor-en-tienda-en-panama-93FE1A171B0FB86961373E686DCF3405) | 2026-10-16 |
| CT16 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-runner-de-restaurante-asistente-de-servicio-en-panama-A73EF1BF32AE497861373E686DCF3405) | 2026-10-16 |
| CT17 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-asistente-de-call-center-con-conocimiento-en-crm-en-panama-AE765FB99BA3FC5661373E686DCF3405) | 2026-10-16 |
| CT22 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-telemercadeo-vendedora-en-panama-534A837BA284616B61373E686DCF3405) | 2026-10-16 |
| CT24 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-fundraiser-solo-si-vives-en-ciudad-de-panama-en-panama-EB87147D6CDE006761373E686DCF3405) | 2026-10-16 |
| CT26 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-auxiliar-de-cocina-temporal-en-panama-59A2BA576578C0E261373E686DCF3405) | 2026-10-16 |
| CT40 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-asistente-de-ventas-reparacion-de-equipos-electronicos-en-san-miguelito-0405873DDCEF169261373E686DCF3405) | 2026-10-16 |
| CT46 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-operario-de-limpieza-en-arraijan-F9BB40DDD3483D6161373E686DCF3405) | 2026-10-16 |
| CT50 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-ayudante-general-en-capira-14824501DAFC488361373E686DCF3405) | 2026-10-16 |
| CT54 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-gestor-de-cobros-chorrera-en-la-chorrera-FA2E50309849BE4861373E686DCF3405) | 2026-10-16 |
| CT57 | Computrabajo Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-auxiliar-de-servicio-al-cliente-en-arraijan-E9AEF4BC154BCF6161373E686DCF3405) | 2026-10-16 |
| K01 | Konzerta Panamá | [Enlace](https://www.konzerta.com/empleos/ayudante-general-temporal-panama-este-1118432953.html) | 2026-10-16 |
| K02 | Konzerta Panamá | [Enlace](https://www.konzerta.com/empleos/ayudante-general-1118453487.html) | 2026-10-16 |
| K03 | Konzerta Panamá | [Enlace](https://www.konzerta.com/empleos/cajera-1118453490.html) | 2026-10-16 |
| K04 | Konzerta Panamá | [Enlace](https://www.konzerta.com/empleos/oficial-de-verificacion-corporacion-el-sol-1118464512.html) | 2026-10-16 |
| K05 | Konzerta Panamá | [Enlace](https://www.konzerta.com/empleos/mercaderista-grupo-melo-1118238026.html) | 2026-10-16 |
| K06 | Konzerta Panamá | [Enlace](https://www.konzerta.com/empleos/mercaderista-1118447009.html) | 2026-10-16 |

**Método:** consulta de páginas individuales, extracción automatizada de la información visible y revisión de enlaces, tareas, requisitos y ubicación el 2026-10-16. Las URL y fechas se centralizan en esta tabla; los archivos `ofertas/` no contienen URL ni nombre comercial real.

## 4. Datos enteramente sintéticos de prueba

- `perfiles/*.json`: diez personajes ficticios de 18 a 29 años, incluida Yaritza del [issue #3](https://github.com/HackathonFISC/equipo-01/issues/3). Horarios, expectativas salariales, presupuestos, experiencias y evidencias son inventados para la demo. Cada horario usa bloques `dias`, `hora_inicio`, `hora_fin` compatibles con `puente/schemas.py`. No hay datos de personas reales.
- `ofertas_prueba/duplicados.json`: dos pares de una misma vacante escritos en formatos de portal distintos y un caso negativo de la misma cadena en dos sucursales. Son **publicaciones simuladas**, no duplicados reales hallados entre Computrabajo y Konzerta. Empresa, sucursal, salario y horarios del fixture son datos de prueba. El caso negativo motivó el arreglo de sucursales distintas de [#75](https://github.com/HackathonFISC/equipo-01/issues/75).
- Los nombres «Empresa A» a «Empresa T» en `ofertas/` son alias de prueba añadidos por el equipo. No fueron publicados por los portales.
- En diez fichas de Computrabajo, el campo consultado solo decía «años de experiencia», sin un número visible. Se conservó así en las fichas; el equipo no infiere una cantidad mínima.

## 5. Taxonomía y decisiones del equipo

`taxonomia.csv` reúne 51 habilidades: 42 ligadas a tareas de las ofertas y los perfiles (#3) y 9 que añadió #51 para los cursos del INADEH (8 las usa hoy `cursos.csv`). Las 12 comunes conservan los sinónimos de ambas versiones. De las 51 entradas, 47 tienen URI y etiqueta verificadas con `language=es` en la [API oficial de ESCO](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api) el 2026-10-16. Las otras cuatro (`cocina`, `comunicacion_efectiva`, `excel`, `ventas`) son alias operativos sin URI ESCO, según #76. Los `skill_id` y sinónimos son una normalización local del equipo para empleos de entrada en Panamá; no son identificadores de ESCO ni prueban que un anuncio exija cada habilidad. ESCO aporta vocabulario, no vacantes panameñas.

## 6. Alcance de cada issue

La matriz de traslados, la tabla de alias y el catálogo de cursos vienen del [issue #51](https://github.com/HackathonFISC/equipo-01/issues/51) (secciones 1 y 2); los cupos y horarios del INADEH son datos de prueba rotulados como tales, según la decisión del 16 de octubre ([#41](https://github.com/HackathonFISC/equipo-01/issues/41)). Las ofertas, los perfiles, los duplicados de prueba y las 42 habilidades de tareas vienen del [issue #3](https://github.com/HackathonFISC/equipo-01/issues/3). Las fotos de volantes del plan inicial no se incluyen: no se obtuvo material autorizado.
