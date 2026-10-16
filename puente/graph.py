"""Grafo LangGraph: guardrails, router, agentes, aprobación del orientador con interrupt() y trazas. Issue #10.

Fase 1: el flujo completo corre con stubs y se detiene en la aprobación. Los stubs están
marcados con `STUB` y no llaman al LLM; la Fase 2 los reemplaza por los agentes reales
pasando `nodos={"mercado": ..., ...}` a `construir_grafo`.

    START → guardrails ─rechazo→ END
                └→ router ─oferta→ mercado ─bloqueada→ alerta_oferta → END
                     │                └→ matching
                     ├─perfil→ perfil → matching
                     └─consulta→ consulta → END
    matching ─sin matches→ sin_matches → END
        └→ auditor ─sesgo (una vez)→ matching
              └→ formacion → aprobacion (interrupt)
    aprobacion: aprobar/editar → postulacion · quitar → matching · rechazar → END
    postulacion → aprobacion si quedan vacantes por decidir, si no END

Contrato con el tablero (#11):
    grafo = construir_grafo(checkpointer=checkpointer_sqlite())
    config = config_caso(perfil_id)
    grafo.invoke(estado, config)                      # corre hasta el interrupt
    aprobacion_pendiente(grafo, config)              # o grafo.get_state(config)
    grafo.invoke(Command(resume={...}), config)       # ver RESPUESTA_APROBACION

LangSmith se activa solo con LANGSMITH_TRACING=true y LANGSMITH_API_KEY en `.env`.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from puente.agents.auditor import ruta_post_auditor
from puente.guardrails import RESPUESTA_FUERA_DE_ALCANCE, nodo_guardrails
from puente.rules.discriminacion import esta_bloqueada, revisar_texto
from puente.schemas import Decision, EstadoPuente, Match, Oferta, Perfil

Nodo = Callable[[dict], Any]

TIPOS_ENTRADA = ("oferta", "perfil", "consulta")
ACCIONES = ("aprobar", "editar", "quitar", "rechazar")
RESPUESTA_APROBACION = {
    "accion": "aprobar | editar | quitar | rechazar",
    "orientador": "nombre de quien decide; obligatorio para aprobar y editar",
    "oferta_id": "vacante cuya lista corta se decide; por defecto la que muestra el interrupt",
    "perfil_id": "solo para quitar: candidato que sale de esa vacante",
    "mensaje": "mensaje a la empresa (editar lo reemplaza)",
    "nota": "nota del orientador",
    "consentidos": "perfil_id cuyo consentimiento confirmó el orientador; solo ellos se postulan",
}
CANDIDATOS_POR_VACANTE = 3
CHECKPOINTS_POR_DEFECTO = "checkpoints.db"
CASO_YARITZA = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "caso_yaritza.json"


class EstadoGrafo(EstadoPuente, total=False):
    """EstadoPuente más las claves que el grafo necesita y aún no están en schemas.py.

    LangGraph descarta en silencio las claves que no declara el estado, así que sin esto
    `entrada`, `excluidos` o el veredicto de guardrails nunca llegarían a los nodos.
    Las de entrada (`entrada`, `tipo_entrada`, `ofertas_crudas`, `entradas_perfil`,
    `excluidos`) llegan a EstadoPuente con #42; entonces esta clase se reduce a las propias del grafo.
    """

    entrada: dict[str, Any]
    tipo_entrada: str
    ofertas_crudas: list[Any]
    entradas_perfil: list[dict[str, Any]]
    guardrails: dict[str, Any]
    excluidos: list[dict[str, str]]
    rutas_formacion: list[dict[str, Any]]
    # Propias del grafo
    ofertas_nuevas: list[str]
    vacantes_resueltas: list[str]
    aviso_aprobacion: str | None
    respuesta: str


# ---------------------------------------------------------------- utilidades


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


def _campo(obj: Any, nombre: str, defecto: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(nombre, defecto)
    return getattr(obj, nombre, defecto)


def _con_decision(estado: dict, agente: str, entrada: str, salida: str, razon: str) -> list:
    """Lista completa de decisiones con una nueva al final. Se cambia por `agregar_decision()` de #42."""
    decision = Decision(agente=agente, entrada_resumen=entrada, salida_resumen=salida, razon=razon)
    return [*(estado.get("decisiones") or []), decision]


