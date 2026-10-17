"""Pruebas unitarias y de aceptación para puente.solver.asignacion y puente.rules.traslados (Issues #4 y #48)."""

from __future__ import annotations

import time
import pytest

from puente.rules.traslados import (
    CORREGIMIENTOS_PANAMA,
    InfoTraslado,
    cargar_traslados,
    construir_matriz_default,
    obtener_traslado,
)
from puente.schemas import SIN_OFERTA
from puente.solver.asignacion import (
    ConfigSolver,
    asignar,
    asignar_orden_simple,
    evaluar_factibilidad_par,
)


@pytest.fixture
def fixture_traslados() -> dict[tuple[str, str], InfoTraslado]:
    """Fixture explícito de matriz de traslados de prueba (#48)."""
    return construir_matriz_default()


def _perfil_yaritza() -> dict:
    """Perfil sintético de Yaritza (20 años, La Chorrera, estudia de noche, fonda familiar)."""
    return {
        "id": "PERFIL-YARITZA",
        "nombre": "Yaritza",
        "edad": 20,
        "corregimiento": "La Chorrera",
        "horario_libre": {
            "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
            "hora_inicio": "07:00",
            "hora_fin": "17:30",
            "clases": {
                "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                "hora_inicio": "18:00",
                "hora_fin": "22:00",
            },
        },
        "salario_minimo": 600.0,
        "presupuesto_pasaje_diario": 3.00,
        "habilidades": [
            {"skill_id": "atencion_cliente", "evidencia": "Atiende clientes en la fonda de su mamá"},
            {"skill_id": "manejo_caja", "evidencia": "Cobra y cuadra la caja diaria en la fonda"},
            {"skill_id": "inventario", "evidencia": "Lleva el control de insumos de cocina"},
        ],
    }


def test_matriz_traslados_csv_medido_y_error_si_falta():
    """Verifica que cargar_traslados() lea data/traslados.csv (#51) y falle con error claro si falta el archivo (#48)."""
    traslados = cargar_traslados()
    for orig in CORREGIMIENTOS_PANAMA:
        for dest in CORREGIMIENTOS_PANAMA:
            info = obtener_traslado(traslados, orig, dest)
            assert info is not None, f"Falta traslado entre {orig} y {dest}"
            assert info.minutos_hora_pico > 0
            assert info.pasaje_ida_usd > 0
            assert info.fuente and info.fuente != "fixture_prueba"

    with pytest.raises(FileNotFoundError):
        cargar_traslados("data/archivo_inexistente_traslados.csv")


def test_yaritza_tocumen_descartada_por_traslado_y_pasaje():
    """Criterio #4 y #48: Yaritza — Tocumen queda descartada con SIN_OFERTA y motivo de traslado y pasaje."""
    yaritza = _perfil_yaritza()
    oferta_tocumen = {
        "id": "OF-TOCUMEN-01",
        "puesto": "Cajera / Atención al Cliente",
        "empresa": "Comercial del Este S.A.",
        "corregimiento": "Tocumen",
        "salario_mensual": 650.0,
        "horario": {
            "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
            "hora_inicio": "08:00",
            "hora_fin": "17:00",
        },
        "habilidades": ["atencion_cliente", "manejo_caja"],
        "bloqueada": False,
    }

    # Caso A: Solo se evalúa la oferta de Tocumen -> sin vacante viable, devuelve SIN_OFERTA y motivo explícito
    matches_solo_tocumen = asignar([yaritza], [oferta_tocumen])
    assert len(matches_solo_tocumen) == 1
    m_tocumen = matches_solo_tocumen[0]
    assert m_tocumen.perfil_id == "PERFIL-YARITZA"
    assert m_tocumen.oferta_id == SIN_OFERTA
    assert m_tocumen.puntaje == 0
    assert m_tocumen.desglose.get("viable") is False
    texto_motivo = (
        f"{m_tocumen.explicacion} {m_tocumen.desglose.get('motivo_descarte', '')}"
    ).lower()
    assert "traslado 95 min > 60" in texto_motivo

    # Caso B: Se evalúa Tocumen junto con una oferta viable en La Chorrera
    oferta_chorrera = {
        "id": "OF-CHORRERA-01",
        "puesto": "Asistente de Caja e Inventario",
        "empresa": "Supermercado Oeste",
        "corregimiento": "La Chorrera",
        "salario_mensual": 650.0,
        "horario": {
            "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
            "hora_inicio": "08:00",
            "hora_fin": "16:30",
        },
        "habilidades": ["atencion_cliente", "manejo_caja", "inventario"],
        "bloqueada": False,
    }
    matches_mixto = asignar([yaritza], [oferta_tocumen, oferta_chorrera])
    assert len(matches_mixto) == 1
    m_viable = matches_mixto[0]
    assert m_viable.oferta_id == "OF-CHORRERA-01"
    # Puntaje (#48): cobertura 3/3 = 100% (los minutos de traslado filtran y desempatan sin restar puntaje)
    assert m_viable.puntaje == 100
    assert "OF-TOCUMEN-01" in m_viable.desglose.get("ofertas_descartadas", {})
    assert "traslado 95 min > 60" in m_viable.desglose["ofertas_descartadas"]["OF-TOCUMEN-01"]


