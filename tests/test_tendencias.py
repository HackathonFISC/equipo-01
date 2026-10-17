"""Tendencias del mercado sobre ofertas procesadas en puente.db (#198). Sin red, sin LLM, base en tmp_path."""

from __future__ import annotations

import random

from puente import metricas
from puente.metricas import SIN_CORREGIMIENTO, SIN_REGLA, tendencias_mercado
from puente.rules.discriminacion import esta_bloqueada, revisar_texto
from puente.schemas import Oferta
from puente.tools import db


def _oferta(id_, corregimiento, salario, habilidades, texto=""):
    """Oferta como la deja Mercado: las alertas salen del motor real de reglas de la Ley 11."""
    hallazgos = revisar_texto(texto)
    return Oferta(
        id=id_,
        puesto="Puesto de prueba",
        empresa="Empresa de prueba",
        corregimiento=corregimiento,
        salario_mensual=salario,
        habilidades=habilidades,
        alertas=[h.como_texto() for h in hallazgos],
        bloqueada=esta_bloqueada(hallazgos),
    )


OFERTAS = [
    _oferta("A", "Calidonia", 500.0, ["atencion_cliente", "manejo_caja"]),
    _oferta("B", "Calidonia", 700.0, ["atencion_cliente"], "Se requiere buena presencia."),
    _oferta("C", "Calidonia", None, ["ventas"]),
    _oferta("D", "", 600.0, ["atencion_cliente", "ventas"], "Solo varones de 18 a 25 años."),
    _oferta("E", "Tocumen", None, [], "Edad: entre 20 y 30 años."),
]


def _base(tmp_path, ofertas=OFERTAS):
    ruta = tmp_path / "puente.db"
    conn = db.conectar(ruta)
    for oferta in ofertas:
        db.guardar_oferta(conn, oferta)
    conn.close()
    return ruta


def _leer(ruta):
    conn = db.conectar(ruta)
    try:
        return db.listar_ofertas(conn, no_bloqueadas=False)
    finally:
        conn.close()


def test_tendencias_sobre_una_base_chica(tmp_path):
    t = tendencias_mercado(_leer(_base(tmp_path)))
    assert t["total_ofertas"] == 5
    assert t["rotulo"] == "datos de la demo: 5 ofertas reales anonimizadas de Computrabajo y Konzerta, 16-oct-2026"
    # Una vez por oferta, de más a menos pedida y, a igual conteo, por skill_id.
    assert t["habilidades"] == [
        {"skill_id": "atencion_cliente", "ofertas": 3},
        {"skill_id": "ventas", "ofertas": 2},
        {"skill_id": "manejo_caja", "ofertas": 1},
    ]
    assert t["habilidades_distintas"] == 3
    # Mediana solo del salario publicado; las ofertas sin salario se cuentan aparte, sin inventar.
    assert t["salario_por_corregimiento"] == [
        {"corregimiento": "Calidonia", "n": 2, "mediana": 600.0, "sin_salario": 1},
        {"corregimiento": SIN_CORREGIMIENTO, "n": 1, "mediana": 600.0, "sin_salario": 0},
        {"corregimiento": "Tocumen", "n": 0, "mediana": None, "sin_salario": 1},
    ]
    assert t["salario_general"] == {"n": 3, "mediana": 600.0, "sin_salario": 2}
    # Bloqueos de la Ley 11 por regla (D tiene edad y sexo: suma en las dos) y alertas que no bloquean.
    assert t["ley_11"] == {
        "bloqueadas": 2,
        "bloqueos_por_regla": [{"regla": "edad", "ofertas": 2}, {"regla": "sexo", "ofertas": 1}],
        "alertas_por_regla": [{"regla": "buena_presencia", "ofertas": 1}],
    }


def test_el_orden_de_entrada_no_cambia_el_resultado():
    mezcladas = OFERTAS[:]
    random.Random(7).shuffle(mezcladas)
    assert tendencias_mercado(mezcladas) == tendencias_mercado(OFERTAS)


def test_top_recorta_las_habilidades_pero_no_los_conteos():
    t = tendencias_mercado(OFERTAS, top=1)
    assert t["habilidades"] == [{"skill_id": "atencion_cliente", "ofertas": 3}]
    assert t["habilidades_distintas"] == 3


def test_bloqueada_sin_regla_legible_se_cuenta_aparte_y_otras_alertas_se_ignoran():
    rara = Oferta(
        id="X", puesto="p", empresa="e", corregimiento="Ancón", bloqueada=True,
        alertas=["baja_confianza: sin transcripción literal", "posible duplicada de K01"],
    )
    t = tendencias_mercado([rara])
    assert t["ley_11"]["bloqueadas"] == 1
    assert t["ley_11"]["bloqueos_por_regla"] == [{"regla": SIN_REGLA, "ofertas": 1}]
    assert t["ley_11"]["alertas_por_regla"] == []


def test_sin_ofertas_no_hay_cifras():
    t = tendencias_mercado([])
    assert t["total_ofertas"] == 0 and t["habilidades"] == [] and t["salario_por_corregimiento"] == []
    assert t["salario_general"] == {"n": 0, "mediana": None, "sin_salario": 0}


def test_terminal_tendencias(tmp_path, capsys):
    ruta = _base(tmp_path)
    assert metricas.main(["--tendencias", "--db", str(ruta)]) == 0
    salida = capsys.readouterr().out
    assert "datos de la demo: 5 ofertas reales anonimizadas de Computrabajo y Konzerta, 16-oct-2026" in salida
    assert "atencion_cliente" in salida and "Calidonia" in salida
    assert "Bloqueadas por la Ley 11 de 2005: 2 de 5." in salida
    assert "buena_presencia" in salida


def test_terminal_sin_ofertas_pide_cargar(tmp_path, capsys):
    assert metricas.main(["--tendencias", "--db", str(tmp_path / "vacia.db")]) == 1
    assert "puente.cargar" in capsys.readouterr().out