def _excluido(estado: dict, perfil_id: str, oferta_id: str) -> bool:
    return any(
        e.get("perfil_id") == perfil_id and e.get("oferta_id") == oferta_id for e in estado.get("excluidos") or []
    )


def _lista_corta_de(estado: dict, oferta_id: str) -> list:
    return [m for m in estado.get("lista_corta") or [] if _campo(m, "oferta_id") == oferta_id]


def vacantes_pendientes(estado: dict) -> list[str]:
    """Vacantes con lista corta que el orientador todavía no decidió, en orden de aparición."""
    resueltas = set(estado.get("vacantes_resueltas") or [])
    pendientes: list[str] = []
    for m in estado.get("lista_corta") or []:
        oid = _campo(m, "oferta_id")
        if oid not in resueltas and oid not in pendientes:
            pendientes.append(oid)
    return pendientes


# ---------------------------------------------------------------- guardrails y router


def _texto_de(valor: Any) -> str:
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict):
        if valor.get("texto") or valor.get("cv_texto"):
            return str(valor.get("texto") or valor.get("cv_texto"))
        if valor.get("respuestas"):
            return "\n".join(str(r) for r in valor["respuestas"])
        return " ".join(str(valor.get(c, "")) for c in ("puesto", "requisitos_texto")).strip()
    return ""


def entrada_desde_estado(estado: dict) -> dict[str, Any]:
    """La entrada que revisa guardrails. Si el tablero no la manda, se arma con el texto de
    `ofertas_crudas` y `entradas_perfil` y la edad del primer perfil."""
    if estado.get("entrada"):
        return dict(estado["entrada"])
    textos = [_texto_de(v) for v in [*(estado.get("ofertas_crudas") or []), *(estado.get("entradas_perfil") or [])]]
    entrada: dict[str, Any] = {}
    if any(textos):
        entrada["texto"] = "\n\n".join(t for t in textos if t)
    edad = next((p.get("edad") for p in estado.get("entradas_perfil") or [] if isinstance(p, dict)), None)
    if edad is not None:
        entrada["edad"] = edad
    return entrada


def nodo_entrada_guardrails(estado: dict) -> dict:
    return nodo_guardrails({**estado, "entrada": entrada_desde_estado(estado)})


def ruta_inicio(estado: dict) -> str:
    """Una invocación sin nada que revisar termina sin efectos ni decisiones."""
    claves = ("entrada", "tipo_entrada", "ofertas_crudas", "entradas_perfil")
    return "guardrails" if any(estado.get(c) for c in claves) else END


def ruta_guardrails(estado: dict) -> str:
    return "router" if (estado.get("guardrails") or {}).get("ok") else END


def tipo_de_entrada(estado: dict) -> str:
    """`tipo_entrada` manda (#42); sin él se infiere de qué trae el estado."""
    tipo = estado.get("tipo_entrada")
    if tipo in TIPOS_ENTRADA:
        return tipo
    if estado.get("ofertas_crudas"):
        return "oferta"
    if estado.get("entradas_perfil"):
        return "perfil"
    return "consulta"


def nodo_router(estado: dict) -> dict:
    tipo = tipo_de_entrada(estado)
    origen = "tipo_entrada" if estado.get("tipo_entrada") == tipo else "inferido del estado"
    return {
        "tipo_entrada": tipo,
        "decisiones": _con_decision(estado, "router", f"tipo_entrada={estado.get('tipo_entrada')!r}", tipo, origen),
    }


def ruta_router(estado: dict) -> str:
    return {"oferta": "mercado", "perfil": "perfil"}.get(estado.get("tipo_entrada", ""), "consulta")


def nodo_consulta(estado: dict) -> dict:
    """MVP: las consultas libres no se atienden todavía."""
    return {
        "respuesta": RESPUESTA_FUERA_DE_ALCANCE,
        "decisiones": _con_decision(
            estado, "consulta", "consulta libre", RESPUESTA_FUERA_DE_ALCANCE, "fuera de alcance en el MVP"
        ),
    }


# ---------------------------------------------------------------- stubs de la Fase 1


