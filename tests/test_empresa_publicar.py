"""La empresa publica una vacante y recibe solo su oferta, no el estado del tablero (#258).

`POST /api/ofertas` devuelve todo `/api/estado`, con los jóvenes y sus datos: es la ruta del orientador. La página
`/empresa` usa `POST /api/empresa/ofertas`. Sin red ni LLM; cada prueba usa su propio estado, su SQLite y su outbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.empresa as empresa
import app.server as srv

CUERPO_BLOQUEADA = {
    "puesto": "Recepcionista",
    "empresa": "Mueblería Nueva (Empresa de Prueba)",
    "corregimiento": "La Chorrera",
    "salario_mensual": 650,
    "requisitos_texto": "Joven de 18 a 25 años con buena presencia, atención amable.",
    "habilidades": ["atencion_cliente"],
}


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "publicar.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    with TestClient(srv.app) as cliente:
        yield cliente, estado


def _nombres_e_ids_de_los_jovenes(estado: dict) -> list[str]:
    datos = []
    for p in estado["perfiles"]:
        datos += [p["id"], p["nombre"]]
    return datos


def test_la_empresa_recibe_solo_el_resultado_de_su_oferta(entorno) -> None:
    cliente, estado = entorno
    jovenes = _nombres_e_ids_de_los_jovenes(estado)
    assert len(jovenes) >= 6  # hay jóvenes en el tablero: si salieran en la respuesta, se verían

    r = cliente.post("/api/empresa/ofertas", json=CUERPO_BLOQUEADA)
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert set(cuerpo) == {"oferta"}
    oferta = cuerpo["oferta"]
    assert set(oferta) == {"id", "puesto", "empresa", "bloqueada", "alertas"}
    assert oferta["puesto"] == "Recepcionista" and oferta["bloqueada"] is True
    frases = {a["frase"] for a in oferta["alertas"]}
    assert "de 18 a 25 años" in frases and "buena presencia" in frases
    assert all(set(a) == {"tipo", "frase", "regla"} for a in oferta["alertas"])
    assert {a["tipo"] for a in oferta["alertas"]} == {"bloqueo", "alerta"}

    # Ningún nombre ni id de joven, y nada del tablero: ni perfiles, ni matches, ni postulaciones, ni decisiones.
    texto = r.text
    for dato in jovenes:
        assert dato not in texto, dato
    for clave in ("perfil_id", "PERFIL-", "perfiles", "matches", "postulaciones", "decisiones", "descartados", "metricas"):
        assert clave not in texto, clave


def test_la_oferta_publicada_por_la_empresa_entra_igual_al_tablero(entorno) -> None:
    """Responder menos no cambia lo que hace: la oferta pasa por la Ley 11 y queda en el estado del orientador."""
    cliente, estado = entorno
    oferta = cliente.post("/api/empresa/ofertas", json=CUERPO_BLOQUEADA).json()["oferta"]
    en_tablero = next(o for o in cliente.get("/api/estado").json()["ofertas"] if o["id"] == oferta["id"])
    assert en_tablero["bloqueada"] is True and en_tablero["empresa"] == CUERPO_BLOQUEADA["empresa"]
    # Una oferta limpia sale sin bloqueo ni alertas y sigue sin traer nada de los jóvenes.
    limpia = cliente.post(
        "/api/empresa/ofertas",
        json={**CUERPO_BLOQUEADA, "puesto": "Cajero", "requisitos_texto": "Cobro en caja y atención amable."},
    )
    assert limpia.status_code == 201
    assert limpia.json()["oferta"]["bloqueada"] is False and limpia.json()["oferta"]["alertas"] == []
    assert "PERFIL-" not in limpia.text


def test_el_orientador_sigue_recibiendo_el_estado_completo_por_su_ruta(entorno) -> None:
    cliente, estado = entorno
    r = cliente.post("/api/ofertas", json=CUERPO_BLOQUEADA)
    assert r.status_code == 200
    cuerpo = r.json()
    assert {"perfiles", "ofertas", "matches", "postulaciones"} <= set(cuerpo)
    assert any(p["nombre"] == "Yaritza" for p in cuerpo["perfiles"])  # lo que el tablero necesita para pintarse


def test_una_empresa_nueva_recibe_su_codigo_al_publicar(entorno) -> None:
    """Antes el código aparecía en la consola en la primera visita a una ruta de la empresa, no al publicar."""
    cliente, estado = entorno
    empresa_id = empresa.id_empresa(CUERPO_BLOQUEADA["empresa"])
    assert empresa_id not in (estado.get("_tokens_empresa") or {})
    assert cliente.post("/api/empresa/ofertas", json=CUERPO_BLOQUEADA).status_code == 201
    assert empresa_id in estado["_tokens_empresa"]
    # El código no viaja en la respuesta de publicar ni en el estado público: solo sale en la consola.
    assert estado["_tokens_empresa"][empresa_id] not in json.dumps(cliente.get("/api/estado").json())


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {**CUERPO_BLOQUEADA, "puesto": "x"}, {**CUERPO_BLOQUEADA, "habilidades": []}, {**CUERPO_BLOQUEADA, "extra": 1}],
)
def test_un_pedido_invalido_da_422_sin_estado_ni_jovenes(entorno, cuerpo: dict) -> None:
    cliente, estado = entorno
    r = cliente.post("/api/empresa/ofertas", json=cuerpo)
    assert r.status_code == 422
    assert "perfiles" not in r.text and "PERFIL-" not in r.text and "Yaritza" not in r.text
