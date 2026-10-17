"""Carta de presentación ciega por plantilla (issue #212).

Todos los datos son SINTÉTICOS (personas y empresas de prueba). Sin red ni LLM.
"""
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil
from puente.tools.carta import (
    CIERRE,
    DESPEDIDA,
    SALUDO,
    contenido_carta,
    generar_carta,
    habilidades_para_carta,
    nombre_habilidad,
)
from puente.tools.pdf import cegar

LUNES_A_VIERNES = ["lunes", "martes", "miercoles", "jueves", "viernes"]
TAXONOMIA = {
    "atencion_cliente": {"nombre_es": "asistir a clientes"},
    "manejo_caja": {"nombre_es": "manejar una caja registradora"},
    "inventario": {"nombre_es": "gestionar el inventario"},
    "limpieza": {"nombre_es": "realizar tareas de limpieza"},
}


def _oferta(habilidades=("atencion_cliente", "manejo_caja"), horario=None):
    return Oferta(
        id="of-1", puesto="Auxiliar de caja", empresa="Super Demo (Empresa de Prueba)",
        corregimiento="Arraiján", salario_mensual=650.0, habilidades=list(habilidades),
        horario=horario or Horario(dias=LUNES_A_VIERNES, hora_inicio="09:00", hora_fin="13:00"),
    )


def _perfil(nombre="Yaritza Ficticia", habilidades=None, horario_libre=None):
    return Perfil(
        id="p-1", nombre=nombre, genero="F", edad=20, corregimiento="Barrio Colón",
        salario_minimo=412.5, presupuesto_pasaje_diario=3.25,
        horario_libre=[Horario(dias=LUNES_A_VIERNES, hora_inicio="08:00", hora_fin="17:00")]
        if horario_libre is None else horario_libre,
        horario_clases=[Horario(dias=LUNES_A_VIERNES, hora_inicio="18:30", hora_fin="21:30")],
        habilidades=habilidades if habilidades is not None else [
            HabilidadPerfil(skill_id="inventario", evidencia="cuento los productos que quedan al cerrar"),
            HabilidadPerfil(skill_id="manejo_caja", evidencia="cobro en caja"),
            HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo pedidos"),
        ],
    )


def _texto_pdf(ruta: Path) -> str:
    return " ".join(" ".join(p.extract_text().split()) for p in PdfReader(ruta).pages)


def test_lleva_saludo_puesto_habilidades_pedidas_con_su_frase_y_cierre():
    lineas = contenido_carta(_perfil(), _oferta(), taxonomia=TAXONOMIA)
    assert lineas[0] == SALUDO.format(empresa="Super Demo (Empresa de Prueba)")
    assert lineas[1].startswith("Me postulo al puesto de Auxiliar de caja.")
    # En el orden de la oferta, cada una con su evidencia literal; inventario no se pide.
    assert lineas[2] == "- Asistir a clientes: «atiendo pedidos»"
    assert lineas[3] == "- Manejar una caja registradora: «cobro en caja»"
    assert not any("inventario" in linea.lower() or "cuento los productos" in linea for linea in lineas)
    assert CIERRE in lineas and lineas[-2] == DESPEDIDA and lineas[-1] == "Yaritza Ficticia"


def test_como_mucho_tres_habilidades_pedidas():
    perfil = _perfil(habilidades=[
        HabilidadPerfil(skill_id=s, evidencia=f"lo hago cada día ({s})")
        for s in ("atencion_cliente", "manejo_caja", "inventario", "limpieza")
    ])
    oferta = _oferta(habilidades=("limpieza", "inventario", "manejo_caja", "atencion_cliente"))
    pedidas, extra = habilidades_para_carta(perfil, oferta)
    assert [h.skill_id for h in pedidas] == ["limpieza", "inventario", "manejo_caja"] and extra == []
    items = [linea for linea in contenido_carta(perfil, oferta, taxonomia=TAXONOMIA) if linea.startswith("- ")]
    assert len(items) == 3


