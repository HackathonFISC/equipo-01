"""Lista corta ciega de la empresa (#167): ver, aceptar, rechazar y vacante cubierta, con token por empresa.

Sin red ni LLM: el tablero corre el grafo con los datos sintéticos de la demo (stubs de Mercado
y Perfil, Matching real) y Telegram queda apagado (conftest). Cada prueba usa su propio estado,
su SQLite y su outbox en `tmp_path`, así no toca el `ESTADO_GLOBAL` de las demás. El aviso del
reacomodo para el joven lo deja el nodo `avisos` del grafo en PUENTE_OUTBOX (conftest).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.empresa as empresa
import app.server as srv
from puente.schemas import EstadoPostulacion
from puente.tools import db

YARITZA, KEVIN, DANIELA = "PERFIL-YARITZA", "PERFIL-KEVIN", "PERFIL-DANIELA"
CHORRERA, VISTA_ALEGRE = "OF-CHORRERA-01", "OF-VISTAALEGRE-02"
SUPER = "supermercado-oeste-s-a"  # «Supermercado Oeste S.A. (Empresa de Prueba)», dueña de OF-CHORRERA-01
PROGRESO = "distribuidora-el-progreso"  # dueña de OF-VISTAALEGRE-02
CEDULA = "8-123-4567"


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Estado propio del tablero con los datos sintéticos de la demo."""
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "empresa.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    with TestClient(srv.app) as cliente:
        yield cliente, estado, tmp_path


def _cred(estado: dict, empresa_id: str = SUPER) -> dict:
    """Encabezado con el token de la empresa: el servidor lo crea al arrancar y lo escribe en la consola."""
    return {"X-Token-Empresa": estado["_tokens_empresa"][empresa_id]}


def _aprobar(cliente: TestClient, perfil_id: str, oferta_id: str) -> None:
    """El orientador aprueba con la casilla «el joven consintió», como en el tablero."""
    r = cliente.post(
        "/api/orientador/accion",
        json={"accion": "aprobar", "perfil_id": perfil_id, "oferta_id": oferta_id, "consentimiento_joven": True},
    )
    assert r.status_code == 200, r.text


def _lista(cliente: TestClient, estado: dict, empresa_id: str = SUPER) -> dict:
    r = cliente.get(f"/api/empresa/{empresa_id}/lista-corta", headers=_cred(estado, empresa_id))
    assert r.status_code == 200, r.text
    return r.json()


def _vacante(lista: dict, oferta_id: str) -> dict:
    return next(v for v in lista["vacantes"] if v["oferta_id"] == oferta_id)


def _accion(cliente: TestClient, estado: dict, empresa_id: str = SUPER, **cuerpo):
    return cliente.post(f"/api/empresa/{empresa_id}/responder", json=cuerpo, headers=_cred(estado, empresa_id))


def _estado_sqlite(ruta_db: Path, perfil_id: str, oferta_id: str) -> str:
    conn = db.conectar(ruta_db)
    try:
        fila = conn.execute(
            "SELECT estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", (perfil_id, oferta_id)
        ).fetchone()
        return str(fila["estado"])
    finally:
        conn.close()


def _decisiones_empresa(ruta_db: Path) -> list[dict]:
    conn = db.conectar(ruta_db)
    try:
        return db.trazas(conn, agente="empresa")
    finally:
        conn.close()


def test_pagina_y_estaticos_de_la_empresa(entorno) -> None:
    cliente, *_ = entorno
    r = cliente.get("/empresa/lista-corta")
    assert r.status_code == 200
    assert "/static/empresa_lista.js" in r.text and "/static/empresa_lista.css" in r.text
    js = cliente.get("/static/empresa_lista.js")
    assert js.status_code == 200
    assert "onclick=" not in js.text  # sin manejadores en línea, como joven.js
    assert "innerHTML" not in js.text  # el DOM se arma con textContent
    assert cliente.get("/static/empresa_lista.css").status_code == 200


