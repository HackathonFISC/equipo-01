"""Vista del joven sin clave (#192): el ejemplo de Yaritza de la fase 1 usa la grabación vigente de Perfil.

`LLM_MODO=reproducir` con el LLM real de la ruta (`llm_puente`), sin falsos: la entrevista que llena el botón
«Usar el ejemplo de Yaritza» de `app/static/joven.js` arma el mismo prompt que el grafo para
`tests/fixtures/caso_yaritza.json`, cuya lectura está en `data/grabaciones` (#132, ver
`tests/test_grabacion_yaritza.py`). Otras respuestas no tienen grabación y la ruta sigue dando 503.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.server as srv
from puente.agents.perfil import construir_perfil
from puente.graph import cargar_taxonomia_grafo
from puente.rules.taxonomia import normalizar_habilidad
from puente.schemas import PerfilExtraido

RAIZ = Path(__file__).resolve().parents[1]


def ejemplo_de_joven_js() -> dict:
    """`EJEMPLO_YARITZA` tal como lo manda el botón de `joven.js`, leído del archivo y no copiado a mano."""
    js = (RAIZ / "app/static/joven.js").read_text(encoding="utf-8")
    inicio = js.index("const EJEMPLO_YARITZA = ")
    literal = js[js.index("{", inicio) : js.index("\n};", inicio) + 2]
    literal = re.sub(r"([{,]\s*)(\w+):", r'\1"\2":', literal)  # claves sin comillas -> JSON
    literal = re.sub(r",(\s*[\]}])", r"\1", literal)  # sin la coma final
    return json.loads(literal)


def cuerpo_del_ejemplo(ejemplo: dict) -> dict:
    """Lo que arma `datosDelFormulario()` tras «Usar el ejemplo de Yaritza»: los horarios van en listas."""
    return {**ejemplo, "horario_libre": [ejemplo["horario_libre"]], "horario_clases": [ejemplo["horario_clases"]]}


def respuestas_del_fixture() -> list[str]:
    caso = json.loads((RAIZ / "tests/fixtures/caso_yaritza.json").read_text(encoding="utf-8"))
    return next(p for p in caso["entradas_perfil"] if p["id"] == "yaritza")["respuestas"]


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Estado propio del tablero y Perfil con el LLM real de la ruta, en modo reproducir y sin red."""
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setenv("LLM_PROVEEDOR", "anthropic")
    monkeypatch.setenv("PUENTE_MODELO_LLM", "claude-opus-5-5")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(RAIZ / "data/grabaciones"))
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "joven.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(srv, "llm_perfil_joven", srv.llm_puente)
    monkeypatch.setattr(srv, "_envios_perfil", {})  # el límite por minuto empieza en cero
    with TestClient(srv.app) as cliente:
        yield cliente, estado


def test_ejemplo_de_joven_js_trae_las_respuestas_del_fixture() -> None:
    ejemplo = ejemplo_de_joven_js()
    assert ejemplo["respuestas"] == respuestas_del_fixture()  # mismas respuestas y mismo orden
    assert ejemplo["edad"] == 20
    assert ejemplo["salario_minimo"] == 400  # lo que dice la quinta respuesta
    assert ejemplo["corregimiento"] in srv.corregimientos_con_traslado()


def test_ruta_y_grafo_arman_el_mismo_prompt_de_perfil() -> None:
    """El formulario no entra al prompt: con las mismas respuestas, la ruta y el grafo piden lo mismo."""
    taxonomia = cargar_taxonomia_grafo()
    prompts: list[str] = []

    def anotar(prompt: str) -> PerfilExtraido:
        prompts.append(prompt)
        return PerfilExtraido()

    caso = json.loads((RAIZ / "tests/fixtures/caso_yaritza.json").read_text(encoding="utf-8"))
    del_grafo = next(p for p in caso["entradas_perfil"] if p["id"] == "yaritza")
    pedido = srv.PerfilJovenRequest.model_validate(cuerpo_del_ejemplo(ejemplo_de_joven_js()))
    de_la_ruta = srv.preparar_entrada_joven(pedido)  # con limpiar_entrada, como la ruta
    for entrada in (del_grafo, de_la_ruta):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_habilidad, llm=anotar)
    assert len(prompts) == 2
    assert prompts[0] == prompts[1]


def test_ejemplo_de_yaritza_arma_el_perfil_con_la_grabacion(entorno) -> None:
    cliente, estado = entorno
    perfiles_antes = len(estado["perfiles"])

    r = cliente.post("/api/joven/perfil", json=cuerpo_del_ejemplo(ejemplo_de_joven_js()))
    assert r.status_code == 201, r.text
    resultado = r.json()
    pid = resultado["perfil"]["id"]
    assert resultado["token"] == estado["_tokens_joven"][pid]

    # Las habilidades de la grabación, que Perfil vuelve a verificar contra la taxonomía y la evidencia literal.
    habilidades = {h["skill_id"]: h["evidencia"] for h in resultado["perfil"]["habilidades"]}
    assert set(habilidades) == {"atencion_cliente", "tomar_pedidos", "manejo_caja", "cocina"}
    assert habilidades["cocina"] == "Sé usar el celular y ayudo a cocinar el almuerzo"
    assert resultado["perfil"]["salario_minimo"] == 400  # manda el formulario, no el LLM

    # El perfil queda listo: entra al tablero, el grafo se recalcula y el resultado se puede volver a pedir.
    assert len(estado["perfiles"]) == perfiles_antes + 1
    assert estado["estado_grafo"] == "interrupt_orientador"
    otra_vez = cliente.get(f"/api/joven/{pid}/resultado", headers={"X-Token-Joven": resultado["token"]})
    assert otra_vez.status_code == 200
    assert otra_vez.json()["perfil"]["habilidades"] == resultado["perfil"]["habilidades"]


def test_otras_respuestas_sin_grabacion_siguen_en_503(entorno) -> None:
    cliente, estado = entorno
    perfiles_antes = len(estado["perfiles"])
    cuerpo = cuerpo_del_ejemplo(ejemplo_de_joven_js())
    cuerpo["respuestas"] = ["Cuido a mis hermanos y les ayudo con las tareas de la escuela."]

    r = cliente.post("/api/joven/perfil", json=cuerpo)
    assert r.status_code == 503
    assert "orientador" in r.json()["detail"] and "terminal" in r.json()["detail"]
    assert "grabaci" not in r.text  # el hash y la ruta de la grabación quedan en la consola, no en la respuesta
    assert len(estado["perfiles"]) == perfiles_antes