def stub_mercado(estado: dict) -> dict:
    """STUB de Mercado (#5): acepta ofertas ya estructuradas, sin LLM, y aplica las reglas legales reales."""
    ofertas = list(estado.get("ofertas") or [])
    errores = list(estado.get("errores") or [])
    nuevas: list[str] = []
    for cruda in estado.get("ofertas_crudas") or []:
        if not isinstance(cruda, dict) or "id" not in cruda:
            errores.append("mercado (stub): solo lee ofertas estructuradas; el texto libre llega con #5")
            continue
        texto = f"{cruda.get('requisitos_texto', '')}\n{cruda.get('texto', '')}"
        hallazgos = revisar_texto(texto)
        oferta = Oferta.model_validate(
            {
                **cruda,
                "alertas": [*cruda.get("alertas", []), *(h.como_texto() for h in hallazgos)],
                "bloqueada": cruda.get("bloqueada", False) or esta_bloqueada(hallazgos),
            }
        )
        ofertas = [o for o in ofertas if o.id != oferta.id] + [oferta]
        nuevas.append(oferta.id)
    bloqueadas = [o.id for o in ofertas if o.id in nuevas and o.bloqueada]
    return {
        "ofertas": ofertas,
        "ofertas_nuevas": nuevas,
        "errores": errores,
        "decisiones": _con_decision(
            estado,
            "mercado",
            f"{len(estado.get('ofertas_crudas') or [])} oferta(s) cruda(s)",
            f"{len(nuevas)} oferta(s), {len(bloqueadas)} bloqueada(s)",
            "STUB #10: sin extracción LLM; reglas de la Ley 11 de 2005 en código",
        ),
    }


def ruta_post_mercado(estado: dict) -> str:
    nuevas = [o for o in estado.get("ofertas") or [] if o.id in (estado.get("ofertas_nuevas") or [])]
    return "alerta_oferta" if nuevas and all(o.bloqueada for o in nuevas) else "matching"


def nodo_alerta_oferta(estado: dict) -> dict:
    nuevas = set(estado.get("ofertas_nuevas") or [])
    alertas = [f"{o.id}: {'; '.join(o.alertas)}" for o in estado.get("ofertas") or [] if o.id in nuevas]
    respuesta = "Oferta bloqueada por requisitos discriminatorios. " + " | ".join(alertas)
    return {
        "respuesta": respuesta,
        "decisiones": _con_decision(
            estado, "alerta_oferta", f"{len(nuevas)} oferta(s) nueva(s)", "bloqueada, no entra al Matching", respuesta
        ),
    }


def stub_perfil(estado: dict) -> dict:
    """STUB de Perfil (#6): arma el Perfil con los campos del formulario y las habilidades ya verificadas."""
    perfiles = list(estado.get("perfiles") or [])
    errores = list(estado.get("errores") or [])
    listos: list[str] = []
    campos = set(Perfil.model_fields)
    for entrada in estado.get("entradas_perfil") or []:
        try:
            perfil = Perfil.model_validate({k: v for k, v in entrada.items() if k in campos})
        except ValueError as exc:
            errores.append(f"perfil (stub): {entrada.get('id', '?')}: {exc}")
            continue
        perfiles = [p for p in perfiles if p.id != perfil.id] + [perfil]
        listos.append(perfil.id)
    return {
        "perfiles": perfiles,
        "errores": errores,
        "decisiones": _con_decision(
            estado,
            "perfil",
            f"{len(estado.get('entradas_perfil') or [])} entrada(s) de perfil",
            f"perfiles listos: {', '.join(listos) or 'ninguno'}",
            "STUB #10: sin LLM; formulario y habilidades con evidencia tal como llegan",
        ),
    }