def test_lista_corta_ciega_sin_nombre_cedula_edad_genero_ni_corregimiento(entorno) -> None:
    """Criterio de #167: la vista nunca muestra datos personales del joven (HTML y JSON)."""
    cliente, estado, _ = entorno
    # La evidencia es literal: puede traer el nombre, la edad, el corregimiento, la cédula y el estado civil.
    yaritza = srv._buscar_perfil(estado, YARITZA)
    yaritza["habilidades"][0]["evidencia"] = (
        f"Yaritza, de 20 años, soltera y mujer, de La Chorrera (cédula {CEDULA}), atiende a los comensales de la fonda"
    )
    srv.recalcular_pipeline_tablero(estado)
    _aprobar(cliente, YARITZA, CHORRERA)

    r = cliente.get(f"/api/empresa/{SUPER}/lista-corta", headers=_cred(estado))
    assert r.status_code == 200, r.text
    [candidato] = _vacante(r.json(), CHORRERA)["candidatos"]
    assert candidato["alias"] == "Candidato 1"
    assert candidato["estado"] == EstadoPostulacion.ENVIADO.value
    assert not {"nombre", "edad", "genero", "corregimiento", "perfil_id"} & set(candidato)
    # Lo que sí ve: habilidades con evidencia (cegada), qué cubre de la oferta, disponibilidad y la entrevista.
    evidencias = " ".join(h["evidencia"] for h in candidato["habilidades"])
    assert "atiende a los comensales de la fonda" in evidencias and "[dato omitido]" in evidencias
    assert {"atencion_cliente", "manejo_caja"} <= set(candidato["cubre"])
    assert "07:00-17:30" in candidato["disponibilidad"]
    assert candidato["entrevista"]["texto"] and candidato["entrevista"]["confirmada"] is False

    pagina = cliente.get("/empresa/lista-corta").text
    for texto in (r.text, pagina):
        assert "yaritza" not in texto.lower()  # ni el nombre ni el perfil_id, que lo lleva
        assert "La Chorrera" not in texto
        assert CEDULA not in texto
        assert "20 años" not in texto
        assert "soltera" not in texto
        assert "femenino" not in texto and "mujer" not in texto
    # Ni el puntaje ni el traslado, que delatan dónde vive.
    assert "puntaje" not in r.text and "minutos_traslado" not in r.text and "pasaje" not in r.text
    # El orientador sí la sigue viendo con su nombre.
    assert "Yaritza" in cliente.get("/api/estado").text


def test_aceptar_genera_el_ics_y_deja_la_postulacion_en_entrevista(entorno) -> None:
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    [candidato] = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]
    ref = candidato["ref"]

    r = _accion(cliente, estado, accion="aceptar", oferta_id=CHORRERA, ref=ref)
    assert r.status_code == 200, r.text
    [aceptado] = _vacante(r.json(), CHORRERA)["candidatos"]
    assert aceptado["estado"] == EstadoPostulacion.ENTREVISTA.value
    assert aceptado["entrevista"]["confirmada"] is True and aceptado["ics"] is True

    # La postulación pasa a entrevista en el tablero y en SQLite (registrar_entrevista de Postulación).
    post = srv._buscar_postulacion(estado, YARITZA, CHORRERA)
    assert post["estado"] == EstadoPostulacion.ENTREVISTA.value
    assert _estado_sqlite(tmp_path / "empresa.db", YARITZA, CHORRERA) == "entrevista"
    publico = cliente.get("/api/estado").json()
    assert publico["conteo_embudo"]["entrevista"] == 1

    # El .ics confirmado, a la misma hora que propuso Postulación, sin el nombre ni el perfil_id.
    ics = cliente.get(f"/api/empresa/{SUPER}/ics/{CHORRERA}/{ref}", headers=_cred(estado))
    assert ics.status_code == 200
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in ics.text and "Entrevista confirmada" in ics.text
    assert "yaritza" not in ics.text.lower() and "yaritza" not in ics.headers["content-disposition"].lower()
    inicio_utc = datetime.fromisoformat(aceptado["entrevista"]["inicio"]).astimezone(timezone.utc)
    assert inicio_utc.strftime("%Y%m%dT%H%M") in ics.text  # la hora que dejó Postulación en `entrevistas`
    # El enlace de descarga lleva el token en la URL, como el .ics del joven.
    token = estado["_tokens_empresa"][SUPER]
    assert cliente.get(f"/api/empresa/{SUPER}/ics/{CHORRERA}/{ref}?token={token}").status_code == 200
    assert any(a.startswith("entrevista_confirmada_") for a in post["archivos_generados"])

    # Una Decision de la empresa, sin datos personales.
    [decision] = _decisiones_empresa(tmp_path / "empresa.db")
    assert "aceptar" in decision["entrada_resumen"] and "entrevista" in decision["salida_resumen"]

    # Ya en entrevista no se vuelve a aceptar.
    assert _accion(cliente, estado, accion="aceptar", oferta_id=CHORRERA, ref=ref).status_code == 422


