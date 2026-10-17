"""Carta de presentación ciega por plantilla, desde la evidencia literal del perfil. Issue #212.

Sin LLM: misma entrada, misma salida. La carta lleva el saludo a la empresa, el puesto, hasta 3
habilidades que la oferta pide y el perfil respalda (si son menos de 2, completa con otras que tengan
evidencia), cada una con su frase literal, y la disponibilidad. Nunca lleva edad, género,
corregimiento, estado civil, hijos, presupuesto, salario pedido ni ofertas descartadas.

La evidencia del joven pasa por `cegar()`, igual que en el CV; toda línea pasa por `limpiar_pii()`.
El texto de la oferta (empresa y puesto) no se ciega: lo escribió la empresa, y tapar ahí el
corregimiento del joven ("Farmacia [dato omitido]") se lo revelaría (#226). El nombre va tal como lo
da la persona y solo si lo dio (#123); Puente no lo agrega ni lo quita.

La plantilla no usa palabras con género ("estimado", "atenta", "señores"): la carta es la misma
para cualquier persona.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from fpdf import FPDF

from puente.rules.taxonomia import cargar_taxonomia
from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil
from puente.tools.pdf import a_latin1, cegar, evidencia_de, formatear_horario, limpiar_pii, skill_id_de

MAX_HABILIDADES = 3
MIN_HABILIDADES = 2
RUTA_TAXONOMIA = Path(__file__).resolve().parents[2] / "data" / "taxonomia.csv"

SALUDO = "Al equipo de selección de {empresa}:"
CIERRE = "Quedo a disposición para una entrevista. Gracias por su tiempo."
DESPEDIDA = "Atentamente,"
PIE = (
    "Carta generada por Puente a partir de lo que la persona contó; "
    "cada habilidad lleva su frase literal."
)


@lru_cache(maxsize=1)
def _taxonomia_por_defecto() -> dict[str, dict]:
    try:
        return cargar_taxonomia(RUTA_TAXONOMIA)
    except OSError:
        return {}


def nombre_habilidad(skill_id: str, taxonomia: dict[str, dict] | None = None) -> str:
    """Nombre legible de la habilidad ("Asistir a clientes"); sin taxonomía, el skill_id con espacios."""
    taxonomia = _taxonomia_por_defecto() if taxonomia is None else taxonomia
    nombre = str((taxonomia.get(skill_id) or {}).get("nombre_es") or "").strip() or skill_id.replace("_", " ")
    return nombre[:1].upper() + nombre[1:]


def habilidades_para_carta(
    perfil: Perfil, oferta: Oferta
) -> tuple[list[HabilidadPerfil], list[HabilidadPerfil]]:
    """``(pedidas, extra)`` para la carta, solo con habilidades que tienen evidencia.

    ``pedidas``: hasta 3 que la oferta pide y el perfil respalda, en el orden en que las lista la
    oferta. ``extra``: si las pedidas son menos de 2, otras habilidades con evidencia del perfil, en
    su orden, hasta completar 2. Un ``skill_id`` repetido en el perfil cuenta una vez.
    """
    con_evidencia: dict[str, HabilidadPerfil] = {}
    for h in perfil.habilidades:
        if evidencia_de(h).strip():
            con_evidencia.setdefault(skill_id_de(h), h)
    pedidas = [con_evidencia[s] for s in dict.fromkeys(oferta.habilidades) if s in con_evidencia]
    pedidas = pedidas[:MAX_HABILIDADES]
    usadas = {skill_id_de(h) for h in pedidas}
    faltan = max(0, MIN_HABILIDADES - len(pedidas))
    extra = [h for s, h in con_evidencia.items() if s not in usadas][:faltan]
    return pedidas, extra


def _cabe(horario: Horario, libres: list[Horario]) -> bool:
    """El horario de la oferta queda dentro de la disponibilidad, día por día.

    Una franja que cruza la medianoche ("22:00-06:00") nunca se da por compatible: comparar las horas
    como texto la haría caber en "18:00-23:00" (#226). Ante la duda, la carta no lo afirma.
    """
    if horario.hora_fin <= horario.hora_inicio:
        return False
    return all(
        any(dia in libre.dias and libre.hora_inicio < libre.hora_fin
            and libre.hora_inicio <= horario.hora_inicio and horario.hora_fin <= libre.hora_fin
            for libre in libres)
        for dia in horario.dias
    )


def contenido_carta(
    perfil: Perfil, oferta: Oferta, *, taxonomia: dict[str, dict] | None = None
) -> list[str]:
    """Líneas de la carta ciega para la empresa, en orden. La última es el nombre, si la persona lo dio."""
    pedidas, extra = habilidades_para_carta(perfil, oferta)

    def item(h: HabilidadPerfil) -> str:
        # La evidencia es literal y puede traer la edad o dónde vive la persona: esto sí se ciega.
        evidencia = cegar(evidencia_de(h).strip(), perfil)
        return f"- {nombre_habilidad(skill_id_de(h), taxonomia)}: «{evidencia}»"

    empresa, puesto = str(oferta.empresa or "").strip(), str(oferta.puesto or "").strip()
    cuerpo = [SALUDO.format(empresa=empresa) if empresa else "Al equipo de selección:"]
    intro = f"Me postulo al puesto de {puesto}." if puesto else "Me postulo a esta vacante."
    if pedidas:
        cuerpo.append(f"{intro} Estas son las habilidades que el puesto pide y que respaldo con lo que "
                      "hago hoy, en mis propias palabras:")
        cuerpo += [item(h) for h in pedidas]
        if extra:
            cuerpo.append("También puedo aportar:")
            cuerpo += [item(h) for h in extra]
    elif extra:
        cuerpo.append(f"{intro} Estas son habilidades que respaldo con lo que hago hoy, en mis propias palabras:")
        cuerpo += [item(h) for h in extra]
    else:
        cuerpo.append(f"{intro} Puedo contar en la entrevista lo que sé hacer.")

    if perfil.horario_libre:
        disponibilidad = f"Disponibilidad: {'; '.join(formatear_horario(h) for h in perfil.horario_libre)}."
        # Solo con horario fijo: uno rotativo queda «por confirmar» en el solver y la carta no lo afirma.
        fijo = oferta.horario is not None and oferta.horario_tipo == "fijo"
        if fijo and _cabe(oferta.horario, perfil.horario_libre):
            disponibilidad += f" El horario del puesto ({formatear_horario(oferta.horario)}) cabe en ella."
    else:
        disponibilidad = "Disponibilidad: por confirmar."
    cuerpo += [disponibilidad, CIERRE, DESPEDIDA]

    lineas = [limpiar_pii(linea) for linea in cuerpo]
    nombre = str(perfil.nombre or "").strip()
    if nombre:
        lineas.append(limpiar_pii(nombre))
    return lineas


def _nombre_seguro(valor: str) -> str:
    return re.sub(r"[^\w-]", "_", str(valor))


def generar_carta(
    perfil: Perfil, oferta: Oferta, destino_dir: Path, *, taxonomia: dict[str, dict] | None = None
) -> Path:
    """Escribe ``carta_<perfil_id>_<oferta_id>.pdf`` en ``destino_dir`` y devuelve la ruta."""
    destino_dir = Path(destino_dir)
    destino_dir.mkdir(parents=True, exist_ok=True)
    ruta = destino_dir / f"carta_{_nombre_seguro(perfil.id)}_{_nombre_seguro(oferta.id)}.pdf"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def linea(texto: str, *, estilo: str = "", tamano: int = 11, alto: float = 6) -> None:
        pdf.set_font("Helvetica", estilo, tamano)
        pdf.multi_cell(0, alto, a_latin1(texto), new_x="LMARGIN", new_y="NEXT")

    contenido = contenido_carta(perfil, oferta, taxonomia=taxonomia)
    indice_despedida = contenido.index(DESPEDIDA)
    linea(contenido[0], estilo="B")
    pdf.ln(3)
    cuerpo = contenido[1:indice_despedida]
    for texto, siguiente in zip(cuerpo, [*cuerpo[1:], ""]):
        linea(texto)
        pdf.ln(1 if texto.startswith("- ") and siguiente.startswith("- ") else 3)
    linea(contenido[indice_despedida])
    for texto in contenido[indice_despedida + 1:]:
        linea(texto, estilo="B")
    pdf.ln(6)
    linea(PIE, estilo="I", tamano=8, alto=4)
    pdf.output(str(ruta))
    return ruta