def stub_matching(estado: dict) -> dict:
    """STUB de Matching (#4): cobertura de habilidades y salario mínimo, k por vacante, sin las parejas excluidas.

    El solver real no recibe exclusiones todavía; para la Fase 2 necesita `parejas_excluidas`.
    """
    perfiles = list(estado.get("perfiles") or [])
    matches: list[Match] = []
    lista_corta: list[Match] = []
    for oferta in estado.get("ofertas") or []:
        if oferta.bloqueada:
            continue
        candidatos = []
        for perfil in perfiles:
            if _excluido(estado, perfil.id, oferta.id):
                continue
            tiene = {h.skill_id for h in perfil.habilidades}
            pide = set(oferta.habilidades)
            cobertura = len(tiene & pide) / len(pide) if pide else 0.0
            motivo = None
            if cobertura == 0:
                motivo = "sin habilidades en común"
            elif oferta.salario_mensual < perfil.salario_minimo:
                motivo = "salario menor que su mínimo"
            match = Match(
                perfil_id=perfil.id,
                oferta_id=oferta.id,
                puntaje=round(cobertura * 100),
                desglose={
                    "cobertura": cobertura,
                    "minutos_traslado": 0,
                    "pasaje_diario": 0.0,
                    "penalizacion": 0,
                    "viable": motivo is None,
                    "motivo_descarte": motivo,
                },
                brechas=sorted(pide - tiene),
                explicacion=f"STUB: cobertura {cobertura:.0%}" + (f"; descartado: {motivo}" if motivo else ""),
            )
            matches.append(match)
            if motivo is None:
                candidatos.append(match)
        candidatos.sort(key=lambda m: (-m.puntaje, m.perfil_id))
        lista_corta.extend(candidatos[:CANDIDATOS_POR_VACANTE])
    excluidos = estado.get("excluidos") or []
    return {
        "matches": matches,
        "lista_corta": lista_corta,
        "decisiones": _con_decision(
            estado,
            "matching",
            f"{len(perfiles)} perfil(es), {len(estado.get('ofertas') or [])} oferta(s), {len(excluidos)} pareja(s) excluida(s)",
            f"{len(lista_corta)} candidato(s) en lista corta",
            "STUB #10: cobertura de habilidades; el solver CP-SAT entra en la Fase 2",
        ),
    }


def con_reintento_auditor(nodo: Nodo) -> Nodo:
    """Si el Auditor devolvió el caso, cuenta el reintento para que `ruta_post_auditor` no entre en bucle."""

    def matching(estado: dict) -> dict:
        cambios = nodo(estado)
        if estado.get("devolver_a_matching"):
            cambios = {
                **cambios,
                "reintentos_auditor": int(estado.get("reintentos_auditor", 0)) + 1,
                "devolver_a_matching": False,
            }
        return cambios

    return matching


def ruta_post_matching(estado: dict) -> str:
    return "auditor" if estado.get("lista_corta") else "sin_matches"


def nodo_sin_matches(estado: dict) -> dict:
    descartes = [
        f"{m.perfil_id} → {m.oferta_id}: {m.desglose.get('motivo_descarte')}"
        for m in estado.get("matches") or []
        if m.desglose.get("motivo_descarte")
    ]
    respuesta = "Sin candidatos viables. " + ("; ".join(descartes) or "No hay ofertas disponibles.")
    return {
        "respuesta": respuesta,
        "decisiones": _con_decision(
            estado, "sin_matches", f"{len(estado.get('matches') or [])} pareja(s) evaluada(s)", "fin sin lista corta", respuesta
        ),
    }


def stub_auditor(estado: dict) -> dict:
    """STUB del Auditor (#8): aprueba la lista corta sin contrafactuales."""
    return {
        "sesgo_detectado": False,
        "devolver_a_matching": False,
        "alertas_auditor": [],
        "decisiones": _con_decision(
            estado,
            "auditor",
            f"{len(estado.get('lista_corta') or [])} candidato(s)",
            "continuar",
            "STUB #10: sin auditoría contrafactual; nodo_auditor entra en la Fase 2",
        ),
    }


def stub_formacion(estado: dict) -> dict:
    """STUB de Formación (#14): no recomienda cursos."""
    return {
        "rutas_formacion": [],
        "decisiones": _con_decision(
            estado, "formacion", "brechas de la lista corta", "0 rutas", "STUB #10: nodo_formacion entra en la Fase 2"
        ),
    }


def stub_postulacion(estado: dict) -> dict:
    """STUB de Postulación (#12): registra lo aprobado; no escribe en outbox/ ni en puente.db."""
    aprobacion = estado.get("aprobacion") or {}
    aprobados = aprobacion.get("aprobados") or []
    return {
        "decisiones": _con_decision(
            estado,
            "postulacion",
            f"aprobación de {aprobacion.get('orientador')} para {aprobacion.get('oferta_id')}",
            f"{len(aprobados)} postulación(es) pendiente(s) de envío",
            "STUB #10: el envío real es postular() de #12 / PR #34",
        ),
    }


