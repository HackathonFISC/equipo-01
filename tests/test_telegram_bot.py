"""Chatbot del joven por Telegram (#160, #221): el guion de WhatsApp con long polling, sin red.

Cada prueba usa su propio estado del tablero, su SQLite y su outbox en `tmp_path`; el envío a Telegram es un
doble que anota cada mensaje y Perfil recibe un LLM falso. El id del chat es SINTÉTICO.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import app.server as srv
import app.telegram_bot as tg
import app.whatsapp as bot
from puente.schemas import EstadoPostulacion
from puente.tools import db
from puente.tools import whatsapp as wa
from tests.test_whatsapp import FORMULARIO, RESPUESTAS, LLMFalso

CHAT_ID = 777000160  # SINTÉTICO


@pytest.fixture
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "tg.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    llm = LLMFalso()
    monkeypatch.setattr(srv, "llm_perfil_joven", llm)
    monkeypatch.setattr(srv, "_envios_perfil", {})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-telegram-de-prueba")
    enviados: list[tuple[str, str]] = []

    def telegram_falso(token, chat_id, texto, adjuntos):
        assert token == "token-telegram-de-prueba" and adjuntos == []
        enviados.append((chat_id, texto))
        return {"ok": True}

    monkeypatch.setattr(tg, "enviar_por_telegram", telegram_falso)
    return estado, llm, tmp_path, enviados


def _update(texto: str | None, *, update_id: int = 1, tipo_chat: str = "private") -> dict:
    mensaje = {"message_id": update_id, "chat": {"id": CHAT_ID, "type": tipo_chat}, "date": 1760000000}
    if texto is not None:
        mensaje["text"] = texto
    return {"update_id": update_id, "message": mensaje}


def _decir(texto: str | None, **kw) -> None:
    asyncio.run(tg.atender(_update(texto, **kw)))


def _conversaciones(ruta_db: Path) -> list[tuple]:
    conn = db.conectar(ruta_db)
    try:
        wa.crear_tabla(conn)
        return [tuple(f) for f in conn.execute("SELECT numero_hash, paso, perfil_id FROM whatsapp_conversaciones")]
    finally:
        conn.close()


def test_start_saluda_y_hace_la_primera_pregunta(entorno) -> None:
    _, _, tmp_path, enviados = entorno
    _decir("/start")
    assert [c for c, _ in enviados] == [str(CHAT_ID), str(CHAT_ID)]
    assert "Soy Puente" in enviados[0][1] and enviados[1][1] == bot.PREGUNTAS_FORMULARIO[0][1]
    # La conversación queda bajo el hash del chat, nunca el id en claro.
    assert _conversaciones(tmp_path / "tg.db") == [(tg.hash_chat(CHAT_ID), "nombre", None)]
    assert str(CHAT_ID) not in tg.hash_chat(CHAT_ID)


def test_solo_chats_privados_y_texto(entorno) -> None:
    _, _, tmp_path, enviados = entorno
    _decir("hola", tipo_chat="group")
    assert enviados == [] and _conversaciones(tmp_path / "tg.db") == []
    _decir("hola")
    _decir(None, update_id=2)  # una foto o un sticker: el bot pide texto
    assert "solo leo mensajes de texto" in enviados[-1][1]


def test_entrevista_completa_arma_el_perfil_por_telegram(entorno) -> None:
    estado, llm, tmp_path, enviados = entorno
    perfiles_antes = len(estado["perfiles"])
    for i, texto in enumerate(["/start", *FORMULARIO, "no", *RESPUESTAS]):
        _decir(texto, update_id=i)
    assert len(llm.prompts) == 1 and len(estado["perfiles"]) == perfiles_antes + 1
    perfil = estado["perfiles"][-1]
    assert perfil["modalidad_ingreso"].startswith("Telegram")
    assert any("Tu perfil está listo, Yari" in texto for _, texto in enviados)
    assert any(d["salida_resumen"].endswith("desde Telegram (2 habilidades)") for d in estado["decisiones"])
    # Con Telegram respondiendo nada cae al outbox, y el id del chat no queda en la base.
    assert not (tmp_path / "outbox").exists() or not list((tmp_path / "outbox").glob("telegram_*"))
    assert str(CHAT_ID).encode() not in (tmp_path / "tg.db").read_bytes()


def test_no_al_consentimiento_por_telegram(entorno) -> None:
    estado, _, tmp_path, enviados = entorno
    conn = db.conectar(tmp_path / "tg.db")
    try:
        wa.guardar_conversacion(conn, wa.Conversacion(tg.hash_chat(CHAT_ID), "listo", {}, "PERFIL-YARITZA"))
    finally:
        conn.close()
    _decir("hola")
    assert "Responde SÍ o NO" in enviados[-1][1]
    oferta_id = next(
        v["oferta_id"] for v in srv.resultado_joven(estado, "PERFIL-YARITZA")["vacantes"] if v["empresa"] in enviados[-1][1]
    )
    _decir("NO", update_id=2)
    assert any("no enviamos nada" in t for _, t in enviados)
    assert srv._buscar_postulacion(estado, "PERFIL-YARITZA", oferta_id)["estado"] == EstadoPostulacion.NO_SELECCIONADO.value
    ultimo = srv._ultimo_consentimiento(estado, "PERFIL-YARITZA", oferta_id)
    assert (ultimo["otorgado"], ultimo["medio"]) == (False, "telegram")


def test_sin_token_la_respuesta_va_a_outbox(entorno, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, tmp_path, enviados = entorno
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    _decir("hola")
    assert enviados == []
    archivos = sorted((tmp_path / "outbox").glob("telegram_*.txt"))
    assert archivos and any("Soy Puente" in a.read_text(encoding="utf-8") for a in archivos)
    assert not any(str(CHAT_ID) in a.name for a in archivos)


def test_el_bot_solo_arranca_con_el_interruptor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.delenv("PUENTE_TELEGRAM_BOT", raising=False)
    assert not tg.activado()
    monkeypatch.setenv("PUENTE_TELEGRAM_BOT", "1")
    assert tg.activado()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert not tg.activado()
