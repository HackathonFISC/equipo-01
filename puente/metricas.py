"""Indicadores ilustrativos a partir de matches y datos sintéticos de la demo (#47) y tendencias del mercado (#198).

Las comparaciones son escenarios alternativos, no ahorros observados ni sumables entre sí.

Tendencias (#198): `tendencias_mercado` resume las ofertas que Mercado ya procesó y guardó en `puente.db`
(`uv run python -m puente.cargar`). Es código determinístico, sin LLM: habilidades más pedidas, salario
publicado por corregimiento y ofertas bloqueadas por la Ley 11 de 2005 con su regla. Por terminal:

    uv run python -m puente.metricas --tendencias [--db puente.db] [--top 10]
"""

from __future__ import annotations

import argparse
import contextlib
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from puente.rules.traslados import indexar_traslados, obtener_traslado


ROTULO = "Ilustrativo, datos de la demo"
ROTULO_TENDENCIAS = "datos de la demo: {n} ofertas reales anonimizadas de Computrabajo y Konzerta, 16-oct-2026"
SIN_CORREGIMIENTO = "(sin corregimiento)"
SIN_REGLA = "(bloqueada sin regla registrada)"


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
        seleccionado = bool(desglose.get("viable")) and oferta is not None and not _campo(oferta, "bloqueada", False)

        for rechazado_id, motivo in (desglose.get("ofertas_descartadas") or {}).items():
            rechazado_id = str(rechazado_id)
            motivo = str(motivo)
            rechazado = ofertas_por_id.get(rechazado_id)
            if rechazado is None or _campo(rechazado, "bloqueada", False):
                continue
            if not any(palabra in motivo.lower() for palabra in ("traslado", "pasaje")):
                continue
            descartes[(pid, rechazado_id)] = {
                "perfil_id": pid, "oferta_id": rechazado_id, "motivo": motivo
            }
            if not seleccionado or perfil is None:
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


# ---------------------------------------------------------------- tendencias del mercado (#198)


def _regla_de_alerta(alerta: str) -> tuple[str, str] | None:
    """(tipo, regla) de una alerta de Mercado con el formato de `Hallazgo.como_texto`:
    «bloqueo · edad · fundamento · «frase»» o «alerta · foto · …». Las demás alertas (baja confianza,
    posible duplicada) no son reglas de la Ley 11 y devuelven None."""
    partes = [p.strip() for p in str(alerta).split(" · ")]
    if len(partes) >= 2 and partes[0] in ("bloqueo", "alerta") and partes[1]:
        return partes[0], partes[1]
    return None


def tendencias_mercado(ofertas: Iterable[Any], *, top: int | None = None) -> dict[str, Any]:
    """Tendencias de la demanda sobre ofertas ya procesadas por Mercado. Determinístico y sin LLM.

    - ``habilidades``: cuántas ofertas piden cada ``skill_id`` (una vez por oferta), de más a menos pedida;
      a igual conteo, por ``skill_id``. ``top`` recorta la lista; ``None`` la deja completa.
    - ``salario_por_corregimiento``: mediana y n del salario mensual **publicado** (Mercado solo lo acepta si
      la cifra está en el texto). Las ofertas sin salario se cuentan aparte (``sin_salario``) y no entran en
      la mediana; un corregimiento sin ninguna cifra queda con mediana ``None``. No se inventa nada.
    - ``ley_11``: ofertas bloqueadas y cuántas por cada regla que bloquea (edad, sexo); una oferta con dos
      reglas suma en las dos. Aparte, las alertas que no bloquean, también contadas por oferta y regla.

    Cuenta todas las ofertas recibidas, incluidas las bloqueadas: también son parte de lo que publica el mercado.
    """
    lista = sorted(ofertas, key=lambda o: str(_campo(o, "id", "")))
    habilidades: Counter[str] = Counter()
    salarios: dict[str, list[float]] = defaultdict(list)
    sin_salario: Counter[str] = Counter()
    bloqueos: Counter[str] = Counter()
    alertas: Counter[str] = Counter()
    bloqueadas = 0
    for oferta in lista:
        habilidades.update({str(s) for s in (_campo(oferta, "habilidades") or []) if str(s).strip()})
        lugar = str(_campo(oferta, "corregimiento") or "").strip() or SIN_CORREGIMIENTO
        salario = _campo(oferta, "salario_mensual")
        if salario is None:
            sin_salario[lugar] += 1
        else:
            salarios[lugar].append(float(salario))
        reglas_bloqueo, reglas_alerta = set(), set()
        for alerta in _campo(oferta, "alertas") or []:
            leida = _regla_de_alerta(alerta)
            if leida is not None:
                (reglas_bloqueo if leida[0] == "bloqueo" else reglas_alerta).add(leida[1])
        if _campo(oferta, "bloqueada"):
            bloqueadas += 1
            bloqueos.update(reglas_bloqueo or {SIN_REGLA})
        alertas.update(reglas_alerta)

    def ordenado(conteo: Counter[str]) -> list[dict[str, Any]]:
        return [{"clave": k, "ofertas": n} for k, n in sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))]

    lugares = sorted(set(salarios) | set(sin_salario), key=lambda lugar: (-len(salarios[lugar]), lugar))
    por_lugar = [
        {
            "corregimiento": lugar,
            "n": len(salarios[lugar]),
            "mediana": statistics.median(salarios[lugar]) if salarios[lugar] else None,
            "sin_salario": sin_salario[lugar],
        }
        for lugar in lugares
    ]
    todas = [s for valores in salarios.values() for s in valores]
    pedidas = [{"skill_id": f["clave"], "ofertas": f["ofertas"]} for f in ordenado(habilidades)]
    return {
        "rotulo": ROTULO_TENDENCIAS.format(n=len(lista)),
        "total_ofertas": len(lista),
        "habilidades": pedidas[:top] if top is not None else pedidas,
        "habilidades_distintas": len(habilidades),
        "salario_por_corregimiento": por_lugar,
        "salario_general": {"n": len(todas), "mediana": statistics.median(todas) if todas else None,
                            "sin_salario": sum(sin_salario.values())},
        "ley_11": {
            "bloqueadas": bloqueadas,
            "bloqueos_por_regla": [{"regla": f["clave"], "ofertas": f["ofertas"]} for f in ordenado(bloqueos)],
            "alertas_por_regla": [{"regla": f["clave"], "ofertas": f["ofertas"]} for f in ordenado(alertas)],
        },
    }