def ruta_post_postulacion(estado: dict) -> str:
    return "aprobacion" if vacantes_pendientes(estado) else END


# ---------------------------------------------------------------- aprobación del orientador


def _mensaje_sugerido(oferta: Any, candidatos: list[dict]) -> str:
    nombres = ", ".join(c["nombre"] for c in candidatos)
    return (
        f"Buen día, {_campo(oferta, 'empresa', 'equipo')}: desde Puente les presentamos a {nombres} "
        f"para la vacante de {_campo(oferta, 'puesto', '')}. Quedamos atentos para coordinar entrevistas."
    )


def solicitud_aprobacion(estado: dict, oferta_id: str) -> dict[str, Any]:
    """Lo que ve el orientador en el interrupt: una vacante y su lista corta, todo serializable."""
    oferta = next((o for o in estado.get("ofertas") or [] if o.id == oferta_id), None)
    perfiles = {p.id: p for p in estado.get("perfiles") or []}
    candidatos = [
        {
            "perfil_id": m.perfil_id,
            "nombre": _campo(perfiles.get(m.perfil_id), "nombre", m.perfil_id),
            "puntaje": m.puntaje,
            "brechas": list(m.brechas),
            "explicacion": m.explicacion,
        }
        for m in _lista_corta_de(estado, oferta_id)
    ]
    return {
        "tipo": "aprobacion",
        "oferta_id": oferta_id,
        "oferta": {"puesto": _campo(oferta, "puesto"), "empresa": _campo(oferta, "empresa")},
        "candidatos": candidatos,
        "mensaje_sugerido": _mensaje_sugerido(oferta, candidatos),
        "alertas_auditor": list(estado.get("alertas_auditor") or []),
        "vacantes_pendientes": vacantes_pendientes(estado),
        "acciones": list(ACCIONES),
        "respuesta_esperada": RESPUESTA_APROBACION,
        "error": estado.get("aviso_aprobacion"),
    }


def _reintentar(estado: dict, motivo: str) -> Command:
    """Respuesta inválida: se registra y se vuelve a pedir la decisión, sin efectos."""
    return Command(
        goto="aprobacion",
        update={"aviso_aprobacion": motivo, "errores": [*(estado.get("errores") or []), f"aprobacion: {motivo}"]},
    )


