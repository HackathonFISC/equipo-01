"""El CV en PDF es válido y solo lleva habilidades con evidencia (issue #7)."""
import re
import zlib

from puente.tools._contratos import Habilidad, Horario, Oferta, Perfil
from puente.tools.pdf import generar_cv, habilidades_para_cv, limpiar_pii

_STREAM = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.S)


def _oferta():
    return Oferta(
        id="o-1", puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
        habilidades=["caja"],
    )


def _perfil(habilidades):
    return Perfil(id="p-1", nombre="Yaritza Ficticia", edad=20, corregimiento="La Chorrera", habilidades=habilidades)


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
        Habilidad(skill_id="atencion_cliente", evidencia="atiendo la fonda de mi mamá"),
        Habilidad(skill_id="excel", evidencia="   "),
        Habilidad(skill_id="caja", evidencia="cobro en la fonda"),
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
    perfil = _perfil([Habilidad(skill_id="caja", evidencia="cobro en caja, cédula 8-123-4567, tel 6123-4567")])
    texto = _texto_pdf(generar_cv(perfil, _oferta(), tmp_path))
    assert "cobro en caja" in texto
    assert "8-123-4567" not in texto and "6123-4567" not in texto


def test_cv_sin_ninguna_evidencia_sigue_siendo_pdf_valido(tmp_path):
    ruta = generar_cv(_perfil([Habilidad(skill_id="excel", evidencia="")]), _oferta(), tmp_path)
    assert ruta.read_bytes().startswith(b"%PDF")


def test_caracteres_fuera_de_latin1_no_rompen_el_pdf(tmp_path):
    evidencia = "• cobré en caja \U0001F600 — “bien”"
    perfil = _perfil([Habilidad(skill_id="caja", evidencia=evidencia)])
    assert generar_cv(perfil, _oferta(), tmp_path).read_bytes().startswith(b"%PDF")


def test_limpiar_pii():
    assert "8-765-4321" not in limpiar_pii("mi cédula 8-765-4321")
    assert "6123-4567" not in limpiar_pii("llámame al +507 6123-4567")
    assert "PE-1-234" not in limpiar_pii("cédula PE-1-234")
    assert limpiar_pii("atiendo clientes") == "atiendo clientes"
