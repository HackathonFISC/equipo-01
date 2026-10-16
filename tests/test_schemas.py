"""Contratos de puente.schemas (#2) y compatibilidad con el solver (#4) y el auditor (#8)."""
from datetime import datetime

import pytest
from pydantic import ValidationError

from puente.graph import construir_grafo, estado_inicial
from puente.schemas import (
    SIN_OFERTA,
    Decision,
    EstadoPostulacion,
    EstadoPuente,
    Horario,
    Match,
    Oferta,
    Perfil,
)


def _perfil(**cambios):
    datos = dict(
        id="p1",
        nombre="Ana Ficticia",
        genero="F",
        edad=22,
        corregimiento="Bethania",
        horario_libre=[{"dias": ["lunes", "martes"], "hora_inicio": "14:00", "hora_fin": "22:00"}],
        salario_minimo=600,
        presupuesto_pasaje_diario=3.5,
        habilidades=[{"skill_id": "excel", "evidencia": "Curso INADEH"}],
    )
    datos.update(cambios)
    return Perfil(**datos)


def _oferta(**cambios):
    datos = dict(
        id="o1",
        puesto="Cajero",
        empresa="Super Ficticio",
        corregimiento="San Francisco",
        salario_mensual=650,
        horario={"dias": ["lunes"], "hora_inicio": "15:00", "hora_fin": "21:00"},
        habilidades=["atencion_cliente"],
        fuente="demo",
    )
    datos.update(cambios)
    return Oferta(**datos)


def test_perfil_valido():
    perfil = _perfil()
    assert perfil.habilidades[0].skill_id == "excel"
    assert perfil.horario_libre[0].hora_inicio == "14:00"
    assert perfil.horario_clases == []


@pytest.mark.parametrize("edad", [150, 14, 30, -1])
def test_rechaza_edad_fuera_de_rango(edad):
    with pytest.raises(ValidationError):
        _perfil(edad=edad)


@pytest.mark.parametrize("salario", [-100, 0])
def test_rechaza_salario_no_positivo(salario):
    with pytest.raises(ValidationError):
        _oferta(salario_mensual=salario)


def test_rechaza_salario_minimo_negativo():
    with pytest.raises(ValidationError):
        _perfil(salario_minimo=-1)


@pytest.mark.parametrize("hora", ["25:00", "9:00", "14h"])
def test_rechaza_hora_mal_formada(hora):
    with pytest.raises(ValidationError):
        Horario(dias=["lunes"], hora_inicio=hora, hora_fin="18:00")


def test_normaliza_dias():
    assert Horario(dias=["Miércoles", "Sáb"], hora_inicio="08:00", hora_fin="12:00").dias == [
        "miercoles",
        "sabado",
    ]
    with pytest.raises(ValidationError):
        Horario(dias=["funday"], hora_inicio="08:00", hora_fin="12:00")


def test_horario_unico_se_convierte_en_lista():
    perfil = _perfil(horario_libre={"dias": ["sabado"], "hora_inicio": "08:00", "hora_fin": "12:00"})
    assert len(perfil.horario_libre) == 1


def test_oferta_defaults():
    oferta = _oferta()
    assert oferta.bloqueada is False and oferta.alertas == []


def test_estados_postulacion():
    assert [e.value for e in EstadoPostulacion] == [
        "perfil_listo",
        "preseleccionado",
        "aprobado",
        "enviado",
        "entrevista",
        "contratado",
        "no_seleccionado",
    ]


# --- Compatibilidad con el solver (PR #22) ---


def test_match_como_lo_construye_el_solver():
    match = Match(
        perfil_id="p1",
        oferta_id=SIN_OFERTA,
        puntaje=-40,
        desglose={
            "cobertura": 0.5,
            "minutos_traslado": 45,
            "pasaje_diario": 2.5,
            "penalizacion": 10,
            "pasaje_mensual": 55.0,
            "viable": False,
            "motivo_descarte": "horario",
            "ofertas_descartadas": [{"oferta_id": "o1", "motivo": "horario"}],
        },
        brechas=["excel"],
        explicacion="Sin oferta compatible",
    )
    assert match.puntaje == -40 and isinstance(match.puntaje, int)
    assert match.desglose["motivo_descarte"] == "horario"
    assert match.model_dump()["desglose"]["pasaje_mensual"] == 55.0


def test_desglose_es_dict_para_el_auditor():
    """El auditor (#24) solo lee `viable` si `isinstance(desglose, dict)`."""
    desglose = {"cobertura": 0, "minutos_traslado": 0, "pasaje_diario": 0, "penalizacion": 0, "viable": False}
    match = Match(perfil_id="p1", oferta_id="o1", puntaje=0, desglose=desglose)
    assert isinstance(match.desglose, dict)
    assert match.desglose.get("viable", True) is False


@pytest.mark.parametrize(
    "desglose",
    [
        {"cobertura": 1.0, "minutos_traslado": 30, "pasaje_diario": 1.0},
        {"cobertura": "alta", "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0},
        {"cobertura": True, "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0},
    ],
)
def test_desglose_exige_claves_numericas(desglose):
    with pytest.raises(ValidationError):
        Match(perfil_id="p1", oferta_id="o1", puntaje=0, desglose=desglose)


def test_solver_lee_horarios():
    perfil = _perfil()
    assert getattr(perfil, "horario_clases", None) == []
    oferta = _oferta()
    assert oferta.horario.dias == ["lunes"] and oferta.horario.hora_fin == "21:00"


# --- Compatibilidad con el auditor (PR #24) ---


def test_decision_timestamp_por_defecto_utc():
    decision = Decision(agente="auditor", entrada_resumen="3 perfiles", salida_resumen="sin sesgo", razon="ok")
    assert datetime.fromisoformat(decision.timestamp).utcoffset().total_seconds() == 0
    explicita = Decision(agente="a", entrada_resumen="e", salida_resumen="s", razon="r", timestamp="2026-10-16T12:00:00+00:00")
    assert explicita.timestamp == "2026-10-16T12:00:00+00:00"


def test_auditor_copia_perfil_con_otro_nombre_y_genero():
    variante = _perfil().model_copy(update={"nombre": "Luis Ficticio", "genero": "M"})
    assert variante.nombre == "Luis Ficticio" and variante.genero == "M" and variante.edad == 22


def test_estado_tiene_claves_del_auditor_y_solver():
    claves = EstadoPuente.__annotations__
    for clave in [
        "ofertas", "perfiles", "matches", "lista_corta", "aprobacion", "decisiones", "errores",
        "auditoria", "reporte_auditor", "sesgo_detectado", "devolver_a_matching", "accion_auditor",
        "alertas_auditor", "traslados", "config_solver", "reintentos_auditor",
    ]:
        assert clave in claves
    assert EstadoPuente.__total__ is False


# --- Grafo ---


def test_grafo_recorre_punta_a_punta():
    final = construir_grafo().invoke(estado_inicial())
    assert final["decisiones"] == [] and final["errores"] == []


def test_listas_completas_no_se_duplican():
    """Sin reductores: un nodo que devuelve la lista completa no duplica entradas."""
    from langgraph.graph import END, START, StateGraph

    decision = Decision(agente="a", entrada_resumen="e", salida_resumen="s", razon="r")

    def nodo(estado):
        return {"decisiones": estado["decisiones"] + [decision]}

    grafo = StateGraph(EstadoPuente)
    grafo.add_node("n", nodo)
    grafo.add_edge(START, "n")
    grafo.add_edge("n", END)
    final = grafo.compile().invoke({"decisiones": [decision]})
    assert len(final["decisiones"]) == 2
