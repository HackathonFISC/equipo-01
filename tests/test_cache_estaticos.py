"""Las páginas y `/static` salen con `Cache-Control: no-cache` (#255). Sin red ni claves.

Sin la cabecera, el navegador decide por su cuenta cuánto guarda un archivo que no cambió de URL, y un JS
corregido puede no llegar a una pantalla que ya estaba abierta. `no-cache` obliga a preguntar al servidor, que
responde 304 sin cuerpo si el archivo no cambió.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

PAGINAS = ("/", "/joven", "/empresa", "/empresa/lista-corta")
ESTATICOS = ("/static/app.js", "/static/styles.css", "/static/joven_fase2.js", "/static/joven_fase2.css")


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    return TestClient(srv.app)


@pytest.mark.parametrize("ruta", [*PAGINAS, *ESTATICOS])
def test_paginas_y_estaticos_piden_revalidar(cliente, ruta):
    respuesta = cliente.get(ruta)
    assert respuesta.status_code == 200, ruta
    assert respuesta.headers.get("cache-control") == "no-cache", ruta


@pytest.mark.parametrize("ruta", ["/", "/static/app.js"])
def test_head_tambien_lleva_la_cabecera(cliente, ruta):
    assert cliente.head(ruta).headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize("ruta", ESTATICOS)
def test_un_estatico_sin_cambios_se_revalida_con_304_sin_cuerpo(cliente, ruta):
    etiqueta = cliente.get(ruta).headers.get("etag")
    assert etiqueta, f"{ruta} debe traer ETag para poder revalidar"
    segunda = cliente.get(ruta, headers={"If-None-Match": etiqueta})
    assert segunda.status_code == 304
    assert segunda.content == b""
    assert segunda.headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize("ruta", PAGINAS)
def test_una_pagina_siempre_llega_completa_y_fresca(cliente, ruta):
    # Las páginas las entrega un FileResponse suelto, que no evalúa If-None-Match: con no-cache el navegador
    # pregunta cada vez y recibe la página entera (pesan unos 30 KB), así que nunca ve una versión vieja.
    primera = cliente.get(ruta)
    segunda = cliente.get(ruta, headers={"If-None-Match": primera.headers.get("etag", "")})
    assert segunda.status_code == 200
    assert segunda.content == primera.content
    assert segunda.headers.get("cache-control") == "no-cache"


def test_la_api_no_cambia(cliente):
    # La cabecera es solo para lo que el navegador carga como archivo: los datos de la API no se tocan.
    assert "cache-control" not in cliente.get("/api/estado").headers
