"""Pruebas unitarias y de aceptación para puente.solver.asignacion y puente.rules.traslados (Issue #4)."""

from __future__ import annotations

import time

from puente.rules.traslados import (
    CORREGIMIENTOS_PANAMA,
    cargar_traslados,
    obtener_traslado,
)
from puente.solver.asignacion import (
    ConfigSolver,
    asignar,
    evaluar_factibilidad_par,
)


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


def test_matriz_traslados_completa():
    """Verifica que la matriz de traslados cubra todos los pares de los 12 corregimientos."""
    traslados = cargar_traslados()
    for orig in CORREGIMIENTOS_PANAMA:
        for dest in CORREGIMIENTOS_PANAMA:
            info = obtener_traslado(traslados, orig, dest)
            assert info is not None, f"Falta traslado entre {orig} y {dest}"
            assert info.minutos_hora_pico > 0
            assert info.pasaje_ida_usd > 0


def test_yaritza_tocumen_descartada_por_traslado_y_pasaje():
    """Criterio 1: Yaritza — la oferta de Tocumen queda descartada con motivo de traslado o pasaje."""
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

    # Caso A: Solo se evalúa la oferta de Tocumen -> sin vacante viable, devuelve motivo explícito
    matches_solo_tocumen = asignar([yaritza], [oferta_tocumen])
    assert len(matches_solo_tocumen) == 1
    m_tocumen = matches_solo_tocumen[0]
    assert m_tocumen.perfil_id == "PERFIL-YARITZA"
    assert m_tocumen.puntaje == 0
    assert m_tocumen.desglose.get("viable") is False
    texto_motivo = (
        f"{m_tocumen.explicacion} {m_tocumen.desglose.get('motivo_descarte', '')}"
    ).lower()
    assert "traslado 95 min > 60" in texto_motivo or "pasaje" in texto_motivo

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
    # Puntaje calculado a mano: cobertura 3/3 = 100%, traslado interno La Chorrera = 15 min -> 100 - 15 = 85
    assert m_viable.puntaje == 85
    assert "OF-TOCUMEN-01" in m_viable.desglose.get("ofertas_descartadas", {})
    assert "traslado 95 min > 60" in m_viable.desglose["ofertas_descartadas"]["OF-TOCUMEN-01"]