def nodo_aprobacion(estado: dict) -> Command:
    """Se detiene con interrupt() y, según la respuesta del orientador, decide a dónde sigue.

    Nada antes del interrupt tiene efectos: al reanudar, LangGraph vuelve a correr el nodo desde el inicio.
    """
    pendientes = vacantes_pendientes(estado)
    if not pendientes:
        return Command(goto=END)
    solicitud = solicitud_aprobacion(estado, pendientes[0])
    respuesta = interrupt(solicitud)

    if not isinstance(respuesta, dict):
        return _reintentar(estado, f"la respuesta debe ser un dict, llegó {type(respuesta).__name__}")
    accion = respuesta.get("accion")
    oferta_id = respuesta.get("oferta_id") or solicitud["oferta_id"]
    orientador = str(respuesta.get("orientador") or "").strip()
    if accion not in ACCIONES:
        return _reintentar(estado, f"acción {accion!r} no válida; usar {', '.join(ACCIONES)}")
    if oferta_id not in pendientes:
        return _reintentar(estado, f"la vacante {oferta_id!r} no tiene lista corta pendiente")
    en_lista = [m.perfil_id for m in _lista_corta_de(estado, oferta_id)]
    nota = str(respuesta.get("nota") or "")
    base = {"aviso_aprobacion": None}

    def decision(salida: str, razon: str) -> list:
        entrada = f"lista corta de {oferta_id}: {', '.join(en_lista)}"
        return _con_decision(estado, "aprobacion", entrada, salida, f"{orientador or 'orientador'}: {razon}")

    if accion == "quitar":
        perfil_id = respuesta.get("perfil_id")
        if perfil_id not in en_lista:
            return _reintentar(estado, f"{perfil_id!r} no está en la lista corta de {oferta_id}")
        return Command(
            goto="matching",
            update={
                **base,
                "aprobacion": None,
                "excluidos": [*(estado.get("excluidos") or []), {"perfil_id": perfil_id, "oferta_id": oferta_id}],
                "reintentos_auditor": 0,
                "devolver_a_matching": False,
                "decisiones": decision(f"quitar {perfil_id}", nota or "quitado de la lista corta; se recalcula"),
            },
        )

    resueltas = [*(estado.get("vacantes_resueltas") or []), oferta_id]
    if accion == "rechazar":
        siguiente = "aprobacion" if [v for v in pendientes if v != oferta_id] else END
        return Command(
            goto=siguiente,
            update={
                **base,
                "aprobacion": None,
                "vacantes_resueltas": resueltas,
                "decisiones": decision("rechazar", nota or "lista corta rechazada; no se postula a nadie"),
            },
        )

    if not orientador:
        return _reintentar(estado, "falta `orientador`: sin él postular() no envía nada")
    consentidos = set(respuesta.get("consentidos") or [])
    aprobados = [pid for pid in en_lista if pid in consentidos]
    sin_consentimiento = [pid for pid in en_lista if pid not in consentidos]
    if not aprobados:
        return _reintentar(estado, "ningún candidato con consentimiento confirmado en `consentidos`")
    aprobacion = {
        "orientador": orientador,
        "timestamp": _ahora(),
        "aprobados": [{"perfil_id": pid, "oferta_id": oferta_id} for pid in aprobados],
        "oferta_id": oferta_id,
        "accion": accion,
        "mensaje": respuesta.get("mensaje") or solicitud["mensaje_sugerido"],
        "nota": nota,
        "sin_consentimiento": sin_consentimiento,
    }
    razon = f"aprobados {', '.join(aprobados)}"
    if sin_consentimiento:
        razon += f"; sin consentimiento, no se postulan: {', '.join(sin_consentimiento)}"
    return Command(
        goto="postulacion",
        update={
            **base,
            "aprobacion": aprobacion,
            "vacantes_resueltas": resueltas,
            "decisiones": decision(accion, razon),
        },
    )


# ---------------------------------------------------------------- construcción


NODOS_FASE_1: dict[str, Nodo] = {
    "mercado": stub_mercado,
    "perfil": stub_perfil,
    "matching": stub_matching,
    "auditor": stub_auditor,
    "formacion": stub_formacion,
    "postulacion": stub_postulacion,
}


def construir_grafo(nodos: dict[str, Nodo] | None = None, checkpointer: Any = None):
    """Compila el grafo. `nodos` reemplaza cualquiera de NODOS_FASE_1 (Fase 2 o tests).

    Sin checkpointer el grafo no puede detenerse en la aprobación; el tablero y la demo
    usan `checkpointer_sqlite()`.
    """
    agentes = {**NODOS_FASE_1, **(nodos or {})}
    grafo = StateGraph(EstadoGrafo)
    grafo.add_node("guardrails", nodo_entrada_guardrails)
    grafo.add_node("router", nodo_router)
    grafo.add_node("consulta", nodo_consulta)
    grafo.add_node("mercado", agentes["mercado"])
    grafo.add_node("alerta_oferta", nodo_alerta_oferta)
    grafo.add_node("perfil", agentes["perfil"])
    grafo.add_node("matching", con_reintento_auditor(agentes["matching"]))
    grafo.add_node("sin_matches", nodo_sin_matches)
    grafo.add_node("auditor", agentes["auditor"])
    grafo.add_node("formacion", agentes["formacion"])
    grafo.add_node("aprobacion", nodo_aprobacion, destinations=("postulacion", "matching", "aprobacion", END))
    grafo.add_node("postulacion", agentes["postulacion"])

    grafo.add_conditional_edges(START, ruta_inicio, ["guardrails", END])
    grafo.add_conditional_edges("guardrails", ruta_guardrails, ["router", END])
    grafo.add_conditional_edges("router", ruta_router, ["mercado", "perfil", "consulta"])
    grafo.add_edge("consulta", END)
    grafo.add_conditional_edges("mercado", ruta_post_mercado, ["alerta_oferta", "matching"])
    grafo.add_edge("alerta_oferta", END)
    grafo.add_edge("perfil", "matching")
    grafo.add_conditional_edges("matching", ruta_post_matching, ["auditor", "sin_matches"])
    grafo.add_edge("sin_matches", END)
    grafo.add_conditional_edges("auditor", ruta_post_auditor, {"matching": "matching", "continuar": "formacion"})
    grafo.add_edge("formacion", "aprobacion")
    grafo.add_conditional_edges("postulacion", ruta_post_postulacion, ["aprobacion", END])
    return grafo.compile(checkpointer=checkpointer)


