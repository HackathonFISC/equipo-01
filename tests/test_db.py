"""Tablas, transiciones del embudo e historial de puente.tools.db (issue #7)."""
import sqlite3

import pytest

from puente.tools import db
from puente.tools._contratos import Decision, Habilidad, Horario, Oferta, Perfil


def _perfil(id_="p1"):
    return Perfil(
        id=id_, nombre="Yaritza Ficticia", edad=20, corregimiento="La Chorrera",
        habilidades=[Habilidad(skill_id="atencion_cliente", evidencia="atiendo la fonda")],
    )


def _oferta(id_="o1"):
    return Oferta(
        id=id_, puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
    )


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "puente.db")
    yield c
    c.close()


def _postulacion(conn):
    db.guardar_perfil(conn, _perfil())
    db.guardar_oferta(conn, _oferta())
    return db.crear_postulacion(conn, "p1", "o1")


def test_crea_las_cinco_tablas(conn):
    nombres = {f["name"] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"perfiles", "ofertas", "postulaciones", "postulaciones_historial", "decisiones"} <= nombres


def test_crear_tablas_es_idempotente(conn):
    db.crear_tablas(conn)
    db.crear_tablas(conn)


def test_ruta_por_defecto_viene_de_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "otra.db"))
    c = db.conectar()
    c.close()
    assert (tmp_path / "otra.db").exists()


def test_guardar_perfil_y_oferta_hacen_upsert(conn):
    db.guardar_perfil(conn, _perfil())
    db.guardar_perfil(conn, _perfil().model_copy(update={"corregimiento": "Arraiján"}))
    db.guardar_oferta(conn, _oferta())
    db.guardar_oferta(conn, _oferta())
    assert conn.execute("SELECT COUNT(*) FROM perfiles").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0] == 1
    assert "Arraiján" in conn.execute("SELECT datos_json FROM perfiles").fetchone()["datos_json"]


def test_postulacion_nace_preseleccionada_con_historial(conn):
    pid = _postulacion(conn)
    assert db.obtener_postulacion(conn, pid)["estado"] == "preseleccionado"
    assert [h["estado_nuevo"] for h in db.historial(conn, pid)] == ["preseleccionado"]


def test_no_se_repite_la_postulacion_perfil_oferta(conn):
    _postulacion(conn)
    with pytest.raises(sqlite3.IntegrityError):
        db.crear_postulacion(conn, "p1", "o1")


def test_clave_foranea_exige_perfil_y_oferta(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.crear_postulacion(conn, "nadie", "nada")


def test_embudo_completo_deja_historial_en_orden(conn):
    pid = _postulacion(conn)
    for estado in ["aprobado", "enviado", "entrevista", "contratado"]:
        db.cambiar_estado(conn, pid, estado, f"paso a {estado}")
    h = db.historial(conn, pid)
    assert [(x["estado_anterior"], x["estado_nuevo"]) for x in h] == [
        (None, "preseleccionado"), ("preseleccionado", "aprobado"), ("aprobado", "enviado"),
        ("enviado", "entrevista"), ("entrevista", "contratado"),
    ]
    assert h[1]["motivo"] == "paso a aprobado"
    assert db.obtener_postulacion(conn, pid)["estado"] == "contratado"


def test_no_seleccionado_es_final(conn):
    pid = _postulacion(conn)
    db.cambiar_estado(conn, pid, "no_seleccionado", "descartado")
    with pytest.raises(ValueError):
        db.cambiar_estado(conn, pid, "aprobado", "no vuelve")


@pytest.mark.parametrize("nuevo", ["preseleccionado", "perfil_listo", "contratado", "aprobado"])
def test_transicion_invalida_desde_enviado(conn, nuevo):
    pid = _postulacion(conn)
    db.cambiar_estado(conn, pid, "aprobado", "ok")
    db.cambiar_estado(conn, pid, "enviado", "ok")
    antes = db.historial(conn, pid)
    with pytest.raises(ValueError):
        db.cambiar_estado(conn, pid, nuevo, "no debe pasar")
    assert db.historial(conn, pid) == antes
    assert db.obtener_postulacion(conn, pid)["estado"] == "enviado"


def test_saltarse_aprobado_es_invalido(conn):
    pid = _postulacion(conn)
    with pytest.raises(ValueError):
        db.cambiar_estado(conn, pid, "enviado", "sin aprobación humana")
    assert len(db.historial(conn, pid)) == 1


def test_cambiar_estado_de_postulacion_inexistente(conn):
    with pytest.raises(ValueError):
        db.cambiar_estado(conn, 999, "aprobado", "nada")


def test_registrar_decision(conn):
    did = db.registrar_decision(
        conn, Decision(agente="postulacion", entrada_resumen="e", salida_resumen="s", razon="r")
    )
    fila = conn.execute("SELECT * FROM decisiones WHERE id = ?", (did,)).fetchone()
    assert (fila["agente"], fila["razon"]) == ("postulacion", "r")
    assert fila["timestamp"]


def test_registrar_decision_como_dict_del_grafo(conn):
    """Los nodos Auditor, Formación y el tablero emiten dicts con timestamp ISO en texto."""
    did = db.registrar_decision(
        conn,
        {
            "agente": "auditor",
            "entrada_resumen": "3 matches",
            "salida_resumen": "sin sesgo",
            "razon": "contrafactuales iguales",
            "timestamp": "2026-10-16T14:00:00Z",
        },
    )
    fila = conn.execute("SELECT * FROM decisiones WHERE id = ?", (did,)).fetchone()
    assert (fila["agente"], fila["entrada_resumen"]) == ("auditor", "3 matches")
    assert (fila["salida_resumen"], fila["razon"]) == ("sin sesgo", "contrafactuales iguales")
    assert fila["timestamp"] == "2026-10-16T14:00:00Z"


def test_registrar_decision_dict_sin_timestamp_usa_la_hora_actual(conn):
    did = db.registrar_decision(
        conn, {"agente": "formacion", "entrada_resumen": "e", "salida_resumen": "s", "razon": "r"}
    )
    assert conn.execute("SELECT timestamp FROM decisiones WHERE id = ?", (did,)).fetchone()[0]
