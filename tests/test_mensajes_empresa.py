"""Mensajes del joven a la empresa (#289): limpios, ciegos, con aviso en outbox y solo para su empresa.

Sin red ni LLM ni Twilio real: el envío por WhatsApp lleva un transporte falso y, sin credenciales, el aviso queda
en `outbox/`. El joven escribe con su código y la empresa lee con el suyo; ninguna ve el `perfil_id` ni el teléfono.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.mensajes as mensajes_app
import app.server as srv
from puente.tools import db
from puente.tools import mensajes as entrega

from tests.test_empresa import CHORRERA, PROGRESO, SUPER, VISTA_ALEGRE, YARITZA, _aprobar, _cred, _lista, _vacante, entorno  # noqa: F401

CEDULA = "8-123-4567"
TELEFONO = "6123-4567"
NUMERO_EMPRESA = "whatsapp:+50761110000"
TEXTO = (
    f"Hola, soy Yaritza, tengo 20 años, vivo en La Chorrera, mi cédula es {CEDULA} y mi teléfono {TELEFONO}. "
    "¿Puedo pasar mañana a dejar mi CV?"
)


def _joven(estado: dict, perfil_id: str = YARITZA) -> dict:
    return {"X-Token-Joven": estado["_tokens_joven"][perfil_id]}


def _escribir(cliente: TestClient, estado: dict, texto: str = TEXTO, oferta_id: str = CHORRERA):
    return cliente.post(
        f"/api/joven/{YARITZA}/mensaje", json={"oferta_id": oferta_id, "texto": texto}, headers=_joven(estado)
    )


def _mensajes(cliente: TestClient, estado: dict, empresa_id: str = SUPER) -> list[dict]:
    r = cliente.get(f"/api/empresa/{empresa_id}/mensajes", headers=_cred(estado, empresa_id))
    assert r.status_code == 200, r.text
    return r.json()["mensajes"]


@pytest.fixture(autouse=True)
def _sin_twilio(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM", "TWILIO_WHATSAPP_EMPRESA"):
        monkeypatch.delenv(variable, raising=False)


def test_el_mensaje_se_limpia_se_ciega_y_la_empresa_lo_ve_como_candidato_n(entorno) -> None:
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)

    r = _escribir(cliente, estado)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["se_limpio"] is True
    limpio = r.json()["texto"]
    for dato in (CEDULA, TELEFONO, "Yaritza", "20 años", "La Chorrera"):
        assert dato not in limpio
    assert "dejar mi CV" in limpio  # lo que sí quería decir

    [mensaje] = _mensajes(cliente, estado)
    ref = _vacante(_lista(cliente, estado), CHORRERA)["candidatos"][0]["ref"]
    assert mensaje["alias"] == "Candidato 1" and mensaje["ref"] == ref and mensaje["texto"] == limpio
    assert mensaje["oferta_id"] == CHORRERA
    # La empresa nunca ve el perfil_id (lleva el nombre) ni lo que se quitó.
    crudo = cliente.get(f"/api/empresa/{SUPER}/mensajes", headers=_cred(estado)).text
    assert "perfil" not in crudo.lower() and "yaritza" not in crudo.lower()
    for dato in (CEDULA, TELEFONO, "La Chorrera", "20 años"):
        assert dato not in crudo

    # La tabla guarda el texto limpio con su ref opaca, y el teléfono del joven no está en ningún campo.
    conn = db.conectar(tmp_path / "empresa.db")
    try:
        filas = [dict(f) for f in conn.execute("SELECT * FROM mensajes").fetchall()]
        columnas = {c["name"] for c in conn.execute("PRAGMA table_info(mensajes)").fetchall()}
    finally:
        conn.close()
    assert columnas == {"id", "perfil_id", "oferta_id", "ref", "texto", "creado"}
    [fila] = filas
    assert fila["texto"] == limpio and fila["oferta_id"] == CHORRERA
    assert TELEFONO not in " ".join(str(v) for v in fila.values()) and CEDULA not in " ".join(str(v) for v in fila.values())


def test_un_correo_o_un_usuario_de_redes_no_llega_a_la_empresa(entorno) -> None:
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    contactos = "Escríbeme a jperez88@gmail.com o a @yari_chorrera20, o a mi otro correo y.perez+cv@correo.com.pa. Gracias."
    r = _escribir(cliente, estado, contactos)
    assert r.status_code == 200, r.text
    assert r.json()["se_limpio"] is True
    [mensaje] = _mensajes(cliente, estado)
    assert mensaje["texto"].count("[CORREO]") == 2 and mensaje["texto"].count("[USUARIO]") == 1
    avisos = list((tmp_path / "outbox").glob("mensaje_a_empresa_*.txt"))
    assert len(avisos) == 1
    for texto in (r.text, mensaje["texto"], avisos[0].read_text(encoding="utf-8")):
        for dato in ("jperez88", "gmail", "yari_chorrera20", "y.perez", "correo.com.pa"):
            assert dato not in texto
    assert "Gracias" in mensaje["texto"]


def test_tapar_contactos_no_toca_un_texto_sin_correo_ni_usuario() -> None:
    assert mensajes_app.tapar_contactos("¿Puedo ir mañana a las 8? Mi CV está listo.") == "¿Puedo ir mañana a las 8? Mi CV está listo."
    assert mensajes_app.tapar_contactos("Hola @c y a@b") == "Hola @c y a@b"  # ni un @ suelto ni algo que no es correo
    assert mensajes_app.tapar_contactos("a@b.co, @juan_22") == "[CORREO], [USUARIO]"


def test_sin_twilio_el_aviso_queda_en_outbox_con_el_texto_limpio(entorno) -> None:
    cliente, estado, tmp_path = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    assert _escribir(cliente, estado).status_code == 200

    avisos = [f for f in (tmp_path / "outbox").glob("mensaje_a_empresa_*.txt")]
    assert len(avisos) == 1
    texto = avisos[0].read_text(encoding="utf-8")
    assert "Nuevo mensaje de Candidato 1" in texto and "dejar mi CV" in texto
    for dato in (CEDULA, TELEFONO, "Yaritza", "La Chorrera"):
        assert dato not in texto


def test_con_el_numero_de_la_empresa_sin_credenciales_el_aviso_va_al_outbox_de_whatsapp(
    entorno, monkeypatch: pytest.MonkeyPatch
) -> None:
    cliente, estado, tmp_path = entorno
    monkeypatch.setenv("TWILIO_WHATSAPP_EMPRESA", NUMERO_EMPRESA)
    _aprobar(cliente, YARITZA, CHORRERA)
    assert _escribir(cliente, estado).status_code == 200

    [aviso] = list((tmp_path / "outbox").glob("whatsapp_*.txt"))
    assert "Nuevo mensaje de Candidato 1" in aviso.read_text(encoding="utf-8")
    assert not list((tmp_path / "outbox").glob("mensaje_a_empresa_*.txt"))


def test_con_credenciales_el_aviso_sale_por_whatsapp_al_numero_de_la_empresa(
    entorno, monkeypatch: pytest.MonkeyPatch
) -> None:
    cliente, estado, tmp_path = entorno
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACdemo")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token-demo")
    monkeypatch.setenv("TWILIO_WHATSAPP_EMPRESA", NUMERO_EMPRESA)
    llamadas: list[tuple] = []

    def transporte(sid, token, remitente, destino, texto):
        llamadas.append((destino, texto))
        return {"sid": "SM1"}

    original = entrega.whatsapp.enviar
    monkeypatch.setattr(
        entrega.whatsapp, "enviar", lambda numero, texto, **kw: original(numero, texto, **{**kw, "transporte": transporte})
    )
    _aprobar(cliente, YARITZA, CHORRERA)
    assert _escribir(cliente, estado).status_code == 200

    [(destino, texto)] = llamadas
    assert destino == "whatsapp:+50761110000"
    assert "Nuevo mensaje de Candidato 1" in texto and TELEFONO not in texto and CEDULA not in texto
    assert not list((tmp_path / "outbox").glob("whatsapp_*.txt"))  # salió por Twilio, no quedó respaldo


def test_otra_empresa_no_ve_el_mensaje(entorno) -> None:
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    assert _escribir(cliente, estado).status_code == 200

    assert len(_mensajes(cliente, estado, SUPER)) == 1
    assert _mensajes(cliente, estado, PROGRESO) == []
    # Con el código de la otra empresa tampoco se lee la ruta de la dueña.
    r = cliente.get(f"/api/empresa/{SUPER}/mensajes", headers=_cred(estado, PROGRESO))
    assert r.status_code == 401


def test_sin_codigo_responde_401_en_las_dos_rutas(entorno) -> None:
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    cuerpo = {"oferta_id": CHORRERA, "texto": "Hola"}

    assert cliente.post(f"/api/joven/{YARITZA}/mensaje", json=cuerpo).status_code == 401
    r = cliente.post(f"/api/joven/{YARITZA}/mensaje", json=cuerpo, headers={"X-Token-Joven": "no-es-el-codigo"})
    assert r.status_code == 401
    otro = next(p for p in estado["_tokens_joven"] if p != YARITZA)
    assert cliente.post(f"/api/joven/{YARITZA}/mensaje", json=cuerpo, headers=_joven(estado, otro)).status_code == 401

    assert cliente.get(f"/api/empresa/{SUPER}/mensajes").status_code == 401
    assert cliente.get(f"/api/empresa/{SUPER}/mensajes", headers={"X-Token-Empresa": "no"}).status_code == 401
    assert _mensajes(cliente, estado) == []  # y nada de eso dejó un mensaje


def test_solo_se_escribe_a_una_empresa_que_ya_recibio_el_cv_y_el_texto_se_valida(entorno) -> None:
    cliente, estado, _ = entorno
    # Sin aprobación del orientador la empresa no ve al joven: no hay a quién avisar.
    r = _escribir(cliente, estado)
    assert r.status_code == 409 and "ya recibió tu CV" in r.json()["detail"]

    _aprobar(cliente, YARITZA, CHORRERA)
    assert _escribir(cliente, estado, "   ").status_code == 422
    assert _escribir(cliente, estado, "x" * (mensajes_app.MAX_CARACTERES_MENSAJE + 1)).status_code == 422
    assert _escribir(cliente, estado, oferta_id="OF-NO-EXISTE").status_code == 404
    assert _escribir(cliente, estado, oferta_id="../etc").status_code == 422
    # Una vacante de otra empresa a la que no se postuló tampoco.
    assert _escribir(cliente, estado, oferta_id=VISTA_ALEGRE).status_code == 409
    assert _mensajes(cliente, estado) == []


def test_si_el_joven_retira_su_consentimiento_la_empresa_deja_de_ver_el_mensaje(entorno) -> None:
    cliente, estado, _ = entorno
    _aprobar(cliente, YARITZA, CHORRERA)
    assert _escribir(cliente, estado).status_code == 200
    assert len(_mensajes(cliente, estado)) == 1

    estado["consentimientos"].append(
        {"perfil_id": YARITZA, "oferta_id": CHORRERA, "otorgado": False, "medio": "joven_web", "timestamp": "9999"}
    )
    assert _mensajes(cliente, estado) == []


def test_la_entrega_nunca_lanza_aunque_no_haya_donde_escribir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWILIO_WHATSAPP_EMPRESA", raising=False)
    archivo = tmp_path / "es-un-archivo"
    archivo.write_text("x", encoding="utf-8")
    resultado = entrega.entregar_a_la_empresa("Hola", SUPER, outbox=archivo)  # un archivo no es una carpeta
    assert resultado.ok is False and resultado.ruta is None


def test_la_pagina_del_joven_y_la_de_la_empresa_traen_los_mensajes(entorno) -> None:
    cliente, *_ = entorno
    empresa_js = cliente.get("/static/empresa_lista.js").text
    ruta_mensajes = 'rutaEmpresa("mensajes")'
    assert ruta_mensajes in empresa_js and "Nuevo mensaje de" in empresa_js and "innerHTML" not in empresa_js
    assert 'id="mensajes"' in cliente.get("/empresa/lista-corta").text
    joven_js = cliente.get("/static/joven_fase2.js").text
    assert "/mensaje`" in joven_js and "innerHTML" not in joven_js
    assert "mensajes_empresa" in Path(srv.__file__).read_text(encoding="utf-8")
