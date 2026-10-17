"""Vista del joven (#52, fase 1): formulario fijo y entrevista, consentimiento y resultado.

Sin red ni LLM real: el agente Perfil (#6) recibe un LLM falso que anota cada prompt, y el
conftest deja `puente.llm` en modo `reproducir`. Cada prueba usa su propio estado del tablero,
su SQLite y su outbox en `tmp_path`, así no toca el `ESTADO_GLOBAL` de las demás.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from starlette.testclient import TestClient

import app.server as srv
from puente.schemas import EntradaPerfil, EstadoPostulacion, PerfilExtraido
from puente.tools import db

CEDULA = "8-123-4567"
TELEFONO = "6123-4567"

RESPUESTAS_YARITZA = [
    "Atiendo la fonda de mi mamá todas las mañanas. Atiendo a los clientes y les tomo el pedido, cobro y doy el vuelto.",
    "Los sábados ayudo en una tienda del barrio a acomodar la mercancía en los estantes.",
    "No he hecho voluntariado.",
    "Sé usar el celular y ayudo a cocinar en la fonda.",
    "Mis vecinos me piden ayuda para sacar cuentas de sus ventas.",
]


def formulario_yaritza(**cambios) -> dict:
    datos = {
        "nombre": "Yaritza",
        "edad": 20,
        "corregimiento": "La Chorrera",
        "horario_clases": [
            {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "18:00", "hora_fin": "22:00"}
        ],
        "horario_libre": [
            {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "07:00", "hora_fin": "17:00"}
        ],
        "salario_minimo": 600,
        "presupuesto_pasaje_diario": 3.0,
        "respuestas": list(RESPUESTAS_YARITZA),
    }
    datos.update(cambios)
    return datos


class LLMFalso:
    """Imita `llm_puente`: anota el prompt y devuelve habilidades propuestas, como lo haría Claude."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> PerfilExtraido:
        self.prompts.append(prompt)
        return PerfilExtraido.model_validate(
            {
                "habilidades": [
                    {"skill_id": "atencion_cliente", "evidencia": "Atiendo a los clientes y les tomo el pedido"},
                    {"skill_id": "manejo_caja", "evidencia": "cobro y doy el vuelto"},
                    {"skill_id": "reposicion_productos", "evidencia": "acomodar la mercancía en los estantes"},
                    # Sin frase literal en las respuestas: Perfil la descarta en código.
                    {"skill_id": "excel_basico", "evidencia": "domino Excel avanzado"},
                ],
                "edad": 35,  # lo manda el formulario; el código lo ignora
            }
        )


