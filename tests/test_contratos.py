"""Los contratos de las herramientas usan los de puente.schemas cuando existen (issue #7)."""
from types import SimpleNamespace

import pytest

import puente.schemas as schemas
from puente.tools import _contratos
from puente.tools.pdf import habilidades_para_cv

NOMBRES = ["Decision", "EstadoPostulacion", "Habilidad", "Horario", "Match", "Oferta", "Perfil"]


@pytest.mark.parametrize("nombre", NOMBRES)
def test_usa_el_contrato_real_si_puente_schemas_lo_define(nombre):
    if hasattr(schemas, nombre):
        assert getattr(_contratos, nombre) is getattr(schemas, nombre)
    else:
        assert nombre in _contratos.STUBS_EN_USO


def test_stubs_en_uso_son_exactamente_los_que_faltan_en_schemas():
    faltan = tuple(n for n in NOMBRES if not hasattr(schemas, n))
    assert set(_contratos.STUBS_EN_USO) == set(faltan)
    assert _contratos.USA_SCHEMAS_REALES == (not faltan)


def test_un_nombre_faltante_no_descarta_los_demas(monkeypatch):
    """Si #2 define Perfil pero no Habilidad, se usa el Perfil real y solo Habilidad es copia."""
    import importlib.util

    class PerfilReal:  # contrato inventado solo para la prueba
        pass

    monkeypatch.setattr(schemas, "Perfil", PerfilReal, raising=False)
    # Copia del módulo con otro nombre: no toca las clases que ya usan los demás tests.
    spec = importlib.util.spec_from_file_location("_contratos_copia", _contratos.__file__)
    copia = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copia)
    assert copia.Perfil is PerfilReal
    assert "Perfil" not in copia.STUBS_EN_USO
    if not hasattr(schemas, "Habilidad"):
        assert "Habilidad" in copia.STUBS_EN_USO


def test_lectores_de_habilidad_aceptan_modelo_o_dict():
    modelo = _contratos.Habilidad(skill_id="excel", evidencia="llevo el inventario")
    como_dict = {"skill_id": "excel", "evidencia": "llevo el inventario"}
    for h in (modelo, como_dict):
        assert _contratos.skill_id_de(h) == "excel"
        assert _contratos.evidencia_de(h) == "llevo el inventario"
    assert _contratos.evidencia_de({"skill_id": "caja"}) == ""
    assert _contratos.evidencia_de({"skill_id": "caja", "evidencia": None}) == ""


def test_habilidades_para_cv_con_habilidades_como_dict():
    perfil = SimpleNamespace(habilidades=[
        {"skill_id": "excel", "evidencia": ""},
        {"skill_id": "atencion_cliente", "evidencia": "atiendo la fonda"},
        {"skill_id": "caja", "evidencia": "cobro en la fonda"},
    ])
    oferta = SimpleNamespace(habilidades=["caja"])
    util = habilidades_para_cv(perfil, oferta)
    assert [_contratos.skill_id_de(h) for h in util] == ["caja", "atencion_cliente"]