def test_con_una_sola_pedida_completa_hasta_dos_con_otra_habilidad_con_evidencia():
    perfil = _perfil(habilidades=[
        HabilidadPerfil(skill_id="excel_basico", evidencia=""),  # sin evidencia: nunca entra
        HabilidadPerfil(skill_id="inventario", evidencia="cuento los productos que quedan al cerrar"),
        HabilidadPerfil(skill_id="manejo_caja", evidencia="cobro en caja"),
        HabilidadPerfil(skill_id="limpieza", evidencia="dejo el local limpio"),
    ])
    pedidas, extra = habilidades_para_carta(perfil, _oferta(habilidades=("manejo_caja", "excel_basico")))
    assert [h.skill_id for h in pedidas] == ["manejo_caja"]
    assert [h.skill_id for h in extra] == ["inventario"]
    lineas = contenido_carta(perfil, _oferta(habilidades=("manejo_caja", "excel_basico")), taxonomia=TAXONOMIA)
    assert lineas[2] == "- Manejar una caja registradora: «cobro en caja»"
    assert lineas[3] == "También puedo aportar:"
    assert lineas[4] == "- Gestionar el inventario: «cuento los productos que quedan al cerrar»"
    assert not any("excel" in linea.lower() or "limpio" in linea for linea in lineas)


def test_sin_habilidades_con_evidencia_no_inventa_ninguna():
    perfil = _perfil(habilidades=[HabilidadPerfil(skill_id="manejo_caja", evidencia="  ")])
    lineas = contenido_carta(perfil, _oferta(), taxonomia=TAXONOMIA)
    assert not any(linea.startswith("- ") for linea in lineas)
    assert lineas[1] == "Me postulo al puesto de Auxiliar de caja. Puedo contar en la entrevista lo que sé hacer."


def test_un_skill_id_repetido_cuenta_una_vez_con_su_primera_evidencia():
    perfil = _perfil(habilidades=[
        HabilidadPerfil(skill_id="manejo_caja", evidencia="cobro en caja"),
        HabilidadPerfil(skill_id="manejo_caja", evidencia="cuadro la caja al cierre"),
        HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo pedidos"),
    ])
    pedidas, _ = habilidades_para_carta(perfil, _oferta(habilidades=("manejo_caja", "manejo_caja", "atencion_cliente")))
    assert [(h.skill_id, h.evidencia) for h in pedidas] == [
        ("manejo_caja", "cobro en caja"), ("atencion_cliente", "atiendo pedidos")
    ]


def test_disponibilidad_segun_el_horario_libre_y_si_cabe_el_del_puesto():
    lineas = contenido_carta(_perfil(), _oferta(), taxonomia=TAXONOMIA)
    disponibilidad = next(linea for linea in lineas if linea.startswith("Disponibilidad:"))
    assert disponibilidad == (
        "Disponibilidad: lunes a viernes, 08:00-17:00. "
        "El horario del puesto (lunes a viernes, 09:00-13:00) cabe en ella."
    )
    # Un puesto que se sale de la franja libre no se presenta como compatible.
    noche = _oferta(horario=Horario(dias=LUNES_A_VIERNES, hora_inicio="16:00", hora_fin="20:00"))
    disponibilidad = next(linea for linea in contenido_carta(_perfil(), noche) if linea.startswith("Disponibilidad:"))
    assert disponibilidad == "Disponibilidad: lunes a viernes, 08:00-17:00."
    sabado = _oferta(horario=Horario(dias=["sabado"], hora_inicio="09:00", hora_fin="13:00"))
    assert "cabe" not in next(linea for linea in contenido_carta(_perfil(), sabado) if linea.startswith("Disp"))
    sin_libre = contenido_carta(_perfil(horario_libre=[]), _oferta())
    assert "Disponibilidad: por confirmar." in sin_libre