def test_rechazar_a_yaritza_recalcula_y_el_tablero_lo_muestra(entorno) -> None:
    """Criterio de #167: la empresa rechaza a Yaritza y /api/estado muestra la lista corta recalculada."""
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    antes = cliente.get("/api/estado").json()
    assert (YARITZA, CHORRERA) in {(m["perfil_id"], m["oferta_id"]) for m in antes["matches"]}
    [candidato] = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]

    r = _accion(
        cliente, estado, accion="rechazar", oferta_id=CHORRERA, ref=candidato["ref"],
        nota="Elegimos otro perfil; llamar al 6123-4567",
    )
    assert r.status_code == 200, r.text
    vacante = _vacante(r.json(), CHORRERA)
    assert vacante["candidatos"] == []  # ya no la ve
    assert vacante["en_revision"] == 2  # Kevin y Daniela siguen en la lista corta, esperando al orientador

    # El tablero del orientador: la lista corta sin la pareja, la postulación en no_seleccionado y la traza.
    publico = cliente.get("/api/estado").json()
    pares = {(m["perfil_id"], m["oferta_id"]) for m in publico["matches"]}
    assert (YARITZA, CHORRERA) not in pares
    assert {(KEVIN, CHORRERA), (DANIELA, CHORRERA), (YARITZA, VISTA_ALEGRE)} <= pares  # solo sale de esa vacante
    post = next(p for p in publico["postulaciones"] if (p["perfil_id"], p["oferta_id"]) == (YARITZA, CHORRERA))
    assert post["estado"] == EstadoPostulacion.NO_SELECCIONADO.value
    assert _estado_sqlite(tmp_path / "empresa.db", YARITZA, CHORRERA) == "no_seleccionado"
    assert any(e["origen"] == "empresa" and e["perfil_id"] == YARITZA for e in publico["excluidos"])
    agentes = [d["agente"] for d in publico["decisiones_db"]]
    assert agentes.index("empresa") < agentes.index("evento_empresa") < agentes.index("avisos")
    assert "6123-4567" not in json.dumps(publico["decisiones_db"], ensure_ascii=False)
    # El interrupt del orientador espera la lista corta nueva de esa vacante.
    candidatos = [c["perfil_id"] for c in publico["interrupt_pendiente"]["candidatos"]]
    assert publico["interrupt_pendiente"]["oferta_id"] == CHORRERA and YARITZA not in candidatos

    # El aviso al joven queda en outbox/ (Telegram apagado) y el del orientador en /api/estado.
    avisos = [p.read_text(encoding="utf-8") for p in Path(os.environ["PUENTE_OUTBOX"]).glob("joven_PERFIL-YARITZA_*.txt")]
    assert any("no siguió con tu postulación" in t for t in avisos)
    assert any("rechazó a Yaritza" in a["texto"] for a in publico["avisos_orientador"])

    # El ciclo sigue: el orientador aprueba a Kevin y la empresa lo ve.
    _aprobar(cliente, KEVIN, CHORRERA)
    assert [c["alias"] for c in _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]] == ["Candidato 1"]

    # Repetir el rechazo con la referencia vieja ya no encuentra a nadie.
    assert _accion(cliente, estado, accion="rechazar", oferta_id=CHORRERA, ref=candidato["ref"]).status_code == 404


def test_vacante_cubierta_con_el_candidato_aceptado(entorno) -> None:
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    [candidato] = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]
    # Sin entrevista aceptada no se contrata desde la vista.
    assert _accion(cliente, estado, accion="cubierta", oferta_id=CHORRERA, ref=candidato["ref"]).status_code == 422
    assert _accion(cliente, estado, accion="aceptar", oferta_id=CHORRERA, ref=candidato["ref"]).status_code == 200

    r = _accion(cliente, estado, accion="cubierta", oferta_id=CHORRERA, ref=candidato["ref"])
    assert r.status_code == 200, r.text
    vacante = _vacante(r.json(), CHORRERA)
    assert vacante["cubierta"] is True
    assert [c["estado"] for c in vacante["candidatos"]] == [EstadoPostulacion.CONTRATADO.value]

    publico = cliente.get("/api/estado").json()
    assert CHORRERA not in {m["oferta_id"] for m in publico["matches"]}  # sale del Matching
    assert CHORRERA in publico["vacantes_cubiertas"]
    assert _estado_sqlite(tmp_path / "empresa.db", YARITZA, CHORRERA) == "contratado"
    assert publico["conteo_embudo"]["contratado"] == 1
    assert len(_decisiones_empresa(tmp_path / "empresa.db")) == 2  # aceptar y cubierta

    # Cubierta, ya no admite respuestas.
    assert _accion(cliente, estado, accion="cubierta", oferta_id=CHORRERA).status_code == 422


