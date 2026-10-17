"""La landing (#281) lleva a cada rol a su vista y no carga nada de fuera. Sin red ni claves."""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def pagina(monkeypatch, tmp_path):
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "landing.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    respuesta = TestClient(srv.app).get("/inicio")
    assert respuesta.status_code == 200
    return respuesta.text


def test_las_dos_entradas_grandes_y_el_acceso_del_orientador(pagina):
    enlaces = set(re.findall(r'href="([^"#]*)', pagina))
    assert {"/joven", "/empresa", "/orientador"} <= enlaces
    assert "Busco empleo" in pagina and "Tengo una vacante" in pagina
    assert "Acceso orientador" in pagina


def test_no_pide_recursos_de_otro_servidor(pagina):
    # La demo corre sin conexión: ni fuentes ni scripts ni hojas de estilo externas.
    externos = re.findall(r'(?:src|href)="(https?:)?//[^"]*"', pagina)
    assert not externos, f"la landing pide recursos externos: {externos}"


def test_el_titulo_dice_skillmatch(pagina):
    assert re.search(r"<title>SkillMatch", pagina)


def test_la_raiz_es_la_landing_y_el_tablero_vive_en_orientador(monkeypatch, tmp_path):
    # #293: la landing es la entrada; el tablero del orientador se abre en /orientador.
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "raiz.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    cliente = TestClient(srv.app)
    assert "Busco empleo" in cliente.get("/").text
    tablero = cliente.get("/orientador")
    assert tablero.status_code == 200 and 'id="flujo-pasos"' in tablero.text
