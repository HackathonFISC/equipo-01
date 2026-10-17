# CVs sintéticos (#223)

Diez CVs de personas **inventadas**, escritos por el equipo con los formatos que circulan en Panamá: hoja de vida
formal con cédula y estado civil, mensaje de WhatsApp, formulario de bolsa de empleo, CV de redes con emojis y
PDF exportado de Word o Canva. Cédulas `N-000-00NN`, teléfonos `6000-00NN`, correos `@ejemplo.com` y
direcciones «Ficticia» son inventados a propósito para probar la limpieza de PII de Perfil.

Cada `cv_NN_*` tiene al lado su `perfil_NN.json`: es lo que el agente Perfil debería extraer, con evidencia
literal del CV. No es salida del LLM; lo escribió el equipo y lo verifica `tests/test_datos_cvs.py`. No están en
`data/perfiles/` a propósito: sumarlos cambia el grupo de candidatos de la demo (listas cortas de Matching, la
cifra del redactor, el tablero). Para sumarlos después del video basta copiarlos allí y revisar
`tests/test_redactor.py`, que cuenta 10 perfiles. Los PDF salen de `generar_pdf.py` (`uv run python data/cvs/generar_pdf.py`).

| CV | Formato | Persona (ficticia) | Qué ejercita |
|---|---|---|---|
| `cv_11_vista_alegre.txt` | Hoja de vida formal | 19 años, Vista Alegre, primer empleo | Cédula, fecha de nacimiento, estado civil y dirección con etiqueta |
| `cv_12_betania.pdf` | PDF de una página | 23 años, Pueblo Nuevo, bilingüe | Alias Pueblo Nuevo → Betania; turnos rotativos; teléfono con +507 |
| `cv_13_san_francisco.pdf` | PDF de estudiante | 21 años, San Francisco, UTP | `horario_clases` de mañana; trabajo informal (reparar celulares) |
| `cv_14_pedregal_whatsapp.txt` | Mensaje de WhatsApp | 27 años, Pedregal, madre | Sin CV formal; hasta 9.° grado; horario corto por los hijos; emojis |
| `cv_15_ancon.txt` | CV escolar corto | 18 años, Ancón, IPT | Edad mínima; práctica profesional como única experiencia |
| `cv_16_la_chorrera.pdf` | PDF formal | 25 años, Barrio Balboa | Alias Barrio Balboa → La Chorrera; dirección en PDF; licencia |
| `cv_17_san_miguelito_formulario.txt` | Formulario de bolsa de empleo | 22 años, Belisario Porras, ngäbe | Primaria completa; idioma materno; campo «Sexo» |
| `cv_18_juan_diaz.pdf` | PDF formal | 29 años, Juan Díaz, padre | Edad máxima; estudia de noche (`horario_clases`); cobros y caja |
| `cv_19_tocumen_redes.txt` | CV de redes con emojis | 20 años, Mañanitas | Alias Mañanitas → Tocumen; ventas informales; clases los sábados |
| `cv_20_calidonia.pdf` | PDF de una página | 24 años, Santa Ana | Alias Santa Ana → Calidonia; farmacia; sin cédula en el CV |

Nada de esto se manda a una empresa: el CV ciego de Postulación sale del `Perfil`, no de estos archivos.
