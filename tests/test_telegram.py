"""Telegram con enviador doble y respaldo en outbox/, sin red ni token (issue #7)."""
import email.parser
import email.policy
import http.server
import threading
import urllib.request

import pytest

from puente.tools import telegram
from puente.tools.telegram import ResultadoEnvio, enviar_lista_corta

_URLOPEN_REAL = urllib.request.urlopen  # el autouse lo bloquea; la prueba del servidor local lo restaura


@pytest.fixture(autouse=True)
def sin_red_ni_claves(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMPRESA", raising=False)

    def prohibido(*a, **k):
        raise AssertionError("no debe haber red en las pruebas")

    monkeypatch.setattr(urllib.request, "urlopen", prohibido)


def test_envia_con_el_doble(tmp_path):
    recibidos = []

    def doble(token, chat_id, texto, adjuntos):
        recibidos.append((token, chat_id, texto, adjuntos))
        return {"ok": True}

    r = enviar_lista_corta("hola", [tmp_path / "a.pdf"], token="t-falso", chat_id="chat-demo",
                           outbox=tmp_path / "outbox", enviar=doble)
    assert isinstance(r, ResultadoEnvio) and (r.canal, r.ok) == ("telegram", True)
    assert recibidos == [("t-falso", "chat-demo", "hola", [tmp_path / "a.pdf"])]
    assert not (tmp_path / "outbox").exists()


def test_lee_token_y_chat_del_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t-env")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-env")
    vistos = []
    enviar_lista_corta("x", outbox=tmp_path, enviar=lambda *a: vistos.append(a[:2]) or {"ok": True})
    assert vistos == [("t-env", "chat-env")]


def test_sin_token_usa_outbox_y_no_toca_la_red(tmp_path):
    r = enviar_lista_corta("Lista corta de prueba", [tmp_path / "cv.pdf"], outbox=tmp_path / "outbox")
    assert (r.canal, r.ok) == ("outbox", True)
    assert r.ruta.parent == tmp_path / "outbox" and r.ruta.name.startswith("mensaje_")
    contenido = r.ruta.read_text(encoding="utf-8")
    assert "Lista corta de prueba" in contenido and "cv.pdf" in contenido


def test_sin_chat_tambien_usa_outbox(tmp_path):
    r = enviar_lista_corta("x", token="t", outbox=tmp_path)
    assert r.canal == "outbox"


def test_error_de_red_usa_outbox_sin_filtrar_el_token(tmp_path):
    def falla(token, chat_id, texto, adjuntos):
        raise OSError(f"sin red https://api.telegram.org/bot{token}/sendMessage")

    r = enviar_lista_corta("x", token="TOKEN-SECRETO", chat_id="c", outbox=tmp_path, enviar=falla)
    assert (r.canal, r.ok) == ("outbox", True)
    assert "TOKEN-SECRETO" not in r.detalle
    assert "TOKEN-SECRETO" not in r.ruta.read_text(encoding="utf-8")


def test_respuesta_no_ok_usa_outbox(tmp_path):
    r = enviar_lista_corta("x", token="t", chat_id="c", outbox=tmp_path, enviar=lambda *a: {"ok": False})
    assert r.canal == "outbox"


def test_dos_mensajes_seguidos_no_se_pisan(tmp_path):
    a = enviar_lista_corta("uno", outbox=tmp_path)
    b = enviar_lista_corta("dos", outbox=tmp_path)
    assert a.ruta != b.ruta and len(list(tmp_path.glob("mensaje_*.txt"))) == 2


def test_si_el_respaldo_falla_no_lanza(tmp_path):
    bloqueo = tmp_path / "es_un_archivo"
    bloqueo.write_text("x")
    r = enviar_lista_corta("x", outbox=bloqueo / "outbox")
    assert (r.canal, r.ok, r.ruta) == ("outbox", False, None)


def test_transporte_real_arma_las_llamadas_sin_red(tmp_path, monkeypatch):
    from puente.tools.telegram import enviar_por_telegram

    llamadas = []

    class Respuesta:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def falso(peticion, timeout=None):
        llamadas.append((peticion.full_url, peticion.data))
        return Respuesta()

    monkeypatch.setattr(urllib.request, "urlopen", falso)
    adjunto = tmp_path / "cv.pdf"
    adjunto.write_bytes(b"%PDF-falso")
    resultado = enviar_por_telegram("T0KEN", "123", "hola", [adjunto])
    assert resultado == {"ok": True}
    assert [u.rsplit("/", 1)[1] for u, _ in llamadas] == ["sendMessage", "sendDocument"]
    assert all(u.startswith("https://api.telegram.org/botT0KEN/") for u, _ in llamadas)
    assert b"%PDF-falso" in llamadas[1][1] and b'filename="cv.pdf"' in llamadas[1][1]


def test_transporte_real_contra_un_servidor_local_con_multipart_valido(tmp_path, monkeypatch):
    """sendDocument se arma como multipart que un servidor HTTP real puede interpretar.

    No toca la red externa: ``_API`` apunta a un servidor en 127.0.0.1 que juega a ser la API de
    Telegram. No sustituye la prueba con el bot de demo (ver el PR), pero valida el formato.
    """
    recibidas = []

    class FalsaApi(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            cuerpo = self.rfile.read(int(self.headers["Content-Length"]))
            recibidas.append((self.path, self.headers["Content-Type"], cuerpo))
            respuesta = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(respuesta)))
            self.end_headers()
            self.wfile.write(respuesta)

        def log_message(self, *args):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), FalsaApi)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        monkeypatch.setattr(urllib.request, "urlopen", _URLOPEN_REAL)
        monkeypatch.setattr(telegram, "_API", f"http://127.0.0.1:{servidor.server_address[1]}")
        pdf = tmp_path / "cv_p1_o1.pdf"
        pdf.write_bytes(b"%PDF-1.4 binario \x00\xff\r\n con saltos")
        resultado = telegram.enviar_por_telegram("TOKEN-FALSO", "chat-de-prueba", "hola equipo", [pdf])
    finally:
        servidor.shutdown()
        servidor.server_close()

    assert resultado == {"ok": True}
    assert [ruta for ruta, *_ in recibidas] == ["/botTOKEN-FALSO/sendMessage", "/botTOKEN-FALSO/sendDocument"]
    _, tipo, cuerpo = recibidas[1]
    mensaje = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        b"Content-Type: " + tipo.encode() + b"\r\n\r\n" + cuerpo
    )
    partes = {p.get_param("name", header="content-disposition"): p for p in mensaje.iter_parts()}
    assert partes["chat_id"].get_content().strip() == "chat-de-prueba"
    assert partes["document"].get_filename() == "cv_p1_o1.pdf"
    assert partes["document"].get_payload(decode=True) == pdf.read_bytes()
