"""Casos de impacto ilustrativo para el issue #47."""

import pytest

from puente.metricas import calcular_metricas
from puente.solver.asignacion import asignar


def test_yaritza_descartes_comparacion_y_evidencia_literal():
    perfil = {
        "id": "Y", "nombre": "Yaritza", "edad": 20,
        "corregimiento": "La Chorrera", "salario_minimo": 500,
        "presupuesto_pasaje_diario": 3,
        "habilidades": [
            {"skill_id": "caja", "evidencia": "Cobro en la fonda"},
            {"skill_id": "excel", "evidencia": ""},
        ],
    }
    ofertas = [
        {"id": "C", "corregimiento": "La Chorrera", "salario_mensual": 650,
         "habilidades": ["caja", "excel"]},
        {"id": "T", "corregimiento": "Tocumen", "salario_mensual": 650,
         "habilidades": ["caja"]},
    ]
    rutas = {
        ("La Chorrera", "La Chorrera"): (15, 0.50),
        ("La Chorrera", "Tocumen"): (95, 2.35),
    }
    matches = asignar([perfil], ofertas, traslados=rutas)
    resultado = calcular_metricas(matches, [perfil], ofertas, rutas)

    assert resultado["rotulo"] == "Ilustrativo, datos de la demo"
    assert resultado["total_ofertas_descartadas_traslado_pasaje"] == 1
    assert "traslado" in resultado["ofertas_descartadas_traslado_pasaje"][0]["motivo"]
    assert resultado["comparaciones_mensuales"] == [{
        "perfil_id": "Y", "oferta_elegida_id": "C", "oferta_descartada_id": "T",
        "motivo_descarte": resultado["ofertas_descartadas_traslado_pasaje"][0]["motivo"],
        "diferencia_minutos_mes": 3520, "diferencia_usd_mes": 81.4,
    }]
    assert resultado["habilidades_informales_con_evidencia"] == [
        {"perfil_id": "Y", "skill_id": "caja", "evidencia_literal": "Cobro en la fonda"}
    ]


def test_sin_match_viable_no_inventa_ahorros_ni_habilidades():
    perfil = {"id": "P", "corregimiento": "La Chorrera", "habilidades": [
        {"skill_id": "caja", "evidencia": "Cobro en la fonda"}
    ]}
    oferta = {"id": "T", "corregimiento": "Tocumen", "habilidades": ["caja"]}
    match = {"perfil_id": "P", "oferta_id": "T", "desglose": {
        "viable": False, "ofertas_descartadas": {"T": "traslado 95 min > 60"}
    }}
    resultado = calcular_metricas([match], [perfil], [oferta], {})
    assert resultado["total_ofertas_descartadas_traslado_pasaje"] == 1
    assert resultado["comparaciones_mensuales"] == []
    assert resultado["total_habilidades_informales_con_evidencia"] == 0


def test_duplicados_y_ruta_ausente_no_inflan_metricas():
    perfil = {"id": "P", "corregimiento": "Origen", "habilidades": [
        {"skill_id": "caja", "evidencia": "Cobro en la fonda"}
    ]}
    ofertas = [
        {"id": "A", "corregimiento": "Destino", "habilidades": ["caja"]},
        {"id": "B", "corregimiento": "Otro", "habilidades": ["caja"]},
    ]
    match = {"perfil_id": "P", "oferta_id": "A", "desglose": {
        "viable": True, "minutos_traslado": 30, "pasaje_diario": 1.0,
        "ofertas_descartadas": {"B": "sin dato de traslado (Origen -> Otro)"},
    }}
    resultado = calcular_metricas([match, match], [perfil], ofertas, {})
    assert resultado["total_ofertas_descartadas_traslado_pasaje"] == 1
    assert resultado["comparaciones_mensuales"] == []
    assert resultado["total_habilidades_informales_con_evidencia"] == 1


def test_oferta_bloqueada_no_cuenta_como_descarte_de_traslado():
    perfil = {"id": "P", "corregimiento": "Origen", "habilidades": []}
    ofertas = [
        {"id": "A", "corregimiento": "Origen", "bloqueada": False, "habilidades": []},
        {"id": "B", "corregimiento": "Lejos", "bloqueada": True, "habilidades": []},
        {"id": "C", "corregimiento": "Lejos", "bloqueada": False, "habilidades": []},
    ]
    match = {"perfil_id": "P", "oferta_id": "A", "desglose": {
        "viable": True, "minutos_traslado": 10, "pasaje_diario": 0.5,
        "ofertas_descartadas": {
            "B": "oferta bloqueada por Ley 11; traslado 90 min > 60",
            "C": "traslado 90 min > 60",
        },
    }}
    rutas = {("Origen", "Lejos"): (90, 3.0)}
    resultado = calcular_metricas([match], [perfil], ofertas, rutas)
    assert resultado["total_ofertas_descartadas_traslado_pasaje"] == 1
    assert [d["oferta_id"] for d in resultado["ofertas_descartadas_traslado_pasaje"]] == ["C"]
    assert [d["oferta_descartada_id"] for d in resultado["comparaciones_mensuales"]] == ["C"]


def test_dias_invalidos():
    with pytest.raises(ValueError):
        calcular_metricas([], [], [], {}, dias_laborables=0)
