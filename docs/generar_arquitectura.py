"""Genera docs/arquitectura.png: agentes, herramientas, flujo y punto de intervención humana. Issue #15.

Uso: uv run python docs/generar_arquitectura.py

Dibuja con Pillow (dependencia de fpdf2) a doble resolución y reduce, para que las líneas
salgan suaves. Cuando cambie el flujo de puente/graph.py, se edita este archivo y se vuelve
a correr; el PNG no se edita a mano.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SALIDA = Path(__file__).resolve().parent / "arquitectura.png"
ANCHO, ALTO = 2000, 1270
E = 2  # escala de dibujo

TINTA = "#1F2937"
TENUE = "#6B7280"
ROJO = "#C0282D"
ESTILOS = {
    "codigo": ("#E8F0FB", "#2B65B0"),
    "llm": ("#FFF4D6", "#D08A0E"),
    "humano": ("#FCE0E0", ROJO),
    "accion": ("#E6F4EA", "#2E7D46"),
    "panel": ("#F4F5F7", TENUE),
}
COLOR_INSIGNIA = {"llm": "#C9820A", "codigo": "#2B65B0", "humano": ROJO}

FUENTES = {
    False: ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc"),
    True: ("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Helvetica.ttc"),
}


def fuente(tamano: int, negrita: bool = False) -> ImageFont.FreeTypeFont:
    for nombre in FUENTES[negrita]:
        try:
            return ImageFont.truetype(nombre, tamano * E)
        except OSError:
            continue
    return ImageFont.load_default(size=tamano * E)


def e(*valores: float) -> tuple[int, ...]:
    return tuple(int(v * E) for v in valores)


def partir(texto: str, letra: ImageFont.FreeTypeFont, ancho: float) -> list[str]:
    lineas: list[str] = []
    for parrafo in texto.split("\n"):
        actual = ""
        for palabra in parrafo.split():
            prueba = f"{actual} {palabra}".strip()
            if letra.getlength(prueba) <= ancho * E or not actual:
                actual = prueba
            else:
                lineas.append(actual)
                actual = palabra
        lineas.append(actual)
    return lineas


def texto_centrado(d: ImageDraw.ImageDraw, x: float, y: float, texto: str, letra, color: str = TINTA) -> None:
    d.text(e(x, y), texto, font=letra, fill=color, anchor="mt")


def caja(d, x0, y0, x1, y1, estilo, titulo, cuerpo, insignia=None, tam_cuerpo=17):
    relleno, borde = ESTILOS[estilo]
    d.rounded_rectangle(e(x0, y0, x1, y1), radius=14 * E, fill=relleno, outline=borde, width=3 * E)
    if insignia:
        letra = fuente(15)
        ancho = letra.getlength(insignia) / E + 20
        d.rounded_rectangle(e(x0 + 14, y0 - 15, x0 + 14 + ancho, y0 + 13), radius=8 * E, fill=COLOR_INSIGNIA[estilo])
        d.text(e(x0 + 24, y0 - 1), insignia, font=letra, fill="white", anchor="lm")
    cx = (x0 + x1) / 2
    texto_centrado(d, cx, y0 + 20, titulo, fuente(24, True))
    letra = fuente(tam_cuerpo)
    y = y0 + 58
    for linea in partir(cuerpo, letra, x1 - x0 - 28):
        texto_centrado(d, cx, y, linea, letra)
        y += tam_cuerpo + 6


def flecha(d, puntos, color=TINTA, ancho=3):
    puntos_e = [e(x, y) for x, y in puntos]
    d.line(puntos_e, fill=color, width=ancho * E, joint="curve")
    (xa, ya), (xb, yb) = puntos[-2], puntos[-1]
    largo = 13
    if xa == xb:
        s = 1 if yb > ya else -1
        punta = [(xb, yb), (xb - 7, yb - s * largo), (xb + 7, yb - s * largo)]
    else:
        s = 1 if xb > xa else -1
        punta = [(xb, yb), (xb - s * largo, yb - 7), (xb - s * largo, yb + 7)]
    d.polygon([e(x, y) for x, y in punta], fill=color)


def rotulo(d, x, y, texto, color=TINTA, tamano=16, ancla="mm"):
    letra = fuente(tamano)
    caja_texto = d.textbbox(e(x, y), texto, font=letra, anchor=ancla)
    d.rectangle((caja_texto[0] - 4 * E, caja_texto[1] - 2 * E, caja_texto[2] + 4 * E, caja_texto[3] + 2 * E), fill="white")
    d.text(e(x, y), texto, font=letra, fill=color, anchor=ancla)


def dibujar() -> Image.Image:
    img = Image.new("RGB", e(ANCHO, ALTO), "white")
    d = ImageDraw.Draw(img)

    d.text(e(40, 26), "Puente: arquitectura de agentes", font=fuente(38, True), fill=TINTA)
    d.text(
        e(40, 80),
        "Reto 3, empleabilidad juvenil. LangGraph orquesta 6 agentes; el LLM trabaja en los bordes y el código decide en el centro.",
        font=fuente(21),
        fill=TENUE,
    )

    # Fila 1: entrada, guardrails, router y los dos agentes de entrada.
    caja(d, 40, 150, 290, 340, "codigo", "Entrada",
         "Oferta sucia, perfil (formulario más CV o entrevista de 5 preguntas) o consulta")
    caja(d, 340, 150, 690, 340, "codigo", "Guardrails",
         "#9, #56. Archivos y textos, edad de 18 a 29, audio, fuera de alcance; la inyección solo se marca. "
         "Quita cédula, teléfono y dirección. Rechazo: mensaje y fin")
    caja(d, 740, 150, 980, 340, "codigo", "Router",
         "#10. Según el tipo de entrada. Consulta: respuesta fija y fin")
    caja(d, 1060, 130, 1420, 335, "llm", "1 · Mercado",
         "#5, #43. Oferta validada. Edad y sexo BLOQUEAN (Ley 11 de 2005, arts. 1 y 2); otras causales, "
         "buena presencia, foto y estado civil ALERTAN. Duplicados por similitud; salario sin inventar. "
         "Bloqueada: alerta y fin",
         insignia="LLM extrae", tam_cuerpo=16)
    caja(d, 1060, 375, 1420, 545, "llm", "2 · Perfil",
         "#6, #44. Formulario validado en código. Cada habilidad con skill_id y frase literal de evidencia. "
         "Quita datos personales antes del LLM",
         insignia="LLM extrae", tam_cuerpo=16)

    flecha(d, [(290, 245), (340, 245)])
    flecha(d, [(690, 245), (740, 245)])
    flecha(d, [(980, 245), (1020, 245), (1020, 232), (1060, 232)])
    flecha(d, [(1020, 245), (1020, 460), (1060, 460)])
    rotulo(d, 1020, 200, "oferta", TENUE, 15)
    rotulo(d, 1020, 425, "perfil", TENUE, 15)

    # Fila 2: Matching, Auditor y Formación (de derecha a izquierda).
    caja(d, 1060, 605, 1420, 795, "codigo", "3 · Matching",
         "#4, #48. OR-Tools CP-SAT: traslado <= 60 min, pasaje <= 15 % del salario y <= presupuesto, "
         "horario sin cruce, salario >= mínimo del joven; hasta 3 por vacante",
         insignia="LLM solo explica", tam_cuerpo=16)
    caja(d, 620, 605, 990, 795, "codigo", "5 · Auditor",
         "#8, #50. Variantes de nombre, género y corregimiento (con el mismo traslado); línea base de 3 corridas",
         tam_cuerpo=16)
    caja(d, 180, 605, 550, 795, "codigo", "4 · Formación",
         "#14, #53. Brechas a cursos del INADEH con cupo, horario y traslado factibles (set cover voraz)",
         insignia="LLM solo explica", tam_cuerpo=16)

    flecha(d, [(1420, 232), (1470, 232), (1470, 700), (1420, 700)])
    flecha(d, [(1240, 545), (1240, 605)])
    flecha(d, [(1060, 700), (990, 700)])
    flecha(d, [(620, 700), (550, 700)])
    rotulo(d, 585, 672, "sin sesgo", TENUE, 15)

    # Aristas de regreso.
    flecha(d, [(805, 795), (805, 830), (1160, 830), (1160, 795)], color=ROJO)
    rotulo(d, 982, 852, "sesgo: recalcula una sola vez; si persiste, alerta al orientador", ROJO, 16)
    flecha(d, [(560, 945), (560, 900), (1340, 900), (1340, 795)], color=ROJO)
    rotulo(d, 950, 900, "quitar candidato: vuelve a Matching", ROJO, 16)

    # Fila 3: orientador, postulación y acciones.
    flecha(d, [(365, 795), (365, 945)])
    rotulo(d, 375, 870, "lista corta por vacante", TENUE, 15, ancla="lm")
    caja(d, 130, 945, 590, 1135, "humano", "interrupt(): Orientador",
         "#10, #11. Una vacante a la vez: aprobar, editar, quitar o rechazar. Confirma el consentimiento "
         "de cada joven. Nada sale sin su aprobación",
         insignia="HUMANO", tam_cuerpo=16)
    caja(d, 670, 945, 1000, 1135, "codigo", "6 · Postulación",
         "#7, #12. Solo tras la aprobación y el consentimiento; luego, la siguiente vacante",
         tam_cuerpo=16)
    caja(d, 1080, 945, 1420, 1135, "accion", "Acciones",
         "SQLite (postulaciones y decisiones)\nCV en PDF (fpdf2)\nTelegram (sin token: outbox/)\n.ics de la entrevista",
         tam_cuerpo=16)
    flecha(d, [(590, 1040), (670, 1040)])
    rotulo(d, 630, 1012, "aprobar", TENUE, 15)
    flecha(d, [(1000, 1040), (1080, 1040)])

    # Panel de herramientas y datos.
    relleno, borde = ESTILOS["panel"]
    d.rounded_rectangle(e(1530, 130, 1960, 1135), radius=14 * E, fill=relleno, outline=borde, width=3 * E)
    texto_centrado(d, 1745, 150, "Herramientas y datos", fuente(26, True))
    elementos = [
        ("traslados.csv", "minutos a hora pico y pasaje entre 13 nodos, medidos con fuente y fecha (Matching, Formación, Auditor)"),
        ("taxonomia.csv", "21 habilidades con skill_id y URI de ESCO (Mercado, Perfil)"),
        ("cursos.csv", "9 cursos con títulos reales del INADEH; cupos y horarios de prueba (Formación)"),
        ("Reglas Ley 11 de 2005", "en código, sin LLM (Mercado)"),
        ("Checkpointer SQLite", "guarda cada caso por thread_id y lo reanuda tras el interrupt()"),
        ("Decisiones", "cada nodo agrega una Decision con su razón; tabla decisiones en SQLite con #12"),
        ("LangSmith", "trazas del grafo y del LLM, sin entradas ni salidas"),
    ]
    y = 205
    titulo, cuerpo = fuente(20, True), fuente(16)
    for nombre, detalle in elementos:
        d.text(e(1555, y), nombre, font=titulo, fill=TINTA)
        y += 28
        for linea in partir(detalle, cuerpo, 380):
            d.text(e(1555, y), linea, font=cuerpo, fill=TENUE)
            y += 22
        y += 18

    # Leyenda y notas.
    d.text(e(40, 1160), "Leyenda", font=fuente(26, True), fill=TINTA)
    x = 200
    for estilo, etiqueta in (
        ("codigo", "Código determinístico (sin LLM)"),
        ("llm", "Agente con LLM en el borde"),
        ("humano", "Intervención humana"),
        ("accion", "Acciones reales"),
    ):
        relleno, borde = ESTILOS[estilo]
        d.rounded_rectangle(e(x, 1162, x + 40, 1190), radius=6 * E, fill=relleno, outline=borde, width=3 * E)
        letra = fuente(18)
        d.text(e(x + 52, 1176), etiqueta, font=letra, fill=TINTA, anchor="lm")
        x += 52 + letra.getlength(etiqueta) / E + 45
    notas = (
        "El LLM solo extrae (Mercado, Perfil) o redacta la explicación (Matching, Formación); hoy Gemini, Claude Opus 5.5 con #45. "
        "Flechas rojas: aristas de regreso.",
        "Grafo de main: fase 1 de #10, con el flujo completo, el interrupt() y el checkpointer; los nodos de los 6 agentes "
        "son stubs hasta la fase 2. Los números 1 a 6 son los agentes.",
    )
    for i, nota in enumerate(notas):
        d.text(e(40, 1208 + i * 26), nota, font=fuente(16), fill=TENUE)

    # Paleta de 256 colores: el PNG pesa menos de la mitad y no se nota en el texto.
    return img.resize((ANCHO, ALTO), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=256)


if __name__ == "__main__":
    dibujar().save(SALIDA, optimize=True)
    print(f"Diagrama escrito en {SALIDA}")
