"""Nodo Matching: llama al solver CP-SAT (puente.solver) y genera la explicación con la plantilla determinística sobre el desglose, sin llamadas al LLM. Issues #4, #10 y #48."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import puente.schemas as _schemas_mod
from puente.rules.traslados import InfoTraslado, cargar_traslados
from puente.solver.asignacion import ConfigSolver, MatchFallback, asignar

RedactorMatchFn = Callable[[Any], str]


def redactar_explicacion_match(
    match: Any,
    redactor_llm: RedactorMatchFn | None = None,
) -> str:
    """Devuelve la explicación en español del match sin alterar jamás el puntaje ni la viabilidad del solver.

    Por defecto utiliza la plantilla determinística generada sobre el desglose del solver, sin LLM (#48).
    """
    if redactor_llm is not None:
        try:
            texto = redactor_llm(match)
            if texto and texto.strip():
                return texto.strip()
        except Exception:
            pass
    return str(getattr(match, "explicacion", ""))


def ejecutar_matching(
    perfiles: Sequence[Any],
    ofertas: Sequence[Any],
    traslados: dict[tuple[str, str], InfoTraslado] | Sequence[Any] | None = None,
    config: ConfigSolver | dict[str, Any] | None = None,
    redactor_llm: RedactorMatchFn | None = None,
    *,
    excluidos: Any = None,
    parejas_fijas: Any = None,
) -> list[MatchFallback]:
    """Ejecuta el solver CP-SAT respetando exclusiones y parejas fijas."""
    matriz = traslados if traslados is not None else cargar_traslados()
    resultados = asignar(
        perfiles,
        ofertas,
        matriz,
        config=config,
        excluidos=excluidos,
        parejas_fijas=parejas_fijas,
    )
    if redactor_llm is not None:
        for m in resultados:
            m.explicacion = redactar_explicacion_match(m, redactor_llm=redactor_llm)
    return resultados


def _construir_decision(
    *,
    agente: str,
    entrada_resumen: str,
    salida_resumen: str,
    razon: str,
    timestamp: str,
) -> Any:
    cls_dec = getattr(_schemas_mod, "Decision", None)
    if cls_dec is not None and isinstance(cls_dec, type):
        try:
            return cls_dec(
                agente=agente,
                entrada_resumen=entrada_resumen,
                salida_resumen=salida_resumen,
                razon=razon,
                timestamp=timestamp,
            )
        except Exception:
            pass
    return {
        "timestamp": timestamp,
        "agente": agente,
        "entrada_resumen": entrada_resumen,
        "salida_resumen": salida_resumen,
        "razon": razon,
    }


def _cargar_contexto_db(
    state: dict[str, Any],
    perfiles: list[Any],
    ofertas: list[Any],
    parejas_fijas: list[Any],
) -> tuple[list[Any], list[Any], list[Any]]:
    """Si existen los lectores de #46 en puente.tools.db y el estado o entorno lo indica, incorpora perfiles, ofertas activas y parejas_fijas."""
    usar_db = bool(state.get("usar_db")) or ("db_conn" in state) or ("db_ruta" in state)
    if not usar_db and (perfiles or ofertas) and not os.getenv("PUENTE_DB"):
        return perfiles, ofertas, parejas_fijas

    try:
        from puente.tools import db as _db_mod
    except Exception:
        return perfiles, ofertas, parejas_fijas

    tiene_lectores = any(
        hasattr(_db_mod, fn)
        for fn in ("listar_perfiles", "listar_ofertas_activas", "listar_ofertas", "parejas_fijas")
    )
    if not tiene_lectores:
        return perfiles, ofertas, parejas_fijas

    conn = state.get("db_conn")
    cerrar_conn = False
    try:
        if conn is None:
            ruta_db = state.get("db_ruta") or os.getenv("PUENTE_DB")
            if not ruta_db and not usar_db:
                return perfiles, ofertas, parejas_fijas
            conn = _db_mod.conectar(ruta_db)
            cerrar_conn = True

        if hasattr(_db_mod, "listar_perfiles") and (usar_db or not perfiles):
            ids_p = {
                (p.get("id") if isinstance(p, dict) else getattr(p, "id", None))
                for p in perfiles
            }
            for p_db in _db_mod.listar_perfiles(conn):
                pid = p_db.get("id") if isinstance(p_db, dict) else getattr(p_db, "id", None)
                if pid and pid not in ids_p:
                    perfiles.append(p_db)
                    ids_p.add(pid)

        if (usar_db or not ofertas) and (
            hasattr(_db_mod, "listar_ofertas_activas") or hasattr(_db_mod, "listar_ofertas")
        ):
            fn_ofertas = getattr(
                _db_mod, "listar_ofertas_activas", getattr(_db_mod, "listar_ofertas", None)
            )
            if fn_ofertas is not None:
                ids_o = {
                    (o.get("id") if isinstance(o, dict) else getattr(o, "id", None))
                    for o in ofertas
                }
                for o_db in fn_ofertas(conn):
                    bloq = (
                        o_db.get("bloqueada", False)
                        if isinstance(o_db, dict)
                        else getattr(o_db, "bloqueada", False)
                    )
                    oid = o_db.get("id") if isinstance(o_db, dict) else getattr(o_db, "id", None)
                    if not bloq and oid and oid not in ids_o:
                        ofertas.append(o_db)
                        ids_o.add(oid)

        if hasattr(_db_mod, "parejas_fijas"):
            for pf in _db_mod.parejas_fijas(conn):
                if pf not in parejas_fijas:
                    parejas_fijas.append(pf)
    except Exception:
        pass
    finally:
        if cerrar_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return perfiles, ofertas, parejas_fijas


def nodo_matching(state: dict[str, Any]) -> dict[str, Any]:
    """Nodo compatible con LangGraph (EstadoPuente) que ejecuta el solver determinístico y registra la Decision."""
    perfiles = list(state.get("perfiles") or [])
    ofertas = list(state.get("ofertas") or [])
    traslados = state.get("traslados")
    config = state.get("config_solver")
    redactor_llm = state.get("redactor_matching_llm")
    excluidos = list(state.get("excluidos") or state.get("parejas_excluidas") or [])
    parejas_fijas = list(state.get("parejas_fijas") or [])

    perfiles, ofertas, parejas_fijas = _cargar_contexto_db(
        state, perfiles, ofertas, parejas_fijas
    )

    resultados = ejecutar_matching(
        perfiles=perfiles,
        ofertas=ofertas,
        traslados=traslados,
        config=config,
        redactor_llm=redactor_llm,
        excluidos=excluidos,
        parejas_fijas=parejas_fijas,
    )

    sin_oferta = getattr(_schemas_mod, "SIN_OFERTA", "SIN_OFERTA")
    lista_corta = [
        m
        for m in resultados
        if m.desglose.get("viable", bool(m.oferta_id))
        and m.oferta_id
        and m.oferta_id != sin_oferta
    ]

    decisiones = list(state.get("decisiones", []))
    decisiones.append(
        _construir_decision(
            agente="matching",
            entrada_resumen=(
                f"{len(perfiles)} perfil(es) × {len(ofertas)} oferta(s)"
                + (f", {len(excluidos)} excluida(s)" if excluidos else "")
            ),
            salida_resumen=(
                f"{len(lista_corta)} asignación(es) viable(s) en lista corta "
                f"({len(resultados) - len(lista_corta)} sin vacante viable)"
            ),
            razon=(
                "Asignación entera determinística con OR-Tools CP-SAT "
                "(traslado <= 60 min, pasaje <= 15% salario, sin choque de horario, tope 2 vacantes por joven)."
            ),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    )

    return {
        "matches": resultados,
        "lista_corta": lista_corta,
        "decisiones": decisiones,
    }
