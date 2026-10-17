"""Vista del centro de formación (#286): el tercer lado del mercado, en solo lectura.

Agrega por curso lo que Formación (#14, #53) ya calcula para cada joven: a cuántos se lo recomienda, qué vacantes
abre o mejora y cuánta presión pone sobre sus cupos. Suma las habilidades más pedidas por las ofertas que ningún
curso del catálogo cubre (`tendencias_mercado`, #198): qué curso convendría abrir.

Solo conteos: ningún nombre, id, edad ni corregimiento de los jóvenes. Un grupo de menos de 3 personas se muestra
como «menos de 3» para que nadie pueda reconocer a quién se refiere. Sin LLM y sin escrituras: es una ruta GET.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from puente.agents.formacion import NATURALEZA_CUPOS_HORARIO, cargar_cursos
from puente.graph import cargar_taxonomia_grafo

_registro = logging.getLogger(__name__)
RUTA_STATIC = Path(__file__).resolve().parent / "static"
MINIMO_GRUPO = 3
ROTULO = "Ilustrativo, datos de la demo"


def _servidor():
    import app.server as srv  # import tardío: server.py importa este módulo para registrar sus rutas

    return srv


def _rutas_formacion(estado: dict[str, Any]) -> list[dict[str, Any]]:
    """Las rutas de formación de todos los jóvenes, tal como las dejó el nodo Formación en el estado del grafo."""
    grafo, config = estado.get("_grafo"), estado.get("_config_grafo")
    if grafo is None or config is None:
        return []
    try:
        rutas = grafo.get_state(config).values.get("rutas_formacion") or []
    except Exception:  # sin grafo listo, la vista muestra el catálogo sin demanda
        _registro.warning("Vista de formación: no se pudieron leer las rutas del grafo", exc_info=True)
        return []
    return [dict(r) for r in rutas if isinstance(r, dict)]


def _conteo_visible(n: int) -> dict[str, Any]:
    """El número y cómo mostrarlo: de 1 a 2 personas se dice «menos de 3» (#286)."""
    if 0 < n < MINIMO_GRUPO:
        return {"valor": None, "texto": f"menos de {MINIMO_GRUPO}"}
    return {"valor": n, "texto": str(n)}


def resumen_centro(estado: dict[str, Any]) -> dict[str, Any]:
    """Lo que ve el centro de formación: sus cursos con demanda, cupos y vacantes que abren, y las brechas sin curso."""
    taxonomia = cargar_taxonomia_grafo()
    nombre = lambda sid: taxonomia.get(sid, {}).get("nombre_es", sid)  # noqa: E731
    cursos = cargar_cursos()
    rutas = _rutas_formacion(estado)

    jovenes_por_curso: dict[str, set[str]] = {}
    abre_por_curso: dict[str, set[str]] = {}
    mejora_por_curso: dict[str, set[str]] = {}
    for ruta in rutas:
        perfil = str(ruta.get("perfil_id") or "")
        for curso in ruta.get("cursos_seleccionados") or []:
            clave = str(curso.get("id") or curso.get("curso") or "")
            if not clave:
                continue
            jovenes_por_curso.setdefault(clave, set()).add(perfil)
            abre_por_curso.setdefault(clave, set()).update(ruta.get("vacantes_abiertas") or [])
            mejora_por_curso.setdefault(clave, set()).update(ruta.get("vacantes_mejoradas") or [])

    filas: list[dict[str, Any]] = []
    cubiertas: set[str] = set()
    for curso in cursos:
        clave = curso.id or curso.curso
        demanda = len(jovenes_por_curso.get(clave, set()) | jovenes_por_curso.get(curso.curso, set()))
        cubiertas.update(curso.skill_ids)
        filas.append(
            {
                "id": curso.id,
                "curso": curso.curso,
                "area": curso.area,
                "sede": curso.corregimiento,  # la sede del curso, no el lugar de ningún joven
                "horario": curso.horario,
                "horas": curso.horas,
                "costo": curso.costo,
                "cupos": curso.cupos,
                "habilidades": [{"skill_id": s, "nombre": nombre(s)} for s in curso.skill_ids],
                "demanda": _conteo_visible(demanda),
                "_orden": demanda,
                # Con 1 o 2 personas la proporción delataría el número exacto: no se da.
                "presion_cupos": None if not curso.cupos or 0 < demanda < MINIMO_GRUPO else round(demanda / curso.cupos, 2),
                "sin_cupo": curso.cupos == 0,
                "vacantes_que_abre": len(abre_por_curso.get(clave, set())),
                "vacantes_que_mejora": len(mejora_por_curso.get(clave, set())),
            }
        )
    filas.sort(key=lambda f: (-f["_orden"], f["curso"]))
    for f in filas:
        del f["_orden"]  # el número exacto de 1 o 2 no sale de aquí

    brechas: list[dict[str, Any]] = []
    total_ofertas = 0
    try:
        from puente.metricas import tendencias_mercado

        tendencias = tendencias_mercado(estado.get("ofertas") or [])
        total_ofertas = int(tendencias.get("total_ofertas") or 0)
        brechas = [
            {"skill_id": h["skill_id"], "nombre": nombre(h["skill_id"]), "ofertas": h["ofertas"]}
            for h in tendencias.get("habilidades") or []
            if h.get("skill_id") and h["skill_id"] not in cubiertas
        ][:8]
    except Exception:  # sin tendencias la vista sigue con el catálogo
        _registro.warning("Vista de formación: no se pudieron calcular las tendencias", exc_info=True)

    jovenes = {str(r.get("perfil_id") or "") for r in rutas if r.get("cursos_seleccionados")}
    return {
        "rotulo": ROTULO,
        "naturaleza_cupos_horario": NATURALEZA_CUPOS_HORARIO,
        "totales": {
            "cursos": len(filas),
            "cupos": sum(f["cupos"] for f in filas),
            "jovenes_con_ruta": _conteo_visible(len(jovenes)),
            "cursos_con_demanda": sum(1 for f in filas if f["demanda"]["texto"] != "0"),
            "ofertas_analizadas": total_ofertas,
        },
        "cursos": filas,
        "brechas_sin_curso": brechas,
    }


async def endpoint_pagina(_request: Request) -> FileResponse:
    return FileResponse(RUTA_STATIC / "formacion.html")


async def endpoint_resumen(_request: Request) -> JSONResponse:
    return JSONResponse(resumen_centro(_servidor().ESTADO_GLOBAL))