def test_la_carta_es_ciega_aunque_la_evidencia_traiga_datos_personales():
    perfil = _perfil(habilidades=[
        HabilidadPerfil(
            skill_id="atencion_cliente",
            evidencia="tengo 20 años, soy soltera y vivo en Barrio Colón; atiendo pedidos mientras cuido a mis hijos",
        ),
        HabilidadPerfil(skill_id="manejo_caja", evidencia="cobro en caja, cédula 8-123-4567, tel. 6123-4567"),
    ])
    texto = "\n".join(contenido_carta(perfil, _oferta(), taxonomia=TAXONOMIA))
    bajo = texto.lower()
    for dato in ("20 años", "soltera", "barrio colón", "mis hijos", "8-123-4567", "6123-4567"):
        assert dato not in bajo, dato
    assert "atiendo pedidos" in bajo and "[dato omitido]" in texto
    # Nunca lleva lo que no es del trabajo: género, presupuesto ni salario pedido.
    assert not re.search(r"\bF\b", texto)
    assert "3.25" not in texto and "412" not in texto and "género" not in bajo and "edad" not in bajo


def test_la_plantilla_no_tiene_palabras_que_cegar_tape():
    """Con evidencia limpia no aparece ningún «[dato omitido]»: la plantilla es neutra."""
    lineas = contenido_carta(_perfil(), _oferta(), taxonomia=TAXONOMIA)
    assert all("[dato omitido]" not in linea for linea in lineas)
    assert all(cegar(linea, _perfil(), del_orientador=True) == linea for linea in lineas[:-1])
    for palabra in ("estimad", "señor", "atent", "interesad", "candidat"):
        assert palabra not in " ".join(lineas[:-2]).lower(), palabra


@pytest.mark.parametrize("nombre", ["", "   "])
def test_sin_nombre_la_carta_no_lo_agrega(nombre):
    lineas = contenido_carta(_perfil(nombre=nombre), _oferta(), taxonomia=TAXONOMIA)
    assert lineas[-1] == DESPEDIDA


def test_el_nombre_va_tal_como_lo_da_la_persona():
    lineas = contenido_carta(_perfil(nombre="Yaritza (personaje ficticio)"), _oferta(), taxonomia=TAXONOMIA)
    assert lineas[-1] == "Yaritza (personaje ficticio)"


def test_nombre_de_habilidad_desde_la_taxonomia_del_repo_y_sin_ella():
    assert nombre_habilidad("atencion_cliente") == "Asistir a clientes"  # data/taxonomia.csv
    assert nombre_habilidad("manejo_caja", {}) == "Manejo caja"
    assert nombre_habilidad("no_existe") == "No existe"


def test_misma_entrada_misma_carta():
    assert contenido_carta(_perfil(), _oferta()) == contenido_carta(_perfil(), _oferta())


def test_generar_carta_escribe_el_pdf_ciego(tmp_path):
    perfil = _perfil(habilidades=[
        HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo pedidos; nací en Barrio Colón en 2006"),
        HabilidadPerfil(skill_id="manejo_caja", evidencia="cobro en caja"),
    ])
    ruta = generar_carta(perfil, _oferta(), tmp_path / "outbox", taxonomia=TAXONOMIA)
    assert ruta == tmp_path / "outbox" / "carta_p-1_of-1.pdf"
    assert ruta.read_bytes().startswith(b"%PDF")
    texto = _texto_pdf(ruta)
    assert "Al equipo de selección de Super Demo (Empresa de Prueba):" in texto
    assert "«atiendo pedidos;" in texto and "«cobro en caja»" in texto
    assert "Yaritza Ficticia" in texto and "Carta generada por SkillMatch" in texto
    for dato in ("Barrio Colón", "2006", "20 años"):
        assert dato not in texto, dato


def test_la_carta_no_usa_el_llm():
    fuente = Path("puente/tools/carta.py").read_text(encoding="utf-8")
    assert "puente.llm" not in fuente and "generar_estructurado" not in fuente and "generar_texto" not in fuente


# ---------------------------------------------------------------- #226: revisión posterior al merge del #217


