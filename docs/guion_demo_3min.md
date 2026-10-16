# Guion y Video Demo de 3 Minutos · Puente (Reto 3: Empleabilidad)

> **Issue:** #16  
> **Duración máxima:** `03:00` (180 segundos)  
> **Caso guía:** **Yaritza** (20 años, reside en La Chorrera, estudia de noche 18:00–22:00, sin CV formal, atiende la fonda de su mamá)  
> **Interfaz en vivo:** `uv run uvicorn app.server:app --reload --port 8000` → `http://127.0.0.1:8000`

---

## Estructura de 7 Pasos (Cronograma de Grabación)

| Paso | Tiempo | Pantalla / Pestaña del Tablero | Qué se muestra en vivo | Guion hablado (Locución) |
| :--- | :---: | :--- | :--- | :--- |
| **1. El Problema Real en Panamá** | `0:00 – 0:20` (20 s) | Cabecera y KPIs de `http://127.0.0.1:8000` | Mostrar los 4 indicadores principales y seleccionar a **Yaritza (`PERFIL-YARITZA`)**. | *"En Panamá, miles de jóvenes de 18 a 28 años quedan fuera del mercado formal por tres barreras invisibles: no tienen CV tradicional, enfrentan avisos que filtran ilegalmente por edad o sexo, o reciben vacantes cuyo traslado en hora pico supera una hora y consume más del 15 % del sueldo. Presentamos **Puente**."* |
| **2. Vigilancia Legal (Ley 11 de 2005)** | `0:20 – 0:50` (30 s) | **Pestaña 2: Ofertas Bloqueadas y Alertas (Ley 11)** | Mostrar `OF-BLOQ-EDAD-04` (*"edad entre 18 a 25 años"*) y `OF-BLOQ-SEXO-05` (*"personal masculino únicamente"*), más la alerta preventiva por *"buena presencia"* en `OF-VISTAALEGRE-02`. | *"Primero, el **Agente de Mercado** y sus reglas determinísticas aplican la **Ley 11 de 2005 de Panamá**: bloquean automáticamente ofertas que exigen rangos de edad o sexo antes de entrar al emparejamiento, y marcan alertas preventivas cuando aparece 'buena presencia' o solicitud de foto, citando siempre la frase literal y el artículo legal."* |
| **3. Agente de Perfil sin CV y sin PII** | `0:50 – 1:15` (25 s) | **Pestaña 1: Lista Corta** (Caja *Habilidades Cubiertas y Evidencia Literal*) | Señalar las 3 habilidades extraídas de la fonda familiar de Yaritza (`atencion_cliente`, `manejo_caja`, `inventario`) con su cita textual exacta. | *"Segundo, el **Agente de Perfil** entrevista a Yaritza sin exigirle CV, elimina cualquier dato personal identificable y traduce su trabajo diario en la fonda de su mamá a habilidades de la taxonomía ESCO respaldadas por evidencia literal."* |
| **4. Solver Determinístico CP-SAT y Ruta INADEH** | `1:15 – 1:50` (35 s) | **Pestaña 1: Lista Corta** (Cajas *Brechas INADEH* y *Ofertas Descartadas*) | Señalar el puntaje CP-SAT, el descarte de `OF-TOCUMEN-03` (*"traslado 95 min > 60 min; pasaje mensual $103.40 > 15% del salario"*) y el curso gratuito sugerido en **INADEH La Chorrera** para cubrir `excel_basico`. | *"Tercero, ningún LLM decide quién califica. Nuestro solver entero **OR-Tools CP-SAT** descarta la vacante de Tocumen porque desde La Chorrera toma 95 minutos en hora pico y el pasaje supera el 15 % del salario, priorizando vacantes en La Chorrera y Vista Alegre. Y donde a Yaritza le falta `excel_basico`, el algoritmo **Set Cover voraz** recomienda un curso gratuito en INADEH La Chorrera a 15 minutos de su casa."* |
| **5. Agente Auditor Contrafactual** | `1:50 – 2:15` (25 s) | **Pestaña 5: Agente Auditor (Sesgo)** | Mostrar el `100%` de consistencia en las 4 variantes de nombre/género con corregimiento fijo, y hacer clic en **Simular Inyección de Sesgo** y luego restaurar. | *"Cuarto, antes de llegar al orientador, el **Agente Auditor** ejecuta pruebas contrafactuales cambiando nombre y género pero manteniendo fijo el corregimiento de residencia. Si detecta cualquier variación en habilidades o puntaje, activa la arista de regreso a Matching."* |
| **6. Humano en el Ciclo (`interrupt()`) y Postulación** | `2:15 – 2:45` (30 s) | **Pestaña 1: Lista Corta** → **Pestaña 4: Embudo** | Escribir observación en la tarjeta de Yaritza (`OF-CHORRERA-01`), hacer clic en **✓ Aprobar y Enviar**, y mostrar en **Pestaña 4** el cambio a `enviado` y los 3 archivos en `outbox/` (`cv_*.pdf`, `telegram_*.txt`, `entrevista_*.ics`). | *"Quinto, el grafo se detiene en `interrupt()`. El orientador humano revisa el desglose y aprueba el envío. Solo tras esa firma humana se dispara el **Agente de Postulación**, que genera el CV en PDF sin PII, envía la lista corta a la empresa y crea la invitación de calendario `.ics`."* |
| **7. Trazabilidad Completa y Cierre** | `2:45 – 3:00` (15 s) | **Pestaña 3: Trazas de Decisiones** | Filtrar por `orientador_humano` y `postulacion` mostrando las entradas en `decisiones`. | *"Cada decisión —desde los guardrails hasta la aprobación humana— queda registrada y auditable. **Puente**: inteligencia artificial multiagente con optimización determinística y supervisión humana para el empleo juvenil justo en Panamá."* |

---

## Comandos para Preparar y Grabar la Demo

```bash
# 1. Levantar el servidor web del tablero
uv run uvicorn app.server:app --host 127.0.0.1 --port 8000

# 2. Abrir en el navegador
# http://127.0.0.1:8000
```

## Enlace al Video Grabado (Hora 21)

- **Estado del guion:** Completado y sincronizado con las 5 pestañas del tablero web.
- **URL del video (≤ 3:00 min):** *(Se insertará aquí y en `README.md` al finalizar la grabación en la Hora 21).*
