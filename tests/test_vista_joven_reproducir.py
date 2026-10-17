"""Vista del joven sin clave (#192, #194): los ejemplos de Yaritza usan el guion del video y su grabación de Perfil.

`LLM_MODO=reproducir` con el LLM real de la ruta (`llm_puente`), sin falsos: el botón «Usar el ejemplo de Yaritza» de
`app/static/joven.js` (fase 1) y «Usar la respuesta de Yaritza» de `app/static/joven_fase2.js` (chat) traen las 5
respuestas de `data/demo/yaritza_chat.json`. El formulario y el chat arman el mismo prompt de Perfil, cuyo hash es
`llamadas.chat` de `data/demo/grabaciones_video.json`: la grabación del video sirve para los dos. Otras respuestas no
tienen grabación y la ruta sigue dando 503. `tests/fixtures/caso_yaritza.json` no cambia: es el de la terminal.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import app.joven_fase2 as fase2
import app.server as srv
from puente.agents.perfil import construir_perfil
from puente.graph import cargar_caso, cargar_taxonomia_grafo, nodo_matching_grafo
from puente.llm import clave_grabacion
from puente.rules.taxonomia import normalizar_habilidad
from puente.schemas import PerfilExtraido

RAIZ = Path(__file__).resolve().parents[1]
GUION = json.loads((RAIZ / "data/demo/yaritza_chat.json").read_text(encoding="utf-8"))
RESPUESTAS_GUION: list[str] = [paso["respuesta"] for paso in GUION["entrevista"]]
MANIFIESTO = json.loads((RAIZ / "data/demo/grabaciones_video.json").read_text(encoding="utf-8"))
GRABACION_CHAT = RAIZ / "data/grabaciones" / f"{MANIFIESTO['llamadas']['chat']}.json"
HABILIDADES_DEL_VIDEO = {"atencion_cliente", "tomar_pedidos", "manejo_caja", "cocina", "preparar_vegetales", "pago_facturas"}
L_V = ["lunes", "martes", "miercoles", "jueves", "viernes"]


def constante_js(ruta: str, nombre: str) -> Any:
    """La constante `nombre` de un .js de la vista tal como la usa su botón, leída del archivo y no copiada a mano."""
    js = (RAIZ / ruta).read_text(encoding="utf-8")
    inicio = js.index(f"const {nombre} = ") + len(f"const {nombre} = ")
    literal = js[inicio : js.index(";\n", inicio)]
    literal = re.sub(r"([{,]\s*)(\w+):", r'\1"\2":', literal)  # claves sin comillas -> JSON
    literal = re.sub(r",(\s*[\]}])", r"\1", literal)  # sin la coma final
    return json.loads(literal)


def ejemplo_de_joven_js() -> dict:
    """`EJEMPLO_YARITZA`: lo que llena «Usar el ejemplo de Yaritza» en el formulario de la fase 1."""
    return constante_js("app/static/joven.js", "EJEMPLO_YARITZA")


def respuestas_del_chat_js() -> list[str]:
    """`EJEMPLO_CHAT_YARITZA`: lo que pega «Usar la respuesta de Yaritza» en el chat, una por pregunta."""
    return constante_js("app/static/joven_fase2.js", "EJEMPLO_CHAT_YARITZA")


def cuerpo_del_ejemplo(ejemplo: dict) -> dict:
    """Lo que arma `datosDelFormulario()` tras «Usar el ejemplo de Yaritza»: la vista llena un solo bloque por horario
    y, sin bloque libre, deja los días sin marcar y manda `horario_libre: []`."""
    return {**ejemplo, "horario_libre": ejemplo["horario_libre"][:1], "horario_clases": ejemplo["horario_clases"][:1]}


def formulario_del_ejemplo() -> dict:
    """Lo que manda el chat al abrirse con el formulario del ejemplo: lo mismo que la fase 1, sin las respuestas."""
    cuerpo = cuerpo_del_ejemplo(ejemplo_de_joven_js())
    del cuerpo["respuestas"]
    return cuerpo


def revisar_habilidades(habilidades: list[dict]) -> None:
    """Las de la grabación del video, que Perfil vuelve a verificar contra la taxonomía y la evidencia literal."""
    evidencias = {h["skill_id"]: h["evidencia"] for h in habilidades}
    assert set(evidencias) == HABILIDADES_DEL_VIDEO
    grabacion = json.loads(GRABACION_CHAT.read_text(encoding="utf-8"))
    assert evidencias == {h["skill_id"]: h["evidencia"] for h in grabacion["salida"]["habilidades"]}  # ni una menos
    for evidencia in evidencias.values():
        assert evidencia in " ".join(RESPUESTAS_GUION)  # la frase de Yaritza, tal cual
    assert evidencias["pago_facturas"] == "a pagar la luz por Yappy"


@pytest.fixture
def reproducir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Perfil con el LLM real de la ruta, en modo reproducir y sin red: solo lee `data/grabaciones`."""
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setenv("LLM_PROVEEDOR", MANIFIESTO["proveedor"])
    monkeypatch.setenv("PUENTE_MODELO_LLM", MANIFIESTO["modelo"])
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(RAIZ / "data/grabaciones"))
    monkeypatch.delenv("LLM_PROVEEDOR_RESPALDO", raising=False)


