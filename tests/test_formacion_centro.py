"""Vista del centro de formación (#286): solo lectura, solo conteos, sin datos de jóvenes."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.server as srv


@pytest.fixture
def cliente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "formacion.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    with TestClient(srv.app) as c:
        yield c, estado


def test_pagina_y_estaticos_del_centro(cliente) -> None:
    c, _ = cliente
    pagina = c.get("/formacion")
    assert pagina.status_code == 200
    assert "centro de formación" in pagina.text.lower()
    for ruta in ("/static/formacion.js", "/static/formacion.css"):
        assert c.get(ruta).status_code == 200
    roles = {r["rol"]: r for r in c.get("/api/admin/operacion").json()["roles_sistema"]}
    assert roles["formacion"]["ruta"] == "/formacion"


def test_resumen_por_curso_sin_datos_de_jovenes(cliente) -> None:
    c, estado = cliente
    r = c.get("/api/formacion/resumen")
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["rotulo"] == "Ilustrativo, datos de la demo"
    assert datos["totales"]["cursos"] == len(datos["cursos"]) > 0
    curso = datos["cursos"][0]
    for campo in ("curso", "sede", "horario", "cupos", "demanda", "vacantes_que_abre", "habilidades"):
        assert campo in curso
    # Demanda de 1 o 2 personas no sale como número.
    for c_ in datos["cursos"]:
        assert c_["demanda"]["valor"] is None or c_["demanda"]["valor"] == 0 or c_["demanda"]["valor"] >= 3
        if c_["demanda"]["valor"] is None:  # ni la presión sobre cupos deja deducir el 1 o el 2
            assert c_["presion_cupos"] is None
    assert any(c_["demanda"]["valor"] is None for c_ in datos["cursos"]), "la demo debería tener grupos pequeños"
    texto = r.text.lower()
    for perfil in estado.get("perfiles", []):
        assert str(perfil.get("id", "")).lower() not in texto
        assert str(perfil.get("nombre", "")).lower() not in texto
    assert "perfil_id" not in texto and "edad" not in texto


def test_brechas_sin_curso_no_incluyen_habilidades_cubiertas(cliente) -> None:
    c, _ = cliente
    datos = c.get("/api/formacion/resumen").json()
    cubiertas = {h["skill_id"] for curso in datos["cursos"] for h in curso["habilidades"]}
    assert not cubiertas & {b["skill_id"] for b in datos["brechas_sin_curso"]}


def test_la_ruta_es_solo_lectura(cliente) -> None:
    c, _ = cliente
    assert c.post("/api/formacion/resumen", json={}).status_code in (403, 405)