def test_sin_token_o_con_el_de_otra_empresa_responde_401(entorno) -> None:
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    ref = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"][0]["ref"]
    cuerpo = {"accion": "rechazar", "oferta_id": CHORRERA, "ref": ref}
    for cabeceras in ({}, {"X-Token-Empresa": "no-es-el-token-de-nadie"}, _cred(estado, PROGRESO)):
        assert cliente.get(f"/api/empresa/{SUPER}/lista-corta", headers=cabeceras).status_code == 401
        assert cliente.post(f"/api/empresa/{SUPER}/responder", json=cuerpo, headers=cabeceras).status_code == 401
        assert cliente.get(f"/api/empresa/{SUPER}/ics/{CHORRERA}/{ref}", headers=cabeceras).status_code == 401
    assert cliente.get("/api/empresa/no-existe/lista-corta", headers=_cred(estado)).status_code == 401
    assert srv._buscar_postulacion(estado, YARITZA, CHORRERA)["estado"] == EstadoPostulacion.ENVIADO.value

    # Ids con caracteres raros, cuerpos inválidos, enormes o de otro sitio: 4xx sin efectos.
    assert cliente.get("/api/empresa/a;b/lista-corta", headers=_cred(estado)).status_code == 422
    assert _accion(cliente, estado, accion="rechazar", oferta_id="../x", ref=ref).status_code == 422
    assert _accion(cliente, estado, accion="rechazar", oferta_id=CHORRERA).status_code == 422  # falta ref
    assert _accion(cliente, estado, accion="borrar", oferta_id=CHORRERA, ref=ref).status_code == 422
    assert _accion(cliente, estado, accion="rechazar", oferta_id=CHORRERA, ref=ref, extra=1).status_code == 422
    enorme = json.dumps({**cuerpo, "nota": "x" * (srv.MAX_BYTES_CUERPO_JOVEN + 1)})
    grande = cliente.post(
        f"/api/empresa/{SUPER}/responder", content=enorme, headers={**_cred(estado), "Content-Type": "application/json"}
    )
    assert grande.status_code == 413
    otro_sitio = cliente.post(
        f"/api/empresa/{SUPER}/responder",
        content=json.dumps(cuerpo),
        headers={**_cred(estado), "Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
    )
    assert otro_sitio.status_code == 403
    assert srv._buscar_postulacion(estado, YARITZA, CHORRERA)["estado"] == EstadoPostulacion.ENVIADO.value

    # /api/estado no trae los tokens de las empresas.
    publico = cliente.get("/api/estado").text
    assert all(token not in publico for token in estado["_tokens_empresa"].values())


def test_una_empresa_no_ve_ni_toca_candidatos_de_ofertas_ajenas(entorno) -> None:
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    _aprobar(cliente, YARITZA, VISTA_ALEGRE)

    del_super = _lista(cliente, estado, SUPER)
    del_progreso = _lista(cliente, estado, PROGRESO)
    assert [v["oferta_id"] for v in del_super["vacantes"]] == [CHORRERA]
    assert [v["oferta_id"] for v in del_progreso["vacantes"]] == [VISTA_ALEGRE]
    ref_super = _vacante(del_super, CHORRERA)["candidatos"][0]["ref"]
    ref_progreso = _vacante(del_progreso, VISTA_ALEGRE)["candidatos"][0]["ref"]
    assert ref_super != ref_progreso  # la referencia es por empresa: no se pueden cruzar

    # Progreso no actúa sobre la vacante de Supermercado Oeste, ni con su propia referencia en otra vacante.
    ajena = _accion(cliente, estado, PROGRESO, accion="rechazar", oferta_id=CHORRERA, ref=ref_super)
    assert ajena.status_code == 404
    cruzada = _accion(cliente, estado, PROGRESO, accion="aceptar", oferta_id=VISTA_ALEGRE, ref=ref_super)
    assert cruzada.status_code == 404
    ics_ajeno = cliente.get(f"/api/empresa/{PROGRESO}/ics/{CHORRERA}/{ref_super}", headers=_cred(estado, PROGRESO))
    assert ics_ajeno.status_code == 404
    assert srv._buscar_postulacion(estado, YARITZA, CHORRERA)["estado"] == EstadoPostulacion.ENVIADO.value


def test_candidatos_sin_consentimiento_o_sin_envio_no_aparecen(entorno) -> None:
    cliente, estado, _ = entorno
    # Sin aprobación del orientador nadie llega a la empresa: solo el número en revisión.
    vacante = _vacante(_lista(cliente, estado), CHORRERA)
    assert vacante["candidatos"] == [] and vacante["en_revision"] == 3

    # El «sí» del joven en /joven todavía no basta: falta que el orientador apruebe y se envíe.
    par = {"perfil_id": YARITZA, "oferta_id": CHORRERA}
    token_joven = estado["_tokens_joven"][YARITZA]
    r = cliente.post("/api/joven/consentimiento", json={**par, "otorgado": True}, headers={"X-Token-Joven": token_joven})
    assert r.status_code == 200
    assert _vacante(_lista(cliente, estado), CHORRERA)["candidatos"] == []

    # Una postulación marcada como enviada sin consentimiento vigente (o con un «no») tampoco sale.
    srv._buscar_postulacion(estado, KEVIN, CHORRERA)["estado"] = EstadoPostulacion.ENVIADO.value
    srv._buscar_postulacion(estado, DANIELA, CHORRERA)["estado"] = EstadoPostulacion.ENVIADO.value
    estado["consentimientos"].append({**par, "perfil_id": DANIELA, "otorgado": False, "medio": "joven_web", "timestamp": "x"})
    assert _vacante(_lista(cliente, estado), CHORRERA)["candidatos"] == []

    # Con consentimiento y envío, sí.
    srv._buscar_postulacion(estado, KEVIN, CHORRERA)["estado"] = EstadoPostulacion.PRESELECCIONADO.value
    _aprobar(cliente, YARITZA, CHORRERA)
    assert len(_vacante(_lista(cliente, estado), CHORRERA)["candidatos"]) == 1


def test_codigos_de_empresa_en_la_consola_y_nuevos_tras_reset(entorno, caplog: pytest.LogCaptureFixture) -> None:
    cliente, estado, _ = entorno
    assert set(estado["_tokens_empresa"]) >= {SUPER, PROGRESO}
    viejo = estado["_tokens_empresa"][SUPER]
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    assert cliente.post("/api/reset").status_code == 200
    nuevo = srv.ESTADO_GLOBAL["_tokens_empresa"][SUPER]
    assert nuevo != viejo
    assert f"/empresa/lista-corta#codigo={SUPER}.{nuevo}" in caplog.text
    assert cliente.get(f"/api/empresa/{SUPER}/lista-corta", headers={"X-Token-Empresa": viejo}).status_code == 401
    assert cliente.get(f"/api/empresa/{SUPER}/lista-corta", headers={"X-Token-Empresa": nuevo}).status_code == 200


def test_id_de_empresa_y_cegado_para_la_empresa() -> None:
    assert empresa.id_empresa("Supermercado Oeste S.A. (Empresa de Prueba)") == SUPER
    assert empresa.id_empresa("Logística Istmeña (Empresa de Prueba)") == "logistica-istmena"
    perfil = srv.Perfil(
        id="p1", nombre="María José", edad=21, corregimiento="Barrio Colón", salario_minimo=500,
        presupuesto_pasaje_diario=2, habilidades=[],
    )
    ciego = empresa.cegar_para_empresa("Soy maria jose, de 21 años, vivo en Barrio Colon y María atiende la caja", perfil)
    assert "maria" not in ciego.lower() and "María" not in ciego
    assert "21 años" not in ciego and "Colon" not in ciego
    assert "atiende la caja" in ciego


def test_con_puente_outbox_el_ics_que_confirma_la_empresa_va_a_esa_carpeta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin RUTA_OUTBOX fijada a mano, la vista de la empresa escribe y lee en PUENTE_OUTBOX (#186)."""
    carpeta = tmp_path / "outbox_de_la_demo"
    monkeypatch.setenv("PUENTE_OUTBOX", str(carpeta))
    assert srv.RUTA_OUTBOX == srv._RUTA_OUTBOX_POR_DEFECTO  # nadie la fijó: manda la variable
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "empresa.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    with TestClient(srv.app) as cliente:
        _aprobar(cliente, YARITZA, CHORRERA)
        [candidato] = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]
        ref = candidato["ref"]
        assert _accion(cliente, estado, accion="aceptar", oferta_id=CHORRERA, ref=ref).status_code == 200
        assert cliente.get(f"/api/empresa/{SUPER}/ics/{CHORRERA}/{ref}", headers=_cred(estado)).status_code == 200
    assert list(carpeta.glob(f"{empresa.PREFIJO_ICS}{CHORRERA}_*.ics"))  # el .ics confirmado quedó ahí


# ---------------------------------------------------------------- #235: las rutas viejas ya no saltan el código de la empresa


def _estado_y_avisos(cliente: TestClient, estado: dict, tmp_path: Path) -> tuple[str, int]:
    """Estado de la postulación de Yaritza a la vacante del súper y cuántos avisos al joven hay en outbox."""
    post = next(p for p in estado["postulaciones"] if p["perfil_id"] == YARITZA and p["oferta_id"] == CHORRERA)
    avisos = len(list((tmp_path / "outbox").glob("joven_*"))) if (tmp_path / "outbox").exists() else 0
    return post["estado"], avisos


@pytest.mark.parametrize("tipo", ["rechaza", "cubierta"])
def test_evento_de_empresa_sin_codigo_ya_no_rechaza_ni_cubre(entorno, tipo: str) -> None:
    """`POST /api/empresa/evento` con rechaza o cubierta daba 200 sin código y dejaba la pareja en no_seleccionado sin
    avisar al joven. Ahora responde 409 y no cambia nada (#235)."""
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    antes = _estado_y_avisos(cliente, estado, tmp_path)
    assert antes[0] == "enviado"

    r = cliente.post(
        "/api/empresa/evento",
        json={"tipo": tipo, "perfil_id": YARITZA, "oferta_id": CHORRERA, "empresa": "Cualquiera", "nota": "x"},
    )
    assert r.status_code == 409
    assert "/empresa/lista-corta" in r.json()["detail"] and "código" in r.json()["detail"]
    assert _estado_y_avisos(cliente, estado, tmp_path) == antes  # ni estado ni avisos cambiaron
    assert _estado_sqlite(tmp_path / "empresa.db", YARITZA, CHORRERA) == "enviado"


def test_la_ruta_oficial_para_rechazar_sigue_funcionando_con_codigo(entorno) -> None:
    """El 409 no toca el camino bueno: rechazar con el código de la empresa, por la lista corta, sigue dando 200."""
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    [candidato] = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]
    r = _accion(cliente, estado, accion="rechazar", oferta_id=CHORRERA, ref=candidato["ref"], nota="eligió otro perfil")
    assert r.status_code == 200, r.text


