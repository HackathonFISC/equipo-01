"""El CV en PDF es válido y solo lleva habilidades con evidencia (issue #7)."""
import re
import zlib

from types import SimpleNamespace

import pytest

from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil
from puente.tools.pdf import (
    evidencia_de,
    generar_cv,
    habilidades_para_cv,
    limpiar_pii,
    skill_id_de,
)

_STREAM = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.S)


def _oferta():
    return Oferta(
        id="o-1", puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
        habilidades=["caja"],
    )


def _perfil(habilidades):
    return Perfil(
        id="p-1", nombre="Yaritza Ficticia", edad=20, corregimiento="La Chorrera",
        salario_minimo=300.0, presupuesto_pasaje_diario=2.0, habilidades=habilidades,
    )


def _texto_pdf(ruta):
    """Texto dibujado en el PDF: fpdf2 comprime los streams con zlib."""
    texto = ""
    for bloque in _STREAM.findall(ruta.read_bytes()):
        try:
            texto += zlib.decompress(bloque).decode("latin-1")
        except zlib.error:
            pass
    return texto


def test_cv_solo_con_evidencia(tmp_path):
    perfil = _perfil([
        HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo la fonda de mi mamá"),
        HabilidadPerfil(skill_id="excel", evidencia="   "),
        HabilidadPerfil(skill_id="caja", evidencia="cobro en la fonda"),
    ])
    util = habilidades_para_cv(perfil, _oferta())
    assert [h.skill_id for h in util] == ["caja", "atencion_cliente"]  # la que pide la oferta, primero
    ruta = generar_cv(perfil, _oferta(), tmp_path / "outbox")
    assert ruta.name == "cv_p-1_o-1.pdf"
    assert ruta.read_bytes().startswith(b"%PDF")
    texto = _texto_pdf(ruta)
    assert "atencion_cliente" in texto and "caja" in texto
    assert "excel" not in texto  # evidencia vacía: no entra al CV


def test_cv_no_lleva_cedula_ni_telefono(tmp_path):
    perfil = _perfil([HabilidadPerfil(skill_id="caja", evidencia="cobro en caja, cédula 8-123-4567, tel 6123-4567")])
    texto = _texto_pdf(generar_cv(perfil, _oferta(), tmp_path))
    assert "cobro en caja" in texto
    assert "8-123-4567" not in texto and "6123-4567" not in texto


def test_cv_sin_ninguna_evidencia_sigue_siendo_pdf_valido(tmp_path):
    ruta = generar_cv(_perfil([HabilidadPerfil(skill_id="excel", evidencia="")]), _oferta(), tmp_path)
    assert ruta.read_bytes().startswith(b"%PDF")


def test_caracteres_fuera_de_latin1_no_rompen_el_pdf(tmp_path):
    evidencia = "• cobré en caja \U0001F600 — “bien”"
    perfil = _perfil([HabilidadPerfil(skill_id="caja", evidencia=evidencia)])
    assert generar_cv(perfil, _oferta(), tmp_path).read_bytes().startswith(b"%PDF")


def test_limpiar_pii():
    assert "8-765-4321" not in limpiar_pii("mi cédula 8-765-4321")
    assert "6123-4567" not in limpiar_pii("llámame al +507 6123-4567")
    assert "PE-1-234" not in limpiar_pii("cédula PE-1-234")
    assert limpiar_pii("atiendo clientes") == "atiendo clientes"


@pytest.mark.parametrize(
    "texto, dato",
    [
        ("mi celular es 61234567", "61234567"),
        ("tel 6123 4567", "6123 4567"),
        ("celular +50761234567", "61234567"),
        ("llámame al (507) 6123-4567", "6123-4567"),
        ("fijo 233-4567", "233-4567"),
        ("fijo 2334567", "2334567"),
        ("cédula 8 123 4567", "123 4567"),
        ("cédula 08-123-4567", "123-4567"),
        ("cédula E-8-12345", "E-8-12345"),
        ("cédula PE 1 234", "PE 1 234"),
        ("vivo en calle 5, casa 12-B", "calle 5"),
        ("vivo en calle 5, casa 12-B", "casa 12-B"),
        ("apto 3A en edificio Los Pinos", "apto 3A"),
        ("Dirección: Calle Quinta, La Chorrera", "Calle Quinta"),
    ],
)
def test_limpiar_pii_quita_variantes_sin_separador_con_espacios_y_direcciones(texto, dato):
    limpio = limpiar_pii(texto)
    assert dato not in limpio
    assert "[dato omitido]" in limpio


@pytest.mark.parametrize(
    "texto",
    [
        "trabajé del 20-10-2026 al 30-11-2026",
        "inicio 2026-10-20 10:00 hora de Panamá",
        "salario 1200 1500",
        "gano $1,200.50 al mes",
        "turno de 8 a 16 horas",
        "atendí 120 clientes en 3 años",
        "8 de 12 de 2020",
        "1. Yaritza - La Chorrera",
    ],
)
def test_limpiar_pii_no_borra_fechas_montos_ni_horarios(texto):
    assert limpiar_pii(texto) == texto


def test_cv_no_lleva_celular_sin_separador_ni_direccion(tmp_path):
    perfil = _perfil([
        HabilidadPerfil(skill_id="caja", evidencia="cobro en caja, tel 61234567, vivo en calle 5 casa 12"),
    ])
    texto = _texto_pdf(generar_cv(perfil, _oferta(), tmp_path))
    assert "cobro en caja" in texto
    assert "61234567" not in texto and "calle 5" not in texto and "casa 12" not in texto


def test_lectores_de_habilidad_aceptan_modelo_o_dict():
    modelo = HabilidadPerfil(skill_id="excel", evidencia="llevo el inventario")
    como_dict = {"skill_id": "excel", "evidencia": "llevo el inventario"}
    for h in (modelo, como_dict):
        assert skill_id_de(h) == "excel"
        assert evidencia_de(h) == "llevo el inventario"
    assert evidencia_de({"skill_id": "caja"}) == ""
    assert evidencia_de({"skill_id": "caja", "evidencia": None}) == ""


def test_habilidades_para_cv_con_habilidades_como_dict():
    perfil = SimpleNamespace(habilidades=[
        {"skill_id": "excel", "evidencia": ""},
        {"skill_id": "atencion_cliente", "evidencia": "atiendo la fonda"},
        {"skill_id": "caja", "evidencia": "cobro en la fonda"},
    ])
    oferta = SimpleNamespace(habilidades=["caja"])
    util = habilidades_para_cv(perfil, oferta)
    assert [skill_id_de(h) for h in util] == ["caja", "atencion_cliente"]
