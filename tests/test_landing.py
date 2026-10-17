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


@pytest.mark.parametrize("ruta", ["/joven", "/empresa", "/empresa/lista-corta"])
def test_joven_y_empresa_salen_a_la_landing_y_no_al_tablero(monkeypatch, tmp_path, ruta):
    # #301: desde la vista del joven o de la empresa se sale a la landing, no al tablero del orientador.
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "salida.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    html = TestClient(srv.app).get(ruta).text
    assert 'href="/">Salir al inicio</a>' in html
    assert 'href="/orientador"' not in html


def test_el_tablero_trae_el_tablero_de_agentes_y_no_el_bloque_inec(monkeypatch, tmp_path):
    # #315: Trazas abre con una tarjeta por agente y ya no sale el bloque de contexto INEC debajo de los indicadores.
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "trazas.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    cliente = TestClient(srv.app)
    html = cliente.get("/orientador").text
    assert 'id="tablero-agentes"' in html
    assert "panel-metricas-impacto" not in html
    assert "function renderizarTableroAgentes" in cliente.get("/static/app.js").text