def test_postulaciones_de_la_empresa_piden_codigo_y_son_ciegas(entorno) -> None:
    """Sin código no hay lista; con el código, cada empresa ve solo lo suyo y sin `perfil_id` ni nombre (#235)."""
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    assert cliente.get("/api/empresa/postulaciones").status_code == 401
    assert cliente.get("/api/empresa/postulaciones", headers={"X-Token-Empresa": "no-es-el-codigo"}).status_code == 401

    r = cliente.get("/api/empresa/postulaciones", headers=_cred(estado, SUPER))
    assert r.status_code == 200
    [fila] = r.json()["postulaciones"]
    assert fila["oferta_id"] == CHORRERA and fila["estado"] == "enviado"
    texto = r.text
    for dato in ("perfil_id", "nombre_perfil", "PERFIL-", "Yaritza", "yaritza"):
        assert dato not in texto, dato
    # La misma referencia opaca con que la empresa responde en la lista corta.
    [candidato] = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"]
    assert fila["ref"] == candidato["ref"]

    # Otra empresa, con su propio código, no ve las postulaciones del súper.
    ajena = cliente.get("/api/empresa/postulaciones", headers=_cred(estado, PROGRESO))
    assert ajena.status_code == 200 and ajena.json()["postulaciones"] == []


# ---------------------------------------------------------------- #258: /empresa solo publica y el rol lleva a la lista ciega


