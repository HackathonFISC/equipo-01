"""El CV del aplicante alimenta Perfil y matching; archivos y PII no se persisten."""
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import server as srv


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "cv.db")
    prompts = []

    def modelo(prompt):
        prompts.append(prompt)
        return {"habilidades": [{"skill_id": "manejo_caja", "evidencia": "Hacía el cobro en caja"}]}

    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "llm_perfil_joven", modelo)
    monkeypatch.setattr(srv, "_envios_subida", {})
    with TestClient(srv.app) as cliente:
        yield cliente, estado, prompts


def subir(cliente, archivo=None, datos=None):
    campos = datos or {"nombre": "Joven sintético", "edad": 20, "corregimiento": "La Chorrera",
                       "salario_minimo": 400, "presupuesto_pasaje_diario": 3,
                       "horario_libre": [{"dias": ["lunes"], "hora_inicio": "08:00", "hora_fin": "17:00"}]}
    return cliente.post("/api/joven/cv", data={"datos": json.dumps(campos)},
                        files={"archivo": archivo or ("cv.pdf", Path("tests/fixtures/cv_sintetico.pdf").read_bytes(), "application/pdf")})


def test_pdf_sin_entrevista_crea_perfil_token_y_resultado_privado(entorno):
    cliente, estado, prompts = entorno
    r = subir(cliente)
    assert r.status_code == 201, r.text
    resultado = r.json()
    pid = resultado["perfil"]["id"]
    assert prompts and "6999-1111" not in prompts[0] and "8-999-1111" not in prompts[0]
    assert resultado["perfil"]["habilidades"][0]["skill_id"] == "manejo_caja"
    assert resultado["token"] == estado["_tokens_joven"][pid]
    assert cliente.get(f"/api/joven/{pid}/resultado").status_code == 401
    assert cliente.get(f"/api/joven/{pid}/resultado", headers={"X-Token-Joven": resultado["token"]}).status_code == 200
    assert not any("cv_texto" in e or "cv_ruta" in e for e in estado.get("entradas_perfil", []))


def test_foto_pasa_por_ocr_y_perfil(entorno, monkeypatch):
    cliente, estado, prompts = entorno
    leidos = []

    def leer(tipo, contenido):
        leidos.append(tipo)
        return "Experiencia en la fonda: Hacía el cobro en caja. Teléfono 6999-1111."

    monkeypatch.setattr(srv, "leer_texto_de_cv", leer)
    r = subir(cliente, ("cv.png", b"\x89PNG\r\n\x1a\n" + b"x" * 40, "image/png"))
    assert r.status_code == 201, r.text
    assert leidos == ["png"] and len(prompts) == 1
    assert "6999-1111" not in prompts[0]


def test_ejecutable_renombrado_no_llega_a_perfil(entorno):
    cliente, _, prompts = entorno
    assert subir(cliente, ("cv.pdf", b"MZ" + b"x" * 100, "application/pdf")).status_code == 422
    assert not prompts


def test_cv_sin_texto_pide_alternativa_y_no_inventa_perfil(entorno, monkeypatch):
    cliente, estado, prompts = entorno
    antes = len(estado["perfiles"])
    monkeypatch.setattr(srv, "leer_texto_de_cv", lambda *a: "")
    r = subir(cliente)
    assert r.status_code == 422 and "foto más clara" in r.json()["detail"]
    assert not prompts and len(estado["perfiles"]) == antes


def test_datos_invalidos_no_llaman_modelo(entorno):
    cliente, _, prompts = entorno
    assert subir(cliente, datos={"edad": 17}).status_code == 422
    assert not prompts


def test_subida_desde_otro_sitio_se_rechaza(entorno):
    cliente, _, prompts = entorno
    r = cliente.post("/api/joven/cv", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and not prompts