def _monto(valor: float | None) -> str:
    return "—" if valor is None else f"B/. {valor:,.2f}"


def formatear_tendencias(t: dict[str, Any]) -> str:
    """Texto para la terminal, con el rótulo de los datos arriba."""
    lineas = [f"Tendencias del mercado ({t['rotulo']})", ""]
    lineas.append(f"Habilidades más pedidas ({t['habilidades_distintas']} distintas en {t['total_ofertas']} ofertas):")
    lineas += [f"  {h['ofertas']:>3}  {h['skill_id']}" for h in t["habilidades"]] or ["  (ninguna oferta trae habilidades)"]
    general = t["salario_general"]
    lineas += [
        "",
        f"Salario mensual publicado por corregimiento (mediana; n con salario; sin salario aparte). "
        f"General: {_monto(general['mediana'])} con n={general['n']}, {general['sin_salario']} sin salario publicado.",
    ]
    lineas += [
        f"  {f['corregimiento']:<28} mediana {_monto(f['mediana']):>13}  n={f['n']:<3} sin salario={f['sin_salario']}"
        for f in t["salario_por_corregimiento"]
    ]
    ley = t["ley_11"]
    lineas += ["", f"Bloqueadas por la Ley 11 de 2005: {ley['bloqueadas']} de {t['total_ofertas']}."]
    lineas += [f"  {b['ofertas']:>3}  {b['regla']}" for b in ley["bloqueos_por_regla"]]
    if ley["alertas_por_regla"]:
        lineas.append("Alertas que no bloquean (las revisa el orientador):")
        lineas += [f"  {a['ofertas']:>3}  {a['regla']}" for a in ley["alertas_por_regla"]]
    lineas += ["", "Una oferta con dos reglas suma en las dos. Sin LLM: todo se calcula en código sobre puente.db."]
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m puente.metricas", description="Indicadores de Puente (#47, #198).")
    parser.add_argument("--tendencias", action="store_true", help="tendencias del mercado sobre las ofertas de puente.db")
    parser.add_argument("--db", type=Path, default=None, help="base SQLite (por defecto $PUENTE_DB o puente.db)")
    parser.add_argument("--top", type=int, default=10, help="habilidades a mostrar (por defecto 10; 0 = todas)")
    argumentos = parser.parse_args(argv)
    if not argumentos.tendencias:
        parser.print_help()
        return 0
    from puente.tools import db

    conn = db.conectar(argumentos.db)
    try:
        ofertas = db.listar_ofertas(conn, no_bloqueadas=False)
    finally:
        conn.close()
    if not ofertas:
        print("No hay ofertas en la base. Cárgalas primero: uv run python -m puente.cargar")
        return 1
    print(formatear_tendencias(tendencias_mercado(ofertas, top=argumentos.top or None)))
    return 0


if __name__ == "__main__":
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows usa cp1252
    raise SystemExit(main())
