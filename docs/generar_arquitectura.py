"""Genera docs/arquitectura.png: los 14 nodos y las aristas de `construir_grafo()` de puente/graph.py. Issue #15.

Uso: uv run python docs/generar_arquitectura.py

Los nodos (NODOS), las aristas (ARISTAS) y los finales (FIN) son datos: `tests/test_arquitectura.py` los compara con
`construir_grafo().get_graph()` y falla si el grafo cambia y este archivo no. El PNG lleva una huella de esos datos
(`puente-huella`) y la prueba también falla si el PNG no se regeneró después de tocarlos. Cuando cambie el flujo de
puente/graph.py se edita este archivo, se vuelve a correr y se sube el PNG; el PNG no se edita a mano.

Dibuja con Pillow (dependencia de fpdf2, sin dependencias nuevas) a doble resolución y reduce, para que las líneas
salgan suaves.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

SALIDA = Path(__file__).resolve().parent / "arquitectura.png"
ANCHO, ALTO = 2390, 1570
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

# Cuadrícula: 6 columnas y 3 filas de cajas del mismo tamaño.
COL = [185, 585, 985, 1385, 1785, 2185]
FILA = {1: 285, 2: 585, 3: 885}
W_CAJA, H_CAJA = 310, 224

# Los 14 nodos de `construir_grafo()` más `__start__` (la entrada). `fin`: el flujo puede terminar ahí.
NODOS: dict[str, dict] = {
    "__start__": dict(
        col=0, fila=1, estilo="codigo", titulo="Entrada", fin=True,
        cuerpo="Oferta sucia, perfil (formulario, CV o entrevista de 5 preguntas), consulta o respuesta de la empresa. "
        "Sin nada que revisar: fin",
    ),
    "guardrails": dict(
        col=1, fila=1, estilo="codigo", titulo="Guardrails", fin=True,
        cuerpo="Archivos y textos, edad de 18 a 29, audio, fuera de alcance; la inyección solo se marca. "
        "Quita cédula, teléfono y dirección. Rechazo: mensaje y fin",
    ),
    "router": dict(
        col=2, fila=1, estilo="codigo", titulo="Router",
        cuerpo="Según el tipo de entrada: oferta, perfil, consulta o respuesta de la empresa (esta entra sin Guardrails)",
    ),
    "mercado": dict(
        col=3, fila=1, estilo="llm", titulo="Mercado", insignia="LLM extrae",
        cuerpo="Oferta validada. Edad y sexo BLOQUEAN (Ley 11 de 2005, arts. 1 y 2); buena presencia, foto y estado civil "
        "ALERTAN. Duplicados por similitud; salario sin inventar",
    ),
    "alerta_oferta": dict(
        col=4, fila=1, estilo="codigo", titulo="Alerta de oferta", fin=True,
        cuerpo="Oferta bloqueada: no entra al Matching; la respuesta lleva la frase y la regla de cada alerta. Fin",
    ),
    "consulta": dict(
        col=0, fila=2, estilo="codigo", titulo="Consulta", fin=True,
        cuerpo="Consulta libre: respuesta fija de fuera de alcance, sin LLM",
    ),
    "avisos": dict(
        col=1, fila=2, estilo="codigo", titulo="Avisos",
        cuerpo="Avisa al joven (outbox/ o Telegram) y al orientador. Después se recalcula en Matching",
    ),
    "evento_empresa": dict(
        col=2, fila=2, estilo="codigo", titulo="Evento de la empresa",
        cuerpo="La empresa rechaza o cubre una vacante en su lista ciega: se excluye la pareja y el caso se reacomoda",
    ),
    "perfil": dict(
        col=3, fila=2, estilo="llm", titulo="Perfil", insignia="LLM extrae",
        cuerpo="Formulario validado en código. Cada habilidad con skill_id y frase literal de evidencia. "
        "Quita datos personales antes del LLM",
    ),
    "matching": dict(
        col=4, fila=2, estilo="codigo", titulo="Matching",
        cuerpo="OR-Tools CP-SAT: traslado hasta 60 min, pasaje hasta 15 % del salario y del presupuesto, horario sin "
        "cruce, salario mínimo; hasta 3 candidatos por plaza y 2 vacantes por joven",
    ),
    "sin_matches": dict(
        col=5, fila=2, estilo="codigo", titulo="Sin matches", fin=True,
        cuerpo="Sin candidatos viables: la respuesta trae el motivo de cada descarte. Fin",
    ),
    "postulacion": dict(
        col=1, fila=3, estilo="codigo", titulo="Postulación", fin=True,
        cuerpo="Solo tras la aprobación y el consentimiento: CV ciego y carta en PDF, .ics de la entrevista, mensaje a la "
        "empresa y SQLite. Luego, la siguiente vacante",
    ),
    "aprobacion": dict(
        col=2, fila=3, estilo="humano", titulo="Aprobación humana", insignia="HUMANO", fin=True,
        cuerpo="interrupt() del orientador, vacante por vacante: aprobar, editar, quitar o rechazar. Confirma el "
        "consentimiento de cada joven. Nada sale sin su aprobación",
    ),
    "formacion": dict(
        col=3, fila=3, estilo="codigo", titulo="Formación",
        cuerpo="Brechas a cursos del INADEH con cupo, horario sin cruce y traslado factible (set cover voraz). "
        "Explicación con LLM opcional, apagada",
    ),
    "auditor": dict(
        col=4, fila=3, estilo="codigo", titulo="Auditor",
        cuerpo="Repite con variantes de nombre, género y corregimiento. Recalcula una sola vez; si la diferencia persiste, "
        "la pasa al orientador",
    ),
}
# No es un nodo del grafo: lo que hace Postulación hacia afuera.
ACCIONES = dict(
    col=0, fila=3, estilo="accion", titulo="Acciones reales",
    cuerpo="SQLite (postulaciones y decisiones)\nCV ciego y carta en PDF (fpdf2)\n.ics de la entrevista\n"
    "Telegram (sin token: outbox/)",
)

PANEL = (
    ("Datos", (
        ("traslados.csv", "minutos a hora pico y pasaje entre 13 nodos (91 filas), medidos con fuente y fecha "
         "(Matching, Formación, Auditor)"),
        ("taxonomia.csv", "51 habilidades con skill_id y URI de ESCO (Mercado, Perfil)"),
        ("cursos.csv", "9 cursos con títulos reales del INADEH; cupos y horarios de prueba (Formación)"),
    )),
    ("Reglas y LLM", (
        ("Ley 11 de 2005", "reglas en código, sin LLM: edad y sexo bloquean, lo demás alerta (Mercado)"),
        ("puente.llm", "capa común del LLM: Claude Opus 5.5 con modos vivo, grabar y reproducir; con las grabaciones, "
         "la demo corre sin red ni claves"),
        ("Guardrails", "tamaños, edad de 18 a 29, audio, inyección y datos personales, antes de cualquier agente"),
    )),
    ("Estado y salidas", (
        ("Checkpointer SQLite", "guarda cada caso por thread_id y lo reanuda tras el interrupt()"),
        ("Decisiones", "una Decision por nodo, con su razón, en el estado y en la tabla decisiones de SQLite"),
        ("outbox/ y Telegram", "CV, carta, .ics y mensajes; sin token quedan en outbox/"),
    )),
)

LEYENDA = (
    ("codigo", "Código determinístico (sin LLM)"),
    ("llm", "Agente con LLM en el borde"),
    ("humano", "Intervención humana"),
    ("accion", "Acciones reales"),
)
NOTAS = (
    "El LLM solo extrae (Mercado, Perfil). Matching explica con una plantilla y Formación solo con un LLM opcional, apagado. "
    "Flechas rojas: regresos. «fin»: el flujo puede terminar en ese nodo.",
    "Dibujado a partir de construir_grafo(): 14 nodos. Este archivo lo regenera (uv run python docs/generar_arquitectura.py) "
    "y una prueba compara sus nodos y aristas con el grafo real.",
)


@dataclass(frozen=True)
class Arista:
    origen: str
    destino: str
    etiqueta: str = ""
    color: str = TINTA


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


# ---------------------------------------------------------------- geometría de la cuadrícula


def caja_de(datos: dict) -> tuple[float, float, float, float]:
    x, y = COL[datos["col"]], FILA[datos["fila"]]
    return x - W_CAJA / 2, y - H_CAJA / 2, x + W_CAJA / 2, y + H_CAJA / 2


def borde(nodo: str, lado: str, desplazamiento: float = 0) -> tuple[float, float]:
    """Punto del borde de un nodo: izq, der, arr o aba, corrido `desplazamiento` a lo largo del borde."""
    x0, y0, x1, y1 = caja_de(ACCIONES if nodo == "acciones" else NODOS[nodo])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return {"izq": (x0, cy + desplazamiento), "der": (x1, cy + desplazamiento),
            "arr": (cx + desplazamiento, y0), "aba": (cx + desplazamiento, y1)}[lado]


def centro_x(nodo: str) -> float:
    return COL[(ACCIONES if nodo == "acciones" else NODOS[nodo])["col"]]


def pasillo(fila_arriba: int) -> float:
    """Altura de la franja libre entre una fila de cajas y la siguiente."""
    return (FILA[fila_arriba] + H_CAJA / 2 + FILA[fila_arriba + 1] - H_CAJA / 2) / 2


def recta(origen: str, lado_o: str, destino: str, lado_d: str, desp_o: float = 0, desp_d: float | None = None):
    return [borde(origen, lado_o, desp_o), borde(destino, lado_d, desp_o if desp_d is None else desp_d)]


# Cada arista del grafo real, con su recorrido (lista de puntos) y dónde va su rótulo.
# `ARISTAS` y `FIN` juntos son las 31 aristas de `construir_grafo().get_graph().edges`.
def _rutas() -> list[tuple[Arista, list[tuple[float, float]], tuple[float, float] | None]]:
    p1, p2 = pasillo(1), pasillo(2)
    x_rojo = (caja_de(NODOS["matching"])[2] + caja_de(NODOS["sin_matches"])[0]) / 2  # entre Matching y Sin matches
    x_regreso = (caja_de(NODOS["formacion"])[2] + caja_de(NODOS["auditor"])[0]) / 2  # entre Formación y Auditor
    y_abajo = caja_de(NODOS["aprobacion"])[3] + 95
    r = []

    def hay(a, ruta, etiqueta_en=None):
        r.append((a, ruta, etiqueta_en))

    mitad = lambda ruta: ((ruta[0][0] + ruta[-1][0]) / 2, (ruta[0][1] + ruta[-1][1]) / 2)  # noqa: E731

    # Entrada y Guardrails
    ruta = recta("__start__", "der", "guardrails", "izq")
    hay(Arista("__start__", "guardrails"), ruta)
    y_alto = caja_de(NODOS["router"])[1] - 28
    x_e, x_r = centro_x("__start__") + 40, centro_x("router")
    ruta = [(x_e, caja_de(NODOS["__start__"])[1]), (x_e, y_alto), (x_r, y_alto), (x_r, caja_de(NODOS["router"])[1])]
    hay(Arista("__start__", "router", "evento de la empresa: entra sin Guardrails"), ruta, ((x_e + x_r) / 2, y_alto))
    ruta = recta("guardrails", "der", "router", "izq")
    hay(Arista("guardrails", "router"), ruta)

    # Router
    ruta = recta("router", "der", "mercado", "izq")
    hay(Arista("router", "mercado", "oferta"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = [borde("router", "aba", 120), (centro_x("router") + 120, p1), (centro_x("perfil") - 60, p1),
            (centro_x("perfil") - 60, caja_de(NODOS["perfil"])[1])]
    hay(Arista("router", "perfil", "perfil"), ruta, ((ruta[0][0] + ruta[2][0]) / 2, p1))
    ruta = [borde("router", "aba", -120), (centro_x("router") - 120, p1), (centro_x("consulta"), p1),
            (centro_x("consulta"), caja_de(NODOS["consulta"])[1])]
    hay(Arista("router", "consulta", "consulta"), ruta, ((ruta[0][0] + ruta[2][0]) / 2, p1))
    ruta = recta("router", "aba", "evento_empresa", "arr")
    hay(Arista("router", "evento_empresa", "responde la empresa"), ruta, (centro_x("router"), p1))

    # Mercado
    ruta = recta("mercado", "der", "alerta_oferta", "izq")
    hay(Arista("mercado", "alerta_oferta", "bloqueada"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = [borde("mercado", "aba", 90), (centro_x("mercado") + 90, p1), (centro_x("matching") - 100, p1),
            (centro_x("matching") - 100, caja_de(NODOS["matching"])[1])]
    hay(Arista("mercado", "matching", "no bloqueada"), ruta, ((ruta[0][0] + ruta[2][0]) / 2, p1))

    # Perfil y Matching
    hay(Arista("perfil", "matching"), recta("perfil", "der", "matching", "izq", -20))
    ruta = recta("matching", "der", "sin_matches", "izq", -20)
    hay(Arista("matching", "sin_matches", "nadie viable"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = recta("matching", "aba", "auditor", "arr", -60)
    hay(Arista("matching", "auditor", "variantes"), ruta, (centro_x("matching"), p2))
    ruta = recta("auditor", "arr", "matching", "aba", 60)
    hay(Arista("auditor", "matching", "recalcula una vez", ROJO), ruta, (ruta[0][0] + 12, p2 + 17))

    # Auditor, Formación y Aprobación
    ruta = recta("auditor", "izq", "formacion", "der")
    hay(Arista("auditor", "formacion", "continuar"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = [borde("auditor", "izq", -50), (x_regreso, borde("auditor", "izq", -50)[1]),
            (x_regreso, borde("perfil", "der", 40)[1]), borde("perfil", "der", 40)]
    hay(Arista("auditor", "perfil", "a Perfil", ROJO), ruta, (x_regreso, p2 + 28))
    ruta = recta("formacion", "izq", "aprobacion", "der")
    hay(Arista("formacion", "aprobacion", "lista corta"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = recta("aprobacion", "izq", "postulacion", "der", -30)
    hay(Arista("aprobacion", "postulacion", "aprobar"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = recta("postulacion", "der", "aprobacion", "izq", 30)
    hay(Arista("postulacion", "aprobacion", "siguiente"), ruta, (mitad(ruta)[0], ruta[0][1] + 14))
    x_a, y_a = centro_x("aprobacion"), caja_de(NODOS["aprobacion"])[3]
    ruta = [(x_a - 60, y_a), (x_a - 60, y_a + 36), (x_a + 60, y_a + 36), (x_a + 60, y_a)]
    hay(Arista("aprobacion", "aprobacion", "rechazar: siguiente vacante"), ruta, (x_a, y_a + 56))
    ruta = [borde("aprobacion", "aba", 110), (x_a + 110, y_abajo), (x_rojo, y_abajo), (x_rojo, borde("matching", "der", 50)[1]),
            borde("matching", "der", 50)]
    hay(Arista("aprobacion", "matching", "quitar a un candidato: vuelve a Matching", ROJO), ruta,
        ((ruta[1][0] + ruta[2][0]) / 2, y_abajo))

    # Respuesta de la empresa
    ruta = recta("evento_empresa", "izq", "avisos", "der")
    hay(Arista("evento_empresa", "avisos", "cambió"), ruta, (mitad(ruta)[0], ruta[0][1] - 14))
    ruta = recta("evento_empresa", "aba", "aprobacion", "arr")
    hay(Arista("evento_empresa", "aprobacion", "sin cambios"), ruta, (centro_x("evento_empresa"), p2 + 26))
    ruta = [borde("avisos", "aba"), (centro_x("avisos"), p2), (centro_x("matching") - 140, p2),
            (centro_x("matching") - 140, caja_de(NODOS["matching"])[3])]
    hay(Arista("avisos", "matching", "después, recalcula"), ruta, ((centro_x("avisos") + centro_x("evento_empresa")) / 2 + 320, p2))

    return r


_RUTAS = _rutas()
ARISTAS: list[Arista] = [a for a, _, _ in _RUTAS]
FIN: list[str] = sorted(nombre for nombre, datos in NODOS.items() if datos.get("fin"))


def aristas_del_grafo() -> set[tuple[str, str]]:
    """Las aristas que dibuja este archivo, en el formato de `get_graph().edges` (los finales van a `__end__`)."""
    return {(a.origen, a.destino) for a in ARISTAS} | {(n, "__end__") for n in FIN}


def huella() -> str:
    """Resumen de todo lo que se dibuja: si cambia algo de los datos, el PNG guardado ya no corresponde."""
    datos = [NODOS, ACCIONES, [(a.origen, a.destino, a.etiqueta, a.color) for a in ARISTAS], FIN, PANEL, LEYENDA, NOTAS]
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- dibujo


def caja(d, x0, y0, x1, y1, estilo, titulo, cuerpo, insignia=None, tam_cuerpo=16, fin=False):
    relleno, borde_color = ESTILOS[estilo]
    d.rounded_rectangle(e(x0, y0, x1, y1), radius=14 * E, fill=relleno, outline=borde_color, width=3 * E)
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
    if fin:
        marca = fuente(14, True)
        d.rounded_rectangle(e(x1 - 62, y1 - 34, x1 - 12, y1 - 12), radius=10 * E, fill=TINTA)
        d.text(e(x1 - 37, y1 - 23), "fin", font=marca, fill="white", anchor="mm")


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


def rotulo(d, x, y, texto, color=TINTA, tamano=14, ancla="mm"):
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
        "Reto 3, empleabilidad juvenil. LangGraph orquesta 14 nodos; el LLM trabaja en los bordes y el código decide en el centro.",
        font=fuente(21),
        fill=TENUE,
    )

    # Flechas primero, para que las cajas queden encima.
    for arista, ruta, _ in _RUTAS:
        flecha(d, ruta, arista.color)
    for nodo, datos in NODOS.items():
        caja(d, *caja_de(datos), datos["estilo"], datos["titulo"], datos["cuerpo"], datos.get("insignia"), fin=datos.get("fin", False))
    caja(d, *caja_de(ACCIONES), ACCIONES["estilo"], ACCIONES["titulo"], ACCIONES["cuerpo"])
    flecha(d, [borde("postulacion", "izq"), borde("acciones", "der")])
    for arista, _, rotulo_en in _RUTAS:
        if arista.etiqueta and rotulo_en:
            rotulo(d, *rotulo_en, arista.etiqueta, arista.color)

    # Panel de herramientas y datos.
    y0, y1 = 1135, 1405
    relleno, borde_color = ESTILOS["panel"]
    d.rounded_rectangle(e(40, y0, ANCHO - 40, y1), radius=14 * E, fill=relleno, outline=borde_color, width=3 * E)
    d.text(e(64, y0 + 14), "Herramientas, datos y estado", font=fuente(24, True), fill=TINTA)
    ancho_col = (ANCHO - 80 - 48) / len(PANEL)
    for i, (_, filas) in enumerate(PANEL):
        x = 64 + i * ancho_col
        y = y0 + 60
        for titulo, texto in filas:
            d.text(e(x, y), titulo, font=fuente(18, True), fill=TINTA)
            y += 26
            for linea in partir(texto, fuente(16), ancho_col - 40):
                d.text(e(x, y), linea, font=fuente(16), fill=TENUE)
                y += 22
            y += 10

    # Leyenda y notas.
    x = 40
    d.text(e(x, 1447), "Leyenda", font=fuente(26, True), fill=TINTA, anchor="lm")
    x += 130
    for estilo, etiqueta in LEYENDA:
        relleno, borde_color = ESTILOS[estilo]
        d.rounded_rectangle(e(x, 1433, x + 40, 1461), radius=6 * E, fill=relleno, outline=borde_color, width=3 * E)
        letra = fuente(18)
        d.text(e(x + 52, 1447), etiqueta, font=letra, fill=TINTA, anchor="lm")
        x += 52 + letra.getlength(etiqueta) / E + 45
    for i, nota in enumerate(NOTAS):
        d.text(e(40, 1492 + i * 28), nota, font=fuente(16), fill=TENUE)

    # Paleta de 256 colores: el PNG pesa menos de la mitad y no se nota en el texto.
    return img.resize((ANCHO, ALTO), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=256)


def guardar(ruta: Path = SALIDA) -> Path:
    info = PngInfo()
    info.add_text("puente-huella", huella())
    dibujar().save(ruta, optimize=True, pnginfo=info)
    return ruta


if __name__ == "__main__":
    print(f"Diagrama escrito en {guardar()}")
