"""CV adaptado a la oferta en PDF con fpdf2, solo con habilidades con evidencia. Issue #7."""
from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

from puente.tools._contratos import Habilidad, Oferta, Perfil

# Cédulas panameñas (8-123-4567, PE-1-234, E-8-12345) y teléfonos (+507 6123-4567, 6123-4567).
_PATRON_CEDULA = re.compile(r"\b(?:PE|E|N|AV|PI)-\d{1,4}-\d{1,6}\b|\b\d{1,2}-\d{1,4}-\d{1,6}\b")
_PATRON_TELEFONO = re.compile(r"(?:\+?507[\s-]?)?\b\d{3,4}[\s-]\d{4}\b|\+507[\s-]?\d{7,8}\b")

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
    """Quita cédula y teléfono de un texto libre (defensa extra: Perfil ya los quita antes)."""
    texto = _PATRON_CEDULA.sub("[dato omitido]", texto)
    return _PATRON_TELEFONO.sub("[dato omitido]", texto)


def a_latin1(texto: str) -> str:
    """Pasa el texto a algo que Helvetica pueda dibujar; lo que no cabe queda como '?'."""
    for origen, destino in _SUSTITUCIONES.items():
        texto = texto.replace(origen, destino)
    return texto.encode("latin-1", errors="replace").decode("latin-1")


def habilidades_para_cv(perfil: Perfil, oferta: Oferta) -> list[Habilidad]:
    """Solo habilidades con evidencia no vacía; primero las que pide la oferta."""
    con_evidencia = [h for h in perfil.habilidades if (h.evidencia or "").strip()]
    pedidas = set(oferta.habilidades)
    pedidas_primero = [h for h in con_evidencia if h.skill_id in pedidas]
    resto = [h for h in con_evidencia if h.skill_id not in pedidas]
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
            linea(h.skill_id, estilo="B")
            linea(f"Evidencia: {h.evidencia.strip()}")
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
