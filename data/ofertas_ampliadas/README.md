# Ofertas reales ampliadas (#223)

Aquí hay 43 anuncios reales de Panamá, consultados el 2026-10-17, en el mismo formato de ficha que `data/ofertas/`. Cada ficha usa una empresa de prueba («Empresa U» a «Empresa BK»), y su URL está en `data/FUENTES.md`, sección 7.1.

| Prefijo | Portal | Fichas |
|---|---|---|
| `CT101`–`CT118` | Computrabajo Panamá | 18 |
| `E01`–`E10` | Encuentra24 Panamá | 10 |
| `IN01`–`IN06` | Indeed Panamá | 6 |
| `LI01` | LinkedIn Panamá | 1 |
| `VP01`–`VP07` | VacantesPanama.com (avisos de redes y WhatsApp) | 7 |
| `PR01` | Nota de La Prensa sobre una convocatoria | 1 |

**Por qué no están en `data/ofertas/`.** Mercado las lee con el LLM y todavía no tienen grabación. En `data/ofertas/` aparecerían en el catálogo del tablero y en `puente.cargar`, y en modo `reproducir`, sin red, fallarían.

Para sumarlas después de la demo:

1. Cópialas a `data/ofertas/`.
2. Corre `LLM_MODO=grabar uv run python -m puente.cargar`. Usa unas 43 llamadas.
3. Sube las grabaciones nuevas de `data/grabaciones/` en el mismo PR.

No uses `puente.cargar --ofertas data/ofertas_ampliadas` sobre la base de la demo: la carga quita de la base las ofertas que no vienen en esa carpeta.

**Qué agregan respecto de las 20 de `data/ofertas/`:**
- **Sectores nuevos:**
  - Servicios: barbería, manicura, bar de casino, hotel, farmacia, transporte escolar, trabajo doméstico.
  - Logística y operación: aeropuerto, montacarga, lectura de medidores, plomería, mecánica industrial.
  - Comida y ventas: carrito de comida, encuestas, ventas remotas.
  - Oficina y tecnología: pasantías, soporte técnico.
- **Horarios y pagos:**
  - Jornada partida (E03), nocturna (E06) y turnos rotativos.
  - Medio tiempo, remoto (E10) e híbrido (IN04).
  - Pagos que no son un salario mensual fijo: comisión (E08), por encuesta (IN02), por día (VP06) y asignación de pasantía (IN03).
- **Ley 11 de 2005.** 25 fichas traen frases para las reglas:
  - Sexo: CT108, CT109, E03, E05, E06, VP02, VP03, VP04, IN05, PR01.
  - Edad: el campo «Edad: entre X y Y años» de Computrabajo (10 fichas CT); además IN03, IN05, PR01, VP04 y VP07.
  - Foto: CT110, E05, E07, IN05, VP01.
  - Buena presencia o presentación: CT111, E06 y VP01; «presentación personal» en CT103, E01, E03, E04 y VP02.
- **Casos de borde:**
  - IN03: pasantía con un rango de edad que viene del programa público.
  - PR01: convocatoria solo para mujeres como acción de inclusión.
  - CT115: pide nacionalidad panameña.
  - E10: trabajo remoto, sin traslado.
  - Fuera de los 13 nodos de `traslados.csv`: CT116 (Penonomé), CT117 e IN05 (Colón).