@pytest.fixture
def entorno(reproducir, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Estado propio del tablero y la ruta con su LLM real (`llm_puente`)."""
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "joven.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(srv, "llm_perfil_joven", srv.llm_puente)
    monkeypatch.setattr(srv, "_envios_perfil", {})  # el límite por minuto empieza en cero
    with TestClient(srv.app) as cliente:
        yield cliente, estado


def test_los_dos_botones_traen_las_respuestas_del_guion() -> None:
    ejemplo = ejemplo_de_joven_js()
    # Letra por letra y en orden: una letra distinta cambia el hash y la vista ya no encuentra la lectura grabada.
    assert ejemplo["respuestas"] == RESPUESTAS_GUION
    assert respuestas_del_chat_js() == RESPUESTAS_GUION
    formulario = GUION["formulario"]
    for campo in ("edad", "corregimiento", "horario_clases", "horario_libre", "salario_minimo", "presupuesto_pasaje_diario"):
        assert ejemplo[campo] == formulario[campo], campo
    assert ejemplo["corregimiento"] in srv.corregimientos_con_traslado()


def test_formulario_y_chat_arman_el_prompt_de_la_grabacion_del_video(tmp_path: Path) -> None:
    """El formulario no entra al prompt: la fase 1 y el chat con los ejemplos piden lo mismo, y su hash, calculado como
    lo hace `puente.llm` al buscar la grabación, es `llamadas.chat` del manifiesto del video."""
    taxonomia = cargar_taxonomia_grafo()
    prompts: list[str] = []

    def anotar(prompt: str) -> PerfilExtraido:
        prompts.append(prompt)
        return PerfilExtraido()

    pedido = srv.PerfilJovenRequest.model_validate(cuerpo_del_ejemplo(ejemplo_de_joven_js()))
    de_la_fase_1 = srv.preparar_entrada_joven(pedido)  # con limpiar_entrada, como la ruta

    # El chat con las funciones de sus rutas (app/joven_fase2.py): abrir, una respuesta por pregunta y cerrar.
    estado: dict[str, Any] = {"perfiles": [], "_db_path": str(tmp_path / "chat.db")}
    pid = fase2.iniciar_entrevista(estado, formulario_del_ejemplo())["perfil_id"]
    for indice, respuesta in enumerate(respuestas_del_chat_js()):
        assert fase2.responder_pregunta(estado, pid, indice, respuesta)["respuesta_guardada"] == respuesta
    del_chat = fase2.preparar_cierre(estado, pid)

    for entrada in (de_la_fase_1, del_chat):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_habilidad, llm=anotar)
    assert len(prompts) == 2
    assert prompts[0] == prompts[1]
    # Perfil llama a `generar_estructurado` sin sistema, con PerfilExtraido y sin archivos.
    clave = clave_grabacion(MANIFIESTO["proveedor"], MANIFIESTO["modelo"], None, prompts[0], PerfilExtraido, [])
    assert clave == MANIFIESTO["llamadas"]["chat"]
    assert GRABACION_CHAT.is_file()


def test_sin_horario_libre_yaritza_queda_en_las_dos_listas_de_la_demo(reproducir) -> None:
    """El guion no trae horario libre y el ejemplo tampoco: el Matching no recorta por días. Con las ofertas de la demo
    por terminal, la Yaritza del formulario queda en las dos listas cortas; con lunes a viernes de 07:00 a 17:00
    saldría de la de cocina, que es sábado y domingo."""
    _, caso = cargar_caso()
    taxonomia = cargar_taxonomia_grafo()
    cuerpo = cuerpo_del_ejemplo(ejemplo_de_joven_js())
    assert cuerpo["horario_libre"] == []

    listas: dict[str, set[str]] = {}
    lunes_a_viernes = [{"dias": L_V, "hora_inicio": "07:00", "hora_fin": "17:00"}]
    for nombre, libre in (("sin horario libre", []), ("lunes a viernes", lunes_a_viernes)):
        pedido = srv.PerfilJovenRequest.model_validate({**cuerpo, "horario_libre": libre})
        perfil, _ = construir_perfil(
            srv.preparar_entrada_joven(pedido), taxonomia=taxonomia, normalizar=normalizar_habilidad, llm=srv.llm_puente
        )
        cambios = nodo_matching_grafo({**caso, "perfiles": [perfil, *caso["perfiles"]]})
        listas[nombre] = {m.oferta_id for m in cambios["lista_corta"] if m.perfil_id == perfil.id}
    assert listas["sin horario libre"] == {"of-cajero-vista-alegre", "of-ayudante-cocina-chorrera"}
    assert listas["lunes a viernes"] == {"of-cajero-vista-alegre"}


def test_ejemplo_de_yaritza_arma_el_perfil_con_la_grabacion(entorno) -> None:
    cliente, estado = entorno
    perfiles_antes = len(estado["perfiles"])

    r = cliente.post("/api/joven/perfil", json=cuerpo_del_ejemplo(ejemplo_de_joven_js()))
    assert r.status_code == 201, r.text
    resultado = r.json()
    pid = resultado["perfil"]["id"]
    assert resultado["token"] == estado["_tokens_joven"][pid]
    revisar_habilidades(resultado["perfil"]["habilidades"])
    assert resultado["perfil"]["salario_minimo"] == 400  # manda el formulario, no el LLM

    # El perfil queda listo: entra al tablero, el grafo se recalcula y el resultado se puede volver a pedir.
    assert len(estado["perfiles"]) == perfiles_antes + 1
    assert estado["estado_grafo"] == "interrupt_orientador"
    otra_vez = cliente.get(f"/api/joven/{pid}/resultado", headers={"X-Token-Joven": resultado["token"]})
    assert otra_vez.status_code == 200
    assert otra_vez.json()["perfil"]["habilidades"] == resultado["perfil"]["habilidades"]


def test_chat_con_la_respuesta_de_yaritza_arma_el_perfil_con_la_grabacion(entorno) -> None:
    """Como el navegador: el formulario del ejemplo abre el chat, «Usar la respuesta de Yaritza» en cada pregunta y
    «Terminar y ver mi resultado»."""
    cliente, estado = entorno
    r = cliente.post("/api/joven/entrevista", json=formulario_del_ejemplo())
    assert r.status_code == 201, r.text
    pid, cabeceras = r.json()["perfil_id"], {"X-Token-Joven": r.json()["token"]}
    for indice, respuesta in enumerate(respuestas_del_chat_js()):
        r = cliente.post(f"/api/joven/{pid}/entrevista", json={"indice": indice, "respuesta": respuesta}, headers=cabeceras)
        assert r.status_code == 200, r.text
        assert r.json()["respuesta_guardada"] == respuesta  # nada que quitar: el prompt queda como el grabado
    assert r.json()["completa"] is True

    r = cliente.post(f"/api/joven/{pid}/entrevista/terminar", json={}, headers=cabeceras)
    assert r.status_code == 201, r.text
    revisar_habilidades(r.json()["perfil"]["habilidades"])
    assert pid in {p["id"] for p in estado["perfiles"]}
    assert estado["estado_grafo"] == "interrupt_orientador"


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
