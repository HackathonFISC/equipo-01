"""CV adaptado a la oferta en PDF con fpdf2, solo con habilidades con evidencia. Issue #7."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fpdf import FPDF

from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil

# Cédulas panameñas: 8-123-4567, 08 123 4567 (provincias 1 a 13), PE-1-234, E-8-12345.
# La provincia numérica se limita a 1-13 y no se lee dentro de una fecha u hora ("20-10-2026",
# "2026-10-20 10:00"). Una fecha ambigua como 08-10-2026 se trata como cédula: ante la duda, se omite.
_PROVINCIA = r"(?:0?[1-9]|1[0-3])"
_PATRON_CEDULA = re.compile(
    r"\b(?i:PE|E|N|AV|PI)-\d{1,4}-\d{1,6}\b"
    r"|\b(?:PE|E|N|AV|PI)\s\d{1,4}\s\d{1,6}\b"
    rf"|(?<![\d:/.-])\b{_PROVINCIA}[\s-]\d{{1,4}}[\s-]\d{{1,6}}\b(?![:/])"
)
# Teléfonos panameños con o sin +507, con separador o sin él: celulares de 8 dígitos que
# empiezan en 6 (6123-4567, 61234567) y fijos de 7 (233-4567, 2334567). Las cifras sueltas que
# no encajan ("salario 1200 1500") no se tocan.
_NUMERO_TEL = r"(?:6\d{3}|[2-57]\d{2})[\s-]?\d{4}(?!\d)"
_PATRON_TELEFONO = re.compile(
    rf"(?:\+\s?507[\s-]?|\(507\)\s?|(?<!\w)507[\s-]){_NUMERO_TEL}"
    rf"|(?<!\w){_NUMERO_TEL}"
)
# Dirección: heurística mínima. Palabra de domicilio seguida de número ("calle 5", "casa 12-B",
# "apto 3A") o una línea "dirección: ...". No reconoce direcciones sin número.
_PATRON_DIRECCION = re.compile(
    r"\b(?i:calle|casa|apto|apt|apartamento|edificio|avenida|av|lote|manzana|mz)\.?\s*"
    r"(?:(?i:n[oº°]\.?)\s*|#\s*)?[A-Za-z]?\d+[\w-]*"
    r"|\b(?i:direcci[oó]n)\s*:\s*[^\n,;]+"
)

# Las fuentes core de fpdf2 (Helvetica) solo admiten latin-1: sustituimos lo más común.
_SUSTITUCIONES = {
    "•": "-",  # viñeta
    "–": "-",
    "—": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
}


def limpiar_pii(texto: str) -> str:
    """Quita cédula, teléfono y dirección de un texto libre (defensa extra: Perfil ya los quita)."""
    for patron in (_PATRON_CEDULA, _PATRON_TELEFONO, _PATRON_DIRECCION):
        texto = patron.sub("[dato omitido]", texto)
    return texto


def a_latin1(texto: str) -> str:
    """Pasa el texto a algo que Helvetica pueda dibujar; lo que no cabe queda como '?'."""
    for origen, destino in _SUSTITUCIONES.items():
        texto = texto.replace(origen, destino)
    return texto.encode("latin-1", errors="replace").decode("latin-1")


def _campo(habilidad, nombre: str) -> str:
    """Lee un campo de una habilidad que puede ser un modelo o un dict."""
    valor = habilidad.get(nombre) if isinstance(habilidad, dict) else getattr(habilidad, nombre, None)
    return str(valor or "")


def skill_id_de(habilidad) -> str:
    """``skill_id`` de una habilidad, sea ``HabilidadPerfil`` o dict."""
    return _campo(habilidad, "skill_id")


def evidencia_de(habilidad) -> str:
    """Frase que evidencia la habilidad (vacía si no hay), sea ``HabilidadPerfil`` o dict."""
    return _campo(habilidad, "evidencia")


def habilidades_para_cv(perfil: Perfil, oferta: Oferta) -> list[HabilidadPerfil]:
    """Solo habilidades con evidencia no vacía; primero las que pide la oferta.

    Cada habilidad puede ser un modelo o un dict (ver ``skill_id_de`` y ``evidencia_de``).
    """
    con_evidencia = [h for h in perfil.habilidades if evidencia_de(h).strip()]
    pedidas = set(oferta.habilidades)
    pedidas_primero = [h for h in con_evidencia if skill_id_de(h) in pedidas]
    resto = [h for h in con_evidencia if skill_id_de(h) not in pedidas]
    return pedidas_primero + resto


def _nombre_seguro(valor: str) -> str:
    return re.sub(r"[^\w-]", "_", str(valor))


_ETIQUETA_DIAS = {
    "lunes": "lunes", "martes": "martes", "miercoles": "miércoles", "jueves": "jueves",
    "viernes": "viernes", "sabado": "sábado", "domingo": "domingo",
}
_ORDEN_DIAS = tuple(_ETIQUETA_DIAS)


def formatear_horario(h: Horario) -> str:
    """Texto legible de un bloque semanal: "lunes a viernes, 08:00-12:00"."""
    dias = [d for d in _ORDEN_DIAS if d in h.dias] + [d for d in h.dias if d not in _ORDEN_DIAS]
    indices = [_ORDEN_DIAS.index(d) for d in dias if d in _ORDEN_DIAS]
    consecutivos = (
        len(dias) == len(indices) and len(dias) >= 3
        and indices == list(range(indices[0], indices[0] + len(indices)))
    )
    nombres = [_ETIQUETA_DIAS.get(d, d) for d in dias]
    if consecutivos:
        texto_dias = f"{nombres[0]} a {nombres[-1]}"
    elif len(nombres) > 1:
        texto_dias = ", ".join(nombres[:-1]) + f" y {nombres[-1]}"
    else:
        texto_dias = nombres[0]
    return f"{texto_dias}, {h.hora_inicio}-{h.hora_fin}"


_ACENTOS = {"a": "aáàäâ", "e": "eéèëê", "i": "iíìïî", "o": "oóòöô", "u": "uúùüû", "n": "nñ"}
_TERMINOS_GENERO = re.compile(
    r"\b(?:femenin[oa]s?|masculin[oa]s?|mujer(?:es)?|hombres?|var[oó]n(?:es)?|g[eé]nero)\b",
    re.IGNORECASE,
)
# Estado civil. "madre soltera" sale entera; "madre" o "mamá" sola no ("la fonda de mi mamá").
_ESTADO_CIVIL = re.compile(
    r"\b(?:madre|padre|mam[aá]|pap[aá])\s+solter[oa]s?\b"
    r"|\b(?:solter|casad|divorciad|viud|separad|concubin)[oa]s?\b"
    r"|\bconvivientes?\b"
    r"|\buni[oó]n\s+(?:libre|de\s+hecho|consensual|estable)\b",
    re.IGNORECASE,
)
# "unido/a" solo cuando dice el estado civil ("está unida", "estado civil: unido"): en "equipo
# unido" o "Estados Unidos" no es un dato de la persona. Se conserva el verbo.
_UNIDO = re.compile(r"\b(soy|es|est[aá]|estoy|vive|vivo|civil\s*:?)\s+unid[oa]\b", re.IGNORECASE)
_EN_PAREJA = re.compile(r"\b(vivo|vive|estoy|est[aá]|estado civil\s*:?)\s+en pareja\b", re.IGNORECASE)
# Pareja e hijos (#148): "su esposo me apoya", "unida con su pareja", "tengo 2 hijos", "mamá de dos
# niñas". "mi mamá" o "la fonda de mi mamá" no se tocan: solo cuenta con hijos o pareja detrás.
_CANTIDAD = r"(?:\d+|un[oa]?|dos|tres|cuatro|cinco)"
_HIJOS = r"(?:hij[oa]s?|beb[eé]s?|nen[eao]s?)"
_FAMILIA = re.compile(
    r"\bunid[oa]\s+(?:con|a)\s+(?:mi|su)\s+(?:pareja|esposo|esposa|marido|novio|novia)\b"
    r"|\b(?:mi|su)\s+(?:esposo|esposa|marido|pareja|novio|novia)\b"
    rf"|\b(?:mis?|sus?)\s+{_HIJOS}\b"  # "cuidar a mis hijos", "y mi hija"
    rf"|\b(?:tengo|tiene)\s+(?:{_CANTIDAD}\s+|\w+\s+)?(?:{_HIJOS}|niñ[oa]s)\b"  # "tengo hijos pequeños", "tengo un bebé"
    rf"|\b(?:mam[aá]|madre|pap[aá]|padre)\s+de\s+{_CANTIDAD}\s+(?:hij|niñ|nen)[oa]s?\b"
    r"|\b(?:madre|padre)\s+de\s+familia\b",
    re.IGNORECASE,
)
_EMBARAZO = re.compile(r"\b(?:embarazad[oa]s?|embarazos?|encinta|gestante)\b", re.IGNORECASE)
# Edad en palabras, de 18 a 29 (la demo es con jóvenes de 18 a 29), solo seguida de "años" o
# después de "edad": "dos años de experiencia" no es una edad y no se toca.
_EDAD_PALABRAS = (
    r"(?:die(?:ci|z\s+y\s+)(?:ocho|nueve)|veinte"
    r"|veinti(?:[uú]n|uno|d[oó]s|tr[eé]s|cuatro|cinco|s[eé]is|siete|ocho|nueve))"
)
_EDAD_EN_PALABRAS = re.compile(
    rf"\bedad\s*(?:de|:)?\s*{_EDAD_PALABRAS}\b(?:\s*a[nñ]os)?|\b{_EDAD_PALABRAS}\s+a[nñ]os\b",
    re.IGNORECASE,
)
# Año o fecha de nacimiento: "nacida en 2006", "nació el 3 de mayo de 2006", "nacida el 20/11/2006",
# "año de nacimiento: 2006". Con etiqueta se ciega siempre, sin mirar la edad del perfil (#148).
_FECHA_NUM = r"(?:\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}|(?:19|20)\d{2}[/.-]\d{1,2}[/.-]\d{1,2})(?!\d)"
_NACIMIENTO = re.compile(
    r"\bnac(?:id[oa]s?|i[oó]|í)\s+(?:en|el)\s+(?:el\s+)?(?:a[nñ]o\s+)?"
    rf"(?:{_FECHA_NUM}|(?:(?:\d{{1,2}}\s+de\s+)?[a-záéíóú]+\s+del?\s+)?(?:19|20)\d{{2}}\b)"
    # "Nací en La Chorrera en 2006": el lugar entre medio también sale (dice de dónde es).
    r"|\bnac(?:id[oa]s?|i[oó]|í)\s+en\s+[^.,;\n]{0,40}?\b(?:19|20)\d{2}\b"
    r"|\b(?:a[nñ]o|fecha)\s+de\s+nacimiento\b(?:\s*(?::|es|fue)?\s*"
    r"(?:(?:\d{1,2}\s+de\s+)?[a-záéíóú]+\s+del?\s+(?:19|20)\d{2}\b|\d[\d/.-]*\d))?",
    re.IGNORECASE,
)
# Una fecha numérica sin etiqueta solo se trata como nacimiento si corresponde a la edad declarada
# del perfil, con ±1 año porque la edad del formulario no siempre cuadra al día. Así se conservan
# las fechas de experiencia laboral, que son recientes.
_FECHA_NUMERICA = re.compile(
    r"(?<!\d)(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2})(?!\d)"
)
# Sexo dicho de forma coloquial. Solo en el mensaje del orientador: en la evidencia literal del
# joven "chica" suele ser otra cosa ("manejo la caja chica de la fonda").
_TERMINOS_SEXO_COLOQUIALES = re.compile(
    r"\b(?:chic[oa]s?|muchach[oa]s?|se[nñ]oritas?)\b", re.IGNORECASE
)


def _patron_literal(texto: str) -> str:
    """Patrón que encuentra ``texto`` sin importar mayúsculas ni tildes (Colón = colon)."""
    piezas = []
    for c in texto.strip():
        base = c.lower()
        for plano, variantes in _ACENTOS.items():
            if base in variantes:
                piezas.append(f"[{variantes}{variantes.upper()}]")
                break
        else:
            piezas.append(re.escape(c))
    return "".join(piezas)


def cegar(texto: str, perfil: Perfil, *, del_orientador: bool = False, hoy: date | None = None) -> str:
    """Quita de un texto que va a la empresa lo que identifica al joven por lo que no es su trabajo.

    Reemplaza por "[dato omitido]" el corregimiento del perfil, su edad ("20 años", "edad 20"),
    la edad en palabras de 18 a 29 ("veinte años"), el año de nacimiento ("nacida en 2006"), el
    estado civil ("soltera", "madre soltera", "está unida", "separada"), fechas numéricas que
    corresponden a su edad, el embarazo y los términos de género. Se aplica a cada línea que sale
    hacia la empresa (CV y mensaje): la evidencia es
    literal y puede traer "tengo 20 años y vivo en La Chorrera".

    La edad en cifras sigue siendo solo la exacta del perfil, para no borrar "2 años de
    experiencia" ni "trabajé 3 años". ``del_orientador=True`` es para el texto libre del
    orientador: quita además "chica", "muchacha" y parecidos, que en la evidencia del joven
    suelen ser otra cosa ("la caja chica"). `hoy` permite fijar la fecha de referencia en pruebas.
    """
    patrones = []
    if str(perfil.corregimiento or "").strip():
        patrones.append(rf"(?<!\w){_patron_literal(perfil.corregimiento)}(?!\w)")
    if perfil.edad is not None:
        patrones.append(rf"\bedad\s*(?:de|:)?\s*{perfil.edad}\b(?:\s*a[nñ]os)?")
        patrones.append(rf"\b{perfil.edad}\s*a[nñ]os\b")
    for patron in patrones:
        texto = re.sub(patron, "[dato omitido]", texto, flags=re.IGNORECASE)
    texto = _UNIDO.sub(lambda m: f"{m.group(1)} [dato omitido]", texto)
    texto = _EN_PAREJA.sub(lambda m: f"{m.group(1)} [dato omitido]", texto)
    terminos = [_NACIMIENTO, _FAMILIA, _ESTADO_CIVIL, _EMBARAZO, _EDAD_EN_PALABRAS, _TERMINOS_GENERO]
    if del_orientador:
        terminos.append(_TERMINOS_SEXO_COLOQUIALES)
    for patron in terminos:
        texto = patron.sub("[dato omitido]", texto)
    if perfil.edad is not None:
        fecha_actual = hoy or date.today()

        def ocultar_fecha(coincidencia: re.Match[str]) -> str:
            partes = re.split(r"[/-]", coincidencia.group())
            anio, mes, dia = (int(p) for p in partes) if len(partes[0]) == 4 else (
                int(partes[2]), int(partes[1]), int(partes[0])
            )
            try:
                nacimiento = date(anio, mes, dia)
            except ValueError:
                return coincidencia.group()
            edad_en_fecha = fecha_actual.year - nacimiento.year - (
                (fecha_actual.month, fecha_actual.day) < (nacimiento.month, nacimiento.day)
            )
            return "[dato omitido]" if abs(edad_en_fecha - perfil.edad) <= 1 else coincidencia.group()

        texto = _FECHA_NUMERICA.sub(ocultar_fecha, texto)
    return texto


def contenido_cv(perfil: Perfil, oferta: Oferta) -> list[str]:
    """Líneas del CV ciego para la empresa (issue #12).

    Lleva nombre, puesto, habilidades con evidencia y disponibilidad. Nunca corregimiento,
    edad, género, foto ni estado civil: la empresa decide por lo que la persona sabe hacer.
    """
    lineas = [perfil.nombre, f"Postula a: {oferta.puesto} en {oferta.empresa}", "Habilidades"]
    # Todo lo que sale del perfil pasa por cegar(): la evidencia es literal y puede traer la edad
    # o el lugar donde vive la persona.
    habilidades = habilidades_para_cv(perfil, oferta)
    if habilidades:
        for h in habilidades:
            lineas.append(skill_id_de(h))
            lineas.append(cegar(f"Evidencia: {evidencia_de(h).strip()}", perfil))
    else:
        lineas.append("Sin habilidades con evidencia registrada.")
    if perfil.horario_libre:
        disponibilidad = "; ".join(formatear_horario(h) for h in perfil.horario_libre)
    else:
        disponibilidad = "por confirmar"
    lineas.append(f"Disponibilidad: {disponibilidad}")
    return lineas


def generar_cv(perfil: Perfil, oferta: Oferta, destino_dir: Path) -> Path:
    """Escribe ``cv_<perfil_id>_<oferta_id>.pdf`` en ``destino_dir`` y devuelve la ruta.

    CV ciego: nombre ficticio, puesto al que se postula, habilidades con evidencia y
    disponibilidad. Sin corregimiento, edad, género, cédula ni teléfono.
    """
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)
    ruta = destino_dir / f"cv_{_nombre_seguro(perfil.id)}_{_nombre_seguro(oferta.id)}.pdf"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def linea(texto: str, *, estilo: str = "", tamano: int = 11, alto: float = 6) -> None:
        pdf.set_font("Helvetica", estilo, tamano)
        pdf.multi_cell(0, alto, a_latin1(limpiar_pii(texto)), new_x="LMARGIN", new_y="NEXT")

    contenido = contenido_cv(perfil, oferta)
    linea(contenido[0], estilo="B", tamano=18, alto=10)
    pdf.ln(3)
    linea(contenido[1], estilo="B", tamano=12)
    pdf.ln(3)
    linea(contenido[2], estilo="B", tamano=13, alto=8)
    for texto in contenido[3:-1]:
        if texto.startswith("Evidencia: ") or texto.startswith("Sin habilidades"):
            linea(texto)
            pdf.ln(1)
        else:
            linea(texto, estilo="B")
    pdf.ln(2)
    linea(contenido[-1], estilo="B")
    pdf.ln(4)
    linea(
        "CV generado por Puente a partir de lo que la persona contó; "
        "solo incluye habilidades con evidencia.",
        estilo="I",
        tamano=8,
        alto=4,
    )
    pdf.output(str(ruta))
    return ruta