def test_tres_escenarios_calculados_a_mano_sin_violaciones():
    """Criterio 2: 0 violaciones de restricciones duras en 3 escenarios con solución calculada a mano."""
    traslados = cargar_traslados()
    cfg = ConfigSolver(
        max_minutos_traslado=60,
        max_porcentaje_salario_pasaje=0.15,
        dias_laborables=22,
        max_candidatos_por_vacante=3,
    )

    # --- ESCENARIO 1: Filtros de restricciones duras (traslado, pasaje 15%, horario clases, salario, bloqueada) ---
    yaritza = _perfil_yaritza()
    ofertas_esc1 = [
        # 1. Viable en Vista Alegre (traslado 20 min, pasaje $0.75 ida -> $1.50/día, $33/mes <= 15% de $650 = $97.50)
        # Cobertura: 2/4 = 50% -> 50 - 20 = 30 pts
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
        # 2. Inviable por cruce de horario con clases nocturnas (14:00 a 20:00 cruza con 18:00-22:00)
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
        # 3. Inviable por salario menor al mínimo del perfil ($550 < $600)
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
        # 4. Inviable por estar bloqueada por discriminación
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
        # 5. Inviable por pasaje > 15% del salario (La Chorrera -> Calidonia: 55 min <= 60, pero pasaje $1.85 ida -> $3.70/día -> $81.40/mes > $3.00/día presupuesto)
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
    assert res1[0].puntaje == 30  # 50% cobertura (50) - 20 min (20) = 30
    assert res1[0].brechas == ["excel_basico", "facturacion"]

    # --- ESCENARIO 2: Capacidad máxima k=3 candidatos por vacante ---
    # 5 candidatos en Bella Vista compiten por 1 oferta en Bella Vista (15 min traslado)
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
        {"id": "P1", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1"]},               # 25 - 15 = 10
        {"id": "P2", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1", "s2", "s3", "s4"]}, # 100 - 15 = 85
        {"id": "P3", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1", "s2", "s3"]},       # 75 - 15 = 60
        {"id": "P4", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": []},                     # 0 - 15 = -15
        {"id": "P5", "corregimiento": "Bella Vista", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["s1", "s2"]},             # 50 - 15 = 35
    ]
    res2 = asignar(perfiles_esc2, [oferta_bv], traslados, cfg)
    asignados_esc2 = [m for m in res2 if m.desglose.get("viable") is True]
    assert len(asignados_esc2) == 3
    assert {m.perfil_id for m in asignados_esc2} == {"P2", "P3", "P5"}
    puntajes_por_id = {m.perfil_id: m.puntaje for m in asignados_esc2}
    assert puntajes_por_id == {"P2": 85, "P3": 60, "P5": 35}

    # --- ESCENARIO 3: Optimización global 1-a-1 (max_candidatos_por_vacante=1, max_vacantes_por_perfil=1) ---
    cfg_1a1 = ConfigSolver(max_candidatos_por_vacante=1, max_vacantes_por_perfil=1)
    ofertas_esc3 = [
        {"id": "O1", "corregimiento": "La Chorrera", "salario_mensual": 700.0, "habilidades": ["a", "b"]},
        {"id": "O2", "corregimiento": "La Chorrera", "salario_mensual": 700.0, "habilidades": ["a"]},
    ]
    perfiles_esc3 = [
        # PA tiene ["a", "b"]: en O1 saca 100-15=85, en O2 saca 100-15=85
        {"id": "PA", "corregimiento": "La Chorrera", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["a", "b"]},
        # PB solo tiene ["a"]: en O1 saca 50-15=35, en O2 saca 100-15=85
        {"id": "PB", "corregimiento": "La Chorrera", "salario_minimo": 600.0, "presupuesto_pasaje_diario": 3.0, "habilidades": ["a"]},
    ]
    # Óptimo a mano: PA -> O1 (85) y PB -> O2 (85), suma = 170 (si PA tomara O2, PB->O1 daría 85+35=120)
    res3 = asignar(perfiles_esc3, ofertas_esc3, traslados, cfg_1a1)
    mapa_esc3 = {m.perfil_id: (m.oferta_id, m.puntaje) for m in res3}
    assert mapa_esc3["PA"] == ("O1", 85)
    assert mapa_esc3["PB"] == ("O2", 85)

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
                continue
            conteo_por_oferta[m.oferta_id] = conteo_por_oferta.get(m.oferta_id, 0) + 1
            ev = evaluar_factibilidad_par(p_map[m.perfil_id], o_map[m.oferta_id], traslados, conf)
            assert ev["viable"] is True, f"Violación detectada en {m}: {ev['motivos']}"
            assert m.desglose["minutos_traslado"] <= conf.max_minutos_traslado
        for oid, cnt in conteo_por_oferta.items():
            assert cnt <= conf.max_candidatos_por_vacante, f"Oferta {oid} excedió cupo k"


def test_determinismo_misma_entrada_misma_salida():
    """Criterio 3: Misma entrada, misma salida (num_workers=1, semilla fija)."""
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
        [(m.perfil_id, m.oferta_id, m.puntaje, m.brechas) for m in asignar(perfiles, ofertas)]
        for _ in range(5)
    ]
    for corrida in corridas[1:]:
        assert corrida == corridas[0]


def test_rendimiento_20_ofertas_por_10_perfiles_menos_de_2s():
    """Criterio 4: Resuelve 20 ofertas × 10 perfiles en menos de 2 s."""
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


def test_nodo_matching_y_redactor_llm():
    """Verifica que puente.agents.matching llama al solver CP-SAT y registra Decision en EstadoPuente."""
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
        "redactor_matching_llm": lambda m: f"Explicación redactada para {m.perfil_id} ({m.puntaje} pts)",
    }
    nuevo = nodo_matching(estado)
    assert len(nuevo["lista_corta"]) == 1
    m0 = nuevo["lista_corta"][0]
    assert m0.puntaje == 85
    assert "Explicación redactada para PERFIL-YARITZA" in m0.explicacion
    assert len(nuevo["decisiones"]) == 1
    d0 = nuevo["decisiones"][0]
    agente_d0 = d0["agente"] if isinstance(d0, dict) else d0.agente
    assert agente_d0 == "matching"


