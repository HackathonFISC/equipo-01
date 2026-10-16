"""Flujo de punta a punta de Postulación: SQLite, PDF, Telegram (doble) y .ics (issue #7)."""
import urllib.request
from datetime import UTC, datetime, timedelta, timezone

import ics
import pytest

from puente.agents.postulacion import postular, registrar_entrevista
from puente.tools import db
from puente.schemas import Decision, HabilidadPerfil, Horario, Match, Oferta, Perfil
from puente.tools.telegram import ResultadoEnvio

PANAMA = timezone(timedelta(hours=-5))
INICIO = datetime(2026, 10, 20, 10, 0, tzinfo=PANAMA)  # SINTÉTICO: fecha de prueba
ACCIONES = 6  # crear, aprobar, CV, .ics, envío, enviado


def _perfil(id_="p1", nombre="Yaritza Ficticia"):
    return Perfil(
        id=id_, nombre=nombre, edad=20, corregimiento="La Chorrera",
        salario_minimo=300.0, presupuesto_pasaje_diario=2.0,
        habilidades=[
            HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo la fonda de mi mamá"),
            HabilidadPerfil(skill_id="excel", evidencia=""),
        ],
    )


def _oferta(id_="o1"):
    return Oferta(
        id=id_, puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
        habilidades=["atencion_cliente"],
    )


def _match(perfil_id="p1", oferta_id="o1"):
    return Match(
        perfil_id=perfil_id, oferta_id=oferta_id, puntaje=0.9,
        desglose={"cobertura": 1.0, "minutos_traslado": 35, "pasaje_diario": 1.5, "penalizacion": 0.0},
        explicacion="Cubre atención al cliente",
    )


def _aprobacion(aprobados, orientador="Orientadora Ficticia", timestamp="2026-10-20T09:30:00-05:00"):
    """Lo que deja el interrupt() del orientador en EstadoPuente.aprobacion."""
    return {
        "orientador": orientador,
        "timestamp": timestamp,
        "aprobados": [{"perfil_id": m.perfil_id, "oferta_id": m.oferta_id} for m in aprobados],
    }


