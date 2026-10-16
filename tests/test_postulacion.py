"""Flujo de punta a punta de Postulación: SQLite, PDF, Telegram (doble) y .ics (issue #7)."""
import urllib.request
from datetime import datetime, timedelta, timezone

import ics
import pytest

from puente.agents.postulacion import postular, registrar_entrevista
from puente.tools import db
from puente.tools._contratos import Habilidad, Horario, Match, Oferta, Perfil

PANAMA = timezone(timedelta(hours=-5))
INICIO = datetime(2026, 10, 20, 10, 0, tzinfo=PANAMA)  # SINTÉTICO: fecha de prueba
ACCIONES = 6  # crear, aprobar, CV, .ics, envío, enviado


def _perfil(id_="p1", nombre="Yaritza Ficticia"):
    return Perfil(
        id=id_, nombre=nombre, edad=20, corregimiento="La Chorrera",
        habilidades=[
            Habilidad(skill_id="atencion_cliente", evidencia="atiendo la fonda de mi mamá"),
            Habilidad(skill_id="excel", evidencia=""),
        ],
    )


def _oferta(id_="o1"):
    return Oferta(
        id=id_, puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
        habilidades=["atencion_cliente"],
    )


def _match(perfil_id="p1", oferta_id="o1"):
    return Match(perfil_id=perfil_id, oferta_id=oferta_id, puntaje=0.9, explicacion="Cubre atención al cliente")


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "puente.db")
    yield c
    c.close()


@pytest.fixture
def enviador():
    class Doble:
        def __init__(self):
            self.mensajes = []

        def __call__(self, token, chat_id, texto, adjuntos):
            self.mensajes.append((token, chat_id, texto, adjuntos))
            return {"ok": True}

    return Doble()


def _correr(conn, tmp_path, aprobados=None, **extra):
    aprobados = aprobados if aprobados is not None else [_match()]
    perfiles = {"p1": _perfil(), "p2": _perfil("p2", "Marcos Ficticio")}
    ofertas = {"o1": _oferta(), "o2": _oferta("o2")}
    return postular(aprobados, perfiles, ofertas, conn=conn, outbox=tmp_path / "outbox",
                    inicio_entrevista=lambda m: INICIO, **extra)


def test_flujo_match_aprobado(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-de-prueba")
    r = _correr(conn, tmp_path, enviar=enviador)

    (pid,) = r.postulaciones
    assert db.obtener_postulacion(conn, pid)["estado"] == "enviado"
    estados = [(h["estado_anterior"], h["estado_nuevo"]) for h in db.historial(conn, pid)]
    assert estados[1:] == [("preseleccionado", "aprobado"), ("aprobado", "enviado")]

    (pdf,) = (tmp_path / "outbox").glob("cv_*.pdf")
    assert pdf.read_bytes().startswith(b"%PDF")

    assert len(enviador.mensajes) == 1
    token, chat_id, texto, adjuntos = enviador.mensajes[0]
    assert chat_id == "chat-de-prueba"
    assert "Yaritza Ficticia" in texto and "atencion_cliente" in texto
    assert "excel" not in texto  # sin evidencia, no sale
    assert pdf in adjuntos
    assert r.envios[0].canal == "telegram"

    (archivo_ics,) = (tmp_path / "outbox").glob("*.ics")
    (evento,) = ics.Calendar(archivo_ics.read_text(encoding="utf-8")).events
    assert evento.begin.datetime == INICIO and evento.duration == timedelta(minutes=45)


def test_sin_token_usa_outbox(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMPRESA", raising=False)

    def prohibido(*a, **k):
        raise AssertionError("no debe haber red")

    monkeypatch.setattr(urllib.request, "urlopen", prohibido)
    r = _correr(conn, tmp_path)
    assert r.envios[0].canal == "outbox"
    (mensaje,) = (tmp_path / "outbox").glob("mensaje_*.txt")
    assert "Yaritza Ficticia" in mensaje.read_text(encoding="utf-8")
    assert db.obtener_postulacion(conn, r.postulaciones[0])["estado"] == "enviado"


def test_error_de_red_usa_outbox(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-de-prueba")

    def sin_red(*a):
        raise OSError("sin red")

    r = _correr(conn, tmp_path, enviar=sin_red)
    assert r.envios[0].canal == "outbox"
    assert list((tmp_path / "outbox").glob("mensaje_*.txt"))
    assert db.obtener_postulacion(conn, r.postulaciones[0])["estado"] == "enviado"


def test_cada_accion_registra_decision(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-de-prueba")
    r = _correr(conn, tmp_path, enviar=enviador)
    filas = conn.execute("SELECT * FROM decisiones").fetchall()
    assert len(filas) == ACCIONES == len(r.decisiones)
    assert all(f["agente"] == "postulacion" and f["razon"].strip() for f in filas)
    assert not any("token-ficticio" in f["razon"] + f["salida_resumen"] + f["entrada_resumen"] for f in filas)


def test_un_mensaje_por_oferta_con_sus_candidatos(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    r = _correr(conn, tmp_path, [_match("p1", "o1"), _match("p2", "o1"), _match("p1", "o2")], enviar=enviador)
    assert len(enviador.mensajes) == 2 and len(r.postulaciones) == 3
    primero = enviador.mensajes[0][2]
    assert "Yaritza Ficticia" in primero and "Marcos Ficticio" in primero
    assert all(db.obtener_postulacion(conn, p)["estado"] == "enviado" for p in r.postulaciones)
    assert len(list((tmp_path / "outbox").glob("cv_*.pdf"))) == 3


def test_lista_vacia_lanza_sin_efectos(conn, tmp_path):
    with pytest.raises(ValueError):
        _correr(conn, tmp_path, [])
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0
    assert not (tmp_path / "outbox").exists()


def test_match_sin_perfil_u_oferta_lanza_sin_efectos(conn, tmp_path):
    for malo in [_match("nadie", "o1"), _match("p1", "nada")]:
        with pytest.raises(ValueError):
            _correr(conn, tmp_path, [_match("p1", "o1"), malo])
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == 0
    assert not (tmp_path / "outbox").exists()


def test_oferta_bloqueada_no_se_postula(conn, tmp_path):
    ofertas = {"o1": _oferta().model_copy(update={"bloqueada": True})}
    with pytest.raises(ValueError):
        postular([_match()], {"p1": _perfil()}, ofertas, conn=conn, outbox=tmp_path / "outbox")
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0


def test_no_se_postula_dos_veces(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    _correr(conn, tmp_path)
    with pytest.raises(ValueError):
        _correr(conn, tmp_path)


def test_registrar_entrevista(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (pid,) = _correr(conn, tmp_path).postulaciones
    registrar_entrevista(conn, pid)
    assert db.obtener_postulacion(conn, pid)["estado"] == "entrevista"
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == ACCIONES + 1


def test_registrar_entrevista_antes_de_enviar_es_invalido(conn):
    db.guardar_perfil(conn, _perfil())
    db.guardar_oferta(conn, _oferta())
    pid = db.crear_postulacion(conn, "p1", "o1")
    with pytest.raises(ValueError):
        registrar_entrevista(conn, pid)
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == 0
