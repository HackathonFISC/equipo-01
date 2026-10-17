# Fuentes, Metodología de Medición y Clasificación de Datos (`data/`)

Fecha de consulta y consolidación: **2026-10-16** (Panamá).

Este documento distingue explícitamente entre **Dato medido**, **Dato de prueba** y **Supuesto del equipo** para garantizar trazabilidad ante el jurado del Hackathon FISC AI Agents 2026 (Reto 3: Empleabilidad).

---

## 1. Clasificación de fuentes

| Categoría | Archivos / Parámetros | Naturaleza | Fuente y metodología |
| :--- | :--- | :---: | :--- |
| **Traslados y tarifas** | `data/traslados.csv` (`minutos_hora_pico`, `pasaje_ida_usd`, `modo`) | **Dato medido** | Tiempos en transporte público consultados para hora pico de entrada laboral (07:00 a.m.) en Google Maps Transit / Moovit (2026-10-16) y tarifas vigentes de [MiBus](https://www.mibus.com.pa/guia-del-usuario/) (US$0.25 troncal), [Metro de Panamá](https://www.elmetrodepanama.com/) (US$0.35 Línea 1, US$0.50 Línea 2, US$0.85 trasbordo L1+L2) y rutas interurbanas ATTT Panamá Oeste–Albrook (US$1.00 Arraiján, US$1.25 Vista Alegre, US$1.50 Expreso La Chorrera, US$0.50 rutas internas). |
| **Taxonomía de habilidades** | `data/taxonomia.csv` (`skill_id`, `nombre_es`, `sinonimos`, `uri_esco`) | **Estándar oficial ESCO + Supuestos del equipo** | 47 identificadores de habilidades laborales enlazados a URIs reales verificadas en la API de la [Clasificación Europea ESCO v1.2.1](https://esco.ec.europa.eu/es) (`language=es`, consultado el 2026-10-16) con su etiqueta oficial en español; y 4 identificadores generales/alias operativos (`cocina`, `comunicacion_efectiva`, `excel`, `ventas`) con `uri_esco` vacía documentados como **supuesto del equipo** (#76) para no duplicar ni inventar URIs. Los `skill_id` y sinónimos son normalización local del equipo; ver sección 5. |
| **Ofertas reales anonimizadas** | `data/ofertas/CT*.txt`, `data/ofertas/K*.txt` | **Dato medido (con empresas de prueba)** | Anuncios consultados el 2026-10-16 en Computrabajo Panamá y Konzerta Panamá conservando condiciones reales publicadas (incluyendo casos de salario no publicado y frases sujetas a la Ley 11 de 2005), con nombres de empresas de prueba según acuerdo de equipo (#41). URL y fecha de cada anuncio en la sección 3; las 10 ofertas sin traslado utilizable también se listan allí. |
| **Catálogo de cursos INADEH** | `data/cursos.csv` (`curso`, `area`, `horas` vs. `cupos`, `horario`) | **Mixto: títulos reales + Dato de prueba** | Nombres de cursos y áreas tomados de la oferta formativa pública del [INADEH](https://www.inadeh.edu.pa/) (`Hoja de Cálculo Básica`, `Operaciones de Caja Comercial`, `Auxiliar de Almacén, Bodega e Inventario`, `Manipulación Higiénica de Alimentos`, `Inglés Básico`). **Los cupos disponibles y horarios específicos son datos de prueba** rotulados como tales para evaluar las restricciones de horario nocturno y cupo del agente Formación (#14, #51, #53). |
| **Perfiles de jóvenes** | `data/perfiles/*.json`, `tests/fixtures/caso_yaritza.json` | **Dato de prueba (100% sintético)** | Diez perfiles sintéticos de 18 a 29 años, incluida Yaritza, sin datos personales reales (sin cédula, teléfono ni dirección exacta de personas reales). |
| **Duplicados entre portales** | `data/ofertas_prueba/duplicados.json` | **Dato de prueba (simulado)** | Dos pares de una misma vacante en formatos de portal distintos y un caso negativo (misma cadena, dos sucursales); ver sección 4. |
| **Ofertas reales ampliadas** | `data/ofertas_ampliadas/*.txt` | **Dato medido (con empresas de prueba)** | 43 anuncios consultados el 2026-10-17 en Computrabajo, Encuentra24, Indeed, LinkedIn, VacantesPanama.com y La Prensa (#223). Sin grabaciones todavía: fuera del catálogo y de `puente.cargar`; ver sección 7. |
| **CVs de jóvenes** | `data/cvs/` | **Dato de prueba (100% sintético)** | 10 CVs inventados en formatos de Panamá (texto, WhatsApp, formulario, PDF) con su perfil esperado al lado (#223); ver sección 7. |
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

**Ofertas sin traslado utilizable.** Diez ofertas no indican un lugar que exista en `traslados.csv`:
- CT02, CT06, CT11, CT16, CT17, CT22, CT24 y CT26 publican solo «Panamá, Panamá».
- K06 publica «Ciudad de Panamá».
- CT50 está en Capira, fuera de los 13 nodos medidos.

Sin un corregimiento con traslado medido, el Matching no puede comprobar el tope de 60 minutos ni el del pasaje, así que no las asigna a nadie. Se conservan como casos de extracción y de las reglas de la Ley 11 de 2005. CT03 también publica «Panamá, Panamá», pero su propio resumen ubica el trabajo en Juan Díaz; su ficha lo anota como zona de trabajo.

## 4. Datos enteramente sintéticos de prueba

- `perfiles/*.json`: diez personajes ficticios de 18 a 29 años, incluida Yaritza del [issue #3](https://github.com/HackathonFISC/equipo-01/issues/3). Horarios, expectativas salariales, presupuestos, experiencias y evidencias son inventados para la demo. Cada horario usa bloques `dias`, `hora_inicio`, `hora_fin` compatibles con `puente/schemas.py`. No hay datos de personas reales.
- `ofertas_prueba/duplicados.json`: dos pares de una misma vacante escritos en formatos de portal distintos y un caso negativo de la misma cadena en dos sucursales. Son **publicaciones simuladas**, no duplicados reales hallados entre Computrabajo y Konzerta. Empresa, sucursal, salario y horarios del fixture son datos de prueba. El caso negativo tiene una similitud de texto de 0.876, sobre el umbral de 0.85. Desde #89, Mercado no une dos publicaciones de la misma cadena si están en corregimientos distintos, así que ese caso ya no se fusiona ([#75](https://github.com/HackathonFISC/equipo-01/issues/75)).
- Los nombres «Empresa A» a «Empresa T» en `ofertas/` son alias de prueba añadidos por el equipo. No fueron publicados por los portales.
- En diez fichas de Computrabajo, el campo consultado solo decía «años de experiencia», sin un número visible. Se conservó así en las fichas; el equipo no infiere una cantidad mínima.

## 5. Taxonomía y decisiones del equipo

`taxonomia.csv` reúne 51 habilidades: 42 ligadas a tareas de las ofertas y los perfiles (#3) y 9 que añadió #51 para los cursos del INADEH (8 las usa hoy `cursos.csv`). Las 12 comunes conservan los sinónimos de ambas versiones. Las URIs y etiquetas de las 42 de #3 se verificaron con `language=es` en la [API oficial de ESCO](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api) el 2026-10-16. De las 9 de #51, siete URIs no existían y `excel` y `ventas` repetían las de `excel_basico` y `ventas_mostrador` ([#76](https://github.com/HackathonFISC/equipo-01/issues/76)). #101 corrigió cinco con URIs y etiquetas reales de ESCO, verificadas otra vez el 2026-10-16, y dejó sin URI `cocina`, `comunicacion_efectiva`, `excel` y `ventas` como supuesto del equipo. En total quedan 47 URIs verificadas y 4 vacías. Los sinónimos incluyen las frases literales de las ofertas, por ejemplo «Aseo general», «Cierre de ventas» o «Soporte al cliente», para que Mercado no descarte esas habilidades al normalizarlas. Los `skill_id` y sinónimos son una normalización local del equipo para empleos de entrada en Panamá; no son identificadores de ESCO ni prueban que un anuncio exija cada habilidad. ESCO aporta vocabulario, no vacantes panameñas.

## 6. Alcance de cada issue

La matriz de traslados, la tabla de alias y el catálogo de cursos vienen del [issue #51](https://github.com/HackathonFISC/equipo-01/issues/51) (secciones 1 y 2); los cupos y horarios del INADEH son datos de prueba rotulados como tales, según la decisión del 16 de octubre ([#41](https://github.com/HackathonFISC/equipo-01/issues/41)). Las ofertas, los perfiles, los duplicados de prueba y las 42 habilidades de tareas vienen del [issue #3](https://github.com/HackathonFISC/equipo-01/issues/3). Las fotos de volantes del plan inicial no se incluyeron en #3 por falta de material autorizado; #223 añade cinco volantes de ferias, anonimizados, como muestra de prueba (sección 7).

## 7. Ampliación de la muestra (#223): 43 ofertas y 10 CVs

Consulta del **2026-10-17**. Esta ampliación **no entra en la demo**: las carpetas nuevas quedan fuera de `data/ofertas/` y `data/perfiles/` a propósito.

- **Ofertas.** Mercado las lee con el LLM, y todavía no tienen grabación para el modo `reproducir`. En `data/ofertas/` aparecerían en el catálogo del tablero y en `puente.cargar`, y fallarían sin red. Para sumarlas:
  1. Copia los archivos a `data/ofertas/`.
  2. Corre `LLM_MODO=grabar uv run python -m puente.cargar`.
  3. Sube las grabaciones nuevas en el mismo PR.

  No corras `puente.cargar --ofertas data/ofertas_ampliadas` sobre la base de la demo. La carga quita de la base las ofertas que no vienen en la carpeta.
- **Perfiles de los CVs.** Cambiarían el grupo de candidatos de la demo: las listas cortas de Matching, la cifra del redactor y el tablero.

### 7.1 Ofertas reales: `data/ofertas_ampliadas/` (43 fichas)

Las 43 fichas usan el formato de la sección 3, con empresa de prueba («Empresa U» a «Empresa BK») y resumen propio de tareas. El puesto, la zona, el salario, el horario y los requisitos se copian como se publicaron. Los nombres reales de las empresas no se copiaron a las fichas, pero algunas URL de la tabla los llevan (E01, LI01 y PR01).

En los avisos de particulares no se guardó la calle ni el número de casa (E03 y E05). Tampoco se copiaron nombres de reclutadores, teléfonos, correos ni enlaces de WhatsApp.

**Fuentes:**
- Computrabajo Panamá: 18.
- Encuentra24 Panamá: 10.
- VacantesPanama.com: 7. Este sitio recopila anuncios de redes y WhatsApp; su URL es una página de categoría que rota, así que el aviso puede desaparecer.
- Indeed Panamá: 6.
- LinkedIn Panamá: 1.
- Una nota de La Prensa sobre una convocatoria.

**Accesos que no funcionaron:**
- Encuentra24 e Indeed devuelven 403 a las descargas automáticas y se leyeron en un navegador.
- Indeed no muestra la fecha de publicación.
- LinkedIn limita las consultas (429).
- Konzerta no devolvió ofertas sin JavaScript.
- La bolsa del MITRADEL solo enlaza un PDF de 2017.

**Por qué sirven:**
- Cubren sectores que la sección 3 no tenía: barbería, bar, hotel, farmacia, montacarga, aeropuerto, pasantías, construcción, transporte escolar, trabajo doméstico, carrito de comida y soporte técnico.
- Traen horarios nuevos: medio tiempo, nocturno, turnos rotativos, jornada partida, remoto e híbrido.
- Traen pagos distintos del salario mensual: por comisión, por encuesta, por día y asignación de pasantía.
- 25 traen frases para las reglas de la Ley 11 de 2005:
  - **Sexo:** «Sexo: Masculino», «Solo dama», «Saloneros (Caballeros)», «SE NECESITAN DAMAS».
  - **Edad:** el campo «Edad: entre X y Y años» de Computrabajo, «Mayor de 25 años».
  - **Foto:** «Foto reciente», «hoja de vida con foto adjunta».
  - **Apariencia:** «Buena presencia».
- Hay casos de borde a propósito:
  - IN03: pasantía cuyo rango de 18 a 25 años viene del programa público.
  - PR01: convocatoria solo para mujeres como acción de inclusión.
  - CT115: pide «nacionalidad panameña».


| ID | Portal | Puesto | Zona | URL de la publicación | Consulta |
| --- | --- | --- | --- | --- | --- |
| E01 | Encuentra24 Panamá | Operador de restaurante | Multiplaza, Vía Israel (San Francisco) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/operador-de-restaurante-ay-mi-negra/33137890) | 2026-10-17 |
| E02 | Encuentra24 Panamá | Mensajero (motorizado) | Arraiján | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/mensajero/33130014) | 2026-10-17 |
| E03 | Encuentra24 Panamá | Monitora de ruta escolar | San Antonio, Avenida Porras (corregimiento no indicado) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/se-necesitan-damas-para-monitora-de-ruta-escolar/33058978) | 2026-10-17 |
| E04 | Encuentra24 Panamá | Barista - salonero | Obarrio (Bella Vista) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/barista-salonero-con-experiencia/32494295) | 2026-10-17 |
| E05 | Encuentra24 Panamá | Muchacha de servicio y paseadora de perro | Bella Vista | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/muchacha-de-servicio-y-paseadora-de-perro/33127719) | 2026-10-17 |
| E06 | Encuentra24 Panamá | Saloneras | El Coco (La Chorrera), vía a Potrero Grande | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/saloneras/33030663) | 2026-10-17 |
| E07 | Encuentra24 Panamá | Recepcionista de hotel | El Cangrejo (Bella Vista) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/recepcionista-de-hotel/33137229) | 2026-10-17 |
| E08 | Encuentra24 Panamá | Manicurista | Costa del Este (Juan Díaz) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/buscamos-manicuristas/33109774) | 2026-10-17 |
| E09 | Encuentra24 Panamá | Ayudante de mecánico de equipo industrial | Cerro Patacón, Vía Centenario (Ancón) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/ayudante-de-mecanico-de-equipo-industrial/33090727) | 2026-10-17 |
| IN01 | Indeed Panamá | Customer Service Agent (Agente de Servicio al Cliente Bilingüe) | Las Mañanitas / Aeropuerto Internacional de Tocumen | [Enlace](https://pa.indeed.com/viewjob?jk=56153eeac74b37da) | 2026-10-17 |
| IN02 | Indeed Panamá | Encuestador/ Con O Sin Experiencia | La Chorrera (campo en Chorrera y Arraiján) | [Enlace](https://pa.indeed.com/viewjob?jk=910a3c9c2349f086) | 2026-10-17 |
| IN03 | Indeed Panamá | Programa de Pasantías - Panamá | Tocumen (centro operativo del banco) | [Enlace](https://pa.indeed.com/viewjob?jk=65bd692fda10222f) | 2026-10-17 |
| IN04 | Indeed Panamá | Técnico de Soporte TI y Hardware – Junior Avanzado – $800–$900 | San Francisco | [Enlace](https://pa.indeed.com/viewjob?jk=22bb38a457cd38f2) | 2026-10-17 |
| LI01 | LinkedIn Panamá | Customer Service Agent – English (PM Shifts) | Panamá, Panamá | [Enlace](https://pa.linkedin.com/jobs/view/customer-service-agent-%E2%80%93-english-pm-shifts-at-alorica-4470037654) | 2026-10-17 |
| IN05 | Indeed Panamá | Agente De Seguridad Femenina Y Masculino | Colón | [Enlace](https://pa.indeed.com/viewjob?jk=fd76027d9f4b2e3a) | 2026-10-17 |
| IN06 | Indeed Panamá | Auxiliar de Bodega/Picker | Panamá, Provincia de Panamá | [Enlace](https://pa.indeed.com/viewjob?jk=56e1910f27040c3f) | 2026-10-17 |
| E10 | Encuentra24 Panamá | Vendedor remoto (representante comercial independiente) | Remoto (oficina declarada en Vía Ricardo J. Alfaro, Betania) | [Enlace](https://www.encuentra24.com/panama-es/empleos-ofertas-de-trabajos/buscamos-vendedores-remotos-us-500-altas-comisiones/33129876) | 2026-10-17 |
| CT101 | Computrabajo Panamá | Despachador/a | San Miguelito (el anuncio no precisa corregimiento) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-despachadora-en-san-miguelito-BC922157CFDFE60361373E686DCF3405) | 2026-10-17 |
| CT102 | Computrabajo Panamá | Asistente de Operario de Máquina | San Miguelito (el anuncio no precisa corregimiento) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-asistente-de-operario-de-maquina-en-san-miguelito-BE1B7511147D2E4E61373E686DCF3405) | 2026-10-17 |
| CT103 | Computrabajo Panamá | Recepcionista | San Miguelito (el anuncio no precisa corregimiento) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-recepcionista-en-san-miguelito-3CAC06303C5610AE61373E686DCF3405) | 2026-10-17 |
| CT104 | Computrabajo Panamá | Auxiliar de farmacia - Turno rotativo | San Miguelito (el anuncio no precisa corregimiento) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-auxiliar-de-farmacia-turno-rotativo-en-san-miguelito-FAB2FEBC23EA810661373E686DCF3405) | 2026-10-17 |
| CT105 | Computrabajo Panamá | Chófer mensajero motociclista | Arraiján (el anuncio no precisa corregimiento) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-chofer-mensajero-motociclista-en-arraijan-0EF8C20C9C30822C61373E686DCF3405) | 2026-10-17 |
| CT106 | Computrabajo Panamá | Barbero - Tiempo completo en Barbería con nueva ubicación | Arraiján (el anuncio no precisa corregimiento) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-barbero-tiempo-completo-en-barberia-con-nueva-ubicacion-en-arraijan-F96739803694472561373E686DCF3405) | 2026-10-17 |
| CT107 | Computrabajo Panamá | Practicantes universitarios en Ingeniería mecánica, civil, eléctrico, industrial o carreras afines - Panamá Pacifico | Panamá Pacífico | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-practicanes-universitarios-en-ingenieria-mecanica-civil-electrico-industrial-o-carreras-afines-panama-pacifico-en-arraijan-2AAB6B02A9255DDB61373E686DCF3405) | 2026-10-17 |
| CT108 | Computrabajo Panamá | Ayudante de Plomería - Área de Panamá Oeste | Arraiján (trabajo de campo en el área de Panamá Oeste) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-ayudante-de-plomeria-area-de-panama-oeste-en-arraijan-1D19BCC01EB710A561373E686DCF3405) | 2026-10-17 |
| CT109 | Computrabajo Panamá | Operador de Campo: Lectura de Medidores y Distribución de Recibos - Panamá Oeste | La Chorrera (recorridos de campo en Panamá Oeste) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-operador-de-campo-lectura-de-medidores-y-distribucion-de-recibos-panama-oeste-en-la-chorrera-CD903B458AE230D261373E686DCF3405) | 2026-10-17 |
| CT110 | Computrabajo Panamá | Bartender | Parque Lefevre | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-bartender-en-panama-718F64F6CC345E9C61373E686DCF3405) | 2026-10-17 |
| CT111 | Computrabajo Panamá | Atención al Cliente Cajera Gelatería Cafetería - Con Experiencia | El Dorado (corregimiento de Betania) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-atencion-al-cliente-cajera-gelateria-cafeteria-con-experiencia-en-panama-45557830D176E97261373E686DCF3405) | 2026-10-17 |
| CT112 | Computrabajo Panamá | Agente de Seguridad para Costa del Este | Costa del Este | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-agente-de-seguridad-para-costa-del-este-en-panama-BE7AECA3EC255E2861373E686DCF3405) | 2026-10-17 |
| CT113 | Computrabajo Panamá | Agente de servicio al pasajero - Dominio del idioma inglés | Tocumen (Aeropuerto Internacional de Tocumen) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-agente-de-servicio-al-pasajero-dominio-del-idioma-ingles-en-panama-962B22353492CC5D61373E686DCF3405) | 2026-10-17 |
| CT114 | Computrabajo Panamá | Operador de montacarga - Las Mañanitas | Las Mañanitas | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-operador-de-montacarga-las-mananitas-en-panama-094194D3BE1EA47061373E686DCF3405) | 2026-10-17 |
| CT115 | Computrabajo Panamá | Auxiliar de tienda de tequeños | San Francisco | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-auxiliar-de-tienda-de-tequenos-en-panama-9D05062622956E9A61373E686DCF3405) | 2026-10-17 |
| CT116 | Computrabajo Panamá | Barista | Penonomé (Coclé) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-barista-en-penonome-7489D76EA5B4B60361373E686DCF3405) | 2026-10-17 |
| CT117 | Computrabajo Panamá | Ayudante General de Heladeria - Colón | Colón (heladerías en Colón según el texto; no se indica sucursal) | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-ayudante-general-de-heladeria-colon-en-panama-FCC9AE86E83460F461373E686DCF3405) | 2026-10-17 |
| CT118 | Computrabajo Panamá | Agente de Soporte Técnico y Atención al Cliente | Panamá, Panamá | [Enlace](https://pa.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-agente-de-soporte-tecnico-y-atencion-al-cliente-en-panama-B6E748919EBD5E8561373E686DCF3405) | 2026-10-17 |
| VP01 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Cajera / Salonero / Host / Pizzero / Cocinero / Sushero / Gerente/Capitán / Asistente administrativo | Provincia de Panamá | [Enlace](https://www.vacantespanama.com/panama/gastronomia/) | 2026-10-17 |
| VP02 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Saloneros (Caballeros) / Saloneras Damas | Ciudad de Panamá | [Enlace](https://www.vacantespanama.com/panama/gastronomia/) | 2026-10-17 |
| VP03 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Planchadora | Panamá Oeste | [Enlace](https://www.vacantespanama.com/panama/ayudante-general) | 2026-10-17 |
| VP04 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Cuidadora de Ancianos | Provincia de Panamá | [Enlace](https://www.vacantespanama.com/panama/ayudante-general) | 2026-10-17 |
| VP05 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Empleado(a) de Comida Rápida | Calle Aquilino de la Guardia (Bella Vista) | [Enlace](https://www.vacantespanama.com/panama/gastronomia/) | 2026-10-17 |
| VP06 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Operador de Carrito de Hotdog | Provincia de Panamá | [Enlace](https://www.vacantespanama.com/panama/ayudante-general) | 2026-10-17 |
| VP07 | VacantesPanama.com (anuncios recopilados de redes y WhatsApp) | Cajera | No indicado | [Enlace](https://www.vacantespanama.com/panama/cajeros/) | 2026-10-17 |
| PR01 | La Prensa (nota que transcribe la convocatoria) | Operadoras profesionales de bus / Personal de mantenimiento femenino | Calidonia (Zona Paga 5 de Mayo) | [Enlace](https://www.prensa.com/economia/mibus-abre-convocatoria-de-empleo-para-operadoras-y-personal-de-mantenimiento-femenino/) | 2026-10-17 |

### 7.2 Volantes de ferias

Los volantes de ferias quedan fuera del repo por derechos de las marcas; ver #223.

### 7.3 CVs sintéticos: `data/cvs/` (10 CVs y su perfil esperado)

Los 10 CVs son **datos de prueba (100 % sintéticos)**. Tienen formatos de hoja de vida comunes en Panamá:
- hoja de vida formal con cédula, estado civil y nacionalidad;
- mensaje de WhatsApp;
- formulario de bolsa de empleo;
- CV de redes con emojis;
- PDF exportado.

Las secciones y los datos personales que suelen llevar coinciden con lo que describen guías publicadas para Panamá, por ejemplo [I am Beezy (2026)](https://blog.iambeezy.app/es/hoja-de-vida-que-la-lean-panama-2026/) y [Panamá América](https://www.panamaamerica.com.pa/variedades/una-hoja-de-vida-exitosa-1138374): cédula, foto, fecha de nacimiento y estado civil son habituales aunque no se recomienden. Las personas son inventadas. Los CVs cubren los cinco corregimientos que no tenía ningún perfil: Vista Alegre, Betania, San Francisco, Pedregal y Ancón.

Cada `perfil_NN.json` lo escribió el equipo, no el LLM. `tests/test_datos_cvs.py` verifica tres cosas:
- la evidencia de cada habilidad aparece literal en su CV;
- cada habilidad existe en la taxonomía;
- la cédula y el teléfono inventados salen antes de llegar al LLM.
