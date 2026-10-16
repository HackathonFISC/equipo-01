"""elegir_franja: la entrevista respeta clases, horario libre y un margen igual al traslado (issue #12).

Datos SINTÉTICOS: perfiles y ofertas ficticios, sin red ni claves.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from puente.schemas import Horario, Oferta, Perfil
from puente.tools.agenda import HORARIO_LABORAL, PASO_MIN, elegir_franja
from puente.tools.calendario import ZONA_PANAMA

SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes"]
# 2026-10-19 es lunes.
LUNES_8 = datetime(2026, 10, 19, 8, 0, tzinfo=ZONA_PANAMA)
LUNES_7 = datetime(2026, 10, 19, 7, 0, tzinfo=ZONA_PANAMA)


def _h(dias, ini, fin) -> Horario:
    return Horario(dias=dias, hora_inicio=ini, hora_fin=fin)


def _oferta(franjas=None) -> Oferta:
    return Oferta(
        id="o-agenda", puesto="Cajera", empresa="Super Demo", corregimiento="Bella Vista",
        salario_mensual=500.0, franjas_entrevista=franjas or [],
    )


def _perfil(libre=None, clases=None) -> Perfil:
    return Perfil(
        id="p-agenda", nombre="Perfil Ficticio", genero="F", edad=20, corregimiento="Tocumen",
        horario_libre=libre or [], horario_clases=clases or [],
        salario_minimo=400.0, presupuesto_pasaje_diario=2.0,
    )


def test_elegir_franja_respeta_clases_y_margen_de_traslado():
    oferta = _oferta([_h(SEMANA, "16:00", "19:00")])
    perfil = _perfil(clases=[_h(SEMANA, "18:00", "22:00")])
    inicio = elegir_franja(oferta, perfil, 60, desde=LUNES_8, duracion_min=45)
    # 16:00 + 45 + 60 = 17:45 < 18:00. Con 16:30 ya chocaría con las clases.
    assert inicio == datetime(2026, 10, 19, 16, 0, tzinfo=ZONA_PANAMA)
    assert inicio.tzinfo is not None and inicio.utcoffset() == ZONA_PANAMA.utcoffset(None)
    assert inicio > LUNES_8
    fin_con_margen = inicio + timedelta(minutes=45 + 60)
    assert fin_con_margen <= datetime(2026, 10, 19, 18, 0, tzinfo=ZONA_PANAMA)
    # Un inicio 30 min después ya invadiría las clases.
    tarde = inicio + timedelta(minutes=30)
    assert tarde + timedelta(minutes=45 + 60) > datetime(2026, 10, 19, 18, 0, tzinfo=ZONA_PANAMA)


def test_traslado_largo_no_deja_franja_valida():
    oferta = _oferta([_h(SEMANA, "16:00", "19:00")])
    perfil = _perfil(clases=[_h(SEMANA, "18:00", "22:00")])
    # 16:00 + 45 + 90 = 18:15 choca; los inicios posteriores, más aún; y se repite todos los días.
    assert elegir_franja(oferta, perfil, 90, desde=LUNES_8) is None


@pytest.mark.parametrize(
    ("traslado", "esperado"),
    [(0, (8, 0)), (30, (8, 30)), (60, (9, 0)), (75, (9, 15))],
)
def test_oferta_sin_franjas_usa_horario_libre_con_margen_dentro_del_bloque(traslado, esperado):
    perfil = _perfil(libre=[_h(SEMANA, "08:00", "12:00")])
    inicio = elegir_franja(_oferta(), perfil, traslado, desde=LUNES_7)
    assert inicio == datetime(2026, 10, 19, *esperado, tzinfo=ZONA_PANAMA)
    assert inicio.weekday() < 5
    ventana_ini = inicio - timedelta(minutes=traslado)
    ventana_fin = inicio + timedelta(minutes=45 + traslado)
    assert ventana_ini.hour >= 8 and (ventana_fin.hour, ventana_fin.minute) <= (12, 0)


def test_ninguna_franja_posible_devuelve_none():
    # Bloque libre de 1 h: 45 min de entrevista + 2 x 60 de margen no caben nunca.
    perfil = _perfil(libre=[_h(SEMANA, "08:00", "09:00")])
    assert elegir_franja(_oferta(), perfil, 60, desde=LUNES_7) is None


def test_oferta_con_franjas_que_el_joven_no_puede_nunca_devuelve_none():
    oferta = _oferta([_h(["sabado"], "09:00", "12:00")])
    perfil = _perfil(libre=[_h(SEMANA, "08:00", "12:00")])
    assert elegir_franja(oferta, perfil, 20, desde=LUNES_7) is None


def test_el_resultado_es_estrictamente_posterior_a_desde():
    oferta = _oferta([_h(SEMANA, "08:00", "12:00")])
    perfil = _perfil()
    inicio = elegir_franja(oferta, perfil, 0, desde=LUNES_8)
    assert inicio == LUNES_8 + timedelta(minutes=PASO_MIN)
    assert inicio > LUNES_8


def test_desde_a_mitad_de_franja_salta_al_siguiente_paso():
    oferta = _oferta([_h(SEMANA, "08:00", "12:00")])
    desde = datetime(2026, 10, 19, 9, 10, tzinfo=ZONA_PANAMA)
    assert elegir_franja(oferta, _perfil(), 0, desde=desde) == datetime(2026, 10, 19, 9, 15, tzinfo=ZONA_PANAMA)


def test_desde_sin_zona_se_toma_como_panama():
    oferta = _oferta([_h(SEMANA, "08:00", "12:00")])
    inicio = elegir_franja(oferta, _perfil(), 0, desde=datetime(2026, 10, 19, 7, 0))
    assert inicio == datetime(2026, 10, 19, 8, 0, tzinfo=ZONA_PANAMA)


def test_pasa_al_dia_siguiente_cuando_hoy_ya_no_hay_franja():
    oferta = _oferta([_h(SEMANA, "08:00", "12:00")])
    desde = datetime(2026, 10, 19, 11, 30, tzinfo=ZONA_PANAMA)  # a las 11:30 ya no cabe 45 min antes de las 12
    assert elegir_franja(oferta, _perfil(), 0, desde=desde) == datetime(2026, 10, 20, 8, 0, tzinfo=ZONA_PANAMA)


def test_fin_de_semana_se_salta_en_horario_laboral():
    sabado = datetime(2026, 10, 24, 7, 0, tzinfo=ZONA_PANAMA)
    inicio = elegir_franja(_oferta(), _perfil(), 0, desde=sabado)
    assert inicio == datetime(2026, 10, 26, 8, 0, tzinfo=ZONA_PANAMA)  # lunes
    assert inicio.weekday() == 0


def test_sin_franjas_ni_horario_libre_usa_el_horario_laboral_entero():
    inicio = elegir_franja(_oferta(), _perfil(), 30, desde=LUNES_7)
    assert inicio == datetime(2026, 10, 19, 8, 0, tzinfo=ZONA_PANAMA)
    assert HORARIO_LABORAL.hora_inicio == "08:00" and HORARIO_LABORAL.hora_fin == "17:00"
    assert HORARIO_LABORAL.dias == SEMANA


def test_sin_franjas_el_horario_libre_se_recorta_al_horario_laboral():
    # El joven está libre desde las 05:00, pero la entrevista no puede caer antes de las 08:00.
    perfil = _perfil(libre=[_h(SEMANA, "05:00", "10:00")])
    inicio = elegir_franja(_oferta(), perfil, 0, desde=LUNES_7 - timedelta(hours=2))
    assert inicio == datetime(2026, 10, 19, 8, 0, tzinfo=ZONA_PANAMA)


def test_sin_franjas_las_clases_tambien_cuentan():
    perfil = _perfil(
        libre=[_h(SEMANA, "08:00", "17:00")],
        clases=[_h(SEMANA, "08:00", "10:00")],
    )
    inicio = elegir_franja(_oferta(), perfil, 30, desde=LUNES_7)
    # Debe salir de las clases con 30 min de margen: 10:30.
    assert inicio == datetime(2026, 10, 19, 10, 30, tzinfo=ZONA_PANAMA)


def test_el_margen_puede_tocar_las_clases_sin_cruzarse():
    oferta = _oferta([_h(SEMANA, "16:00", "19:00")])
    perfil = _perfil(clases=[_h(SEMANA, "18:00", "22:00")])
    # 16:00 + 45 + 75 = 18:00 justo: tocar el borde no es cruzarse.
    assert elegir_franja(oferta, perfil, 75, desde=LUNES_8) == datetime(2026, 10, 19, 16, 0, tzinfo=ZONA_PANAMA)


def test_con_horario_libre_el_margen_completo_debe_caber_en_un_bloque():
    oferta = _oferta([_h(SEMANA, "08:00", "12:00")])
    perfil = _perfil(libre=[_h(SEMANA, "09:00", "12:00")])
    # Libre desde las 09:00; con 30 min de margen la entrevista no puede empezar antes de las 09:30.
    assert elegir_franja(oferta, perfil, 30, desde=LUNES_7) == datetime(2026, 10, 19, 9, 30, tzinfo=ZONA_PANAMA)


def test_las_clases_solo_cuentan_el_dia_que_corresponden():
    oferta = _oferta([_h(["lunes", "martes"], "16:00", "19:00")])
    perfil = _perfil(clases=[_h(["lunes"], "16:00", "22:00")])
    # El lunes las clases tapan toda la franja; el martes no hay clases.
    assert elegir_franja(oferta, perfil, 0, desde=LUNES_8) == datetime(2026, 10, 20, 16, 0, tzinfo=ZONA_PANAMA)


def test_dias_horizonte_limita_la_busqueda():
    oferta = _oferta([_h(["viernes"], "09:00", "12:00")])
    assert elegir_franja(oferta, _perfil(), 0, desde=LUNES_8, dias_horizonte=3) is None
    assert elegir_franja(oferta, _perfil(), 0, desde=LUNES_8, dias_horizonte=14) == datetime(
        2026, 10, 23, 9, 0, tzinfo=ZONA_PANAMA
    )


def test_desde_por_defecto_es_ahora_en_panama():
    inicio = elegir_franja(_oferta(), _perfil(), 0)
    ahora = datetime.now(ZONA_PANAMA)
    assert inicio is not None
    assert inicio > ahora
    assert inicio - ahora <= timedelta(days=4)  # a lo sumo salta un fin de semana
    assert inicio.weekday() < 5


def test_traslado_negativo_se_trata_como_cero():
    oferta = _oferta([_h(SEMANA, "16:00", "19:00")])
    assert elegir_franja(oferta, _perfil(), -10, desde=LUNES_8) == datetime(2026, 10, 19, 16, 0, tzinfo=ZONA_PANAMA)


def test_duracion_no_positiva_es_un_error():
    with pytest.raises(ValueError):
        elegir_franja(_oferta(), _perfil(), 0, duracion_min=0)
