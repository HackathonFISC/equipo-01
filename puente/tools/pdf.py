"""CV adaptado a la oferta en PDF con fpdf2, solo con habilidades con evidencia. Issue #7."""
from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

from puente.schemas import HabilidadPerfil, Oferta, Perfil

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


def generar_cv(perfil: Perfil, oferta: Oferta, destino_dir: Path) -> Path:
    """Escribe ``cv_<perfil_id>_<oferta_id>.pdf`` en ``destino_dir`` y devuelve la ruta.

    El CV lleva nombre ficticio, corregimiento (nunca dirección exacta), el puesto al que
    se postula y las habilidades que tienen evidencia. No incluye cédula ni teléfono.
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

    linea(perfil.nombre, estilo="B", tamano=18, alto=10)
    linea(f"{perfil.corregimiento}, Panamá")
    pdf.ln(3)
    linea(f"Postula a: {oferta.puesto} en {oferta.empresa}", estilo="B", tamano=12)
    pdf.ln(3)
    linea("Habilidades", estilo="B", tamano=13, alto=8)
    habilidades = habilidades_para_cv(perfil, oferta)
    if habilidades:
        for h in habilidades:
            linea(skill_id_de(h), estilo="B")
            linea(f"Evidencia: {evidencia_de(h).strip()}")
            pdf.ln(1)
    else:
        linea("Sin habilidades con evidencia registrada.")
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