def test_el_selector_de_roles_lleva_a_la_lista_ciega(entorno) -> None:
    """El menú del tablero y `roles_sistema` ya no llevan a la página vieja, que no es ciega (#258)."""
    cliente, _estado, _ = entorno
    roles = {r["rol"]: r for r in cliente.get("/api/admin/operacion").json()["roles_sistema"]}
    assert roles["empresa"]["ruta"] == "/empresa/lista-corta"
    menu = (empresa.RUTA_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'href="/empresa/lista-corta"' in menu and 'href="/empresa"' not in menu
    assert cliente.get(roles["empresa"]["ruta"]).status_code == 200


def test_la_pagina_empresa_solo_publica_y_no_muestra_a_nadie(entorno) -> None:
    """/empresa ya no lista postulaciones ni deja citar, contratar o rechazar: eso es de la lista ciega (#258)."""
    cliente, _estado, _ = entorno
    r = cliente.get("/empresa")
    assert r.status_code == 200
    html = r.text
    for viejo in (
        "Citar a entrevista",
        "Contratar",
        "Rechazar",
        "nombre_perfil",
        "Candidato ciego",
        "/api/empresa/evento",
        "/api/empresa/postulaciones",
        "lista-postulaciones-empresa",
        "perfil_id",
    ):
        assert viejo not in html, viejo
    assert "/api/ofertas" in html and 'href="/empresa/lista-corta"' in html
    assert 'class="boton' not in html  # esas clases no existen en joven.css: los botones son .btn


def test_las_habilidades_de_publicar_no_van_fijas_en_el_codigo(entorno) -> None:
    """La página manda las habilidades que la empresa marca; antes llevaban siempre las mismas dos (#258)."""
    cliente, _estado, _ = entorno
    html = cliente.get("/empresa").text
    assert 'habilidades: ["atencion_cliente", "manejo_caja"]' not in html
    assert html.count('name="habilidad"') >= 4
    assert "Elige al menos una habilidad" in html


def test_pasos_6_y_7_del_video_los_chips_de_yaritza_sobreviven_al_rechazo_y_al_reinicio(entorno) -> None:
    """Paso 6 (aprobar tras «Reiniciar demo») y paso 7 (la empresa rechaza): los archivos siguen en la tarjeta.

    Lo comprueba en tres lugares: /api/estado, el estado guardado en SQLite y el estado que se reconstruye
    cuando el servidor arranca de nuevo (#227).
    """
    cliente, _, tmp_path = entorno
    assert cliente.post("/api/reset").status_code == 200
    estado = srv.ESTADO_GLOBAL  # el reinicio deja un estado nuevo, con sus propios códigos
    _aprobar(cliente, YARITZA, VISTA_ALEGRE)

    def archivos(publico: dict) -> list[str]:
        post = next(p for p in publico["postulaciones"] if p["perfil_id"] == YARITZA and p["oferta_id"] == VISTA_ALEGRE)
        return list(post.get("archivos_generados") or [])

    def completos(nombres: list[str]) -> bool:
        return (
            any(n.startswith("cv_") for n in nombres)
            and any(n.startswith("carta_") for n in nombres)
            and any(n.endswith(".ics") for n in nombres)
            and any(n.startswith("mensaje_") for n in nombres)
        )

    antes = archivos(cliente.get("/api/estado").json())
    assert completos(antes), antes

    [candidato] = _vacante(_lista(cliente, estado, PROGRESO), VISTA_ALEGRE)["candidatos"]
    r = _accion(cliente, estado, PROGRESO, accion="rechazar", oferta_id=VISTA_ALEGRE, ref=candidato["ref"],
                nota="Buscamos experiencia en inventario formal")
    assert r.status_code == 200, r.text

    despues = cliente.get("/api/estado").json()
    assert archivos(despues) == antes  # la empresa rechazó, pero los archivos que ya salieron siguen en la tarjeta
    guardado = srv._cargar_estado_tablero_db(tmp_path / "empresa.db")
    assert guardado is not None and archivos(guardado) == antes  # y en lo que se guardó en SQLite
    reconstruido = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "empresa.db")
    assert archivos(reconstruido) == antes  # y cuando el servidor arranca otra vez
