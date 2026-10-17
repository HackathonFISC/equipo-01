"""Las barras, las píldoras y los textos de límites siguen al umbral del solver (#255). Sin red ni claves.

Antes, `app.js` tenía el 60 min y el 15 % fijos: con el umbral en 95 min, Tocumen quedaba viable y trazado, pero
la pantalla decía «95 min · máx. 60 min». Estas pruebas cargan el `app.js` real en `node` (con un DOM de mentira) y
miran lo que dibuja, incluido el estado verdadero que devuelve la API después de editar el umbral, para que los
nombres de las claves que lee el JS sean los que manda el servidor.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest
from starlette.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]
APP_JS = RAIZ / "app" / "static" / "app.js"
INDEX = RAIZ / "app" / "static" / "index.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="hace falta node para ejecutar app.js")

GUION_NODE = r"""
const vm = require("vm");
const fs = require("fs");
const fuente = fs.readFileSync(process.argv[2], "utf8");
const entrada = JSON.parse(fs.readFileSync(0, "utf8"));
const elemento = () => ({ textContent: "", hidden: false, style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
  setAttribute() {}, closest() { return elemento(); }, addEventListener() {}, querySelector() { return elemento(); } });
const cache = {};
const document = { addEventListener() {}, getElementById: (id) => (cache[id] ||= elemento()) };
const contexto = vm.createContext({ document, window: {}, console });
vm.runInContext(fuente, contexto);
vm.runInContext(`estadoActual = ${JSON.stringify(entrada.estado)};`, contexto);
contexto.m = entrada.m; contexto.perfil = entrada.perfil; contexto.oferta = entrada.oferta;
contexto.motivo = entrada.motivo;
const html = vm.runInContext("renderizarOpcion(m, 0, perfil, oferta)", contexto);
vm.runInContext("renderizarKPIs()", contexto);
console.log(JSON.stringify({
  limites: vm.runInContext("limitesSolver(estadoActual)", contexto),
  maxTraslado: (html.match(/máx\. (\d+(?:\.\d+)?) min/) || [null, null])[1],
  barras: [...html.matchAll(/--v:([\d.]+)/g)].map((x) => Number(x[1])),
  pildoras: vm.runInContext("motivosDescarte(motivo)", contexto).replace(/<[^>]*>/g, ""),
  kpiFoot: cache["kpi-matches-foot"].textContent,
  ayuda: [cache["hint-limite-min"].textContent, cache["hint-limite-pct"].textContent],
}));
"""

MATCH = {
    "perfil_id": "P1", "oferta_id": "O1", "puesto": "Cajero", "empresa": "Empresa", "corregimiento_oferta": "Tocumen",
    "salario_mensual": 700, "puntaje": 100,
    "desglose": {"cobertura": 1, "minutos_traslado": 57, "pasaje_diario": 2.35, "pasaje_mensual": 103.4, "fuente_traslado": "prueba"},
}
MOTIVO = "traslado 100 min > 95; pasaje mensual $140.00 > 20% del salario ($100.00)"


def _dibujar(tmp_path: Path, operacion: dict | None) -> dict:
    estado = {
        "perfiles": [{"id": "P1", "nombre": "Joven de prueba", "habilidades": []}],
        "ofertas": [{"id": "O1", "puesto": "Cajero", "empresa": "Empresa", "salario_mensual": 700, "habilidades": []}],
        "matches": [], "postulaciones": [], "decisiones": [], "auditoria": {},
    }
    if operacion is not None:
        estado["operacion"] = operacion
    guion = tmp_path / "dibujar.js"
    guion.write_text(GUION_NODE, encoding="utf-8")
    datos = {"estado": estado, "m": MATCH, "perfil": estado["perfiles"][0], "oferta": estado["ofertas"][0], "motivo": MOTIVO}
    resultado = subprocess.run(
        ["node", str(guion), str(APP_JS)], input=json.dumps(datos), capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert resultado.returncode == 0, resultado.stderr
    return json.loads(resultado.stdout)


def test_sin_umbrales_en_el_estado_valen_los_de_siempre(tmp_path):
    dibujo = _dibujar(tmp_path, None)
    assert dibujo["limites"] == {"minutos": 60, "pasajePct": 15}
    assert dibujo["maxTraslado"] == "60"
    assert dibujo["barras"][1] == pytest.approx(95)  # 57 de 60 minutos
    assert dibujo["kpiFoot"] == "Traslado ≤ 60 min · pasaje ≤ 15 % del salario"
    assert dibujo["ayuda"] == ["60", "15"]


def test_con_umbral_95_la_barra_dice_max_95_min(tmp_path):
    dibujo = _dibujar(tmp_path, {"umbrales_solver": {"max_minutos_traslado": 95, "max_porcentaje_salario_pasaje": 0.2}})
    assert dibujo["limites"] == {"minutos": 95, "pasajePct": 20}
    assert dibujo["maxTraslado"] == "95"
    assert dibujo["barras"][1] == pytest.approx(60)  # 57 de 95 minutos, no 95 de 60
    assert dibujo["barras"][2] == pytest.approx(103.4 / 700 * 100 / 20 * 100)  # el pasaje, contra el 20 %
    pildoras = unescape(dibujo["pildoras"])  # la píldora escapa el «>» como &gt;
    assert "Traslado 100 min (máx. 95)" in pildoras
    assert "Pasaje > 20 % del salario" in pildoras
    assert dibujo["kpiFoot"] == "Traslado ≤ 95 min · pasaje ≤ 20 % del salario"
    assert dibujo["ayuda"] == ["95", "20"]


def test_con_el_estado_real_de_la_api_tras_editar_el_umbral(tmp_path, monkeypatch):
    """Las claves que lee el JS son las que manda el servidor: se edita el umbral por la API y se dibuja ese estado."""
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "umbrales.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(tmp_path / "outbox"))
    import app.server as srv

    cliente = TestClient(srv.app)
    assert cliente.post("/api/reset").status_code == 200
    respuesta = cliente.post("/api/orientador/umbrales", json={"max_minutos_traslado": 95})
    assert respuesta.status_code == 200, respuesta.text
    operacion = cliente.get("/api/estado").json()["operacion"]
    assert operacion["umbrales_solver"]["max_minutos_traslado"] == 95
    dibujo = _dibujar(tmp_path, operacion)
    assert dibujo["limites"]["minutos"] == 95
    assert dibujo["maxTraslado"] == "95"
    assert dibujo["limites"]["pasajePct"] == 15  # el 15 % no se tocó


@pytest.mark.parametrize(
    "umbrales, esperado",
    [
        ({"max_minutos_traslado": 95, "max_porcentaje_salario_pasaje": 0.155}, {"minutos": 95, "pasajePct": 15.5}),
        ({"max_minutos_traslado": "abc", "max_porcentaje_salario_pasaje": None}, {"minutos": 60, "pasajePct": 15}),
        ({"max_minutos_traslado": 0, "max_porcentaje_salario_pasaje": 0}, {"minutos": 60, "pasajePct": 15}),
        ({}, {"minutos": 60, "pasajePct": 15}),
    ],
)
def test_limites_con_valores_raros_no_rompen_la_pantalla(tmp_path, umbrales, esperado):
    assert _dibujar(tmp_path, {"umbrales_solver": umbrales})["limites"] == esperado


def test_app_js_ya_no_tiene_los_limites_fijos():
    js = APP_JS.read_text(encoding="utf-8")
    assert not re.search(r"\bLIMITE_TRASLADO_MIN\b|\bLIMITE_PASAJE_PCT\b", js), "quedó una constante de límite fija"
    assert not re.search(r"máx\. 60", js), "quedó un «máx. 60» escrito a mano"


def test_los_textos_de_limites_del_html_tienen_con_que_actualizarse():
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    for identificador in ("kpi-matches-foot", "hint-limite-min", "hint-limite-pct"):
        assert f'id="{identificador}"' in html, identificador
        assert f'"{identificador}"' in js, identificador
