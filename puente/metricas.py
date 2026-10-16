"""Indicadores ilustrativos a partir de matches y datos sintéticos de la demo (#47).

Las comparaciones son escenarios alternativos, no ahorros observados ni sumables entre sí.
"""

from __future__ import annotations

from typing import Any, Iterable

from puente.rules.traslados import indexar_traslados, obtener_traslado


ROTULO = "Ilustrativo, datos de la demo"


def _campo(obj: Any, nombre: str, defecto: Any = None) -> Any:
    return obj.get(nombre, defecto) if isinstance(obj, dict) else getattr(obj, nombre, defecto)


def calcular_metricas(
    matches: Iterable[Any],
    perfiles: Iterable[Any],
    ofertas: Iterable[Any],
    traslados: Any = None,
    *,
    dias_laborables: int = 22,
) -> dict[str, Any]:
    """Cuenta descartes por traslado/pasaje y compara cada alternativa con su match.

    ``minutos_traslado`` es solo ida. La comparación mensual usa ida y vuelta.
    Los pares sin ruta no generan una cifra ficticia. Cada habilidad se cuenta una
    vez por perfil cuando tiene evidencia literal y la pide una oferta asignada.
    La procedencia informal de esa habilidad no se puede inferir de la evidencia.
    """
    if not 1 <= dias_laborables <= 31:
        raise ValueError("dias_laborables debe estar entre 1 y 31")

    perfiles_por_id = {str(_campo(p, "id")): p for p in perfiles}
    ofertas_por_id = {str(_campo(o, "id")): o for o in ofertas}
    indice = indexar_traslados(traslados)
    descartes: dict[tuple[str, str], dict[str, str]] = {}
    comparaciones: dict[tuple[str, str, str], dict[str, Any]] = {}
    habilidades: dict[tuple[str, str], dict[str, str]] = {}

    for match in matches:
        pid = str(_campo(match, "perfil_id", ""))
        oid = str(_campo(match, "oferta_id", ""))
        desglose = _campo(match, "desglose", {}) or {}
        if not pid or not isinstance(desglose, dict):
            continue
        perfil = perfiles_por_id.get(pid)
        oferta = ofertas_por_id.get(oid)
        seleccionado = bool(desglose.get("viable")) and oferta is not None

        for rechazado_id, motivo in (desglose.get("ofertas_descartadas") or {}).items():
            rechazado_id = str(rechazado_id)
            motivo = str(motivo)
            if not any(palabra in motivo.lower() for palabra in ("traslado", "pasaje")):
                continue
            descartes[(pid, rechazado_id)] = {
                "perfil_id": pid, "oferta_id": rechazado_id, "motivo": motivo
            }
            if not seleccionado or perfil is None:
                continue
            rechazado = ofertas_por_id.get(rechazado_id)
            if rechazado is None:
                continue
            ruta = obtener_traslado(
                indice, str(_campo(perfil, "corregimiento", "")),
                str(_campo(rechazado, "corregimiento", "")),
            )
            minutos = desglose.get("minutos_traslado")
            pasaje = desglose.get("pasaje_diario")
            if ruta is None or not isinstance(minutos, (int, float)) or not isinstance(pasaje, (int, float)):
                continue
            if minutos < 0 or pasaje < 0 or minutos >= 999 or pasaje >= 999:
                continue
            comparaciones[(pid, oid, rechazado_id)] = {
                "perfil_id": pid,
                "oferta_elegida_id": oid,
                "oferta_descartada_id": rechazado_id,
                "motivo_descarte": motivo,
                "diferencia_minutos_mes": (ruta.minutos_hora_pico - minutos) * 2 * dias_laborables,
                "diferencia_usd_mes": round((ruta.pasaje_diario_usd - pasaje) * dias_laborables, 2),
            }

        if not seleccionado or perfil is None:
            continue
        requeridas = set(_campo(oferta, "habilidades", []) or [])
        for habilidad in _campo(perfil, "habilidades", []) or []:
            skill_id = str(_campo(habilidad, "skill_id", "")).strip()
            evidencia = str(_campo(habilidad, "evidencia", "")).strip()
            if skill_id in requeridas and evidencia:
                habilidades[(pid, skill_id)] = {
                    "perfil_id": pid, "skill_id": skill_id, "evidencia_literal": evidencia
                }

    return {
        "rotulo": ROTULO,
        "ofertas_descartadas_traslado_pasaje": [descartes[k] for k in sorted(descartes)],
        "total_ofertas_descartadas_traslado_pasaje": len(descartes),
        "comparaciones_mensuales": [comparaciones[k] for k in sorted(comparaciones)],
        "habilidades_informales_con_evidencia": [habilidades[k] for k in sorted(habilidades)],
        "total_habilidades_informales_con_evidencia": len(habilidades),
        "supuestos": {
            "dias_laborables": dias_laborables,
            "viajes_por_dia": 2,
            "alcance": "escenarios alternativos sintéticos; diferencias con signo, no ahorro observado",
            "procedencia_habilidades": "evidencia literal; origen informal sin verificar",
        },
    }
