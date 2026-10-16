"""Lecturas para el tablero de puente.tools.db: embudo, trazas, descartes y volcado (issue #12).

Todos los datos son SINTÉTICOS.
"""
import sqlite3

import pytest

from puente.schemas import Decision, HabilidadPerfil, Horario, Match, Oferta, Perfil
from puente.tools import db


def _perfil(id_="p1"):
    return Perfil(
        id=id_, nombre="Yaritza Ficticia", edad=20, corregimiento="La Chorrera",
        salario_minimo=300.0, presupuesto_pasaje_diario=2.0,
        habilidades=[HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo la fonda")],
    )


def _oferta(id_="o1", **extra):
    return Oferta(
        id=id_, puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
        **extra,
    )


def _match(perfil_id="p1", oferta_id="o1", **desglose):
    base = {"cobertura": 0.5, "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0.0}
    return Match(perfil_id=perfil_id, oferta_id=oferta_id, puntaje=1.0, desglose={**base, **desglose})


def _decision(agente, n):
    return Decision(
        agente=agente, entrada_resumen=f"entrada {n}", salida_resumen=f"salida {n}",
        razon=f"razón {n}", timestamp=f"2026-10-16T10:0{n}:00Z",
    )


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "puente.db")
    yield c
    c.close()


def test_trazas_salen_de_la_tabla_decisiones(conn):
    assert db.trazas(conn) == []
    decisiones = [_decision("mercado", 1), _decision("matching", 2), _decision("mercado", 3)]
    assert db.volcar_decisiones(conn, decisiones) == 3
    # Idempotente: volcar de nuevo no duplica
    assert db.volcar_decisiones(conn, decisiones) == 0

    filas = db.trazas(conn)
    assert [f["agente"] for f in filas] == ["mercado", "matching", "mercado"]
    assert [f["razon"] for f in filas] == ["razón 1", "razón 2", "razón 3"]
    assert [f["timestamp"] for f in filas] == [d.timestamp for d in decisiones]
    assert set(filas[0]) == {"id", "agente", "entrada_resumen", "salida_resumen", "razon", "timestamp"}
    assert [f["id"] for f in filas] == sorted(f["id"] for f in filas)

    assert [f["razon"] for f in db.trazas(conn, agente="mercado")] == ["razón 1", "razón 3"]
    # limite devuelve las últimas N, también en orden ascendente
    assert [f["razon"] for f in db.trazas(conn, limite=2)] == ["razón 2", "razón 3"]
    assert [f["razon"] for f in db.trazas(conn, agente="mercado", limite=1)] == ["razón 3"]

    # Una fila insertada con SQL directo aparece en las trazas
    with conn:
        conn.execute(
            "INSERT INTO decisiones (agente, entrada_resumen, salida_resumen, razon, timestamp) "
            "VALUES ('auditor', 'e', 's', 'directo', '2026-10-16T11:00:00Z')"
        )
    assert db.trazas(conn)[-1]["razon"] == "directo"
    assert db.trazas(conn, agente="auditor")[0]["agente"] == "auditor"


def test_volcar_decisiones_acepta_dicts_y_no_repite_las_de_postulacion(conn):
    ya = {
        "agente": "postulacion", "entrada_resumen": "e", "salida_resumen": "s",
        "razon": "r", "timestamp": "2026-10-16T12:00:00Z",
    }
    db.registrar_decision(conn, ya)  # Postulación ya la registró directo
    assert db.volcar_decisiones(conn, [ya, _decision("auditor", 1)]) == 1
    assert len(db.trazas(conn)) == 2


def test_embudo_y_descartes(conn):
    cero = db.embudo(conn)
    assert tuple(cero) == db.ESTADOS
    assert len(cero) == 8
    assert set(cero.values()) == {0}

    db.guardar_perfil(conn, _perfil())
    db.guardar_oferta(conn, _oferta("o1"))
    db.guardar_oferta(conn, _oferta("o2"))
    db.guardar_oferta(conn, _oferta("o3", bloqueada=True, alertas=["pide sexo (Ley 11 de 2005, art. 1)"]))
    p1 = db.crear_postulacion(conn, "p1", "o1")
    db.crear_postulacion(conn, "p1", "o2")
    assert db.embudo(conn)["preseleccionado"] == 2

    db.cambiar_estado(conn, p1, "aprobado", "ok")
    db.cambiar_estado(conn, p1, "consentido", "ok")
    assert db.embudo(conn)["consentido"] == 1
    db.cambiar_estado(conn, p1, "enviado", "ok")
    e = db.embudo(conn)
    assert (e["preseleccionado"], e["aprobado"], e["consentido"], e["enviado"]) == (1, 0, 0, 1)

    # aprobado -> enviado ya no es válido
    p2 = db.crear_postulacion(conn, "p1", "o3", "aprobado")
    with pytest.raises(ValueError):
        db.cambiar_estado(conn, p2, "enviado", "sin consentimiento")

    # descartes del Matching
    descartado = _match(oferta_id="o2", viable=False, motivo_descarte="traslado de 95 min a Tocumen")
    assert db.registrar_descartes(conn, [descartado, _match(oferta_id="o1")]) == 1
    assert db.registrar_descartes(conn, [descartado]) == 0  # idempotente

    de_perfil = db.descartes(conn, perfil_id="p1")
    assert {
        "perfil_id": "p1", "oferta_id": "o2",
        "motivo": "traslado de 95 min a Tocumen", "origen": "matching",
    } in de_perfil
    bloqueada = {
        "perfil_id": None, "oferta_id": "o3",
        "motivo": "pide sexo (Ley 11 de 2005, art. 1)", "origen": "oferta_bloqueada",
    }
    assert bloqueada in de_perfil
    # Las ofertas bloqueadas no dependen de ninguna persona: salen también para otro perfil
    assert db.descartes(conn, perfil_id="otro") == [bloqueada]


def test_descarte_por_viable_false_sin_motivo_y_sin_oferta(conn):
    m_sin_motivo = _match(oferta_id="o9", viable=False)
    m_sin_oferta = _match(oferta_id="SIN_OFERTA", viable=False, motivo_descarte="nada")
    assert db.registrar_descartes(conn, [m_sin_motivo, m_sin_oferta]) == 1
    assert db.descartes(conn) == [
        {"perfil_id": "p1", "oferta_id": "o9", "motivo": "no viable", "origen": "matching"}
    ]


def test_volcar_estado_vuelca_decisiones_y_descartes(conn):
    estado = {
        "decisiones": [_decision("aprobacion", 1).model_dump()],
        "matches": [
            _match(oferta_id="o2", motivo_descarte="traslado de 95 min a Tocumen").model_dump(),
        ],
    }
    assert db.volcar_estado(conn, estado) == {"decisiones": 1, "descartes": 1}
    assert db.volcar_estado(conn, estado) == {"decisiones": 0, "descartes": 0}
    assert db.trazas(conn, agente="aprobacion")[0]["salida_resumen"] == "salida 1"
    assert db.volcar_estado(conn, {}) == {"decisiones": 0, "descartes": 0}


def test_descartes_se_crea_en_una_base_existente(tmp_path):
    """La tabla va con CREATE TABLE IF NOT EXISTS: sirve para un puente.db anterior."""
    ruta = tmp_path / "vieja.db"
    viejo = sqlite3.connect(ruta)
    viejo.execute(
        "CREATE TABLE decisiones (id INTEGER PRIMARY KEY AUTOINCREMENT, agente TEXT NOT NULL, "
        "entrada_resumen TEXT NOT NULL, salida_resumen TEXT NOT NULL, razon TEXT NOT NULL, "
        "timestamp TEXT NOT NULL)"
    )
    viejo.commit()
    viejo.close()
    c = db.conectar(ruta)
    try:
        assert db.descartes(c) == []
    finally:
        c.close()


def test_descartes_con_la_forma_del_solver_real(conn):
    """asignar() deja el descarte en desglose["ofertas_descartadas"], con viable=True y sin motivo."""
    asignado = _match(
        oferta_id="o1", viable=True, motivo_descarte=None,
        ofertas_descartadas={"of-tocumen": "traslado 95 min > 60"},
    )
    sin_vacante = _match(
        perfil_id="p2", oferta_id="o1", viable=False,
        motivo_descarte="Sin vacante viable (of-tocumen: traslado 95 min > 60; o2: salario menor al mínimo)",
        ofertas_descartadas={"of-tocumen": "traslado 95 min > 60", "o2": "salario menor al mínimo"},
    )
    assert db.registrar_descartes(conn, [asignado, sin_vacante]) == 3
    assert db.descartes(conn, perfil_id="p1") == [
        {"perfil_id": "p1", "oferta_id": "of-tocumen", "motivo": "traslado 95 min > 60", "origen": "matching"}
    ]
    # Sin vacante: un motivo por oferta, no el combinado bajo la primera
    assert [(d["oferta_id"], d["motivo"]) for d in db.descartes(conn, perfil_id="p2")] == [
        ("of-tocumen", "traslado 95 min > 60"), ("o2", "salario menor al mínimo")]
    assert db.registrar_descartes(conn, [asignado, sin_vacante]) == 0  # idempotente


def test_motivos_de_descarte_sin_mapa_conserva_el_motivo_por_pareja():
    cupo = _match(oferta_id="o1", viable=False, motivo_descarte="Sin vacante asignada por límite de cupo k=3",
                  ofertas_descartadas={})
    assert db.motivos_de_descarte(cupo) == [("o1", "Sin vacante asignada por límite de cupo k=3")]
    assert db.motivos_de_descarte(_match(viable=True, motivo_descarte=None, ofertas_descartadas={})) == []
