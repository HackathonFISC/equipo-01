"""Pruebas del Tablero Web del Orientador y validaciones de seguridad (Issues #11 y #33)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.server import (
    ESTADOS_POSTULACION,
    ErrorValidacionTablero,
    analizar_nueva_oferta_texto,
    app,
    calcular_matches_tablero,
    obtener_datos_iniciales_demo,
    procesar_accion_orientador,
    registrar_nuevo_perfil,
)


def test_datos_iniciales_demo_incluyen_caso_yaritza_ley11_y_fuentes_sinteticas() -> None:
    assert app.debug is False
    estado = obtener_datos_iniciales_demo()
    assert len(estado["perfiles"]) >= 3
    assert any(p["id"] == "PERFIL-YARITZA" for p in estado["perfiles"])

    # Verificar que todas las fuentes iniciales estén rotuladas como datos de ejemplo (sintéticos)
    for o in estado["ofertas"]:
        assert "sintéticos" in o["fuente"]
        assert "Konzerta" not in o["fuente"]
        assert "Computrabajo" not in o["fuente"]

    # Verificar que las trazas provienen únicamente de los nodos que realmente corrieron
    agentes_trazados = [d["agente"] for d in estado["decisiones"]]
    assert agentes_trazados == ["matching", "formacion", "auditor"]

    # Verificar match de Yaritza en La Chorrera, curso INADEH real y descarte de Tocumen
    matches_yaritza = [
        m for m in estado["matches"] if m["perfil_id"] == "PERFIL-YARITZA"
    ]
    assert len(matches_yaritza) >= 2
    match_chorrera = next(
        m for m in matches_yaritza if m["oferta_id"] == "OF-CHORRERA-01"
    )
    assert "OF-TOCUMEN-03" in match_chorrera["desglose"]["ofertas_descartadas"]
    assert "95 min > 60" in match_chorrera["desglose"]["ofertas_descartadas"]["OF-TOCUMEN-03"]
    assert len(match_chorrera["cursos_recomendados"]) >= 1
    assert "INADEH" in match_chorrera["cursos_recomendados"][0]

    # Verificar ofertas bloqueadas por Ley 11 de 2005 con frase exacta y regla
    bloqueadas = [o for o in estado["ofertas"] if o["bloqueada"]]
    assert len(bloqueadas) == 2
    for o in bloqueadas:
        assert len(o["alertas"]) >= 1
        assert o["alertas"][0]["frase"]
        assert "Ley 11 de 2005" in o["alertas"][0]["regla"]

    # Verificar auditoría contrafactual 100% imparcial
    assert estado["auditoria"]["porcentaje_identico_global"] == 100.0
    assert estado["auditoria"]["sesgo_detectado"] is False
    assert "corregimiento" in estado["auditoria"]["nota_metodologica"].lower()


def test_traslado_faltante_no_inventa_45_min_y_descarta_con_motivo() -> None:
    estado = obtener_datos_iniciales_demo()
    analizar_nueva_oferta_texto(
        estado,
        puesto="Inventarista Regional",
        empresa="Bodega Norte",
        corregimiento="Changuinola",
        salario_mensual=700.0,
        requisitos_texto="Control de inventario en almacén.",
        habilidades=["inventario"],
    )
    id_changuinola = estado["ofertas"][-1]["id"]
    # No debe existir ningún match viable hacia Changuinola
    assert not any(m["oferta_id"] == id_changuinola for m in estado["matches"])
    # Y en ofertas_descartadas de Yaritza debe figurar el motivo 'sin dato de traslado'
    match_yaritza = next(
        m for m in estado["matches"] if m["perfil_id"] == "PERFIL-YARITZA"
    )
    motivo_desc = match_yaritza["desglose"]["ofertas_descartadas"].get(id_changuinola, "")
    assert "sin dato de traslado" in motivo_desc


def test_acciones_orientador_aprobar_editar_quitar_rechazar_y_bloqueo_post_rechazo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.server as srv

    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path)
    estado = obtener_datos_iniciales_demo()

    # 1. Editar observación antes de aprobar
    procesar_accion_orientador(
        estado,
        accion="editar",
        perfil_id="PERFIL-YARITZA",
        oferta_id="OF-CHORRERA-01",
        nota_edicion="Candidata con experiencia verificada en fonda familiar",
    )
    post = next(
        p
        for p in estado["postulaciones"]
        if p["perfil_id"] == "PERFIL-YARITZA" and p["oferta_id"] == "OF-CHORRERA-01"
    )
    assert "fonda familiar" in post["comentario_orientador"]

    # 2. Aprobar match -> genera 3 archivos en outbox/ y cambia estado a enviado
    procesar_accion_orientador(
        estado,
        accion="aprobar",
        perfil_id="PERFIL-YARITZA",
        oferta_id="OF-CHORRERA-01",
        nota_edicion="Aprobado por orientador",
    )
    assert post["estado"] == "enviado"
    assert len(post["archivos_generados"]) == 3
    for nombre_arch in post["archivos_generados"]:
        ruta_arch = (tmp_path / nombre_arch).resolve()
        assert ruta_arch.is_relative_to(tmp_path.resolve())
        assert ruta_arch.exists()

    # 3. Rechazar otro match y comprobar que NO se puede aprobar después de rechazar
    procesar_accion_orientador(
        estado,
        accion="rechazar",
        perfil_id="PERFIL-YARITZA",
        oferta_id="OF-VISTAALEGRE-02",
        nota_edicion="Prefiere vacante más cercana",
    )
    post_rechazado = next(
        p
        for p in estado["postulaciones"]
        if p["perfil_id"] == "PERFIL-YARITZA" and p["oferta_id"] == "OF-VISTAALEGRE-02"
    )
    assert post_rechazado["estado"] == "no_seleccionado"

    with pytest.raises(ErrorValidacionTablero):
        procesar_accion_orientador(
            estado,
            accion="aprobar",
            perfil_id="PERFIL-YARITZA",
            oferta_id="OF-VISTAALEGRE-02",
            nota_edicion="Intento de aprobar después de rechazar",
        )
    assert post_rechazado["estado"] == "no_seleccionado"

    # 4. Quitar candidato y recalcular Matching
    procesar_accion_orientador(
        estado,
        accion="quitar_candidato",
        perfil_id="PERFIL-DANIELA",
        oferta_id="OF-CHORRERA-01",
    )
    assert not any(p["id"] == "PERFIL-DANIELA" for p in estado["perfiles"])


def test_registrar_perfil_con_uuid_no_sobrescribe_yaritza_y_soporta_apostrofo() -> None:
    estado = obtener_datos_iniciales_demo()
    total_inicial = len(estado["perfiles"])

    registrar_nuevo_perfil(
        estado,
        nombre="Yaritza",
        edad=21,
        corregimiento="La Chorrera",
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.0,
        relato_experiencia="Experiencia en tienda de abarrotes.",
        habilidades_ids=["atencion_cliente"],
    )
    assert len(estado["perfiles"]) == total_inicial + 1
    ids_yaritza = [p["id"] for p in estado["perfiles"] if "YARITZA" in p["id"]]
    assert "PERFIL-YARITZA" in ids_yaritza
    assert len(set(ids_yaritza)) == 2

    registrar_nuevo_perfil(
        estado,
        nombre="D'Angelo",
        edad=22,
        corregimiento="Arraiján",
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.0,
        relato_experiencia="Atención en mostrador y cobro en caja.",
        habilidades_ids=["atencion_cliente", "manejo_caja"],
    )
    ultimo = estado["perfiles"][-1]
    assert "'" not in ultimo["id"]
    assert ultimo["nombre"] == "D'Angelo"


def test_endpoints_http_validaciones_path_traversal_415_y_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.server as srv

    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path)

    async def _ejecutar() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            r_reset = await client.post("/api/reset")
            assert r_reset.status_code == 200

            # Verificar que app.js use data-* y addEventListener sin onclick inline
            r_js = await client.get("/static/app.js")
            assert r_js.status_code == 200
            assert "data-accion=" in r_js.text
            assert "onclick=" not in r_js.text
            assert "&#39;" in r_js.text

            # 1. Content-Type inválido -> 415
            r_415 = await client.post(
                "/api/orientador/accion",
                content="accion=aprobar",
                headers={"Content-Type": "text/plain"},
            )
            assert r_415.status_code == 415

            # 2. Intento de Path Traversal (../../../X) -> 422 y 0 archivos fuera de outbox/
            r_traversal = await client.post(
                "/api/orientador/accion",
                json={
                    "accion": "aprobar",
                    "perfil_id": "../../../X",
                    "oferta_id": "OF-CHORRERA-01",
                },
            )
            assert r_traversal.status_code == 422
            assert list(tmp_path.glob("*")) == []

            # 3. Par inexistente -> 422
            r_no_match = await client.post(
                "/api/orientador/accion",
                json={
                    "accion": "aprobar",
                    "perfil_id": "PERFIL-YARITZA",
                    "oferta_id": "OF-NO-EXISTE",
                },
            )
            assert r_no_match.status_code == 422

            # 4. Rechazar y luego intentar aprobar por HTTP -> 422
            r_rech = await client.post(
                "/api/orientador/accion",
                json={
                    "accion": "rechazar",
                    "perfil_id": "PERFIL-YARITZA",
                    "oferta_id": "OF-VISTAALEGRE-02",
                },
            )
            assert r_rech.status_code == 200

            r_reaprob = await client.post(
                "/api/orientador/accion",
                json={
                    "accion": "aprobar",
                    "perfil_id": "PERFIL-YARITZA",
                    "oferta_id": "OF-VISTAALEGRE-02",
                },
            )
            assert r_reaprob.status_code == 422

            # 5. Entradas inválidas en /api/perfiles (edad=150) y /api/ofertas (salario=-10) -> 422
            r_edad = await client.post(
                "/api/perfiles",
                json={
                    "nombre": "Test",
                    "edad": 150,
                    "corregimiento": "La Chorrera",
                    "salario_minimo": 600,
                    "presupuesto_pasaje_diario": 3.0,
                    "relato_experiencia": "Atención en tienda",
                },
            )
            assert r_edad.status_code == 422

            r_sal = await client.post(
                "/api/ofertas",
                json={
                    "puesto": "Cajero",
                    "empresa": "Tienda",
                    "corregimiento": "La Chorrera",
                    "salario_mensual": -10,
                    "requisitos_texto": "Atención",
                },
            )
            assert r_sal.status_code == 422

            # 6. Simular inyección de sesgo y verificar que el Auditor real la detecta
            r_sesgo = await client.post(
                "/api/auditor/simular-sesgo", json={"activar": True}
            )
            assert r_sesgo.status_code == 200
            datos_sesgo = r_sesgo.json()
            assert datos_sesgo["auditoria"]["sesgo_detectado"] is True
            assert datos_sesgo["auditoria"]["accion_grafo"] == "matching"

            await client.post("/api/reset")

    asyncio.run(_ejecutar())