def test_ningun_joven_aparece_en_mas_de_2_listas_cortas(fixture_traslados):
    """Criterio 1 (#48): Por defecto (max_vacantes_por_perfil=2), ningún joven aparece en más de 2 listas cortas."""
    yaritza = _perfil_yaritza()
    ofertas = [
        {
            "id": f"OF-CHO-{k}",
            "puesto": f"Puesto {k}",
            "empresa": f"Empresa {k}",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0 + k * 20,
            "horario": {
                "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                "hora_inicio": "08:00",
                "hora_fin": "16:00",
            },
            "habilidades": ["atencion_cliente", "manejo_caja"],
            "bloqueada": False,
        }
        for k in range(1, 6)  # 5 vacantes todas viables para Yaritza
    ]
    res = asignar([yaritza], ofertas, fixture_traslados)
    viables = [m for m in res if m.desglose.get("viable") is True]
    assert len(viables) == 2


def test_escenario_competencia_cpsat_supera_orden_simple(fixture_traslados):
    """Criterio 2 (#48): En el escenario de competencia, CP-SAT da una solución distinta, y mejor, que el orden simple."""
    # Configuración: 1 plaza por vacante (k=1) y tope por defecto de 2 vacantes por joven
    cfg = ConfigSolver(max_candidatos_por_vacante=1, max_vacantes_por_perfil=2)

    # 3 vacantes en La Chorrera (O1, O2, O3)
    ofertas = [
        {
            "id": "O1",
            "puesto": "Caja y Ventas",
            "empresa": "Comercio 1",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "16:00"},
            "habilidades": ["s1", "s2", "s3", "s4"],
        },
        {
            "id": "O2",
            "puesto": "Atención y Caja",
            "empresa": "Comercio 2",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "16:00"},
            "habilidades": ["s1", "s2", "s3", "s4"],
        },
        {
            "id": "O3",
            "puesto": "Inventario Nocturno",
            "empresa": "Comercio 3",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "14:00", "hora_fin": "20:00"},
            "habilidades": ["s1", "s2", "s3", "s4"],
        },
    ]

    # J1 tiene disponibilidad completa (sin clases nocturnas) y cubre 100% en O1/O2 y 75% en O3
    # J2 estudia de noche (18:00-22:00, por lo que O3 es inviable para J2) y cubre 75% en O1 y O2
    perfiles = [
        {
            "id": "J1",
            "corregimiento": "La Chorrera",
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": ["s1", "s2", "s3", "s4"],
        },
        {
            "id": "J2",
            "corregimiento": "La Chorrera",
            "horario_clases": [{"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "18:00", "hora_fin": "22:00"}],
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": ["s1", "s2", "s3"],
        },
    ]

    # Orden simple voraz por vacante (O1 -> O2 -> O3):
    # En O1 gana J1 (100 > 75), en O2 gana J1 (100 > 75) agotando su tope de 2 vacantes;
    # en O3 J1 ya agotó su tope y J2 tiene cruce de horario, dejando O3 vacante y a J2 sin empleo (2 matches, suma=200).
    res_simple = asignar_orden_simple(perfiles, ofertas, fixture_traslados, cfg)
    viables_simple = [m for m in res_simple if m.desglose.get("viable")]

    # CP-SAT global: asigna J1 a O3 + una de {O1, O2} y J2 a la otra de {O1, O2} -> 3 matches viables, suma=275!
    res_cpsat = asignar(perfiles, ofertas, fixture_traslados, cfg)
    viables_cpsat = [m for m in res_cpsat if m.desglose.get("viable")]

    parejas_simple = {(m.perfil_id, m.oferta_id) for m in viables_simple}
    parejas_cpsat = {(m.perfil_id, m.oferta_id) for m in viables_cpsat}
    suma_simple = sum(m.puntaje for m in viables_simple)
    suma_cpsat = sum(m.puntaje for m in viables_cpsat)

    assert parejas_cpsat != parejas_simple
    assert len(viables_cpsat) == 3 > len(viables_simple) == 2
    assert suma_cpsat == 275 > suma_simple == 200


def test_oferta_sin_salario_ni_horario_no_se_descarta_y_requiere_confirmacion(fixture_traslados):
    """Criterio 3 (#48): Una oferta sin salario publicado no se descarta: queda con requiere_confirmacion y penalización."""
    yaritza = _perfil_yaritza()
    oferta_konzerta = {
        "id": "OF-KONZERTA-SIN-SALARIO",
        "puesto": "Dependiente de Mostrador",
        "empresa": "Fonda Central (datos de prueba)",
        "corregimiento": "La Chorrera",
        "salario_mensual": None,
        "salario_tipo": "no_publicado",
        "horario": None,
        "horario_tipo": "no_publicado",
        "habilidades": ["atencion_cliente", "manejo_caja", "inventario"],
        "bloqueada": False,
    }
    res = asignar([yaritza], [oferta_konzerta], fixture_traslados)
    assert len(res) == 1
    m = res[0]
    assert m.oferta_id == "OF-KONZERTA-SIN-SALARIO"
    assert m.desglose["viable"] is True
    assert "salario" in m.desglose["requiere_confirmacion"]
    assert "horario" in m.desglose["requiere_confirmacion"]
    # Cobertura 100% - 20 pts (10 por salario + 10 por horario) = 80
    assert m.desglose["penalizacion"] == 20
    assert m.puntaje == 80


def test_mismo_puntaje_entre_jovenes_de_distinto_corregimiento_y_desempate_propio(fixture_traslados):
    """Requisito #48: Los minutos de traslado filtran y desempatan, pero no restan puntaje entre jóvenes."""
    joven_chorrera = {
        "id": "P-CHORRERA",
        "corregimiento": "La Chorrera",
        "salario_minimo": 600.0,
        "presupuesto_pasaje_diario": 4.00,
        "habilidades": ["atencion_cliente", "manejo_caja"],
    }
    joven_calidonia = {
        "id": "P-CALIDONIA",
        "corregimiento": "Calidonia",
        "salario_minimo": 600.0,
        "presupuesto_pasaje_diario": 4.00,
        "habilidades": ["atencion_cliente", "manejo_caja"],
    }
    oferta_calidonia = {
        "id": "OF-CALIDONIA",
        "corregimiento": "Calidonia",
        "salario_mensual": 750.0,
        "habilidades": ["atencion_cliente", "manejo_caja"],
    }
    res = asignar([joven_chorrera, joven_calidonia], [oferta_calidonia], fixture_traslados)
    por_id = {m.perfil_id: m for m in res}
    # Con habilidades iguales, el joven de La Chorrera tiene el mismo puntaje (100) que el de Calidonia
    assert por_id["P-CHORRERA"].puntaje == 100
    assert por_id["P-CALIDONIA"].puntaje == 100

    # Pero para un mismo joven con tope=1 entre dos vacantes de igual cobertura, desempata la más cercana
    oferta_chorrera = {
        "id": "OF-CHORRERA",
        "corregimiento": "La Chorrera",
        "salario_mensual": 750.0,
        "habilidades": ["atencion_cliente", "manejo_caja"],
    }
    res_desempate = asignar(
        [joven_chorrera],
        [oferta_calidonia, oferta_chorrera],
        fixture_traslados,
        ConfigSolver(max_vacantes_por_perfil=1),
    )
    assert len(res_desempate) == 1
    assert res_desempate[0].oferta_id == "OF-CHORRERA"


def test_excluidos_plazas_y_parejas_fijas(fixture_traslados):
    """Requisito #48: Respetar Oferta.plazas, no asignar excluidos y mantener parejas_fijas."""
    perfiles = [
        {"id": f"P{i}", "corregimiento": "La Chorrera", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1"]}
        for i in range(1, 6)
    ]
    oferta_dos_plazas = {
        "id": "OF-PLAZAS-2",
        "corregimiento": "La Chorrera",
        "salario_mensual": 700.0,
        "plazas": 2,
        "habilidades": ["s1"],
    }
    # Con k=2 por plaza y plazas=2, caben 4 candidatos; si excluimos a P1, P1 queda descartado con SIN_OFERTA
    cfg = ConfigSolver(max_candidatos_por_vacante=2, max_vacantes_por_perfil=2)
    res = asignar(
        perfiles,
        [oferta_dos_plazas],
        fixture_traslados,
        cfg,
        excluidos=[{"perfil_id": "P1", "oferta_id": "OF-PLAZAS-2", "origen": "orientador", "motivo": "quitar candidato"}],
        parejas_fijas=[("P5", "OF-PLAZAS-2")],
    )
    viables = [m for m in res if m.desglose.get("viable")]
    assert len(viables) == 4
    ids_viables = {m.perfil_id for m in viables}
    assert "P1" not in ids_viables
    assert "P5" in ids_viables

    descarte_p1 = next(m for m in res if m.perfil_id == "P1")
    assert descarte_p1.oferta_id == SIN_OFERTA
    assert descarte_p1.desglose["viable"] is False


def test_tres_escenarios_calculados_a_mano_sin_violaciones(fixture_traslados):
    """Criterio 2 (#4): 0 violaciones de restricciones duras en 3 escenarios con solución calculada a mano."""
    traslados = fixture_traslados
    cfg = ConfigSolver(
        max_minutos_traslado=60,
        max_porcentaje_salario_pasaje=0.15,
        dias_laborables=22,
        max_candidatos_por_vacante=3,
    )

    # --- ESCENARIO 1: Filtros de restricciones duras (traslado, pasaje 15%, horario clases, salario, bloqueada) ---
    yaritza = _perfil_yaritza()
    ofertas_esc1 = [
        {
            "id": "OF-VIABLE-VA",
            "puesto": "Auxiliar de Tienda",
            "empresa": "Tienda Vista Alegre",
            "corregimiento": "Vista Alegre",
            "salario_mensual": 650.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "16:00"},
            "habilidades": ["atencion_cliente", "manejo_caja", "excel_basico", "facturacion"],
            "bloqueada": False,
        },
        {
            "id": "OF-CRUCE-HORARIO",
            "puesto": "Cajera Turno Tarde-Noche",
            "empresa": "Mini Super Chorrera",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "14:00", "hora_fin": "20:00"},
            "habilidades": ["atencion_cliente", "manejo_caja"],
            "bloqueada": False,
        },
        {
            "id": "OF-SALARIO-BAJO",
            "puesto": "Ayudante General",
            "empresa": "Bodega Oeste",
            "corregimiento": "La Chorrera",
            "salario_mensual": 550.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "15:00"},
            "habilidades": ["inventario"],
            "bloqueada": False,
        },
        {
            "id": "OF-BLOQUEADA",
            "puesto": "Recepcionista",
            "empresa": "Oficina Chorrera",
            "corregimiento": "La Chorrera",
            "salario_mensual": 700.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "16:00"},
            "habilidades": ["atencion_cliente"],
            "bloqueada": True,
        },
        {
            "id": "OF-PASAJE-ALTO",
            "puesto": "Dependiente",
            "empresa": "Almacén Calidonia",
            "corregimiento": "Calidonia",
            "salario_mensual": 600.0,
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "16:00"},
            "habilidades": ["atencion_cliente", "manejo_caja", "inventario"],
            "bloqueada": False,
        },
    ]

    res1 = asignar([yaritza], ofertas_esc1, traslados, cfg)
    assert len(res1) == 1
    assert res1[0].oferta_id == "OF-VIABLE-VA"
    assert res1[0].puntaje == 50  # 50% cobertura (2/4 habilidades)
    assert res1[0].brechas == ["excel_basico", "facturacion"]

    # --- ESCENARIO 2: Capacidad máxima k=3 candidatos por vacante ---
    oferta_bv = {
        "id": "OF-BV-UNICA",
        "puesto": "Auxiliar Administrativo",
        "empresa": "Servicios Centro",
        "corregimiento": "Bella Vista",
        "salario_mensual": 700.0,
        "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "17:00"},
        "habilidades": ["s1", "s2", "s3", "s4"],
        "bloqueada": False,
    }
    perfiles_esc2 = [
        {"id": "P1", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1"]},
        {"id": "P2", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1", "s2", "s3", "s4"]},
        {"id": "P3", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1", "s2", "s3"]},
        {"id": "P4", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": []},
        {"id": "P5", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1", "s2"]},
    ]
    res2 = asignar(perfiles_esc2, [oferta_bv], traslados, cfg)
    asignados_esc2 = [m for m in res2 if m.desglose.get("viable") is True]
    assert len(asignados_esc2) == 3
    assert {m.perfil_id for m in asignados_esc2} == {"P2", "P3", "P5"}
    puntajes_por_id = {m.perfil_id: m.puntaje for m in asignados_esc2}
    assert puntajes_por_id == {"P2": 100, "P3": 75, "P5": 50}

    # --- ESCENARIO 3: Optimización global 1-a-1 (max_candidatos_por_vacante=1, max_vacantes_por_perfil=1) ---
    cfg_1a1 = ConfigSolver(max_candidatos_por_vacante=1, max_vacantes_por_perfil=1)
    ofertas_esc3 = [
        {"id": "O1", "corregimiento": "La Chorrera", "salario_mensual": 700.0, "habilidades": ["a", "b"]},
        {"id": "O2", "corregimiento": "La Chorrera", "salario_mensual": 700.0, "habilidades": ["a"]},
    ]
    perfiles_esc3 = [
        {"id": "PA", "corregimiento": "La Chorrera", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["a", "b"]},
        {"id": "PB", "corregimiento": "La Chorrera", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["a"]},
    ]
    res3 = asignar(perfiles_esc3, ofertas_esc3, traslados, cfg_1a1)
    mapa_esc3 = {m.perfil_id: (m.oferta_id, m.puntaje) for m in res3}
    assert mapa_esc3["PA"] == ("O1", 100)
    assert mapa_esc3["PB"] == ("O2", 100)

    # Verificar 0 violaciones de restricciones duras en todas las asignaciones viables de los 3 escenarios
    for lista_res, lista_perfiles, lista_ofertas, conf in [
        (res1, [yaritza], ofertas_esc1, cfg),
        (res2, perfiles_esc2, [oferta_bv], cfg),
        (res3, perfiles_esc3, ofertas_esc3, cfg_1a1),
    ]:
        p_map = {p["id"]: p for p in lista_perfiles}
        o_map = {o["id"]: o for o in lista_ofertas}
        conteo_por_oferta: dict[str, int] = {}
        for m in lista_res:
            if not m.desglose.get("viable"):
                assert m.oferta_id == SIN_OFERTA
                continue
            conteo_por_oferta[m.oferta_id] = conteo_por_oferta.get(m.oferta_id, 0) + 1
            ev = evaluar_factibilidad_par(p_map[m.perfil_id], o_map[m.oferta_id], traslados, conf)
            assert ev["viable"] is True, f"Violación detectada en {m}: {ev['motivos']}"
            assert m.desglose["minutos_traslado"] <= conf.max_minutos_traslado
        for oid, cnt in conteo_por_oferta.items():
            assert cnt <= conf.max_candidatos_por_vacante, f"Oferta {oid} excedió cupo k"


def test_determinismo_misma_entrada_misma_salida(fixture_traslados):
    """Criterio 3 (#4): Misma entrada, misma salida (num_workers=1, semilla fija)."""
    perfiles = [
        {
            "id": f"P-{i:02d}",
            "corregimiento": CORREGIMIENTOS_PANAMA[i % len(CORREGIMIENTOS_PANAMA)],
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.50,
            "habilidades": [f"skill_{i % 5}", f"skill_{(i + 1) % 5}"],
        }
        for i in range(8)
    ]
    ofertas = [
        {
            "id": f"OF-{j:02d}",
            "corregimiento": CORREGIMIENTOS_PANAMA[j % len(CORREGIMIENTOS_PANAMA)],
            "salario_mensual": 680.0,
            "habilidades": [f"skill_{j % 5}", f"skill_{(j + 2) % 5}"],
        }
        for j in range(12)
    ]

    corridas = [
        [(m.perfil_id, m.oferta_id, m.puntaje, m.brechas) for m in asignar(perfiles, ofertas, fixture_traslados)]
        for _ in range(5)
    ]
    for corrida in corridas[1:]:
        assert corrida == corridas[0]


def test_rendimiento_20_ofertas_por_10_perfiles_menos_de_2s():
    """Criterio 4 (#4): Resuelve 20 ofertas × 10 perfiles en menos de 2 s."""
    traslados = cargar_traslados()
    perfiles = [
        {
            "id": f"PERF-{i:02d}",
            "nombre": f"Candidato {i}",
            "edad": 18 + (i % 10),
            "corregimiento": CORREGIMIENTOS_PANAMA[i % len(CORREGIMIENTOS_PANAMA)],
            "horario_libre": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "07:00", "hora_fin": "18:00"},
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.50,
            "habilidades": [f"sk_{k}" for k in range(i % 6, (i % 6) + 5)],
        }
        for i in range(10)
    ]
    ofertas = [
        {
            "id": f"VAC-{j:02d}",
            "puesto": f"Puesto {j}",
            "empresa": f"Empresa {j}",
            "corregimiento": CORREGIMIENTOS_PANAMA[j % len(CORREGIMIENTOS_PANAMA)],
            "salario_mensual": 650.0 + (j * 10),
            "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "17:00"},
            "habilidades": [f"sk_{k}" for k in range(j % 5, (j % 5) + 4)],
            "bloqueada": False,
        }
        for j in range(20)
    ]

    t0 = time.perf_counter()
    matches = asignar(perfiles, ofertas, traslados)
    duracion = time.perf_counter() - t0

    assert duracion < 2.0, f"El solver tardó {duracion:.3f}s (límite: 2.0s)"
    assert len(matches) >= 10


def test_nodo_matching_y_plantilla_deterministica():
    """Verifica que puente.agents.matching llama al solver CP-SAT, respeta excluidos y registra Decision."""
    from puente.agents.matching import nodo_matching

    yaritza = _perfil_yaritza()
    oferta = {
        "id": "OF-CHORRERA-01",
        "puesto": "Cajera",
        "empresa": "Fonda Oeste",
        "corregimiento": "La Chorrera",
        "salario_mensual": 650.0,
        "habilidades": ["atencion_cliente", "manejo_caja"],
        "bloqueada": False,
    }
    estado = {
        "perfiles": [yaritza],
        "ofertas": [oferta],
        "decisiones": [],
    }
    nuevo = nodo_matching(estado)
    assert len(nuevo["lista_corta"]) == 1
    m0 = nuevo["lista_corta"][0]
    assert m0.puntaje == 100
    assert "Asignado a OF-CHORRERA-01" in m0.explicacion
    assert len(nuevo["decisiones"]) == 1
    d0 = nuevo["decisiones"][0]
    agente_d0 = d0["agente"] if isinstance(d0, dict) else d0.agente
    assert agente_d0 == "matching"


def test_cobertura_cero_descarta_en_lista_corta_salvo_oferta_sin_habilidades():
    """Regresión #98: cobertura 0 descarta con motivo claro cuando la oferta exige habilidades, pero no si no pide ninguna."""
    maria = {
        "id": "maria",
        "nombre": "María Ficticia",
        "corregimiento": "La Chorrera",
        "salario_minimo": 450.0,
        "presupuesto_pasaje_diario": 4.00,
        "habilidades": [{"skill_id": "cocina", "evidencia": "Fonda"}],
    }
    yaritza = _perfil_yaritza()

    oferta_cajero = {
        "id": "of-cajero-vista-alegre",
        "puesto": "Cajero(a) de minisúper",
        "empresa": "Minisúper Ficticio El Ahorro",
        "corregimiento": "Vista Alegre",
        "salario_mensual": 550.0,
        "habilidades": ["manejo_caja", "atencion_cliente"],
        "bloqueada": False,
    }
    oferta_sin_requisitos = {
        "id": "of-sin-habilidades",
        "puesto": "Ayudante general",
        "empresa": "Bodega Oeste",
        "corregimiento": "La Chorrera",
        "salario_mensual": 620.0,
        "habilidades": [],
        "bloqueada": False,
    }

    traslados = construir_matriz_default()
    matches = asignar([maria, yaritza], [oferta_cajero], traslados)
    ids_corta = [m.perfil_id for m in matches if m.en_lista_corta]
    assert "PERFIL-YARITZA" in ids_corta
    assert "maria" not in ids_corta

    descarte_maria = next(m for m in matches if m.perfil_id == "maria" and m.oferta_id == "of-cajero-vista-alegre")
    assert descarte_maria.en_lista_corta is False
    assert "ninguna habilidad requerida en común" in str(descarte_maria.desglose.get("motivo_descarte"))
    assert set(descarte_maria.brechas) == {"manejo_caja", "atencion_cliente"}

    # Si la vacante no exige habilidades, nadie se descarta por cobertura
    matches_sin_req = asignar([maria], [oferta_sin_requisitos], traslados)
    assert [m.perfil_id for m in matches_sin_req if m.en_lista_corta] == ["maria"]


