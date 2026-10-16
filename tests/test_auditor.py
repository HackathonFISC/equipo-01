"""Pruebas unitarias y de aceptación para el Agente Auditor v2 (Issues #8 y #50)."""

from __future__ import annotations

import copy

from puente.agents.auditor import (
    auditar_lista_corta,
    auditar_perfil,
    generar_variantes_contrafactuales,
    nodo_auditor,
    renderizar_plantilla_genero,
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
            "cv_texto": "{El|La} joven Yaritza es {graduado|graduada} de bachiller y {encargado|encargada} de caja en fonda familiar en La Chorrera.",
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
            "cv_texto": "{El|La} joven Kevin trabajó como {ayudante|ayudanta} de bodega e inventario en San Miguelito.",
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


def test_variantes_conservan_corregimiento_intacto_y_adaptan_concordancia():
    """Verifica que las variantes cambian nombre, género y concordancia gramatical, nunca el corregimiento."""
    perfiles, _ = _datos_base()
    yaritza = perfiles[0]
    variantes = generar_variantes_contrafactuales(yaritza)
    assert len(variantes) >= 6
    for v in variantes:
        assert v["corregimiento"] == "La Chorrera"
        assert v["edad"] == 20
        assert v["salario_minimo"] == 600.0
        assert (v["nombre"], v["genero"]) != ("Yaritza", "femenino")
        assert "{" not in v["cv_texto"]
        if v["genero"] == "masculino":
            assert "El joven" in v["cv_texto"]
            assert "graduado" in v["cv_texto"]
        elif v["genero"] == "femenino":
            assert "La joven" in v["cv_texto"]
            assert "graduada" in v["cv_texto"]


def test_renderizar_plantilla_genero():
    plantilla = "{El|La} joven {nombre} es {graduado|graduada} en {corregimiento}."
    masc = renderizar_plantilla_genero(
        plantilla, "masculino", nombre="Carlos", corregimiento="La Chorrera"
    )
    fem = renderizar_plantilla_genero(
        plantilla, "femenino", nombre="Yaritza", corregimiento="La Chorrera"
    )
    assert masc == "El joven Carlos es graduado en La Chorrera."
    assert fem == "La joven Yaritza es graduada en La Chorrera."


def test_reporte_con_porcentaje_resultados_identicos_linea_base_y_corregimiento():
    """Criterios #8 y #50 Fase 2: Reporte muestra N variantes, línea base (3 corridas) y variante de corregimiento."""
    perfiles, ofertas = _datos_base()
    reporte = auditar_lista_corta(perfiles, ofertas)

    assert reporte.sesgo_detectado is False
    assert reporte.devolver_a_matching is False
    assert reporte.accion_grafo == "continuar"
    assert reporte.porcentaje_identico_global == 100.0
    assert reporte.corridas_linea_base == 3
    assert reporte.total_variantes_evaluadas > 0
    assert f"no se detectaron diferencias en {reporte.total_variantes_evaluadas} variantes" == reporte.resumen_corto
    assert len(reporte.perfiles_auditados) == 2

    for r_perfil in reporte.perfiles_auditados:
        assert r_perfil.porcentaje_identico == 100.0
        assert r_perfil.variantes_identicas == r_perfil.total_variantes
        assert r_perfil.corridas_linea_base == 3
        assert r_perfil.linea_base_estable is True
        assert r_perfil.variante_corregimiento is not None
        assert r_perfil.variante_corregimiento.misma_fila_traslados is True
        assert r_perfil.variante_corregimiento.sesgo_detectado is False
        assert r_perfil.sesgo_detectado is False
        assert r_perfil.diferencias == []

    assert "100.0%" in reporte.reporte_markdown
    assert "Línea base" in reporte.reporte_markdown
    assert "Variante Corregimiento" in reporte.reporte_markdown
    assert "P-YARITZA" in reporte.reporte_markdown
    assert "P-KEVIN" in reporte.reporte_markdown
    assert "corregimiento" in reporte.reporte_markdown.lower()
    assert "traslado" in reporte.reporte_markdown.lower()


def test_sesgo_inyectado_en_perfil_se_detecta_y_vuelve_a_matching_o_perfil():
    """Un sesgo inyectado a propósito en extracción de perfil se detecta en el primer pase."""
    perfiles, ofertas = _datos_base()

    def fn_perfil_sesgada(perfil: dict) -> dict:
        copia = copy.deepcopy(perfil)
        if str(copia.get("genero", "")).lower() == "femenino":
            copia["habilidades"] = copia["habilidades"][:1]
        return copia

    res_yaritza = auditar_perfil(
        perfiles[0],
        ofertas,
        fn_perfil=fn_perfil_sesgada,
    )
    assert res_yaritza.sesgo_detectado is True
    assert res_yaritza.origen_sesgo == "perfil"
    assert res_yaritza.porcentaje_identico < 100.0
    assert len(res_yaritza.diferencias) > 0
    assert any("habilidades cambiaron" in d.motivo_diferencia for d in res_yaritza.diferencias)

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
    assert nuevo_estado["reintentos_auditor"] == 1
    assert nuevo_estado["accion_auditor"] == "matching"
    assert ruta_post_auditor(nuevo_estado) == "matching"
    assert len(nuevo_estado["decisiones"]) == 1
    assert len(nuevo_estado["alertas_auditor"]) >= 1

    # Si el estado incluye entradas_perfil, el recálculo de sesgo de extracción apunta a 'perfil'
    estado_con_entradas = dict(estado_inicial, entradas_perfil=[{"id": "P-YARITZA", "cv_texto": "..."}])
    estado_retorno_perfil = nodo_auditor(estado_con_entradas, fn_perfil=fn_perfil_sesgada)
    assert estado_retorno_perfil["accion_auditor"] == "perfil"
    assert ruta_post_auditor(estado_retorno_perfil) == "perfil"


def test_fase1_sesgo_persistente_hace_un_solo_recalculo_y_pasa_al_orientador():
    """Criterio #50 Fase 1: un test con sesgo persistente hace un solo recálculo y después lo pasa al orientador."""
    perfiles, ofertas = _datos_base()

    def fn_matching_sesgada(lista_perfiles: list[dict], lista_ofertas: list[dict]) -> list[dict]:
        p = lista_perfiles[0]
        penalizacion_sesgo = 25 if str(p.get("genero", "")).lower() == "masculino" else 0
        return [
            {
                "perfil_id": p["id"],
                "oferta_id": lista_ofertas[0]["id"],
                "puntaje": 85 - penalizacion_sesgo,
                "desglose": {"viable": True, "cobertura": 1.0, "minutos_traslado": 15},
            }
        ]

    estado_0 = {
        "perfiles": [perfiles[0]],
        "ofertas": [ofertas[0]],
        "reintentos_auditor": 0,
        "decisiones": [],
        "errores": [],
    }
    # Pase 1: detecta sesgo por primera vez -> reintentos_auditor=1 y hace 1 recálculo
    estado_1 = nodo_auditor(estado_0, fn_matching=fn_matching_sesgada)
    assert estado_1["sesgo_detectado"] is True
    assert estado_1["reintentos_auditor"] == 1
    assert estado_1["devolver_a_matching"] is True
    assert ruta_post_auditor(estado_1) == "matching"

    # Pase 2: si el sesgo persiste tras el primer recálculo -> reintentos_auditor=2 y pasa al orientador sin bucle
    estado_2 = nodo_auditor(estado_1, fn_matching=fn_matching_sesgada)
    assert estado_2["sesgo_detectado"] is True
    assert estado_2["reintentos_auditor"] == 2
    assert estado_2["devolver_a_matching"] is False
    assert estado_2["accion_auditor"] == "orientador"
    assert ruta_post_auditor(estado_2) == "continuar"
    assert len(estado_2["reporte_auditoria"]["diff_orientador"]) > 0


def test_fase2_linea_base_filtra_variacion_natural_del_extractor():
    """Criterio #50 Fase 2: Solo cuenta como sesgo la diferencia que no aparece en la línea base (3 corridas)."""
    perfiles, ofertas = _datos_base()
    contador = {"n": 0}

    def fn_perfil_con_ruido_base(perfil: dict) -> dict:
        copia = copy.deepcopy(perfil)
        contador["n"] += 1
        # En la 2da corrida de la línea base (con el mismo nombre y género originales), fluctúa 'trabajo_equipo'
        if contador["n"] % 2 == 0:
            copia["habilidades"] = list(copia["habilidades"]) + [
                {"skill_id": "trabajo_equipo", "evidencia": "Apoyo en fonda"}
            ]
        return copia

    res = auditar_perfil(
        perfiles[0],
        ofertas,
        fn_perfil=fn_perfil_con_ruido_base,
        corridas_linea_base=3,
    )
    assert res.linea_base_estable is False
    assert "trabajo_equipo" in res.diferencias_linea_base
    # Como 'trabajo_equipo' ya variaba en la línea base, no se reporta falso positivo de sesgo de género/nombre
    assert res.sesgo_detectado is False


def test_fase2_variante_corregimiento_detecta_sesgo_de_barrio_con_misma_fila_traslados():
    """Criterio #50 Fase 2: Variante de corregimiento con misma fila de traslados detecta sesgo si Perfil penaliza el barrio."""
    perfiles, ofertas = _datos_base()

    def fn_perfil_sesgo_barrio(perfil: dict) -> dict:
        copia = copy.deepcopy(perfil)
        if str(copia.get("corregimiento", "")).lower() == "la chorrera":
            copia["habilidades"] = copia["habilidades"][:1]
        return copia

    res = auditar_perfil(
        perfiles[0],
        ofertas,
        fn_perfil=fn_perfil_sesgo_barrio,
        incluir_variante_corregimiento=True,
    )
    assert res.variante_corregimiento is not None
    assert res.variante_corregimiento.misma_fila_traslados is True
    assert res.variante_corregimiento.sesgo_detectado is True
    assert res.sesgo_detectado is True