_CHECKPOINTERS: dict[str, SqliteSaver] = {}
# Tipos que viajan en el estado y el checkpointer puede reconstruir sin aviso.
TIPOS_SERIALIZABLES = [
    ("puente.schemas", nombre)
    for nombre in ("Oferta", "Perfil", "Match", "Decision", "Horario", "HabilidadPerfil", "EstadoPostulacion")
] + [("puente.solver.asignacion", "MatchFallback")]


def checkpointer_sqlite(ruta: str | Path | None = None) -> SqliteSaver:
    """Checkpointer en su propio archivo (no en puente.db), con una sola conexión por proceso y ruta."""
    ruta = Path(ruta or os.getenv("PUENTE_CHECKPOINTS", CHECKPOINTS_POR_DEFECTO)).resolve()
    if ruta.name == "puente.db":
        raise ValueError("los checkpoints van en un archivo aparte de puente.db")
    clave = str(ruta)
    if clave not in _CHECKPOINTERS:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _CHECKPOINTERS[clave] = SqliteSaver(
            sqlite3.connect(clave, check_same_thread=False),
            serde=JsonPlusSerializer(allowed_msgpack_modules=TIPOS_SERIALIZABLES),
        )
    return _CHECKPOINTERS[clave]


def config_caso(thread_id: str) -> dict[str, Any]:
    """Config de LangGraph para un caso: un thread por caso (el id del perfil) y metadatos para LangSmith."""
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": f"puente:{thread_id}",
        "tags": ["puente", "issue-10"],
        "metadata": {"thread_id": thread_id},
    }


def aprobacion_pendiente(grafo: Any, config: dict) -> dict[str, Any] | None:
    """El valor del interrupt pendiente del caso, o None si el flujo no está detenido."""
    for tarea in grafo.get_state(config).tasks:
        for pendiente in tarea.interrupts:
            return pendiente.value
    return None


def estado_inicial() -> EstadoPuente:
    return EstadoPuente(
        ofertas=[],
        perfiles=[],
        matches=[],
        lista_corta=[],
        aprobacion=None,
        decisiones=[],
        errores=[],
    )


def cargar_caso(ruta: str | Path = CASO_YARITZA) -> tuple[str, dict[str, Any]]:
    """(thread_id, estado de entrada) de un caso JSON como tests/fixtures/caso_yaritza.json."""
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    estado = {
        **estado_inicial(),
        "ofertas": [Oferta.model_validate(o) for o in datos.get("ofertas", [])],
        "perfiles": [Perfil.model_validate(p) for p in datos.get("perfiles", [])],
        "tipo_entrada": datos.get("tipo_entrada", "perfil"),
        "entradas_perfil": datos.get("entradas_perfil", []),
        "ofertas_crudas": datos.get("ofertas_crudas", []),
    }
    return datos.get("thread_id", "caso"), estado


# ---------------------------------------------------------------- demo por consola


def _preguntar_respuesta(solicitud: dict[str, Any], leer: Callable[[str], str]) -> dict[str, Any]:
    accion = ""
    while accion not in ACCIONES:
        accion = leer(f"Acción ({' / '.join(ACCIONES)}): ").strip().lower()
    respuesta: dict[str, Any] = {"accion": accion, "oferta_id": solicitud["oferta_id"]}
    if accion == "quitar":
        respuesta["perfil_id"] = leer("perfil_id a quitar: ").strip()
        return respuesta
    respuesta["orientador"] = leer("Orientador(a): ").strip()
    respuesta["nota"] = leer("Nota (opcional): ").strip()
    if accion == "editar":
        respuesta["mensaje"] = leer("Mensaje editado: ").strip()
    if accion in ("aprobar", "editar"):
        respuesta["consentidos"] = [
            c["perfil_id"]
            for c in solicitud["candidatos"]
            if leer(f"¿{c['nombre']} ({c['perfil_id']}) dio su consentimiento? [s/n]: ").strip().lower().startswith("s")
        ]
    return respuesta


