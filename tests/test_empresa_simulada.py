"""/empresa con la empresa elegida en simulación y sus volantes y vacantes (#310).

El selector y la lista solo cambian lo que se ve en /empresa: salen de rutas que no traen jóvenes, y la lista corta
sigue pidiendo el código de la empresa. Sin red ni claves: el volante de ejemplo se lee con la respuesta grabada.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

import app.server as srv

RAIZ = Path(__file__).resolve().parents[1]
VOLANTE_ESTATICO = RAIZ / "app" / "static" / "ejemplos" / "volante_sintetico.jpg"
VOLANTE_FIXTURE = RAIZ / "tests" / "fixtures" / "volante_sintetico.jpg"
PROHIBIDO = ("PERFIL-", "perfil_id", "nombre_joven", "postulaciones", "candidatos", "perfiles")
CAMPOS_VACANTE = {
    "id", "puesto", "empresa", "bloqueada", "alertas", "corregimiento", "salario_mensual", "horario", "habilidades",
    "requisitos_texto",
}


def _cliente() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://testserver")


def _sin_datos_de_jovenes(texto: str) -> None:
    for clave in PROHIBIDO:
        assert clave not in texto, f"«{clave}» no debe salir en las rutas de la empresa"


def test_el_selector_lista_las_empresas_con_sus_vacantes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")

    async def _ejecutar() -> None:
        async with _cliente() as c:
            await c.post("/api/reset")
            assert "PERFIL-" in json.dumps(srv.ESTADO_GLOBAL, default=str)  # el estado sí tiene jóvenes: la ruta no
            r = await c.get("/api/empresa/empresas")
            assert r.status_code == 200, r.text
            filas = r.json()
            assert isinstance(filas, list) and len(filas) >= 5
            assert all(set(f) == {"empresa_id", "nombre", "n_vacantes"} for f in filas)
            ids = {f["empresa_id"]: f for f in filas}
            assert len(ids) == len(filas)  # sin empresas repetidas
            assert ids["supermercado-oeste-s-a"]["nombre"].startswith("Supermercado Oeste")
            assert sum(f["n_vacantes"] for f in filas) == len([o for o in srv.ESTADO_GLOBAL["ofertas"] if o.get("empresa")])
            _sin_datos_de_jovenes(r.text)

    asyncio.run(_ejecutar())


def test_las_vacantes_son_solo_las_de_esa_empresa_y_sin_jovenes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")

    async def _ejecutar() -> None:
        async with _cliente() as c:
            await c.post("/api/reset")
            filas = (await c.get("/api/empresa/empresas")).json()
            vistas: set[str] = set()
            for fila in filas:
                r = await c.get("/api/empresa/vacantes", params={"empresa": fila["empresa_id"]})
                assert r.status_code == 200, r.text
                cuerpo = r.json()
                assert set(cuerpo) == {"empresa_id", "nombre", "vacantes"}
                assert cuerpo["empresa_id"] == fila["empresa_id"] and cuerpo["nombre"] == fila["nombre"]
                assert len(cuerpo["vacantes"]) == fila["n_vacantes"]
                for v in cuerpo["vacantes"]:
                    assert set(v) == CAMPOS_VACANTE
                    assert v["empresa"] == fila["nombre"]
                    assert v["id"] not in vistas  # ninguna oferta sale en dos empresas
                    vistas.add(v["id"])
                _sin_datos_de_jovenes(r.text)
            assert vistas == {o["id"] for o in srv.ESTADO_GLOBAL["ofertas"] if o.get("empresa")}

            # El resultado de la Ley 11 viaja con la frase: la oferta que pide sexo o edad sale bloqueada.
            bloqueadas = [
                v
                for fila in filas
                for v in (await c.get("/api/empresa/vacantes", params={"empresa": fila["empresa_id"]})).json()["vacantes"]
                if v["bloqueada"]
            ]
            assert bloqueadas and all(v["alertas"] and v["alertas"][0]["frase"] for v in bloqueadas)

            # Una empresa que no existe, o sin indicar cuál, no devuelve nada.
            assert (await c.get("/api/empresa/vacantes", params={"empresa": "no-existe"})).status_code == 404
            assert (await c.get("/api/empresa/vacantes")).status_code == 404

    asyncio.run(_ejecutar())


def test_el_volante_de_ejemplo_sale_en_la_lista_de_su_empresa(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    assert VOLANTE_ESTATICO.read_bytes() == VOLANTE_FIXTURE.read_bytes()  # el que se sirve es el que tiene grabación

    async def _ejecutar() -> None:
        async with _cliente() as c:
            await c.post("/api/reset")
            estatico = await c.get("/static/ejemplos/volante_sintetico.jpg")
            assert estatico.status_code == 200 and estatico.content == VOLANTE_FIXTURE.read_bytes()
            r = await c.post("/api/empresa/volante", files={"archivo": ("volante_sintetico.jpg", estatico.content, "image/jpeg")})
            assert r.status_code == 201, r.text
            oferta = r.json()["oferta"]

            filas = (await c.get("/api/empresa/empresas")).json()
            fila = next(f for f in filas if f["nombre"] == oferta["empresa"])
            cuerpo = (await c.get("/api/empresa/vacantes", params={"empresa": fila["empresa_id"]})).json()
            suya = next(v for v in cuerpo["vacantes"] if v["id"] == oferta["id"])
            assert suya["bloqueada"] is True and any("18 a 25" in str(a) for a in suya["alertas"])
            assert all(v["empresa"] == oferta["empresa"] for v in cuerpo["vacantes"])

            # Subirlo otra vez no lo duplica: el servidor lo rechaza como repetido.
            otra = await c.post("/api/empresa/volante", files={"archivo": ("v.jpg", estatico.content, "image/jpeg")})
            assert otra.status_code == 409 and oferta["id"] in otra.text

    asyncio.run(_ejecutar())


def test_la_lista_corta_sigue_pidiendo_el_codigo_de_la_empresa() -> None:
    async def _ejecutar() -> None:
        async with _cliente() as c:
            await c.post("/api/reset")
            assert (await c.get("/api/empresa/postulaciones")).status_code == 401
            assert (await c.get("/api/empresa/supermercado-oeste-s-a/lista-corta")).status_code == 401
            assert (await c.get("/api/empresa/supermercado-oeste-s-a/mensajes")).status_code == 401

    asyncio.run(_ejecutar())


def test_la_pagina_de_la_empresa_trae_el_selector_los_volantes_y_el_ejemplo() -> None:
    html = (RAIZ / "app" / "static" / "empresa.html").read_text(encoding="utf-8")
    assert "Elige tu empresa (simulación de la demo)" in html
    assert "Tus volantes y vacantes" in html
    assert "Probar con un volante de ejemplo" in html
    assert "/api/empresa/empresas" in html and "/api/empresa/vacantes" in html
    assert "/static/ejemplos/volante_sintetico.jpg" in html and "/api/empresa/volante" in html
    assert '"empresa"' in html and "searchParams" in html and "localStorage" in html  # ?empresa=<id> y respaldo
    assert "innerHTML" not in html
    # El código de la empresa y la lista corta no se tocan desde esta página.
    assert "x-token-empresa" not in html.lower() and "codigo=" not in html