@pytest.fixture(autouse=True)
def sin_red_ni_claves(monkeypatch):
    """Ninguna prueba toca el Telegram real aunque el entorno traiga token (.env, shell de la demo)."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMPRESA", raising=False)

    def prohibido(*a, **k):
        raise AssertionError("no debe haber red en las pruebas")

    monkeypatch.setattr(urllib.request, "urlopen", prohibido)


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
    extra.setdefault("aprobacion", _aprobacion(aprobados))
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
        postular([_match()], {"p1": _perfil()}, ofertas, aprobacion=_aprobacion([_match()]),
                 conn=conn, outbox=tmp_path / "outbox")
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0


def test_doble_ejecucion_no_repite_nada(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    primera = _correr(conn, tmp_path, enviar=enviador)
    (pid,) = primera.postulaciones
    historial = db.historial(conn, pid)
    archivos = sorted(p.name for p in (tmp_path / "outbox").iterdir())

    segunda = _correr(conn, tmp_path, enviar=enviador)

    assert segunda.postulaciones == [pid]
    assert segunda.envios == [] and segunda.archivos == []
    assert len(enviador.mensajes) == 1  # no se vuelve a escribir a la empresa
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 1
    assert db.historial(conn, pid) == historial
    assert sorted(p.name for p in (tmp_path / "outbox").iterdir()) == archivos
    (decision,) = segunda.decisiones
    assert "ya postulada" in decision.razon.lower()
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == ACCIONES + 1


def test_doble_ejecucion_solo_manda_los_nuevos(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    _correr(conn, tmp_path, [_match("p1", "o1")], enviar=enviador)
    r = _correr(conn, tmp_path, [_match("p1", "o1"), _match("p2", "o1")], enviar=enviador)

    assert len(r.postulaciones) == 2 and len(enviador.mensajes) == 2
    segundo = enviador.mensajes[1][2]
    assert "Marcos Ficticio" in segundo and "Yaritza Ficticia" not in segundo
    assert all(db.obtener_postulacion(conn, p)["estado"] == "enviado" for p in r.postulaciones)


def test_fecha_mal_configurada_falla_antes_de_cualquier_efecto(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("PUENTE_ENTREVISTA_INICIO", "mañana")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    args = ([_match()], {"p1": _perfil()}, {"o1": _oferta()})
    aprobacion = _aprobacion(args[0])
    with pytest.raises(ValueError, match="PUENTE_ENTREVISTA_INICIO"):
        postular(*args, aprobacion=aprobacion, conn=conn, outbox=tmp_path / "outbox", enviar=enviador)
    for tabla in ("postulaciones", "decisiones", "perfiles", "ofertas"):
        assert conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0
    assert not (tmp_path / "outbox").exists() and enviador.mensajes == []

    # Con la fecha corregida, el reintento sobre la misma base funciona.
    monkeypatch.setenv("PUENTE_ENTREVISTA_INICIO", "2026-10-20T10:00")
    r = postular(*args, aprobacion=aprobacion, conn=conn, outbox=tmp_path / "outbox", enviar=enviador)
    assert db.obtener_postulacion(conn, r.postulaciones[0])["estado"] == "enviado"


def test_inicio_entrevista_que_no_es_fecha_falla_sin_efectos(conn, tmp_path):
    with pytest.raises(ValueError):
        postular([_match()], {"p1": _perfil()}, {"o1": _oferta()}, aprobacion=_aprobacion([_match()]),
                 conn=conn, outbox=tmp_path / "outbox", inicio_entrevista=lambda m: "mañana")
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0


def test_reintento_tras_envio_fallido_completa_hasta_enviado(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    with monkeypatch.context() as parche:
        parche.setattr(
            "puente.agents.postulacion.enviar_lista_corta",
            lambda *a, **k: ResultadoEnvio("outbox", False, None, "No se pudo escribir el respaldo"),
        )
        r1 = _correr(conn, tmp_path)
    (pid,) = r1.postulaciones
    assert db.obtener_postulacion(conn, pid)["estado"] == "aprobado"

    r2 = _correr(conn, tmp_path, enviar=enviador)

    assert r2.postulaciones == [pid]
    assert db.obtener_postulacion(conn, pid)["estado"] == "enviado"
    assert len(enviador.mensajes) == 1
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 1
    estados = [(h["estado_anterior"], h["estado_nuevo"]) for h in db.historial(conn, pid)]
    assert estados == [(None, "preseleccionado"), ("preseleccionado", "aprobado"), ("aprobado", "enviado")]
    assert len(list((tmp_path / "outbox").glob("cv_*.pdf"))) == 1


def test_reintento_tras_fallo_al_generar_el_cv(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")

    def falla(*a, **k):
        raise OSError("disco lleno")

    with monkeypatch.context() as parche:
        parche.setattr("puente.agents.postulacion.generar_cv", falla)
        with pytest.raises(OSError):
            _correr(conn, tmp_path, enviar=enviador)
    (fila,) = conn.execute("SELECT id, estado FROM postulaciones").fetchall()
    assert fila["estado"] == "aprobado" and enviador.mensajes == []

    r = _correr(conn, tmp_path, enviar=enviador)
    assert r.postulaciones == [fila["id"]]
    assert db.obtener_postulacion(conn, fila["id"])["estado"] == "enviado"
    assert len(enviador.mensajes) == 1 and list((tmp_path / "outbox").glob("cv_*.pdf"))


@pytest.mark.parametrize("estado_previo", ["perfil_listo", "preseleccionado"])
def test_retoma_postulacion_que_no_llego_a_aprobado(conn, tmp_path, monkeypatch, enviador, estado_previo):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    db.guardar_perfil(conn, _perfil())
    db.guardar_oferta(conn, _oferta())
    pid = db.crear_postulacion(conn, "p1", "o1", estado_previo)

    r = _correr(conn, tmp_path, enviar=enviador)

    assert r.postulaciones == [pid]
    assert db.obtener_postulacion(conn, pid)["estado"] == "enviado"
    assert [h["estado_nuevo"] for h in db.historial(conn, pid)][-2:] == ["aprobado", "enviado"]


@pytest.mark.parametrize("estado", ["entrevista", "no_seleccionado"])
def test_postulacion_mas_adelantada_se_salta(conn, tmp_path, enviador, estado):
    db.guardar_perfil(conn, _perfil())
    db.guardar_oferta(conn, _oferta())
    pid = db.crear_postulacion(conn, "p1", "o1")
    for siguiente in ["aprobado", "enviado", estado]:
        db.cambiar_estado(conn, pid, siguiente, "prueba")
    r = _correr(conn, tmp_path, enviar=enviador)
    assert r.postulaciones == [pid] and enviador.mensajes == []
    assert db.obtener_postulacion(conn, pid)["estado"] == estado


def test_ics_y_mensaje_presentan_la_fecha_como_provisional(conn, tmp_path, monkeypatch, enviador):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    r = _correr(conn, tmp_path, enviar=enviador)

    (archivo_ics,) = (tmp_path / "outbox").glob("*.ics")
    (evento,) = ics.Calendar(archivo_ics.read_text(encoding="utf-8")).events
    assert evento.name == "Propuesta de entrevista: Cajera - Super Demo"
    assert "Fecha provisional, por confirmar con la empresa" in evento.description

    texto = enviador.mensajes[0][2]
    assert "Fecha provisional, por confirmar con la empresa" in texto
    assert "Entrevista propuesta: 2026-10-20 10:00" in texto

    razon_ics = next(d.razon for d in r.decisiones if d.salida_resumen.endswith(".ics"))
    assert "provisional" in razon_ics and "por confirmar" in razon_ics


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


def test_las_decisiones_son_del_schema_real_con_timestamp_iso_utc(conn, tmp_path):
    """Con puente.schemas fusionado, Decision.timestamp es str ISO en UTC (no datetime)."""
    r = _correr(conn, tmp_path)
    assert r.decisiones and all(isinstance(d, Decision) for d in r.decisiones)
    for d in r.decisiones:
        assert isinstance(d.timestamp, str)
        assert datetime.fromisoformat(d.timestamp).utcoffset() == timedelta(0)
    for fila in conn.execute("SELECT timestamp FROM decisiones"):
        assert datetime.fromisoformat(fila["timestamp"]).utcoffset() == timedelta(0)


def test_registrar_entrevista_guarda_la_decision_con_timestamp_utc(conn, tmp_path):
    (pid,) = _correr(conn, tmp_path).postulaciones
    registrar_entrevista(conn, pid)
    ultima = conn.execute("SELECT * FROM decisiones ORDER BY id DESC LIMIT 1").fetchone()
    assert ultima["salida_resumen"] == "estado entrevista"
    assert datetime.fromisoformat(ultima["timestamp"]).utcoffset() == timedelta(0)


def test_decision_construida_desde_puente_schemas_se_guarda(conn):
    decision = Decision(
        agente="postulacion", entrada_resumen="e", salida_resumen="s", razon="r",
        timestamp=datetime.now(UTC).isoformat(),
    )
    db.registrar_decision(conn, decision)
    fila = conn.execute("SELECT * FROM decisiones").fetchone()
    assert fila["timestamp"] == decision.timestamp


def test_sin_aprobacion_no_se_hace_nada(conn, tmp_path, enviador):
    for faltante in (None, {}):
        with pytest.raises(ValueError, match="aprobación"):
            _correr(conn, tmp_path, aprobacion=faltante, enviar=enviador)
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == 0
    assert enviador.mensajes == [] and not (tmp_path / "outbox").exists()


def test_postular_exige_el_argumento_aprobacion(conn, tmp_path):
    with pytest.raises(TypeError):
        postular([_match()], {"p1": _perfil()}, {"o1": _oferta()}, conn=conn, outbox=tmp_path)


@pytest.mark.parametrize(
    "dano",
    [
        {"orientador": ""},
        {"orientador": None},
        {"timestamp": ""},
        {"timestamp": "ayer por la tarde"},
        {"aprobados": []},
        {"aprobados": None},
    ],
)
def test_aprobacion_incompleta_no_se_hace_nada(conn, tmp_path, enviador, dano):
    aprobacion = {**_aprobacion([_match()]), **dano}
    with pytest.raises(ValueError):
        _correr(conn, tmp_path, aprobacion=aprobacion, enviar=enviador)
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == 0
    assert enviador.mensajes == [] and not (tmp_path / "outbox").exists()


def test_un_match_no_aprobado_lanza_sin_efectos(conn, tmp_path, enviador):
    aprobacion = _aprobacion([_match("p1", "o1")])  # el orientador solo aprobó p1/o1
    with pytest.raises(ValueError, match="no está en la aprobación"):
        _correr(conn, tmp_path, [_match("p1", "o1"), _match("p2", "o1")],
                aprobacion=aprobacion, enviar=enviador)
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == 0
    assert enviador.mensajes == [] and not (tmp_path / "outbox").exists()


def test_la_aprobacion_de_otra_oferta_no_vale(conn, tmp_path):
    with pytest.raises(ValueError, match="no está en la aprobación"):
        _correr(conn, tmp_path, [_match("p1", "o2")], aprobacion=_aprobacion([_match("p1", "o1")]))
    assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0


def test_el_orientador_y_la_hora_quedan_en_historial_y_decision(conn, tmp_path, enviador, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    aprobacion = _aprobacion([_match()], orientador="Marisol Orientadora", timestamp="2026-10-20T09:30:00-05:00")
    r = _correr(conn, tmp_path, aprobacion=aprobacion, enviar=enviador)
    (pid,) = r.postulaciones
    motivo = next(h["motivo"] for h in db.historial(conn, pid) if h["estado_nuevo"] == "aprobado")
    assert "Marisol Orientadora" in motivo and "2026-10-20T09:30:00-05:00" in motivo
    razon = next(d.razon for d in r.decisiones if d.salida_resumen.endswith("en aprobado"))
    assert "Marisol Orientadora" in razon and "2026-10-20T09:30:00-05:00" in razon
    guardada = conn.execute(
        "SELECT razon FROM decisiones WHERE salida_resumen LIKE '%en aprobado'"
    ).fetchone()["razon"]
    assert "Marisol Orientadora" in guardada


def test_la_aprobacion_acepta_pares_como_match(conn, tmp_path, enviador, monkeypatch):
    """El interrupt puede devolver los Match completos en lugar de dicts perfil/oferta."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "c")
    aprobacion = {**_aprobacion([_match()]), "aprobados": [_match()]}
    r = _correr(conn, tmp_path, aprobacion=aprobacion, enviar=enviador)
    assert db.obtener_postulacion(conn, r.postulaciones[0])["estado"] == "enviado"