def _respuesta_automatica(paso: str, solicitud: dict[str, Any]) -> dict[str, Any]:
    """`aprobar`, `rechazar`, `editar` o `quitar:perfil_id`; consiente a toda la lista corta."""
    accion, _, perfil_id = paso.partition(":")
    return {
        "accion": accion,
        "orientador": "Orientadora Ficticia",
        "oferta_id": solicitud["oferta_id"],
        "perfil_id": perfil_id or None,
        "mensaje": "Mensaje editado en la demo." if accion == "editar" else None,
        "nota": "demo automática",
        "consentidos": [c["perfil_id"] for c in solicitud["candidatos"]],
    }


def _mostrar(solicitud: dict[str, Any], escribir: Callable[[str], None]) -> None:
    oferta = solicitud["oferta"]
    escribir(f"\n⏸  Aprobación pendiente · {solicitud['oferta_id']} · {oferta['puesto']} en {oferta['empresa']}")
    for c in solicitud["candidatos"]:
        brechas = f" · brechas: {', '.join(c['brechas'])}" if c["brechas"] else ""
        escribir(f"   - {c['nombre']} ({c['perfil_id']}) puntaje {c['puntaje']}{brechas}")
    if solicitud["alertas_auditor"]:
        escribir(f"   Auditor: {'; '.join(solicitud['alertas_auditor'])}")
    if solicitud["error"]:
        escribir(f"   ⚠ {solicitud['error']}")
    escribir(f"   Mensaje sugerido: {solicitud['mensaje_sugerido']}")


def main(argv: list[str] | None = None, leer: Callable[[str], str] = input, escribir: Callable[[str], None] = print) -> dict:
    parser = argparse.ArgumentParser(prog="python -m puente.graph", description="Demo del grafo de Puente (#10)")
    parser.add_argument("--demo", default="yaritza", choices=["yaritza"], help="caso de la demo")
    parser.add_argument("--caso", default=str(CASO_YARITZA), help="JSON del caso")
    parser.add_argument("--checkpoints", default=None, help="archivo SQLite de checkpoints (PUENTE_CHECKPOINTS)")
    parser.add_argument("--thread", default=None, help="thread_id; por defecto <id del caso>-<hora>")
    parser.add_argument("--auto", default=None, help="decisiones sin preguntar, p. ej. 'quitar:carlos,aprobar,rechazar'")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass

    caso_id, estado = cargar_caso(args.caso)
    thread_id = args.thread or f"{caso_id}-{datetime.now(UTC):%Y%m%d%H%M%S}"
    grafo = construir_grafo(checkpointer=checkpointer_sqlite(args.checkpoints))
    config = config_caso(thread_id)
    pasos = [p.strip() for p in args.auto.split(",")] if args.auto else None
    escribir(f"Caso {args.demo} · thread_id={thread_id}")

    final = grafo.invoke(estado, config)
    while (solicitud := aprobacion_pendiente(grafo, config)) is not None:
        _mostrar(solicitud, escribir)
        if pasos is not None:
            if not pasos:
                escribir("Sin más decisiones automáticas; el caso queda detenido en la aprobación.")
                return final
            respuesta = _respuesta_automatica(pasos.pop(0), solicitud)
            escribir(f"   → {respuesta['accion']}")
        else:
            respuesta = _preguntar_respuesta(solicitud, leer)
        final = grafo.invoke(Command(resume=respuesta), config)

    escribir("\nFlujo terminado.")
    if final.get("respuesta"):
        escribir(f"Respuesta: {final['respuesta']}")
    if final.get("aprobacion"):
        escribir(f"Última aprobación: {json.dumps(final['aprobacion'], ensure_ascii=False)}")
    escribir("Decisiones: " + " → ".join(d.agente for d in final.get("decisiones", [])))
    return final


if __name__ == "__main__":
    main(sys.argv[1:])
