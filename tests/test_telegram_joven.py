"""Mensaje al joven por Telegram o outbox/, nunca al chat de la empresa (issue #12). Sin red ni claves."""
import urllib.request

import pytest

from puente.tools.telegram import ResultadoEnvio, enviar_al_joven, enviar_lista_corta


@pytest.fixture(autouse=True)
def sin_red_ni_claves(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID_EMPRESA", "TELEGRAM_CHAT_ID_JOVEN"):
        monkeypatch.delenv(var, raising=False)

    def prohibido(*a, **k):
        raise AssertionError("no debe haber red en las pruebas")

    monkeypatch.setattr(urllib.request, "urlopen", prohibido)


def test_mensaje_al_joven_nunca_va_al_chat_de_la_empresa(tmp_path, monkeypatch):
    # Valores SINTÉTICOS: no son claves reales.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-falso")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-empresa-falso")
    llamadas = []

    def doble(token, chat_id, texto, adjuntos):
        llamadas.append(chat_id)
        return {"ok": True}

    r = enviar_al_joven("Hola, joven", outbox=tmp_path / "outbox", enviar=doble)
    assert llamadas == []
    assert (r.canal, r.ok) == ("outbox", True)
    assert r.ruta.parent == tmp_path / "outbox" and r.ruta.name.startswith("joven_")
    assert "Hola, joven" in r.ruta.read_text(encoding="utf-8")
    assert not list((tmp_path / "outbox").glob("mensaje_*.txt"))


def test_con_chat_del_joven_el_doble_recibe_ese_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-falso")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-empresa-falso")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_JOVEN", "chat-joven-falso")
    recibidos = []

    def doble(token, chat_id, texto, adjuntos):
        recibidos.append((token, chat_id, texto, adjuntos))
        return {"ok": True}

    adjunto = tmp_path / "entrevista.ics"
    r = enviar_al_joven("Te postulamos", [adjunto], outbox=tmp_path / "outbox", enviar=doble)
    assert isinstance(r, ResultadoEnvio) and (r.canal, r.ok) == ("telegram", True)
    assert recibidos == [("token-falso", "chat-joven-falso", "Te postulamos", [adjunto])]
    assert not (tmp_path / "outbox").exists()


def test_parametros_explicitos_tienen_prioridad(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID_JOVEN", "chat-del-entorno")
    vistos = []
    enviar_al_joven("x", token="t-param", chat_id="chat-param", outbox=tmp_path,
                    enviar=lambda t, c, *a: vistos.append((t, c)) or {"ok": True})
    assert vistos == [("t-param", "chat-param")]


def test_sin_token_va_a_outbox_con_adjuntos(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID_JOVEN", "chat-joven-falso")
    r = enviar_al_joven("Mensaje", [tmp_path / "entrevista.ics"], outbox=tmp_path / "outbox")
    assert r.canal == "outbox" and r.ruta.name.startswith("joven_")
    contenido = r.ruta.read_text(encoding="utf-8")
    assert "entrevista.ics" in contenido and "Mensaje" in contenido


def test_fallo_del_transporte_cae_a_outbox_sin_filtrar_el_token(tmp_path):
    def falla(token, chat_id, texto, adjuntos):
        raise OSError(f"sin red https://api.telegram.org/bot{token}/sendMessage")

    r = enviar_al_joven("x", token="TOKEN-SECRETO", chat_id="c", outbox=tmp_path, enviar=falla)
    assert (r.canal, r.ok) == ("outbox", True)
    assert "TOKEN-SECRETO" not in r.detalle
    assert "TOKEN-SECRETO" not in r.ruta.read_text(encoding="utf-8")


def test_respuesta_no_ok_cae_a_outbox(tmp_path):
    r = enviar_al_joven("x", token="t", chat_id="c", outbox=tmp_path, enviar=lambda *a: {"ok": False})
    assert r.canal == "outbox" and r.ruta.name.startswith("joven_")


def test_dos_mensajes_al_joven_no_se_pisan(tmp_path):
    a = enviar_al_joven("uno", outbox=tmp_path)
    b = enviar_al_joven("dos", outbox=tmp_path)
    assert a.ruta != b.ruta and len(list(tmp_path.glob("joven_*.txt"))) == 2


def test_la_lista_corta_a_la_empresa_sigue_usando_mensaje_(tmp_path):
    r = enviar_lista_corta("x", outbox=tmp_path)
    assert r.ruta.name.startswith("mensaje_")
