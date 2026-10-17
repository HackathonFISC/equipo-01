# Fuentes, Metodología de Medición y Clasificación de Datos (`data/`)

Fecha de consulta y consolidación: **2026-10-16** (Panamá).

Este documento distingue explícitamente entre **Dato medido**, **Dato de prueba** y **Supuesto del equipo** para garantizar trazabilidad ante el jurado del Hackathon FISC AI Agents 2026 (Reto 3: Empleabilidad).

---

## 1. Clasificación de fuentes

| Categoría | Archivos / Parámetros | Naturaleza | Fuente y metodología |
| :--- | :--- | :---: | :--- |
| **Traslados y tarifas** | `data/traslados.csv` (`minutos_hora_pico`, `pasaje_ida_usd`, `modo`) | **Dato medido** | Tiempos en transporte público consultados para hora pico de entrada laboral (07:00 a.m.) en Google Maps Transit / Moovit (2026-10-16) y tarifas vigentes de [MiBus](https://www.mibus.com.pa/guia-del-usuario/) (US$0.25 troncal), [Metro de Panamá](https://www.elmetrodepanama.com/) (US$0.35 Línea 1, US$0.50 Línea 2, US$0.85 trasbordo L1+L2) y rutas interurbanas ATTT Panamá Oeste–Albrook (US$1.00 Arraiján, US$1.25 Vista Alegre, US$1.50 Expreso La Chorrera, US$0.50 rutas internas). |
| **Taxonomía de habilidades** | `data/taxonomia.csv` (`skill_id`, `nombre_es`, `sinonimos`, `uri_esco`) | **Estándar oficial ESCO + Supuestos del equipo** | 47 identificadores de habilidades laborales enlazados a URIs reales verificadas en la API de la [Clasificación Europea ESCO v1.2.1](https://esco.ec.europa.eu/es) (`language=es`, consultado el 2026-10-16) con su etiqueta oficial en español; y 4 identificadores generales/alias operativos (`cocina`, `comunicacion_efectiva`, `excel`, `ventas`) con `uri_esco` vacía documentados como **supuesto del equipo** (#76) para no duplicar ni inventar URIs. |
| **Ofertas reales anonimizadas** | `data/ofertas/CT*.txt`, `data/ofertas/K*.txt` | **Dato medido (con empresas de prueba)** | Anuncios consultados el 2026-10-16 en Computrabajo Panamá y Konzerta Panamá conservando condiciones reales publicadas (incluyendo casos de salario no publicado y frases sujetas a la Ley 11 de 2005), con nombres de empresas de prueba según acuerdo de equipo (#41). |
| **Catálogo de cursos INADEH** | `data/cursos.csv` (`curso`, `area`, `horas` vs. `cupos`, `horario`) | **Mixto: títulos reales + Dato de prueba** | Nombres de cursos y áreas tomados de la oferta formativa pública del [INADEH](https://www.inadeh.edu.pa/) (`Hoja de Cálculo Básica`, `Operaciones de Caja Comercial`, `Auxiliar de Almacén, Bodega e Inventario`, `Manipulación Higiénica de Alimentos`, `Inglés Básico`). **Los cupos disponibles y horarios específicos son datos de prueba** rotulados como tales para evaluar las restricciones de horario nocturno y cupo del agente Formación (#14, #51, #53). |
| **Perfiles de jóvenes** | `data/perfiles/*.json`, `tests/fixtures/caso_yaritza.json` | **Dato de prueba (100% sintético)** | Perfiles sintéticos sin datos personales reales (sin cédula, teléfono ni dirección exacta de personas reales). |
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