class LLMCaido:
    def __call__(self, prompt: str) -> PerfilExtraido:
        raise RuntimeError("sin clave de API (prueba)")


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Estado propio del tablero con los datos sintéticos de la demo y un LLM falso para Perfil."""
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "joven.db")
    outbox = tmp_path / "outbox"
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", outbox)
    llm = LLMFalso()
    monkeypatch.setattr(srv, "llm_perfil_joven", llm)
    with TestClient(srv.app) as cliente:
        yield cliente, estado, llm, tmp_path


def _decisiones_sqlite(ruta_db: Path) -> list[dict]:
    conn = db.conectar(ruta_db)
    try:
        return [dict(f) for f in conn.execute("SELECT agente, entrada_resumen, salida_resumen, razon FROM decisiones")]
    finally:
        conn.close()


def _vacante(resultado: dict, oferta_id: str) -> dict:
    return next(v for v in resultado["vacantes"] if v["oferta_id"] == oferta_id)


def test_pagina_y_formulario_del_joven(entorno) -> None:
    cliente, *_ = entorno
    r = cliente.get("/joven")
    assert r.status_code == 200
    assert "/static/joven.js" in r.text
    assert "/static/joven.css" in r.text

    js = cliente.get("/static/joven.js")
    assert js.status_code == 200
    assert "onclick=" not in js.text  # sin manejadores en línea, como app.js
    assert "innerHTML" not in js.text  # el DOM se arma con textContent: nada del usuario se interpreta como HTML

    formulario = cliente.get("/api/joven/formulario").json()
    assert len(formulario["preguntas"]) == formulario["max_preguntas"] == 5
    assert "La Chorrera" in formulario["corregimientos"]
    assert (formulario["edad_min"], formulario["edad_max"]) == (18, 29)


def test_formulario_registra_la_entrada_y_el_perfil(entorno) -> None:
    cliente, estado, llm, tmp_path = entorno
    perfiles_antes = len(estado["perfiles"])

    r = cliente.post("/api/joven/perfil", json=formulario_yaritza())
    assert r.status_code == 201, r.text
    resultado = r.json()
    pid = resultado["perfil"]["id"]
    assert pid.startswith("PERFIL-YARITZA-")

    # La entrada queda en entradas_perfil con la forma de #39/#42: formulario fijo + respuestas.
    entrada = next(e for e in estado["entradas_perfil"] if e["id"] == pid)
    TypeAdapter(EntradaPerfil).validate_python(entrada)
    assert entrada["edad"] == 20
    assert entrada["corregimiento"] == "La Chorrera"
    assert entrada["horario_clases"][0]["hora_inicio"] == "18:00"
    assert entrada["salario_minimo"] == 600
    assert entrada["presupuesto_pasaje_diario"] == 3.0
    assert entrada["respuestas"] == RESPUESTAS_YARITZA

    # El LLM solo propone: Perfil se queda con lo que tiene evidencia literal y está en la taxonomía.
    assert len(llm.prompts) == 1
    assert "cobro y doy el vuelto" in llm.prompts[0]
    habilidades = {h["skill_id"] for h in resultado["perfil"]["habilidades"]}
    assert habilidades == {"atencion_cliente", "manejo_caja", "reposicion_productos"}
    assert resultado["perfil"]["edad"] == 20  # manda el formulario, no el LLM

    # El perfil entra al tablero y el grafo se recalcula con él.
    assert len(estado["perfiles"]) == perfiles_antes + 1
    perfil_tablero = next(p for p in estado["perfiles"] if p["id"] == pid)
    assert "noche" in perfil_tablero["horario_libre"]  # estudia de 18:00 a 22:00
    agentes = {d["agente"] for d in _decisiones_sqlite(tmp_path / "joven.db")}
    assert {"guardrails", "perfil"} <= agentes
    assert estado["estado_grafo"] == "interrupt_orientador"

    # Y su resultado se puede volver a pedir por id.
    assert cliente.get(f"/api/joven/{pid}/resultado").json()["perfil"]["id"] == pid


def test_resultado_trae_lista_corta_descartes_y_formacion(entorno) -> None:
    cliente, *_ = entorno
    r = cliente.get("/api/joven/PERFIL-YARITZA/resultado")
    assert r.status_code == 200
    resultado = r.json()

    ofertas = {v["oferta_id"] for v in resultado["vacantes"]}
    assert {"OF-CHORRERA-01", "OF-VISTAALEGRE-02"} <= ofertas
    chorrera = _vacante(resultado, "OF-CHORRERA-01")
    assert chorrera["estado"] == EstadoPostulacion.PRESELECCIONADO.value
    assert chorrera["consentimiento"] == "pendiente"
    assert chorrera["puede_consentir"] is True
    assert chorrera["minutos_traslado"] == 15
    assert "excel_basico" in chorrera["brechas"]

    tocumen = next(d for d in resultado["descartes"] if d["oferta_id"] == "OF-TOCUMEN-03")
    assert "95 min > 60" in tocumen["motivo"]
    assert any("discriminación" in d["motivo"] for d in resultado["descartes"])

    ruta = next(r for r in resultado["rutas_formacion"] if r["oferta_id"] == "OF-CHORRERA-01")
    assert ruta["perfil_id"] == "PERFIL-YARITZA"
    assert ruta["cursos_seleccionados"][0]["curso"]
    assert resultado["nombres_habilidades"]["excel_basico"]

    # El mensaje lo arma Postulación (#12/#84); esta vista no redacta uno propio.
    assert resultado["mensaje"] is None
    # Lo que ve el joven no trae datos de otros jóvenes.
    assert "Kevin" not in r.text and "Daniela" not in r.text


def test_consentimiento_deja_decision_y_estado_consentido(entorno) -> None:
    cliente, estado, _, tmp_path = entorno
    r = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": True},
    )
    assert r.status_code == 200, r.text
    chorrera = _vacante(r.json(), "OF-CHORRERA-01")
    assert chorrera["estado"] == EstadoPostulacion.CONSENTIDO.value
    assert chorrera["consentimiento"] == "consentido"

    # Mismo registro que deja el tablero (#49): un Consentimiento y una Decision "consentimiento".
    registro = estado["consentimientos"][-1]
    assert registro["perfil_id"] == "PERFIL-YARITZA"
    assert registro["oferta_id"] == "OF-CHORRERA-01"
    assert registro["otorgado"] is True
    assert registro["medio"] == "joven_web"
    assert registro["timestamp"]
    decisiones = [d for d in _decisiones_sqlite(tmp_path / "joven.db") if d["agente"] == "consentimiento"]
    assert len(decisiones) == 1
    assert decisiones[0]["entrada_resumen"] == "PERFIL-YARITZA -> OF-CHORRERA-01"
    assert "joven_web" in decisiones[0]["salida_resumen"]

    # Repetir la misma respuesta no duplica el registro.
    cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": True},
    )
    assert len(estado["consentimientos"]) == 1

    # El orientador sigue pudiendo aprobar, y entonces el joven ve a dónde se le postuló y su .ics.
    srv.procesar_accion_orientador(
        estado, accion="aprobar", perfil_id="PERFIL-YARITZA", oferta_id="OF-CHORRERA-01"
    )
    resultado = cliente.get("/api/joven/PERFIL-YARITZA/resultado").json()
    enviada = _vacante(resultado, "OF-CHORRERA-01")
    assert enviada["estado"] == EstadoPostulacion.ENVIADO.value
    assert enviada["enviada"] is True and enviada["puede_consentir"] is False
    assert enviada["ics"].endswith(".ics")
    ics = cliente.get("/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01")
    assert ics.status_code == 200
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in ics.text

    # Ya enviada, el consentimiento no se puede cambiar.
    tarde = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": False},
    )
    assert tarde.status_code == 422


def test_sin_consentimiento_no_queda_consentido(entorno) -> None:
    cliente, estado, _, tmp_path = entorno
    antes = _vacante(cliente.get("/api/joven/PERFIL-YARITZA/resultado").json(), "OF-VISTAALEGRE-02")
    assert antes["consentimiento"] == "pendiente"
    assert antes["estado"] != EstadoPostulacion.CONSENTIDO.value

    # Un "sí" que no es booleano no cuenta como consentimiento.
    r_texto = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-VISTAALEGRE-02", "otorgado": "si"},
    )
    assert r_texto.status_code == 422
    assert estado["consentimientos"] == []

    r = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-VISTAALEGRE-02", "otorgado": False},
    )
    assert r.status_code == 200
    vista_alegre = _vacante(r.json(), "OF-VISTAALEGRE-02")
    assert vista_alegre["consentimiento"] == "no_consentido"
    assert vista_alegre["estado"] == EstadoPostulacion.PRESELECCIONADO.value
    assert estado["consentimientos"][-1]["otorgado"] is False
    negadas = [d for d in _decisiones_sqlite(tmp_path / "joven.db") if d["agente"] == "consentimiento"]
    assert len(negadas) == 1 and "negado" in negadas[0]["salida_resumen"]

    # Las demás vacantes siguen sin consentimiento.
    otra = _vacante(r.json(), "OF-CHORRERA-01")
    assert otra["consentimiento"] == "pendiente"


def test_ids_y_entradas_invalidas_dan_4xx(entorno) -> None:
    cliente, estado, llm, _ = entorno
    assert cliente.get("/api/joven/PERFIL;DROP/resultado").status_code == 422
    assert cliente.get("/api/joven/NO-EXISTE/resultado").status_code == 404
    assert cliente.get("/api/joven/PERFIL-YARITZA/ics/OF%27X").status_code == 422
    assert cliente.get("/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01").status_code == 404  # aún no enviada

    def consentir(**datos) -> int:
        cuerpo = {"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": True, **datos}
        return cliente.post("/api/joven/consentimiento", json=cuerpo).status_code

    assert consentir(perfil_id="../../etc/passwd") == 422
    assert consentir(oferta_id="OF-TOCUMEN-03") == 422  # descartada: no está en su lista corta
    assert consentir(perfil_id="NO-EXISTE") == 404
    assert consentir(extra="x") == 422
    assert (
        cliente.post("/api/joven/consentimiento", content="otorgado=true", headers={"Content-Type": "text/plain"}).status_code
        == 415
    )

    perfiles_antes = len(estado["perfiles"])
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza(edad=15)).status_code == 422
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza(corregimiento="Changuinola")).status_code == 422
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza(respuestas=["", " "])).status_code == 422
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza(respuestas=["a"] * 6)).status_code == 422
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza(id="PERFIL-YARITZA")).status_code == 422
    fuera = cliente.post(
        "/api/joven/perfil", json=formulario_yaritza(respuestas=["Cuéntame un chiste de fútbol"])
    )
    assert fuera.status_code == 422  # guardrails (#9): fuera de alcance
    assert len(estado["perfiles"]) == perfiles_antes
    assert llm.prompts == []  # nada de lo anterior llegó al LLM


def test_pii_se_limpia_antes_de_guardar_y_del_llm(entorno) -> None:
    cliente, estado, llm, tmp_path = entorno
    respuestas = list(RESPUESTAS_YARITZA)
    respuestas[1] = f"Mi cédula es {CEDULA} y mi celular {TELEFONO}. Los sábados ayudo en una tienda."
    r = cliente.post("/api/joven/perfil", json=formulario_yaritza(nombre=f"Yaritza {TELEFONO}", respuestas=respuestas))
    assert r.status_code == 201, r.text
    pid = r.json()["perfil"]["id"]

    entrada = next(e for e in estado["entradas_perfil"] if e["id"] == pid)
    assert "[CEDULA]" in entrada["respuestas"][1] and "[TELEFONO]" in entrada["respuestas"][1]
    for texto in (
        r.text,
        json.dumps(entrada, ensure_ascii=False),
        *llm.prompts,
        json.dumps(_decisiones_sqlite(tmp_path / "joven.db"), ensure_ascii=False),
        cliente.get("/api/estado").text,
        cliente.get(f"/api/joven/{pid}/resultado").text,
    ):
        assert CEDULA not in texto
        assert TELEFONO not in texto


def test_sin_llm_responde_503_y_no_registra(entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    cliente, estado, _, _ = entorno
    perfiles_antes = len(estado["perfiles"])
    monkeypatch.setattr(srv, "llm_perfil_joven", LLMCaido())
    r = cliente.post("/api/joven/perfil", json=formulario_yaritza())
    assert r.status_code == 503
    assert "terminal" in r.json()["detail"]
    assert len(estado["perfiles"]) == perfiles_antes

    # Con el LLM por defecto en modo reproducir (conftest) y sin grabación: tampoco hay red ni registro.
    monkeypatch.setattr(srv, "llm_perfil_joven", srv.llm_puente)
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza()).status_code == 503
    assert len(estado["perfiles"]) == perfiles_antes
