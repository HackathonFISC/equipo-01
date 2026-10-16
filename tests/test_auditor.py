"""Pruebas unitarias y de aceptación para el Agente Auditor (Issue #8)."""

from __future__ import annotations

import copy

from puente.agents.auditor import (
    auditar_lista_corta,
    auditar_perfil,
    generar_variantes_contrafactuales,
    nodo_auditor,
    ruta_post_auditor,
)


def _datos_base():
    perfiles = [
        {
            "id": "P-YARITZA",
            "nombre": "Yaritza",
            "genero": "femenino",
            "edad": 20,
            "corregimiento": "La Chorrera",
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": [
                {"skill_id": "atencion_cliente", "evidencia": "Fonda familiar"},
                {"skill_id": "manejo_caja", "evidencia": "Cobro diario"},
            ],
        },
        {
            "id": "P-KEVIN",
            "nombre": "Kevin",
            "genero": "masculino",
            "edad": 22,
            "corregimiento": "San Miguelito",
            "salario_minimo": 620.0,
            "presupuesto_pasaje_diario": 2.50,
            "habilidades": [
                {"skill_id": "inventario", "evidencia": "Ayudante de bodega"},
            ],
        },
    ]
    ofertas = [
        {
            "id": "OF-CHORRERA-01",
            "puesto": "Cajera / Atención",
            "empresa": "Super Oeste",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "habilidades": ["atencion_cliente", "manejo_caja"],
            "bloqueada": False,
        },
        {
            "id": "OF-SANMIG-01",
            "puesto": "Auxiliar de Inventario",
            "empresa": "Distribuidora Norte",
            "corregimiento": "San Miguelito",
            "salario_mensual": 680.0,
            "habilidades": ["inventario"],
            "bloqueada": False,
        },
    ]
    return perfiles, ofertas


def test_variantes_conservan_corregimiento_intacto():
    """Verifica que las variantes cambian solo nombre y género, nunca el corregimiento."""
    perfiles, _ = _datos_base()
    yaritza = perfiles[0]
    variantes = generar_variantes_contrafactuales(yaritza)
    assert len(variantes) >= 4
    for v in variantes:
        assert v["corregimiento"] == "La Chorrera"
        assert v["edad"] == 20
        assert v["salario_minimo"] == 600.0
        assert (v["nombre"], v["genero"]) != ("Yaritza", "femenino")


def test_reporte_con_porcentaje_resultados_identicos_sin_sesgo():
    """Criterio 1: Reporte con % de resultados idénticos por perfil y explicación de corregimiento fijo."""
    perfiles, ofertas = _datos_base()
    reporte = auditar_lista_corta(perfiles, ofertas)

    assert reporte.sesgo_detectado is False
    assert reporte.devolver_a_matching is False
    assert reporte.accion_grafo == "continuar"
    assert reporte.porcentaje_identico_global == 100.0
    assert len(reporte.perfiles_auditados) == 2

    for r_perfil in reporte.perfiles_auditados:
        assert r_perfil.porcentaje_identico == 100.0
        assert r_perfil.variantes_identicas == r_perfil.total_variantes
        assert r_perfil.sesgo_detectado is False
        assert r_perfil.diferencias == []

    # El reporte Markdown debe ser legible para el tablero e incluir % por perfil y explicación del corregimiento
    assert "100.0%" in reporte.reporte_markdown
    assert "P-YARITZA" in reporte.reporte_markdown
    assert "P-KEVIN" in reporte.reporte_markdown
    assert "corregimiento" in reporte.reporte_markdown.lower()
    assert "traslado" in reporte.reporte_markdown.lower()


def test_sesgo_inyectado_en_perfil_se_detecta_y_vuelve_a_matching():
    """Criterio 2 (parte A): Un sesgo inyectado a propósito en extracción de perfil se detecta y vuelve al Matching."""
    perfiles, ofertas = _datos_base()

    def fn_perfil_sesgada(perfil: dict) -> dict:
        copia = copy.deepcopy(perfil)
        # Inyectamos sesgo artificial: si el género es femenino, le quita una habilidad válida
        if str(copia.get("genero", "")).lower() == "femenino":
            copia["habilidades"] = copia["habilidades"][:1]
        return copia

    res_yaritza = auditar_perfil(
        perfiles[0],
        ofertas,
        fn_perfil=fn_perfil_sesgada,
    )
    assert res_yaritza.sesgo_detectado is True
    assert res_yaritza.porcentaje_identico < 100.0
    assert len(res_yaritza.diferencias) > 0
    assert any("habilidades cambiaron" in d.motivo_diferencia for d in res_yaritza.diferencias)

    # Verificar el comportamiento del nodo_auditor y la arista de regreso a Matching en el grafo
    estado_inicial = {
        "perfiles": perfiles,
        "ofertas": ofertas,
        "lista_corta": [{"perfil_id": "P-YARITZA", "oferta_id": "OF-CHORRERA-01", "puntaje": 85}],
        "decisiones": [],
        "errores": [],
    }
    nuevo_estado = nodo_auditor(estado_inicial, fn_perfil=fn_perfil_sesgada)
    assert nuevo_estado["sesgo_detectado"] is True
    assert nuevo_estado["devolver_a_matching"] is True
    assert nuevo_estado["accion_auditor"] == "matching"
    assert ruta_post_auditor(nuevo_estado) == "matching"
    assert len(nuevo_estado["decisiones"]) == 1
    assert len(nuevo_estado["alertas_auditor"]) >= 1


def test_sesgo_inyectado_en_matching_se_detecta_y_vuelve_a_matching():
    """Criterio 2 (parte B): Un sesgo inyectado en el puntaje de matching por nombre/género se detecta y regresa a Matching."""
    perfiles, ofertas = _datos_base()

    def fn_matching_sesgada(lista_perfiles: list[dict], lista_ofertas: list[dict]) -> list[dict]:
        p = lista_perfiles[0]
        # Inyectar sesgo: penalizar artificialmente si el nombre empieza con 'M' o género es masculino
        penalizacion_sesgo = 25 if str(p.get("genero", "")).lower() == "masculino" else 0
        return [
            {
                "perfil_id": p["id"],
                "oferta_id": lista_ofertas[0]["id"],
                "puntaje": 85 - penalizacion_sesgo,
                "desglose": {"viable": True, "cobertura": 1.0, "minutos_traslado": 15},
            }
        ]

    estado = {
        "perfiles": [perfiles[0]],
        "ofertas": [ofertas[0]],
        "decisiones": [],
        "errores": [],
    }
    salida = nodo_auditor(estado, fn_matching=fn_matching_sesgada)
    assert salida["sesgo_detectado"] is True
    assert salida["devolver_a_matching"] is True
    assert ruta_post_auditor(salida) == "matching"
    assert salida["auditoria"]["porcentaje_identico_global"] < 100.0
