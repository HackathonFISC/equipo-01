"""Nodo Matching: llama al solver (puente.solver) y pide al LLM solo la explicación del puntaje. Issues #4 y #10."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from puente.rules.traslados import InfoTraslado, cargar_traslados
from puente.solver.asignacion import ConfigSolver, MatchFallback, asignar

RedactorMatchFn = Callable[[Any], str]


def redactar_explicacion_match(
    match: Any,
    redactor_llm: RedactorMatchFn | None = None,
) -> str:
    """Devuelve la explicación en español del match sin alterar jamás el puntaje ni la viabilidad del solver."""
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
) -> list[MatchFallback]:
    """Ejecuta el solver CP-SAT y enriquece opcionalmente la redacción de cada Match."""
    matriz = traslados if traslados is not None else cargar_traslados()
    resultados = asignar(perfiles, ofertas, matriz, config=config)
    if redactor_llm is not None:
        for m in resultados:
            m.explicacion = redactar_explicacion_match(m, redactor_llm=redactor_llm)
    return resultados


def nodo_matching(state: dict[str, Any]) -> dict[str, Any]:
    """Nodo compatible con LangGraph (EstadoPuente) que ejecuta el solver determinístico y registra la Decision."""
    perfiles = state.get("perfiles", [])
    ofertas = state.get("ofertas", [])
    traslados = state.get("traslados")
    config = state.get("config_solver")
    redactor_llm = state.get("redactor_matching_llm")

    resultados = ejecutar_matching(
        perfiles=perfiles,
        ofertas=ofertas,
        traslados=traslados,
        config=config,
        redactor_llm=redactor_llm,
    )

    matches_dict = [m.model_dump() for m in resultados]
    lista_corta = [
        m.model_dump()
        for m in resultados
        if m.desglose.get("viable", bool(m.oferta_id)) and m.oferta_id
    ]

    decisiones = list(state.get("decisiones", []))
    decisiones.append(
        {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agente": "matching",
            "entrada_resumen": f"{len(perfiles)} perfil(es) × {len(ofertas)} oferta(s)",
            "salida_resumen": (
                f"{len(lista_corta)} asignación(es) viable(s) en lista corta "
                f"({len(resultados) - len(lista_corta)} sin vacante viable)"
            ),
            "razon": (
                "Asignación entera determinística con OR-Tools CP-SAT "
                "(traslado <= 60 min, pasaje <= 15% salario, sin choque de horario)."
            ),
        }
    )

    nuevo_estado = dict(state)
    nuevo_estado["matches"] = matches_dict
    nuevo_estado["lista_corta"] = lista_corta
    nuevo_estado["decisiones"] = decisiones
    return nuevo_estado
