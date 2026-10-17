"""Genera `tirilla_sintetica.png`: tirilla de matrícula FICTICIA para la demo del horario vivo (#164).

Todo es inventado: la universidad, la estudiante, la cédula (8-000-0000), la carrera y las aulas. El
documento lleva la marca «DOCUMENTO SINTÉTICO · SIN VALIDEZ» y no copia logos ni formatos oficiales.
La cédula ficticia está a propósito: sirve para probar que ningún dato personal sale del lector.

Horario que contiene (lo que el lector debe devolver):
- Lunes y miércoles: Matemática Básica I 18:30-20:00 y Contabilidad I 20:00-21:30.
- Martes y jueves: Inglés Técnico I 18:30-20:00 e Informática Básica 20:00-21:30.
- Viernes: Expresión Oral y Escrita 18:30-21:30.
- Parciales del 19/10/2026 al 23/10/2026, finales del 07/12/2026 al 12/12/2026; el período termina el 12/12/2026.

Ya convertido a `horario_clases`: lunes a viernes 18:30-21:30, el mismo de `data/perfiles/yaritza.json`.

La grabación del LLM (`data/grabaciones/` y `evals/grabaciones/`) depende de los bytes exactos del PNG:
si se regenera la imagen (otra fuente u otra versión de Pillow cambian los bytes), hay que volver a grabar.

    uv run python tests/fixtures/generar_tirilla_sintetica.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DESTINO = Path(__file__).with_name("tirilla_sintetica.png")
ANCHO, ALTO = 1600, 1000

FUENTES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
FUENTES_NEGRITA = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

COLUMNAS = ("Cód.", "Asignatura", "Grupo", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Aula")
ANCHOS = (80, 330, 90, 150, 150, 150, 150, 150, 90, 140)
FILAS = (
    ("0701", "Matemática Básica I", "1N1", "18:30-20:00", "", "18:30-20:00", "", "", "", "Aula 3-102"),
    ("0702", "Contabilidad I", "1N1", "20:00-21:30", "", "20:00-21:30", "", "", "", "Aula 3-104"),
    ("0703", "Inglés Técnico I", "1N1", "", "18:30-20:00", "", "18:30-20:00", "", "", "Lab. Idiomas"),
    ("0704", "Informática Básica", "1N1", "", "20:00-21:30", "", "20:00-21:30", "", "", "Lab. 2"),
    ("0705", "Expresión Oral y Escrita", "1N1", "", "", "", "", "18:30-21:30", "", "Aula 3-110"),
)


def _fuente(rutas: tuple[str, ...], tamano: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for ruta in rutas:
        if Path(ruta).is_file():
            return ImageFont.truetype(ruta, tamano)
    return ImageFont.load_default(size=tamano)


def generar(destino: Path = DESTINO) -> Path:
    imagen = Image.new("RGB", (ANCHO, ALTO), (250, 250, 247))
    dibujo = ImageDraw.Draw(imagen)
    normal, chica = _fuente(FUENTES, 22), _fuente(FUENTES, 19)
    negrita, titulo = _fuente(FUENTES_NEGRITA, 22), _fuente(FUENTES_NEGRITA, 30)
    tinta, gris, rojo = (25, 25, 25), (110, 110, 110), (175, 30, 30)

    dibujo.text((60, 40), "UNIVERSIDAD TECNOLÓGICA FICTICIA", font=titulo, fill=tinta)
    dibujo.text((60, 82), "Centro Regional Ficticio de Panamá Oeste · Secretaría Académica", font=normal, fill=gris)
    dibujo.text((60, 125), "COMPROBANTE DE MATRÍCULA · II SEMESTRE 2026", font=negrita, fill=tinta)
    dibujo.rectangle((1040, 36, 1545, 112), outline=rojo, width=3)
    dibujo.text((1060, 48), "DOCUMENTO SINTÉTICO", font=titulo, fill=rojo)
    dibujo.text((1060, 84), "Demo de Puente · sin validez", font=chica, fill=rojo)

    dibujo.text((60, 180), "Estudiante: Yaritza Ficticia", font=normal, fill=tinta)
    dibujo.text((600, 180), "Cédula: 8-000-0000", font=normal, fill=tinta)
    dibujo.text((1000, 180), "Recibo N.º 000000", font=normal, fill=tinta)
    dibujo.text((60, 215), "Carrera: Técnico en Administración (ficticia)", font=normal, fill=tinta)
    dibujo.text((600, 215), "Turno: Nocturno", font=normal, fill=tinta)
    dibujo.text((1000, 215), "Período: 10/08/2026 al 12/12/2026", font=normal, fill=tinta)

    x0, y = 60, 280
    alto_fila = 52
    xs = [x0]
    for ancho in ANCHOS:
        xs.append(xs[-1] + ancho)
    dibujo.rectangle((x0, y, xs[-1], y + alto_fila), fill=(225, 230, 238))
    for i, nombre in enumerate(COLUMNAS):
        dibujo.text((xs[i] + 10, y + 14), nombre, font=negrita, fill=tinta)
    for fila in FILAS:
        y += alto_fila
        for i, valor in enumerate(fila):
            dibujo.text((xs[i] + 10, y + 14), valor, font=normal, fill=tinta)
    y_fin = y + alto_fila
    for x in xs:
        dibujo.line((x, 280, x, y_fin), fill=gris, width=1)
    for k in range(len(FILAS) + 2):
        dibujo.line((x0, 280 + k * alto_fila, xs[-1], 280 + k * alto_fila), fill=gris, width=1)

    y = y_fin + 40
    dibujo.text((60, y), "Total de asignaturas: 5 · Créditos: 17", font=normal, fill=tinta)
    y += 60
    dibujo.text((60, y), "Calendario académico del semestre", font=negrita, fill=tinta)
    y += 38
    for linea in (
        "Exámenes parciales: del 19/10/2026 al 23/10/2026.",
        "Exámenes finales: del 07/12/2026 al 12/12/2026.",
        "Último día del período: 12/12/2026.",
    ):
        dibujo.text((80, y), "• " + linea, font=normal, fill=tinta)
        y += 34
    dibujo.text(
        (60, ALTO - 60),
        "Generado para pruebas de software. Personas, universidad y datos son ficticios.",
        font=chica,
        fill=gris,
    )
    imagen.save(destino, format="PNG", optimize=True)
    return destino


if __name__ == "__main__":
    print(generar())