def test_el_texto_de_la_oferta_no_se_ciega_y_la_evidencia_si():
    """Tapar el corregimiento en el nombre de la empresa («Farmacia [dato omitido]») se lo revelaría a la empresa."""
    perfil = _perfil(habilidades=[
        HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo pedidos; vivo en Vista Alegre y tengo 20 años"),
    ])
    perfil = perfil.model_copy(update={"corregimiento": "Vista Alegre"})
    oferta = _oferta(habilidades=("atencion_cliente",)).model_copy(update={
        "empresa": "Farmacia Vista Alegre (Empresa de Prueba)",
        "puesto": "Cajero(a) con 20 años o más de experiencia, sucursal Vista Alegre",
    })
    lineas = contenido_carta(perfil, oferta, taxonomia=TAXONOMIA)
    assert lineas[0] == "Al equipo de selección de Farmacia Vista Alegre (Empresa de Prueba):"
    assert lineas[1].startswith("Me postulo al puesto de Cajero(a) con 20 años o más de experiencia, sucursal Vista Alegre.")
    assert lineas[2] == "- Asistir a clientes: «atiendo pedidos; vivo en [dato omitido] y tengo [dato omitido]»"


def test_horario_rotativo_no_se_presenta_como_compatible():
    rotativo = _oferta().model_copy(update={"horario_tipo": "rotativo"})
    disponibilidad = next(linea for linea in contenido_carta(_perfil(), rotativo) if linea.startswith("Disponibilidad:"))
    assert disponibilidad == "Disponibilidad: lunes a viernes, 08:00-17:00."


@pytest.mark.parametrize(
    ("libre", "puesto"),
    [
        (("18:00", "23:00"), ("22:00", "06:00")),  # el turno cruza la medianoche
        (("18:00", "02:00"), ("19:00", "22:00")),  # la disponibilidad cruza la medianoche
    ],
)
def test_franjas_que_cruzan_la_medianoche_no_se_dan_por_compatibles(libre, puesto):
    perfil = _perfil(horario_libre=[Horario(dias=LUNES_A_VIERNES, hora_inicio=libre[0], hora_fin=libre[1])])
    oferta = _oferta(horario=Horario(dias=LUNES_A_VIERNES, hora_inicio=puesto[0], hora_fin=puesto[1]))
    disponibilidad = next(linea for linea in contenido_carta(perfil, oferta) if linea.startswith("Disponibilidad:"))
    assert "cabe" not in disponibilidad


def test_sin_puesto_ni_empresa_no_quedan_huecos():
    oferta = _oferta().model_copy(update={"puesto": "  ", "empresa": ""})
    lineas = contenido_carta(_perfil(), oferta, taxonomia=TAXONOMIA)
    assert lineas[0] == "Al equipo de selección:"
    assert lineas[1].startswith("Me postulo a esta vacante. Estas son")
    assert not any(" de :" in linea or "de ." in linea for linea in lineas)


def test_el_nombre_de_una_habilidad_fuera_de_la_taxonomia_tambien_se_ciega():
    """Sin la habilidad en la taxonomía, el nombre cae al skill_id: no puede llevar el corregimiento del joven (#244)."""
    perfil = _perfil(habilidades=[
        HabilidadPerfil(skill_id="atiende_en_vista_alegre", evidencia="atiendo pedidos"),
    ])
    perfil = perfil.model_copy(update={"corregimiento": "Vista Alegre"})
    oferta = _oferta(habilidades=("atiende_en_vista_alegre",))
    lineas = contenido_carta(perfil, oferta, taxonomia={})
    assert "- Atiende en [dato omitido]: «atiendo pedidos»" in lineas
    assert "vista alegre" not in " ".join(lineas).lower()
    # Con la habilidad en la taxonomía el nombre sale tal cual, sin huecos.
    normal = contenido_carta(_perfil(), _oferta(), taxonomia=TAXONOMIA)
    assert "- Asistir a clientes: «atiendo pedidos»" in normal and not any("[dato omitido]" in x for x in normal)
