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
    monkeypatch.setattr(srv, "_envios_perfil", {})  # el límite por minuto empieza en cero en cada prueba
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


def _cred(estado: dict, perfil_id: str = "PERFIL-YARITZA") -> dict:
    """Encabezado con el token del joven: el servidor lo crea al arrancar para los perfiles semilla."""
    return {"X-Token-Joven": estado["_tokens_joven"][perfil_id]}


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
    assert (formulario["edad_min"], formulario["edad_max"]) == (18, 98)


def test_formulario_registra_la_entrada_y_el_perfil(entorno) -> None:
    cliente, estado, llm, tmp_path = entorno
    perfiles_antes = len(estado["perfiles"])

    r = cliente.post("/api/joven/perfil", json=formulario_yaritza())
    assert r.status_code == 201, r.text
    resultado = r.json()
    pid = resultado["perfil"]["id"]
    assert pid.startswith("PERFIL-JOVEN-") and "YARITZA" not in pid  # el id no lleva el nombre
    assert resultado["token"] == estado["_tokens_joven"][pid]

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
    assert cliente.get(f"/api/joven/{pid}/resultado", headers=_cred(estado, pid)).json()["perfil"]["id"] == pid


def test_resultado_trae_lista_corta_descartes_y_formacion(entorno) -> None:
    cliente, estado, *_ = entorno
    r = cliente.get("/api/joven/PERFIL-YARITZA/resultado", headers=_cred(estado))
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
        headers=_cred(estado),
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
        headers=_cred(estado),
    )
    assert len(estado["consentimientos"]) == 1

    # El orientador sigue pudiendo aprobar, y entonces el joven ve a dónde se le postuló y su .ics.
    srv.procesar_accion_orientador(
        estado, accion="aprobar", perfil_id="PERFIL-YARITZA", oferta_id="OF-CHORRERA-01"
    )
    resultado = cliente.get("/api/joven/PERFIL-YARITZA/resultado", headers=_cred(estado)).json()
    enviada = _vacante(resultado, "OF-CHORRERA-01")
    assert enviada["estado"] == EstadoPostulacion.ENVIADO.value
    assert enviada["enviada"] is True and enviada["puede_consentir"] is False
    assert enviada["ics"].endswith(".ics")
    ics = cliente.get("/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01", headers=_cred(estado))
    assert ics.status_code == 200
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in ics.text

    # Ya enviada, el consentimiento no se puede cambiar.
    tarde = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": False},
        headers=_cred(estado),
    )
    assert tarde.status_code == 422


