"""Las páginas piden sus archivos estáticos con una sola versión (#255).

El servidor no manda `Cache-Control` para `/static`, así que un navegador abierto antes de un cambio se queda con
el JS o el CSS viejo mientras la URL no cambie. La protección es el `?v=` de cada referencia: al corregir un
archivo se sube la versión **en todas las referencias a la vez**. Si una página queda con otra versión (o sin
versión), el navegador puede mezclar archivos de épocas distintas, y eso es justo lo que pasó con `app.js?v=3`
junto a `styles.css?v=4`. Estas pruebas fallan en ese caso. Sin red ni claves.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]
STATIC = RAIZ / "app" / "static"

# `/static/<archivo>.js|css` con su `?v=` si lo trae, tanto en el HTML (src, href) como en el JS que agrega un
# <link> por su cuenta (joven_fase2.js).
REFERENCIA = re.compile(r"/static/([\w.-]+\.(?:js|css))(?:\?v=([\w.-]+))?")

PAGINAS = {  # ruta del servidor -> archivo que la sirve
    "/": "landing.html",
    "/orientador": "index.html",
    "/inicio": "landing.html",
    "/joven": "joven.html",
    "/empresa": "empresa.html",
    "/empresa/lista-corta": "empresa_lista.html",
    "/formacion": "formacion.html",
}


def _referencias() -> list[tuple[str, str, str | None]]:
    """(archivo que referencia, archivo estático referenciado, versión o None) en todo `app/static`."""
    encontradas = []
    for origen in sorted([*STATIC.glob("*.html"), *STATIC.glob("*.js")]):
        for linea in origen.read_text(encoding="utf-8").splitlines():
            if linea.lstrip().startswith("//"):
                continue  # un comentario puede nombrar un archivo sin cargarlo
            for archivo, version in REFERENCIA.findall(linea):
                encontradas.append((origen.name, archivo, version or None))
    return encontradas


def test_hay_referencias_que_revisar():
    # Si esta prueba falla, las demás no estarían revisando nada.
    archivos = {archivo for _, archivo, _ in _referencias()}
    assert {"app.js", "styles.css", "joven.js", "joven_fase2.js", "joven_fase2.css", "joven.css", "empresa_lista.js", "empresa_lista.css"} <= archivos


def test_toda_referencia_a_un_archivo_estatico_lleva_version():
    sin_version = [f"{origen} -> {archivo}" for origen, archivo, version in _referencias() if version is None]
    assert not sin_version, "referencias sin ?v= (el navegador las guardaría para siempre): " + ", ".join(sin_version)


def test_todas_las_paginas_piden_la_misma_version():
    por_version: dict[str, list[str]] = {}
    for origen, archivo, version in _referencias():
        por_version.setdefault(str(version), []).append(f"{origen} -> {archivo}")
    assert len(por_version) == 1, (
        "las referencias piden versiones distintas; al corregir un estático hay que subir TODAS a la vez: "
        + " | ".join(f"?v={version}: {', '.join(sorted(set(refs)))}" for version, refs in sorted(por_version.items()))
    )


def test_cada_referencia_apunta_a_un_archivo_que_existe():
    faltan = sorted({f"{origen} -> {archivo}" for origen, archivo, _ in _referencias() if not (STATIC / archivo).is_file()})
    assert not faltan, "referencias a archivos que no existen en app/static: " + ", ".join(faltan)


def test_el_servidor_entrega_cada_pagina_con_la_version_del_archivo(monkeypatch, tmp_path):
    """Lo que el navegador recibe de verdad: las cuatro páginas del servidor, con la misma versión que los archivos."""
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "version.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    versiones = {}
    cliente = TestClient(srv.app)
    for ruta, archivo in PAGINAS.items():
        respuesta = cliente.get(ruta)
        assert respuesta.status_code == 200, ruta
        # Por bytes: leer el archivo como texto cambiaría los CRLF de un checkout de Windows y la comparación fallaría.
        assert respuesta.content == (STATIC / archivo).read_bytes(), f"{ruta} no sirve {archivo}"
        versiones[ruta] = {version for _, version in re.findall(r"/static/([\w.-]+\.(?:js|css))\?v=([\w.-]+)", respuesta.text)}
    todas = set().union(*versiones.values())
    assert len(todas) == 1, f"las páginas entregadas piden versiones distintas: {versiones}"
    # y los archivos que cargan por su cuenta piden esa misma
    en_js = {version for origen, _, version in _referencias() if origen.endswith(".js")}
    assert en_js == todas, f"el JS pide {en_js} y las páginas {todas}"