def test_sin_consentimiento_no_queda_consentido(entorno) -> None:
    cliente, estado, _, tmp_path = entorno
    antes = _vacante(cliente.get("/api/joven/PERFIL-YARITZA/resultado", headers=_cred(estado)).json(), "OF-VISTAALEGRE-02")
    assert antes["consentimiento"] == "pendiente"
    assert antes["estado"] != EstadoPostulacion.CONSENTIDO.value

    # Un "sí" que no es booleano no cuenta como consentimiento.
    r_texto = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-VISTAALEGRE-02", "otorgado": "si"},
        headers=_cred(estado),
    )
    assert r_texto.status_code == 422
    assert estado["consentimientos"] == []

    r = cliente.post(
        "/api/joven/consentimiento",
        json={"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-VISTAALEGRE-02", "otorgado": False},
        headers=_cred(estado),
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


def _estados_postulacion_sqlite(ruta_db: Path) -> list[str]:
    conn = db.conectar(ruta_db)
    try:
        return [str(f["estado"]) for f in conn.execute("SELECT estado FROM postulaciones")]
    finally:
        conn.close()


def _archivos_outbox(tmp_path: Path) -> list[str]:
    outbox = tmp_path / "outbox"
    return sorted(a.name for a in outbox.iterdir()) if outbox.exists() else []


def test_no_del_joven_frena_el_envio_desde_el_tablero(entorno) -> None:
    """Revisión del PR #106: el joven dice «no» → el orientador aprueba → 422 y nada sale."""
    cliente, estado, _, tmp_path = entorno
    par = {"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01"}
    assert cliente.post("/api/joven/consentimiento", json={**par, "otorgado": False}, headers=_cred(estado)).status_code == 200

    # Un recálculo del grafo (otro joven, el Auditor, quitar a alguien) no reescribe el «no»
    # y el grafo recibe `consentimientos`, que lee `negaron_consentimiento` (#109).
    srv.recalcular_pipeline_tablero(estado, registrar_trazas=True)
    match = next(m for m in estado["matches"] if (m["perfil_id"], m["oferta_id"]) == tuple(par.values()))
    assert match["consentimiento_joven"] is False
    assert srv._buscar_postulacion(estado, **par)["consentimiento_joven"] is False
    valores = estado["_grafo"].get_state(estado["_config_grafo"]).values
    assert [c["otorgado"] for c in valores["consentimientos"]] == [False]

    # El orientador aprueba con la casilla «el joven consintió» marcada, como viene en el tablero.
    r = cliente.post(
        "/api/orientador/accion",
        json={**par, "accion": "aprobar", "consentimiento_joven": True, "consentidos": ["PERFIL-YARITZA"]},
    )
    assert r.status_code == 422, r.text
    assert "no acepta" in r.json()["detail"]

    # Nada salió: ni CV ni .ics en outbox/, ni filas `enviado`, ni un consentimiento del orientador que tape el «no».
    assert _archivos_outbox(tmp_path) == []
    assert EstadoPostulacion.ENVIADO.value not in _estados_postulacion_sqlite(tmp_path / "joven.db")
    assert srv._buscar_postulacion(estado, **par)["estado"] == EstadoPostulacion.PRESELECCIONADO.value
    assert [(c["otorgado"], c["medio"]) for c in estado["consentimientos"]] == [(False, "joven_web")]
    assert srv._calcular_metricas_impacto(estado)["operativas_demo"]["consentimientos_confirmados"] == 0
    resultado = cliente.get("/api/joven/PERFIL-YARITZA/resultado", headers=_cred(estado)).json()
    assert _vacante(resultado, "OF-CHORRERA-01")["enviada"] is False

    # Si el joven cambia a «sí», el orientador ya puede aprobar y se envía.
    assert cliente.post("/api/joven/consentimiento", json={**par, "otorgado": True}, headers=_cred(estado)).status_code == 200
    r = cliente.post("/api/orientador/accion", json={**par, "accion": "aprobar"})
    assert r.status_code == 200, r.text
    assert srv._buscar_postulacion(estado, **par)["estado"] == EstadoPostulacion.ENVIADO.value
    assert any(nombre.endswith(".ics") for nombre in _archivos_outbox(tmp_path))
    assert srv._calcular_metricas_impacto(estado)["operativas_demo"]["consentimientos_confirmados"] == 1


def test_el_grafo_no_envia_a_quien_dijo_no(entorno) -> None:
    """Aunque el grafo reciba `consentidos` con la pareja, el nodo de Postulación del tablero la salta."""
    cliente, estado, _, tmp_path = entorno
    par = {"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01"}
    assert cliente.post("/api/joven/consentimiento", json={**par, "otorgado": False}, headers=_cred(estado)).status_code == 200
    srv.recalcular_pipeline_tablero(estado, registrar_trazas=True)

    grafo, cfg = estado["_grafo"], estado["_config_grafo"]
    resueltas = [v for v in (grafo.get_state(cfg).values.get("vacantes_resueltas") or []) if v != par["oferta_id"]]
    grafo.update_state(cfg, {"vacantes_resueltas": resueltas})
    grafo.invoke(
        srv.Command(
            resume={
                "accion": "aprobar",
                "orientador": "Orientadora Ana Gómez",
                "oferta_id": par["oferta_id"],
                "consentidos": [par["perfil_id"]],
            }
        ),
        cfg,
    )
    assert _archivos_outbox(tmp_path) == []
    assert EstadoPostulacion.ENVIADO.value not in _estados_postulacion_sqlite(tmp_path / "joven.db")


def test_ids_y_entradas_invalidas_dan_4xx(entorno) -> None:
    cliente, estado, llm, _ = entorno
    assert cliente.get("/api/joven/PERFIL;DROP/resultado").status_code == 422
    assert cliente.get("/api/joven/NO-EXISTE/resultado").status_code == 401  # sin token no se sabe si existe
    assert cliente.get("/api/joven/PERFIL-YARITZA/ics/OF%27X").status_code == 422
    # Aún no enviada.
    assert cliente.get("/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01", headers=_cred(estado)).status_code == 404

    def consentir(**datos) -> int:
        cuerpo = {"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": True, **datos}
        return cliente.post("/api/joven/consentimiento", json=cuerpo, headers=_cred(estado)).status_code

    assert consentir(perfil_id="../../etc/passwd") == 422
    assert consentir(oferta_id="OF-TOCUMEN-03") == 422  # descartada: no está en su lista corta
    assert consentir(perfil_id="NO-EXISTE") == 401  # el token de Yaritza no vale para otro id
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
        cliente.get(f"/api/joven/{pid}/resultado", headers=_cred(estado, pid)).text,
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
    assert "sin clave de API" not in r.text  # el detalle del error queda en la consola, no en la respuesta
    assert len(estado["perfiles"]) == perfiles_antes

    # Con el LLM por defecto en modo reproducir (conftest) y sin grabación: tampoco hay red ni registro.
    monkeypatch.setattr(srv, "llm_perfil_joven", srv.llm_puente)
    assert cliente.post("/api/joven/perfil", json=formulario_yaritza()).status_code == 503
    assert len(estado["perfiles"]) == perfiles_antes


def test_token_por_perfil_protege_resultado_consentimiento_e_ics(entorno) -> None:
    """Revisión del PR #106: el id sale en /api/estado, así que no basta como credencial."""
    cliente, estado, _, _ = entorno
    otro = next(p["id"] for p in estado["perfiles"] if p["id"] != "PERFIL-YARITZA")
    par = {"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": True}
    for cabeceras in ({}, {"X-Token-Joven": "no-es-el-token"}, _cred(estado, otro)):
        assert cliente.get("/api/joven/PERFIL-YARITZA/resultado", headers=cabeceras).status_code == 401
        assert cliente.post("/api/joven/consentimiento", json=par, headers=cabeceras).status_code == 401
        assert cliente.get("/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01", headers=cabeceras).status_code == 401
    assert estado["consentimientos"] == []  # nadie consintió por Yaritza

    # Con su token sí, y solo en el encabezado: en la URL quedaría en el registro del servidor (#257).
    assert cliente.get("/api/joven/PERFIL-YARITZA/resultado", headers=_cred(estado)).status_code == 200
    assert cliente.post("/api/joven/consentimiento", json=par, headers=_cred(estado)).status_code == 200
    token = estado["_tokens_joven"]["PERFIL-YARITZA"]
    assert cliente.get("/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01", headers=_cred(estado)).status_code == 404  # aún no enviada
    assert cliente.get(f"/api/joven/PERFIL-YARITZA/ics/OF-CHORRERA-01?token={token}").status_code == 401  # el token en la URL no vale

    # /api/estado no trae tokens ni las respuestas en texto libre de los jóvenes.
    r = cliente.post("/api/joven/perfil", json=formulario_yaritza())
    nuevo = r.json()
    publico = cliente.get("/api/estado")
    assert "entradas_perfil" not in publico.json()
    assert token not in publico.text and nuevo["token"] not in publico.text
    assert RESPUESTAS_YARITZA[0] not in publico.text


def test_horario_del_formulario_llega_al_matching(entorno) -> None:
    """Revisión del PR #106: un joven libre solo los sábados no queda en vacantes de lunes a viernes."""
    cliente, estado, _, _ = entorno
    sabado = formulario_yaritza(
        horario_libre=[{"dias": ["sabado"], "hora_inicio": "08:00", "hora_fin": "12:00"}],
        horario_clases=[],
    )
    r = cliente.post("/api/joven/perfil", json=sabado)
    assert r.status_code == 201, r.text
    resultado = r.json()
    pid = resultado["perfil"]["id"]

    perfil_tablero = next(p for p in estado["perfiles"] if p["id"] == pid)
    assert perfil_tablero["horario_libre_bloques"] == [{"dias": ["sabado"], "hora_inicio": "08:00", "hora_fin": "12:00"}]
    modelos, *_ = srv._construir_modelos_desde_estado(estado)
    modelo = next(m for m in modelos if m.id == pid)
    assert [h.dias for h in modelo.horario_libre] == [["sabado"]] and modelo.horario_clases == []

    # Las vacantes de lunes a viernes salen con motivo; ninguna queda preseleccionada.
    lista_corta = {v["oferta_id"] for v in resultado["vacantes"]}
    assert not lista_corta & {"OF-CHORRERA-01", "OF-VISTAALEGRE-02"}
    motivos = {d["oferta_id"]: d["motivo"] for d in resultado["descartes"]}
    assert "OF-CHORRERA-01" in motivos and "horario" in motivos["OF-CHORRERA-01"].lower()

    # Los perfiles sin bloques (semilla, tablero) siguen con el horario de respaldo de siempre.
    yaritza = next(m for m in modelos if m.id == "PERFIL-YARITZA")
    assert yaritza.horario_libre[0].dias == ["lunes", "martes", "miercoles", "jueves", "viernes"]


def test_csrf_media_type_exacto_y_otro_sitio(entorno) -> None:
    """Revisión del PR #106: un POST que no dispara el preflight del navegador no entra."""
    cliente, estado, _, _ = entorno
    cuerpo = json.dumps({"perfil_id": "PERFIL-YARITZA", "oferta_id": "OF-CHORRERA-01", "otorgado": True})
    truco = cliente.post(
        "/api/joven/consentimiento",
        content=cuerpo,
        headers={**_cred(estado), "Content-Type": "text/plain; x=application/json"},
    )
    assert truco.status_code == 415
    for cabeceras in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://otro-sitio.example"}):
        r = cliente.post(
            "/api/joven/consentimiento",
            content=cuerpo,
            headers={**_cred(estado), "Content-Type": "application/json", **cabeceras},
        )
        assert r.status_code == 403
    assert estado["consentimientos"] == []

    # Desde la misma página sí, también con charset.
    mismo = cliente.post(
        "/api/joven/consentimiento",
        content=cuerpo,
        headers={**_cred(estado), "Content-Type": "application/json; charset=utf-8", "Sec-Fetch-Site": "same-origin"},
    )
    assert mismo.status_code == 200


def test_registro_tiene_tope_de_tamano_y_de_frecuencia(entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisión del PR #106: el POST que llama al LLM no queda abierto a cuerpos enormes ni a ráfagas."""
    cliente, estado, llm, _ = entorno
    enorme = json.dumps(formulario_yaritza(nombre="x" * (srv.MAX_BYTES_CUERPO_JOVEN + 1)))
    r = cliente.post("/api/joven/perfil", content=enorme, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert llm.prompts == []

    monkeypatch.setattr(srv, "LIMITE_PERFILES_POR_MINUTO", 2)
    codigos = [cliente.post("/api/joven/perfil", json=formulario_yaritza()).status_code for _ in range(3)]
    assert codigos == [201, 201, 429]
    assert len(llm.prompts) == 2  # el tercero no llegó al LLM


def test_postular_manda_el_ultimo_registro_de_la_pareja(tmp_path: Path) -> None:
    """Mismo criterio en `postular()` (#84), el grafo y el tablero: manda el último registro."""
    from puente.agents.postulacion import consentimiento_otorgado, postular
    from puente.schemas import HabilidadPerfil, Horario, Match, Oferta, Perfil

    par = ("p1", "o1")
    no = {"perfil_id": "p1", "oferta_id": "o1", "otorgado": False, "medio": "joven_web"}
    si = {"perfil_id": "p1", "oferta_id": "o1", "otorgado": True, "medio": "joven_web"}
    assert consentimiento_otorgado([no, si], par) is True
    assert consentimiento_otorgado([si, no], par) is False
    assert consentimiento_otorgado([], par) is False

    perfil = Perfil(id="p1", nombre="Persona Ficticia", edad=21, corregimiento="La Chorrera",
                    salario_minimo=300.0, presupuesto_pasaje_diario=2.0,
                    habilidades=[HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo clientes")])
    oferta = Oferta(id="o1", puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
                    salario_mensual=420.0, horario=Horario(dias=["lunes"], hora_inicio="08:00", hora_fin="16:00"),
                    habilidades=["atencion_cliente"])
    match = Match(perfil_id="p1", oferta_id="o1", puntaje=90,
                  desglose={"cobertura": 1.0, "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0})
    aprobacion = {"orientador": "Orientadora Ficticia", "timestamp": "2026-10-20T09:30:00-05:00",
                  "aprobados": [{"perfil_id": "p1", "oferta_id": "o1"}]}
    conn = db.conectar(tmp_path / "ultimo.db")
    try:
        with pytest.raises(ValueError, match="Sin consentimiento"):
            postular([match], {"p1": perfil}, {"o1": oferta}, aprobacion=aprobacion, conn=conn,
                     outbox=tmp_path / "outbox", consentimientos=[si, no])
        assert not (tmp_path / "outbox").exists()
        postular([match], {"p1": perfil}, {"o1": oferta}, aprobacion=aprobacion, conn=conn,
                 outbox=tmp_path / "outbox", consentimientos=[no, si])
        assert db.embudo(conn)["enviado"] == 1
    finally:
        conn.close()
