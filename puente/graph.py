"""Grafo LangGraph: guardrails, router, agentes, aprobación del orientador con interrupt() y trazas. Issue #10.

Fase 2: los nodos son los agentes reales de main (Mercado #5/#43, Perfil #6/#44, Matching
con el solver CP-SAT #4, Auditor v2 #50, Formación #14 y Postulación #7/#12, PR #84). Los
stubs de la Fase 1 quedan en `NODOS_FASE_1` para probar la mecánica del grafo.
El LLM de Mercado y Perfil es inyectable (`nodos_fase_2`); por defecto es Claude a través de
`puente.llm` (#45). Los tests y la demo sin conexión usan respuestas grabadas, nunca la red.

Postulación escribe en SQLite (`PUENTE_DB`, por defecto `puente.db`) y deja el CV ciego, el
`.ics` y los mensajes en `PUENTE_OUTBOX` (por defecto `outbox/`), ambos relativos al directorio
de trabajo. Telegram está apagado salvo que se pida (`nodos_fase_2(telegram=True)` o
`--telegram` en la demo): apagado, ningún envío sale a la red aunque `.env` traiga `TELEGRAM_*`.

    START → guardrails ─rechazo→ END
       │        └→ router ─oferta→ mercado ─bloqueada→ alerta_oferta → END
       │             │                └→ matching
       │             ├─perfil→ perfil → matching
       │             ├─consulta→ consulta → END
       └─evento_empresa→ router ─evento_empresa→ evento_empresa → avisos → matching, o aprobacion si no aplica
    matching ─sin matches→ sin_matches → END
        └→ auditor ─sesgo (una vez)→ matching, o perfil si el sesgo viene de la lectura del perfil
              └→ formacion → aprobacion (interrupt)
    aprobacion: aprobar/editar → postulacion · quitar → matching · rechazar → END
    postulacion → aprobacion si quedan vacantes por decidir, si no END

Contrato con el tablero (#11, #49):
    grafo = construir_grafo(checkpointer=checkpointer_sqlite())
    config = config_caso(perfil_id)
    grafo.invoke(limpiar_entrada(estado), config)     # corre hasta el interrupt; sin PII en el checkpoint
    aprobacion_pendiente(grafo, config)              # o grafo.get_state(config)
    grafo.invoke(Command(resume={...}), config)       # ver RESPUESTA_APROBACION
    evento_empresa(grafo, config, oferta_id, perfil_id, tipo="rechaza")  # la empresa respondió (#54)
    evento_empresa(grafo, config, oferta_id, tipo="cubierta")            # luego, aprobacion_pendiente()

Reacomodo (#54): cuando la empresa rechaza a un candidato postulado, su postulación pasa a
`no_seleccionado`, la pareja entra a `excluidos` con `origen="empresa"`, la vacante vuelve a
quedar pendiente y el Matching recalcula; el orientador aprueba la lista corta nueva en el mismo
interrupt de siempre. Una vacante cubierta sale del Matching (`vacantes_cubiertas`). Los avisos
quedan en `avisos_pendientes`; el nodo `avisos` manda en seguida los del joven con `enviar_al_joven`
de #84 (outbox/ o Telegram) y los saca de ahí; los del orientador quedan para el tablero. Demo sin red:

    uv run python -m puente.graph --auto "aprobar,aprobar,empresa_rechaza:yaritza@of-cajero-vista-alegre,aprobar"

Sin `--auto`, la respuesta de la empresa se escribe con la misma sintaxis en la pregunta de acción
(`empresa_rechaza:yaritza@of-cajero-vista-alegre`) o, al terminar el flujo, en la pregunta final.

LangSmith se activa solo con LANGSMITH_TRACING=true y LANGSMITH_API_KEY en `.env`; cada nodo
aparece como un run con su nombre (guardrails, router, mercado, perfil, matching, auditor,
formacion, aprobacion, postulacion, evento_empresa, avisos) bajo el run `puente:<thread_id>`.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from puente.agents.auditor import nodo_auditor, ruta_post_auditor
from puente.agents.formacion import nodo_formacion
from puente.agents.matching import nodo_matching
from puente.agents.mercado import crear_nodo_mercado, extractor_claude
from puente.agents.perfil import llm_puente, nodo_perfil
from puente.agents.postulacion import crear_nodo_postulacion
from puente.guardrails import (
    RESPUESTA_FUERA_DE_ALCANCE,
    limpiar_entrada,
    nodo_guardrails,
    ocultar_trazas_langsmith,
)
from puente.rules.discriminacion import esta_bloqueada, revisar_oferta
from puente.rules.taxonomia import RUTA_TAXONOMIA, cargar_taxonomia
from puente.schemas import (
    REQUIERE_CONFIRMACION,
    Decision,
    EstadoPostulacion,
    EstadoPuente,
    Match,
    Oferta,
    OfertaExtraida,
    Perfil,
    agregar_decision,
)
from puente.solver.asignacion import ConfigSolver
from puente.tools.telegram import Enviador, enviar_al_joven

Nodo = Callable[[dict], Any]

TIPOS_ENTRADA = ("oferta", "perfil", "consulta", "evento_empresa")
ACCIONES = ("aprobar", "editar", "quitar", "rechazar")
EVENTOS_EMPRESA = ("rechaza", "cubierta")
# Estados en que la pareja ya va (o fue) hacia la empresa: solo sobre ellas puede responder.
# La aprobación la deja en `consentido`; Postulación (#84) la pasa a `enviado` (el stub no la mueve).
ESTADOS_ANTE_EMPRESA = (EstadoPostulacion.CONSENTIDO, EstadoPostulacion.ENVIADO, EstadoPostulacion.ENTREVISTA)
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
DB_POR_DEFECTO = "puente.db"
OUTBOX_POR_DEFECTO = "outbox"
RAIZ = Path(__file__).resolve().parents[1]
CASO_YARITZA = RAIZ / "tests" / "fixtures" / "caso_yaritza.json"
TAXONOMIA_MINI = RAIZ / "tests" / "fixtures" / "taxonomia_mini.csv"


class EstadoGrafo(EstadoPuente, total=False):
    """EstadoPuente (#42) más las claves propias del grafo.

    LangGraph descarta en silencio las claves que no declara el estado: sin esto el
    veredicto de guardrails o el contador del Auditor nunca llegarían al nodo siguiente.
    """

    entrada: dict[str, Any]
    guardrails: dict[str, Any]
    ofertas_nuevas: list[str]
    vacantes_resueltas: list[str]
    aviso_aprobacion: str | None
    respuesta: str
    # El Auditor v2 (#50) cuenta sus propios reintentos y lo avisa con esta marca
    _auditor_incremento: bool
    # Reacomodo (#54): el evento de la empresa por atender (se vacía al atenderlo), las vacantes
    # que la empresa ya cubrió y que el Matching no vuelve a proponer, y los avisos que falta enviar
    evento_empresa: dict[str, Any] | None
    vacantes_cubiertas: list[str]
    avisos_pendientes: list[dict[str, Any]]


# ---------------------------------------------------------------- utilidades


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


def _campo(obj: Any, nombre: str, defecto: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(nombre, defecto)
    return getattr(obj, nombre, defecto)


def _decision(estado: dict, agente: str, entrada: str, salida: str, razon: str) -> dict:
    """`{"decisiones": [...]}` con la nueva al final, vía `agregar_decision()` de #42."""
    return agregar_decision(
        estado, Decision(agente=agente, entrada_resumen=entrada, salida_resumen=salida, razon=razon)
    )


def _con_decision(estado: dict, agente: str, entrada: str, salida: str, razon: str) -> list:
    """Lista completa de decisiones; la usan los stubs de la Fase 1."""
    return _decision(estado, agente, entrada, salida, razon)["decisiones"]


def _excluido(estado: dict, perfil_id: str, oferta_id: str) -> bool:
    return any(
        e.get("perfil_id") == perfil_id and e.get("oferta_id") == oferta_id for e in estado.get("excluidos") or []
    )


def _lista_corta_de(estado: dict, oferta_id: str) -> list:
    return [m for m in estado.get("lista_corta") or [] if _campo(m, "oferta_id") == oferta_id]


def _oferta(estado: dict, oferta_id: Any) -> Any:
    return next((o for o in estado.get("ofertas") or [] if _campo(o, "id") == oferta_id), None)


def _nombre(estado: dict, perfil_id: str) -> str:
    perfil = next((p for p in estado.get("perfiles") or [] if _campo(p, "id") == perfil_id), None)
    return _campo(perfil, "nombre") or perfil_id


def clave_postulacion(perfil_id: str, oferta_id: str) -> str:
    """Clave de `postulaciones` en el estado: "perfil_id:oferta_id", la misma que usa Postulación (#12, #84)."""
    return f"{perfil_id}:{oferta_id}"


_ORDEN_POSTULACION = list(EstadoPostulacion)


def negaron_consentimiento(estado: dict, oferta_id: str) -> set[str]:
    """Perfiles cuyo último registro de `consentimientos` para esta oferta es `otorgado=False`.

    El «no» del joven (por ejemplo desde /joven, #52) manda sobre la confirmación del orientador:
    esa pareja no avanza a `consentido` y Postulación (#12) no la envía.
    """
    ultimo: dict[str, bool] = {}
    for registro in estado.get("consentimientos") or []:
        if _campo(registro, "oferta_id") == oferta_id:
            ultimo[_campo(registro, "perfil_id")] = bool(_campo(registro, "otorgado"))
    return {pid for pid, otorgado in ultimo.items() if not otorgado}


def _avanzar(postulaciones: dict, clave: str, nuevo: EstadoPostulacion) -> None:
    """Una postulación solo avanza en el embudo: volver a aprobar a quien ya se envió no la regresa."""
    actual = postulaciones.get(clave)
    if actual is None or _ORDEN_POSTULACION.index(EstadoPostulacion(actual)) < _ORDEN_POSTULACION.index(nuevo):
        postulaciones[clave] = nuevo


def _postulados_en(postulaciones: dict, oferta_id: str) -> list[str]:
    """perfil_id cuya postulación a esa vacante ya va o fue hacia la empresa."""
    sufijo = clave_postulacion("", oferta_id)
    return [c[: -len(sufijo)] for c, e in postulaciones.items() if c.endswith(sufijo) and e in ESTADOS_ANTE_EMPRESA]


def vacantes_pendientes(estado: dict) -> list[str]:
    """Vacantes con lista corta que el orientador todavía no decidió, en el orden de `ofertas`.

    Ese orden no depende del solver: tras quitar un candidato, el orientador vuelve a ver la misma vacante.
    Una vacante que la empresa ya cubrió (#54) nunca queda pendiente.
    """
    resueltas = set(estado.get("vacantes_resueltas") or []) | set(estado.get("vacantes_cubiertas") or [])
    con_lista: list[str] = []
    for m in estado.get("lista_corta") or []:
        oid = _campo(m, "oferta_id")
        if oid not in resueltas and oid not in con_lista:
            con_lista.append(oid)
    orden = {_campo(o, "id"): i for i, o in enumerate(estado.get("ofertas") or [])}
    return sorted(con_lista, key=lambda oid: orden.get(oid, len(orden)))


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
    """Una invocación sin nada que revisar termina sin efectos ni decisiones.

    Un evento de la empresa (#54) va directo al router: no es texto libre de afuera sino una acción
    estructurada del tablero, igual que la respuesta al interrupt, y guardrails solo volvería a revisar
    la entrevista que ya revisó. Su nota llega limpia de PII (`evento_empresa()`).
    """
    if tipo_de_entrada(estado) == "evento_empresa":
        return "router"
    claves = ("entrada", "tipo_entrada", "ofertas_crudas", "entradas_perfil")
    return "guardrails" if any(estado.get(c) for c in claves) else END


def ruta_guardrails(estado: dict) -> str:
    return "router" if (estado.get("guardrails") or {}).get("ok") else END


def tipo_de_entrada(estado: dict) -> str:
    """`tipo_entrada` manda (#42); sin él se infiere de qué trae el estado.

    `evento_empresa` solo cuenta si trae el evento: el estado lo guarda, pero uno ya atendido no se repite.
    """
    tipo = estado.get("tipo_entrada")
    if tipo in TIPOS_ENTRADA and (tipo != "evento_empresa" or estado.get("evento_empresa")):
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
        **_decision(estado, "router", f"tipo_entrada={estado.get('tipo_entrada')!r}", tipo, origen),
    }


def ruta_router(estado: dict) -> str:
    rutas = {"oferta": "mercado", "perfil": "perfil", "evento_empresa": "evento_empresa"}
    return rutas.get(estado.get("tipo_entrada", ""), "consulta")


def nodo_consulta(estado: dict) -> dict:
    """MVP: las consultas libres no se atienden todavía."""
    return {
        "respuesta": RESPUESTA_FUERA_DE_ALCANCE,
        **_decision(estado, "consulta", "consulta libre", RESPUESTA_FUERA_DE_ALCANCE, "fuera de alcance en el MVP"),
    }


# ---------------------------------------------------------------- stubs de la Fase 1


def stub_mercado(estado: dict) -> dict:
    """STUB de Mercado (#5): acepta ofertas ya estructuradas, sin LLM, y aplica las reglas legales reales
    a los requisitos y al puesto (#190)."""
    ofertas = list(estado.get("ofertas") or [])
    errores = list(estado.get("errores") or [])
    nuevas: list[str] = []
    for cruda in estado.get("ofertas_crudas") or []:
        if not isinstance(cruda, dict) or "id" not in cruda:
            errores.append("mercado (stub): solo lee ofertas estructuradas; el texto libre llega con #5")
            continue
        texto = f"{cruda.get('requisitos_texto', '')}\n{cruda.get('texto', '')}"
        hallazgos = revisar_oferta(str(cruda.get("puesto") or ""), texto)
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
        **_decision(
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
    """Si el Auditor devolvió el caso, cuenta el reintento para que `ruta_post_auditor` no entre en bucle.

    El Auditor v2 (#50) ya cuenta sus reintentos (`_auditor_incremento`); entonces no se cuenta dos veces.
    """

    def matching(estado: dict) -> dict:
        cambios = nodo(estado)
        if estado.get("devolver_a_matching") and not estado.get("_auditor_incremento"):
            cambios = {
                **cambios,
                "reintentos_auditor": int(estado.get("reintentos_auditor", 0)) + 1,
                "devolver_a_matching": False,
            }
        return cambios

    return matching


def sin_vacantes_cubiertas(nodo: Nodo) -> Nodo:
    """El nodo (Matching o Auditor) no ve las vacantes que la empresa ya cubrió (#54).

    Vale para cualquier nodo inyectado. Además filtra su salida, por si el Matching las vuelve a leer
    de puente.db (#46). El estado conserva la oferta: el tablero la sigue mostrando como cubierta.
    """

    def sin_cubiertas(estado: dict) -> dict:
        cubiertas = set(estado.get("vacantes_cubiertas") or [])
        if not cubiertas:
            return nodo(estado)
        vista = {**estado, "ofertas": [o for o in estado.get("ofertas") or [] if _campo(o, "id") not in cubiertas]}
        cambios = {k: v for k, v in nodo(vista).items() if k != "ofertas"}
        for clave in ("matches", "lista_corta"):
            if clave in cambios:
                cambios[clave] = [m for m in cambios[clave] or [] if _campo(m, "oferta_id") not in cubiertas]
        return cambios

    return sin_cubiertas


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
        **_decision(
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


def stub_avisos(estado: dict) -> dict:
    """STUB del envío de avisos del reacomodo (#54): los deja en `avisos_pendientes` y no envía nada."""
    return {}


def ruta_post_postulacion(estado: dict) -> str:
    return "aprobacion" if vacantes_pendientes(estado) else END


# ---------------------------------------------------------------- agentes reales (Fase 2)


def cargar_taxonomia_grafo() -> dict[str, dict]:
    """PUENTE_TAXONOMIA o data/taxonomia.csv; mientras #3 no la sube, la mini de los tests, como la demo de Perfil."""
    for ruta in (os.getenv("PUENTE_TAXONOMIA"), RAIZ / RUTA_TAXONOMIA, TAXONOMIA_MINI):
        if ruta and Path(ruta).exists():
            return cargar_taxonomia(ruta)
    return {}


def _extractor_grabado(estado: dict) -> Callable[[Any], Any]:
    """Modo sin conexión de Mercado: la `respuesta_llm` guardada en cada oferta cruda, buscada por su texto,
    validada como la `OfertaExtraida` del contrato que devuelve `extractor_claude`."""
    grabadas = {
        str(c.get("texto", "")).strip(): c["respuesta_llm"]
        for c in estado.get("ofertas_crudas") or []
        if isinstance(c, dict) and c.get("respuesta_llm")
    }

    def extraer(entrada: Any) -> Any:
        try:
            return OfertaExtraida.model_validate(grabadas[entrada.texto.strip()])
        except KeyError:
            raise ValueError("sin respuesta grabada para esta oferta (modo sin conexión)") from None

    return extraer


def _llm_grabado(entrada: dict) -> Callable[[str], str]:
    """Modo sin conexión de Perfil: `respuesta_llm` de la entrada o, si no trae, sus `habilidades`.

    Igual que con Claude vía `puente.llm`, Perfil verifica cada habilidad contra la taxonomía y la evidencia literal.
    """
    texto = json.dumps(entrada.get("respuesta_llm") or {"habilidades": entrada.get("habilidades") or []}, ensure_ascii=False)
    return lambda _prompt: texto


def crear_nodo_mercado_grafo(
    extraer: Callable[[Any], Any] | None = None, *, grabado: bool = False, taxonomia: dict[str, dict] | None = None
) -> Nodo:
    """Mercado real (#5, #43) más `ofertas_nuevas`, que decide si la entrada termina en alerta_oferta."""

    def mercado(estado: dict) -> dict:
        previas = {_campo(o, "id") for o in estado.get("ofertas") or []}
        extractor = _extractor_grabado(estado) if grabado else (extraer or extractor_claude)
        nodo = crear_nodo_mercado(extractor, taxonomia if taxonomia is not None else cargar_taxonomia_grafo())
        cambios = nodo(estado)
        return {**cambios, "ofertas_nuevas": [o.id for o in cambios["ofertas"] if o.id not in previas]}

    return mercado


def crear_nodo_perfil_grafo(
    llm: Callable[[str], str] | None = None, *, grabado: bool = False, taxonomia: dict[str, dict] | None = None
) -> Nodo:
    """Perfil real (#6, #44). La entrevista corre fuera del grafo y llega como un elemento de `entradas_perfil`."""

    def perfil(estado: dict) -> dict:
        tax = taxonomia if taxonomia is not None else cargar_taxonomia_grafo()
        if not grabado:
            return nodo_perfil(estado, llm=llm or llm_puente, taxonomia=tax)
        cambios: dict = {}
        for entrada in estado.get("entradas_perfil") or []:
            parcial = {**estado, **cambios, "entradas_perfil": [entrada]}
            cambios.update(nodo_perfil(parcial, llm=_llm_grabado(entrada), taxonomia=tax))
        return cambios

    return perfil


def _perfil_para_solver(perfil: Any) -> dict:
    """El solver de #4 lanza IndexError con `horario_libre=[]`; sin la clave lo trata como sin restricción.

    Rodeo hasta que #48 lo corrija en `puente/solver/`; el estado conserva el Perfil original.
    """
    datos = perfil.model_dump() if isinstance(perfil, BaseModel) else dict(perfil)
    if not datos.get("horario_libre"):
        datos.pop("horario_libre", None)
    return datos


def _config_solver(estado: dict) -> ConfigSolver:
    config = estado.get("config_solver")
    return config if isinstance(config, ConfigSolver) else ConfigSolver(**(config or {}))


def nodo_matching_grafo(estado: dict) -> dict:
    """Matching real (#4) sin las parejas que el orientador quitó.

    El solver todavía no recibe exclusiones (#48). Sin tope de vacantes por perfil cada vacante se
    resuelve sola, así que se le pide k + (exclusiones de la vacante más afectada), se quitan las
    parejas excluidas y cada vacante se recorta a k por el puntaje del solver: el lugar que deja
    el excluido lo ocupa el siguiente viable. Con tope por perfil solo se filtra.
    """
    excluidos = {(e.get("perfil_id"), e.get("oferta_id")) for e in estado.get("excluidos") or []}
    config = _config_solver(estado)
    k = config.max_candidatos_por_vacante
    holgura = 0
    if excluidos and config.max_vacantes_por_perfil is None:
        holgura = max(Counter(oferta for _, oferta in excluidos).values())
    vista = {
        **estado,
        "perfiles": [_perfil_para_solver(p) for p in estado.get("perfiles") or []],
        "config_solver": config.model_copy(update={"max_candidatos_por_vacante": k + holgura}),
    }
    cambios = nodo_matching(vista)
    if not excluidos:
        return cambios

    lista_corta: list = []
    por_vacante: Counter = Counter()
    sobran: list = []
    candidatos = [m for m in cambios["lista_corta"] if (m.perfil_id, m.oferta_id) not in excluidos]
    for m in sorted(candidatos, key=lambda m: (m.oferta_id, -m.puntaje, m.perfil_id)):
        por_vacante[m.oferta_id] += 1
        (lista_corta if por_vacante[m.oferta_id] <= k else sobran).append(m)
    fuera = {(m.perfil_id, m.oferta_id) for m in sobran} | excluidos
    elegidos = {id(m) for m in lista_corta}
    lista_corta = [m for m in candidatos if id(m) in elegidos]  # conserva el orden del solver
    matches = [m for m in cambios["matches"] if (m.perfil_id, m.oferta_id) not in fuera]

    decisiones = list(cambios["decisiones"])
    origen = {(e.get("perfil_id"), e.get("oferta_id")): e.get("origen") or "orientador" for e in estado.get("excluidos") or []}
    texto = ", ".join(f"{p} → {o} ({origen[(p, o)]})" for p, o in sorted(excluidos))
    if decisiones and isinstance(decisiones[-1], Decision):
        ultima = decisiones[-1]
        decisiones[-1] = ultima.model_copy(
            update={
                "salida_resumen": f"{len(lista_corta)} candidato(s) en lista corta sin las parejas excluidas",
                "razon": f"{ultima.razon} Parejas excluidas por el orientador o la empresa, filtradas en el grafo hasta #48: {texto}.",
            }
        )
    return {**cambios, "matches": matches, "lista_corta": lista_corta, "decisiones": decisiones}


def nodo_auditor_grafo(estado: dict) -> dict:
    """Auditor v2 (#50) con los perfiles en la forma que acepta el solver; el estado conserva los originales."""
    vista = {**estado, "perfiles": [_perfil_para_solver(p) for p in estado.get("perfiles") or []]}
    cambios = nodo_auditor(vista)
    return {k: v for k, v in cambios.items() if k not in ("perfiles", "ofertas")}


def db_por_entorno() -> Path:
    """Base SQLite de Postulación: PUENTE_DB o, si falta o está vacía, puente.db en el directorio de trabajo."""
    return Path(os.getenv("PUENTE_DB") or DB_POR_DEFECTO)


def outbox_por_entorno() -> Path:
    """Carpeta del CV ciego, el .ics y los mensajes: PUENTE_OUTBOX o, si falta, outbox/ en el directorio de trabajo."""
    return Path(os.getenv("PUENTE_OUTBOX") or OUTBOX_POR_DEFECTO)


class TelegramApagado(RuntimeError):
    """El grafo se armó sin Telegram: el mensaje queda en outbox/."""


def sin_telegram(token: str, chat_id: str, texto: str, adjuntos: list[Path]) -> dict:
    """Transporte de Telegram que nunca sale a la red.

    `puente.tools.telegram` solo lo llama si el entorno trae token y chat; como falla, el mensaje
    queda en outbox/ con el motivo "fallo al enviar (TelegramApagado)". Así un `.env` con
    `TELEGRAM_*` no manda mensajes reales desde las pruebas ni desde la demo sin `--telegram`.
    """
    raise TelegramApagado("Telegram apagado en este grafo")


def crear_nodo_avisos(*, outbox: str | Path | None = None, enviar_joven: Enviador | None = None) -> Nodo:
    """Nodo `avisos`: manda a cada joven su aviso del reacomodo (#54) y lo saca de `avisos_pendientes`.

    Corre justo después de `evento_empresa`, antes de recalcular. Usa `enviar_al_joven` de #84:
    Telegram al chat del joven o, si no se puede, `outbox/joven_*.txt`; nunca el chat de la empresa.
    Cada envío deja una Decision con el canal. Los avisos para el orientador se quedan en
    `avisos_pendientes`, porque no hay canal hacia el orientador: los muestra el tablero. También se
    queda un aviso que ni Telegram ni el respaldo pudieron dejar, para reintentarlo.
    """
    carpeta = Path(outbox) if outbox is not None else outbox_por_entorno()

    def avisos(estado: dict) -> dict:
        pendientes = list(estado.get("avisos_pendientes") or [])
        if not any(a.get("para") == "joven" for a in pendientes):
            return {}
        quedan: list[dict[str, Any]] = []
        acumulado = {"decisiones": list(estado.get("decisiones") or [])}
        for aviso in pendientes:
            if aviso.get("para") != "joven":
                quedan.append(aviso)
                continue
            # perfil_id elige el chat propio del joven (TELEGRAM_CHATS_JOVENES, #84) y nombra su outbox/joven_<id>_*.txt
            envio = enviar_al_joven(
                aviso["texto"], perfil_id=aviso.get("perfil_id") or "", outbox=carpeta, enviar=enviar_joven
            )
            if not envio.ok:
                quedan.append(aviso)
            archivo = f", {envio.ruta.name}" if envio.ruta else ""
            acumulado = _decision(
                acumulado,
                "avisos",
                f"aviso de reacomodo para {aviso.get('perfil_id')}: {aviso.get('tipo')} en {aviso.get('oferta_id')}",
                f"canal {envio.canal}, ok={envio.ok}{archivo}",
                envio.detalle,
            )
        return {"avisos_pendientes": quedan, **acumulado}

    return avisos


def nodos_fase_2(
    *,
    llm_perfil: Callable[[str], str] | None = None,
    extraer_oferta: Callable[[Any], Any] | None = None,
    grabado: bool = False,
    taxonomia: dict[str, dict] | None = None,
    ruta_db: str | Path | None = None,
    outbox: str | Path | None = None,
    telegram: bool = False,
    enviar: Enviador | None = None,
    enviar_joven: Enviador | None = None,
) -> dict[str, Nodo]:
    """Los agentes reales. Sin `llm_perfil` ni `extraer_oferta` usan Claude a través de `puente.llm`
    (`llm_puente` y `extractor_claude`); `grabado=True` usa la `respuesta_llm` guardada en cada entrada
    (demo sin conexión).

    Postulación es el nodo real de #84 (`crear_nodo_postulacion`): escribe en `ruta_db` (por defecto
    PUENTE_DB o puente.db) y deja el CV ciego, el .ics y los mensajes en `outbox` (por defecto
    PUENTE_OUTBOX u outbox/). Con `telegram=False`, lo normal, nada sale por Telegram aunque el entorno
    traiga `TELEGRAM_*`: todo queda en outbox. `telegram=True` deja que `puente.tools.telegram` envíe
    con el token y los chats del entorno. `enviar` (empresa) y `enviar_joven` inyectan el transporte
    (en pruebas, un doble) y mandan sobre `telegram`. El nodo `avisos` usa el mismo outbox y transporte.
    """
    base = Path(ruta_db) if ruta_db is not None else db_por_entorno()
    carpeta = Path(outbox) if outbox is not None else outbox_por_entorno()
    apagado = None if telegram else sin_telegram
    a_la_empresa = enviar if enviar is not None else apagado
    al_joven = enviar_joven if enviar_joven is not None else apagado
    return {
        "mercado": crear_nodo_mercado_grafo(extraer_oferta, grabado=grabado, taxonomia=taxonomia),
        "perfil": crear_nodo_perfil_grafo(llm_perfil, grabado=grabado, taxonomia=taxonomia),
        "matching": nodo_matching_grafo,
        "auditor": nodo_auditor_grafo,
        "formacion": nodo_formacion,
        "postulacion": crear_nodo_postulacion(
            ruta_db=base, outbox=carpeta, enviar=a_la_empresa, enviar_joven=al_joven
        ),
        "avisos": crear_nodo_avisos(outbox=carpeta, enviar_joven=al_joven),
    }


# ---------------------------------------------------------------- aprobación del orientador


def _mensaje_sugerido(oferta: Any, candidatos: list[dict]) -> str:
    nombres = ", ".join(c["nombre"] for c in candidatos)
    return (
        f"Buen día, {_campo(oferta, 'empresa', 'equipo')}: desde SkillMatch les presentamos a {nombres} "
        f"para la vacante de {_campo(oferta, 'puesto', '')}. Quedamos atentos para coordinar entrevistas."
    )


def solicitud_aprobacion(estado: dict, oferta_id: str) -> dict[str, Any]:
    """Lo que ve el orientador en el interrupt: una vacante y su lista corta, todo serializable."""
    oferta = next((o for o in estado.get("ofertas") or [] if o.id == oferta_id), None)
    perfiles = {p.id: p for p in estado.get("perfiles") or []}
    rutas = [r for r in estado.get("rutas_formacion") or [] if r.get("oferta_id") == oferta_id]
    postulaciones = estado.get("postulaciones") or {}
    candidatos = []
    for m in _lista_corta_de(estado, oferta_id):
        postulacion = postulaciones.get(clave_postulacion(m.perfil_id, oferta_id))
        candidatos.append(
            {
                "perfil_id": m.perfil_id,
                "nombre": _campo(perfiles.get(m.perfil_id), "nombre", m.perfil_id),
                "puntaje": m.puntaje,
                "brechas": list(m.brechas),
                "explicacion": m.explicacion,
                "requiere_confirmacion": list(m.desglose.get(REQUIERE_CONFIRMACION) or []),
                "rutas_formacion": [r for r in rutas if r.get("perfil_id") == m.perfil_id],
                # Tras un reacomodo (#54) la lista nueva trae a quienes ya se postularon a esta vacante
                "estado_postulacion": str(postulacion) if postulacion else None,
            }
        )
    return {
        "tipo": "aprobacion",
        "oferta_id": oferta_id,
        "oferta": {
            "puesto": _campo(oferta, "puesto"),
            "empresa": _campo(oferta, "empresa"),
            "salario_mensual": _campo(oferta, "salario_mensual"),
            "salario_tipo": _campo(oferta, "salario_tipo"),
            "horario_tipo": _campo(oferta, "horario_tipo"),
            "alertas": list(_campo(oferta, "alertas") or []),
            "requiere_confirmacion": oferta.por_confirmar() if isinstance(oferta, Oferta) else [],
        },
        "candidatos": candidatos,
        "mensaje_sugerido": _mensaje_sugerido(oferta, candidatos),
        "alertas_auditor": list(estado.get("alertas_auditor") or []),
        "reporte_auditor": estado.get("reporte_auditor"),
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
        return _decision(estado, "aprobacion", entrada, salida, f"{orientador or 'orientador'}: {razon}")["decisiones"]

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
    # Aprobado y con consentimiento confirmado: la pareja va hacia la empresa, que podrá responder (#54).
    postulaciones = dict(estado.get("postulaciones") or {})
    negados = negaron_consentimiento(estado, oferta_id)
    for pid in aprobados:
        if pid not in negados:
            _avanzar(postulaciones, clave_postulacion(pid, oferta_id), EstadoPostulacion.CONSENTIDO)
    return Command(
        goto="postulacion",
        update={
            **base,
            "aprobacion": aprobacion,
            "vacantes_resueltas": resueltas,
            "postulaciones": postulaciones,
            "decisiones": decision(accion, razon),
        },
    )


# ---------------------------------------------------------------- reacomodo: la empresa responde (#54)


def aviso_reacomodo(
    estado: dict, evento: dict, no_seleccionados: list[str], contratado: str | None = None
) -> list[dict[str, Any]]:
    """Avisos del reacomodo para cada joven que no siguió y para el orientador. Solo arma el texto; no envía nada.

    Los del joven los manda en seguida el nodo `avisos` (`crear_nodo_avisos`), por Telegram u outbox/.
    """
    tipo, oferta_id = evento["tipo"], evento["oferta_id"]
    oferta = _oferta(estado, oferta_id)
    empresa = _campo(oferta, "empresa") or "La empresa"
    puesto = _campo(oferta, "puesto") or oferta_id
    momento = str(evento.get("timestamp") or _ahora())

    def aviso(para: str, perfil_id: str | None, texto: str) -> dict[str, Any]:
        return {"para": para, "perfil_id": perfil_id, "oferta_id": oferta_id, "tipo": tipo, "texto": texto, "timestamp": momento}

    avisos = []
    for pid in no_seleccionados:
        if tipo == "rechaza":
            texto = f"Hola, {_nombre(estado, pid)}: {empresa} no siguió con tu postulación a {puesto}. Tu orientador(a) ya revisa otras vacantes para ti."
        else:
            texto = f"Hola, {_nombre(estado, pid)}: la vacante de {puesto} en {empresa} ya se cubrió. Tu orientador(a) te avisará de otras opciones."
        avisos.append(aviso("joven", pid, texto))
    nombres = ", ".join(_nombre(estado, pid) for pid in no_seleccionados)
    if tipo == "rechaza":
        texto = f"{empresa} rechazó a {nombres} para {puesto} ({oferta_id}). El Matching recalcula esa lista corta sin la pareja."
    else:
        con = f" con {_nombre(estado, contratado)}" if contratado else ""
        sin = f"; no siguen: {nombres}" if nombres else ""
        texto = f"{empresa} cubrió la vacante de {puesto} ({oferta_id}){con}. Sale del Matching{sin}."
    avisos.append(aviso("orientador", None, texto))
    return avisos


def nodo_evento_empresa(estado: dict) -> Command:
    """La empresa respondió sobre una vacante: rechaza a un candidato postulado o la da por cubierta.

    Rechaza: la postulación pasa a `no_seleccionado`, la pareja entra a `excluidos` con
    `origen="empresa"`, la vacante sale de `vacantes_resueltas` y el Matching recalcula sin ella;
    el grafo vuelve a detenerse en la aprobación con la lista corta nueva.
    Cubierta: la vacante entra a `vacantes_cubiertas` y el Matching no la vuelve a ver; las
    postulaciones que iban hacia ella pasan a `no_seleccionado` (o a `contratado`, si el evento dice
    con quién se cubrió) y el Matching recalcula, porque esos jóvenes quedan libres para otras vacantes.
    En los dos casos sigue el nodo `avisos`, que avisa a los jóvenes antes de recalcular.
    Cada evento deja una Decision. Uno que no aplica (vacante desconocida, pareja sin postular o
    evento repetido) no cambia nada más: deja un error y vuelve a la aprobación pendiente, si la hay.
    """
    evento = dict(estado.get("evento_empresa") or {})
    tipo, oferta_id = evento.get("tipo"), evento.get("oferta_id")
    perfil_id = evento.get("perfil_id") or None
    nota = str(evento.get("nota") or "").strip()
    oferta = _oferta(estado, oferta_id)
    empresa = _campo(oferta, "empresa") or "la empresa"
    puesto = _campo(oferta, "puesto") or oferta_id
    postulaciones = dict(estado.get("postulaciones") or {})
    clave = clave_postulacion(perfil_id, oferta_id) if perfil_id else ""
    entrada = f"{empresa} sobre {oferta_id}: {tipo}" + (f" {perfil_id}" if perfil_id else "")
    quien = str(evento.get("orientador") or "").strip() or "el tablero"
    razon = f"respuesta de {empresa} registrada por {quien}" + (f": {nota}" if nota else "")

    def sin_efecto(motivo: str) -> Command:
        return Command(
            goto="aprobacion",
            update={
                "evento_empresa": None,
                "respuesta": f"Evento de la empresa sin efecto: {motivo}.",
                "errores": [*(estado.get("errores") or []), f"evento_empresa: {motivo}"],
                **_decision(estado, "evento_empresa", entrada, "sin cambios", motivo),
            },
        )

    if tipo not in EVENTOS_EMPRESA:
        return sin_efecto(f"tipo {tipo!r} no válido; usar {', '.join(EVENTOS_EMPRESA)}")
    if oferta is None:
        return sin_efecto(f"la vacante {oferta_id!r} no está en este caso")
    if oferta_id in (estado.get("vacantes_cubiertas") or []):
        return sin_efecto(f"la vacante {oferta_id} ya estaba cubierta")
    if tipo == "rechaza" and not perfil_id:
        return sin_efecto("falta `perfil_id`: la empresa rechaza a un candidato")
    if perfil_id and postulaciones.get(clave) not in ESTADOS_ANTE_EMPRESA:
        actual = postulaciones.get(clave)
        return sin_efecto(f"{perfil_id} {f'ya está en {actual}' if actual else 'no fue postulado'} para {oferta_id}")

    if tipo == "rechaza":
        no_seleccionados, contratado = [perfil_id], None
        excluidos = list(estado.get("excluidos") or [])
        if not _excluido(estado, perfil_id, oferta_id):
            motivo = f"rechazado por {empresa}" + (f": {nota}" if nota else "")
            momento = str(evento.get("timestamp") or _ahora())
            excluidos.append(
                {"perfil_id": perfil_id, "oferta_id": oferta_id, "origen": "empresa", "motivo": motivo, "timestamp": momento}
            )
        cambios: dict[str, Any] = {
            "excluidos": excluidos,
            "vacantes_resueltas": [v for v in estado.get("vacantes_resueltas") or [] if v != oferta_id],
        }
        salida = f"{perfil_id} → no_seleccionado y excluido de {oferta_id}; la vacante vuelve a pendiente y se recalcula"
        respuesta = f"{empresa} rechazó a {_nombre(estado, perfil_id)} para {puesto}: se recalcula la lista corta sin esa pareja."
    else:
        no_seleccionados = [pid for pid in _postulados_en(postulaciones, oferta_id) if pid != perfil_id]
        contratado = perfil_id
        if contratado:
            postulaciones[clave] = EstadoPostulacion.CONTRATADO
        cambios = {"vacantes_cubiertas": [*(estado.get("vacantes_cubiertas") or []), oferta_id]}
        salida = f"{oferta_id} cubierta: sale del Matching" + (f"; {contratado} → contratado" if contratado else "")
        salida += f"; no_seleccionado: {', '.join(no_seleccionados)}" if no_seleccionados else ""
        respuesta = f"{empresa} cubrió la vacante de {puesto}: sale del Matching y se recalcula."
    for pid in no_seleccionados:
        postulaciones[clave_postulacion(pid, oferta_id)] = EstadoPostulacion.NO_SELECCIONADO

    return Command(
        goto="avisos",
        update={
            **cambios,
            "evento_empresa": None,
            "postulaciones": postulaciones,
            "avisos_pendientes": [
                *(estado.get("avisos_pendientes") or []),
                *aviso_reacomodo(estado, evento, no_seleccionados, contratado),
            ],
            "respuesta": respuesta,
            "reintentos_auditor": 0,
            "devolver_a_matching": False,
            **_decision(estado, "evento_empresa", entrada, salida, razon),
        },
    )


def evento_empresa(
    grafo: Any,
    config: dict,
    oferta_id: str,
    perfil_id: str | None = None,
    tipo: str = "rechaza",
    nota: str = "",
    orientador: str = "",
) -> dict[str, Any]:
    """Registra la respuesta de la empresa en el caso y recalcula (#54). La llama el botón del tablero.

    `tipo`: "rechaza" (exige `perfil_id`, el candidato postulado que la empresa no quiere) o
    "cubierta" (`perfil_id` opcional: con quién se cubrió, que pasa a `contratado`). `orientador`
    es quien registra la respuesta en el tablero.

    Por qué así: es un `grafo.invoke()` con una entrada nueva (`tipo_entrada="evento_empresa"`) sobre
    el mismo `thread_id`. LangGraph mezcla esa entrada con el estado del checkpoint y corre desde
    START; si el caso estaba detenido en un interrupt, esa espera se reemplaza por la que deje esta
    corrida. Así el evento pasa por el router, deja su Decision en un nodo propio y reutiliza
    Matching → Auditor → Formación → aprobación sin rutas nuevas. `update_state` más un `Command`
    obligaría a elegir `as_node` y a repetir esas rutas a mano.

    Devuelve el estado tras la corrida: `respuesta` dice qué pasó (también si el evento no aplicó) y
    `aprobacion_pendiente(grafo, config)` trae la lista corta nueva, si la hay.
    """
    evento = {
        "tipo": tipo,
        "oferta_id": oferta_id,
        "perfil_id": perfil_id,
        "nota": limpiar_entrada(nota),  # sin cédula, teléfono ni dirección en el checkpoint
        "orientador": orientador,
        "timestamp": _ahora(),
    }
    return grafo.invoke({"tipo_entrada": "evento_empresa", "evento_empresa": evento}, config)


# ---------------------------------------------------------------- construcción


NODOS_FASE_1: dict[str, Nodo] = {
    "mercado": stub_mercado,
    "perfil": stub_perfil,
    "matching": stub_matching,
    "auditor": stub_auditor,
    "formacion": stub_formacion,
    "postulacion": stub_postulacion,
    "avisos": stub_avisos,
}


def construir_grafo(nodos: dict[str, Nodo] | None = None, checkpointer: Any = None):
    """Compila el grafo con los agentes reales (`nodos_fase_2()`, Claude vía `puente.llm` por defecto).

    `nodos` reemplaza cualquiera de ellos: `nodos_fase_2(grabado=True)` para la demo sin
    conexión, LLM falsos en los tests o `NODOS_FASE_1` para probar solo la mecánica.
    Por defecto Postulación escribe en PUENTE_DB y PUENTE_OUTBOX, sin Telegram (ver `nodos_fase_2`).
    Sin checkpointer el grafo no puede detenerse en la aprobación; el tablero y la demo
    usan `checkpointer_sqlite()`.
    """
    agentes = {**nodos_fase_2(), **(nodos or {})}
    grafo = StateGraph(EstadoGrafo)
    grafo.add_node("guardrails", nodo_entrada_guardrails)
    grafo.add_node("router", nodo_router)
    grafo.add_node("consulta", nodo_consulta)
    grafo.add_node("mercado", agentes["mercado"])
    grafo.add_node("alerta_oferta", nodo_alerta_oferta)
    grafo.add_node("perfil", agentes["perfil"])
    grafo.add_node("matching", con_reintento_auditor(sin_vacantes_cubiertas(agentes["matching"])))
    grafo.add_node("sin_matches", nodo_sin_matches)
    grafo.add_node("auditor", sin_vacantes_cubiertas(agentes["auditor"]))
    grafo.add_node("formacion", agentes["formacion"])
    grafo.add_node("aprobacion", nodo_aprobacion, destinations=("postulacion", "matching", "aprobacion", END))
    grafo.add_node("postulacion", agentes["postulacion"])
    grafo.add_node("evento_empresa", nodo_evento_empresa, destinations=("avisos", "aprobacion"))
    grafo.add_node("avisos", agentes["avisos"])

    grafo.add_conditional_edges(START, ruta_inicio, ["guardrails", "router", END])
    grafo.add_conditional_edges("guardrails", ruta_guardrails, ["router", END])
    grafo.add_conditional_edges("router", ruta_router, ["mercado", "perfil", "consulta", "evento_empresa"])
    grafo.add_edge("consulta", END)
    grafo.add_conditional_edges("mercado", ruta_post_mercado, ["alerta_oferta", "matching"])
    grafo.add_edge("alerta_oferta", END)
    grafo.add_edge("perfil", "matching")
    grafo.add_conditional_edges("matching", ruta_post_matching, ["auditor", "sin_matches"])
    grafo.add_edge("sin_matches", END)
    grafo.add_conditional_edges(
        "auditor", ruta_post_auditor, {"matching": "matching", "perfil": "perfil", "continuar": "formacion"}
    )
    grafo.add_edge("formacion", "aprobacion")
    grafo.add_edge("avisos", "matching")
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
    # `or`, no el valor por defecto de getenv: el .env copiado de .env.example trae «PUENTE_CHECKPOINTS=»
    # vacío, y Path("") es el directorio actual (sqlite no lo puede abrir).
    ruta = Path(ruta or os.getenv("PUENTE_CHECKPOINTS") or CHECKPOINTS_POR_DEFECTO).resolve()
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


def _preguntar_orientador(leer: Callable[[str], str]) -> str:
    """Nombre de quien decide; la terminal no acepta una decisión sin él.

    El grafo solo lo exige para aprobar y editar, pero la Decision de quitar, rechazar o de la
    respuesta de la empresa también dice quién la tomó.
    """
    nombre = ""
    while not nombre:
        nombre = leer("Orientador(a): ").strip()
    return nombre


def _preguntar_respuesta(solicitud: dict[str, Any], leer: Callable[[str], str]) -> dict[str, Any]:
    """La decisión del orientador o, con la sintaxis de `--auto`, la respuesta de la empresa (#54).

    Para la empresa devuelve `{"evento": ...}`: main() lo registra con `evento_empresa()` en vez de reanudar.
    """
    while True:
        texto = leer(f"Acción ({' / '.join(ACCIONES)}; o la respuesta de la empresa: {SINTAXIS_EVENTO}): ").strip()
        if (evento := _evento_tecleado(texto)) is not None:
            return {"evento": _completar_evento(evento, leer)}
        accion = texto.lower()
        if accion in ACCIONES:
            break
    respuesta: dict[str, Any] = {"accion": accion, "oferta_id": solicitud["oferta_id"]}
    if accion == "quitar":
        respuesta["perfil_id"] = leer("perfil_id a quitar: ").strip()
    respuesta["orientador"] = _preguntar_orientador(leer)
    respuesta["nota"] = leer("Nota (opcional): ").strip()
    if accion == "quitar":
        return respuesta
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


PREFIJO_EVENTO = "empresa_"


def _evento_automatico(paso: str) -> dict[str, Any] | None:
    """Paso de `--auto` que trae la respuesta de la empresa (#54), o None si es una decisión del orientador.

    `empresa_rechaza:perfil_id@oferta_id`, `empresa_cubierta:oferta_id` o `empresa_cubierta:perfil_id@oferta_id`
    (con quién se cubrió). Se aplica cuando le toca, haya o no una aprobación pendiente.
    """
    if not paso.startswith(PREFIJO_EVENTO):
        return None
    tipo, _, destino = paso.removeprefix(PREFIJO_EVENTO).partition(":")
    perfil_id, _, oferta_id = destino.rpartition("@")
    return {"tipo": tipo, "oferta_id": oferta_id, "perfil_id": perfil_id or None}


SINTAXIS_EVENTO = "empresa_rechaza:perfil@oferta · empresa_cubierta:[perfil@]oferta"


def _evento_tecleado(texto: str) -> dict[str, Any] | None:
    """La respuesta de la empresa escrita en la terminal, con la sintaxis de `--auto`; None si no es un evento completo.

    También vale un espacio en vez de los dos puntos (`empresa_rechaza yaritza@of-cajero-vista-alegre`).
    Un evento que no aplica al caso (pareja sin postular, vacante desconocida) sí pasa: lo rechaza el grafo
    y queda en `errores`, igual que desde el tablero.
    """
    paso = texto.strip()
    if ":" not in paso:
        paso = ":".join(paso.split(None, 1))
    evento = _evento_automatico(paso)
    if evento is None:
        return None
    evento = {**evento, "oferta_id": evento["oferta_id"].strip(), "perfil_id": (evento["perfil_id"] or "").strip() or None}
    if evento["tipo"] not in EVENTOS_EMPRESA or not evento["oferta_id"]:
        return None
    if evento["tipo"] == "rechaza" and not evento["perfil_id"]:
        return None
    return evento


def _completar_evento(evento: dict[str, Any], leer: Callable[[str], str]) -> dict[str, Any]:
    """Quién registra la respuesta de la empresa y su nota, como en cualquier decisión del orientador."""
    return {**evento, "orientador": _preguntar_orientador(leer), "nota": leer("Nota (opcional): ").strip()}


def _preguntar_evento_final(estado: dict, leer: Callable[[str], str]) -> dict[str, Any] | None:
    """Al terminar el flujo interactivo la empresa todavía puede responder sobre quien ya se postuló (#54).

    Solo pregunta si hay alguna postulación ante la empresa. Enter, o el fin de la entrada, termina.
    """
    if not any(e in ESTADOS_ANTE_EMPRESA for e in (estado.get("postulaciones") or {}).values()):
        return None
    while True:
        try:
            texto = leer(f"¿La empresa respondió? ({SINTAXIS_EVENTO}; Enter para terminar): ").strip()
        except EOFError:
            return None
        if not texto:
            return None
        if (evento := _evento_tecleado(texto)) is not None:
            return _completar_evento(evento, leer)


def _aplicar_evento(grafo: Any, config: dict, evento: dict[str, Any], escribir: Callable[[str], None]) -> dict:
    """Registra el evento y muestra qué pasó. Sin `orientador` ni `nota` (pasos de `--auto`) usa los de la demo."""
    valores = grafo.get_state(config).values
    antes = list(valores.get("avisos_pendientes") or [])
    decisiones_antes = len(valores.get("decisiones") or [])
    sobre = f"{evento['perfil_id']} en " if evento["perfil_id"] else ""
    escribir(f"\n↺  Evento de la empresa · {evento['tipo']} · {sobre}{evento['oferta_id']}")
    final = evento_empresa(
        grafo,
        config,
        evento["oferta_id"],
        evento["perfil_id"],
        tipo=evento["tipo"],
        nota=evento.get("nota", "demo automática"),
        orientador=evento.get("orientador", "Orientadora Ficticia"),
    )
    escribir(f"   {final.get('respuesta')}")
    for decision in (final.get("decisiones") or [])[decisiones_antes:]:
        if decision.agente == "avisos":
            escribir(f"   Aviso enviado · {decision.entrada_resumen} · {decision.salida_resumen}")
    for aviso in final.get("avisos_pendientes") or []:
        if aviso not in antes:
            escribir(f"   Aviso pendiente para {aviso['para']}: {aviso['texto']}")
    return final


def _recortar(texto: str, largo: int = 110) -> str:
    texto = " ".join(str(texto).split())
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


def _traslado(match: Any) -> str:
    """« · 20 min · pasaje $1.50/día ($33.00/mes)» con el desglose del solver; vacío si no hay match."""
    desglose = _campo(match, "desglose") or {}
    partes = []
    if desglose.get("minutos_traslado") is not None:
        partes.append(f"{desglose['minutos_traslado']:g} min")
    if desglose.get("pasaje_diario") is not None:
        mensual = desglose.get("pasaje_mensual")
        partes.append(f"pasaje ${desglose['pasaje_diario']:.2f}/día" + (f" (${mensual:.2f}/mes)" if mensual is not None else ""))
    return "".join(f" · {p}" for p in partes)


def _linea_evidencia(perfil: Any, oferta: Any) -> str | None:
    """La evidencia literal de la primera habilidad que pide la vacante (o de la primera del perfil), con las
    demás habilidades pedidas que citan la misma frase."""
    frases = {
        _campo(h, "skill_id"): _campo(h, "evidencia")
        for h in _campo(perfil, "habilidades") or []
        if _campo(h, "evidencia")
    }
    orden = [s for s in _campo(oferta, "habilidades") or [] if s in frases] or list(frases)
    if not orden:
        return None
    frase = frases[orden[0]]
    return f"Evidencia ({', '.join(s for s in orden if frases[s] == frase)}): «{_recortar(frase)}»"


def _descartes(estado: dict, oferta_id: str, en_lista: set[str]) -> list[str]:
    """Quién no entra a la lista corta de la vacante y por qué: el motivo del solver o la exclusión.

    El solver deja el motivo en `ofertas_descartadas` de cada match del perfil (o en `motivo_descarte` del
    match no viable); quitar un candidato o el rechazo de la empresa (#54) lo deja en `excluidos`.
    """
    motivos: dict[str, str] = {}
    for m in estado.get("matches") or []:
        desglose = _campo(m, "desglose") or {}
        motivo = (desglose.get("ofertas_descartadas") or {}).get(oferta_id)
        if not motivo and _campo(m, "oferta_id") == oferta_id:
            motivo = desglose.get("motivo_descarte")
        if motivo:
            motivos.setdefault(_campo(m, "perfil_id"), motivo)
    for e in estado.get("excluidos") or []:
        if e.get("oferta_id") == oferta_id:
            motivos[e.get("perfil_id")] = e.get("motivo") or "quitado por el orientador"
    return [f"{_nombre(estado, pid)} ({pid}): {motivo}" for pid, motivo in motivos.items() if pid not in en_lista]


def _resumen_auditor(estado: dict, solicitud: dict[str, Any]) -> str | None:
    """El resumen del Auditor (#50): `auditoria` del estado o, si no está, la línea «Resumen» del reporte."""
    auditoria = estado.get("auditoria") or {}
    if auditoria.get("resumen_corto"):
        partes = [auditoria["resumen_corto"]]
        if auditoria.get("porcentaje_identico_global") is not None:
            partes.append(f"consistencia {auditoria['porcentaje_identico_global']:.1f}%")
        if auditoria.get("corridas_linea_base"):
            partes.append(f"línea base de {auditoria['corridas_linea_base']} corridas")
        if auditoria.get("accion_grafo") == "orientador":
            partes.append("tope de recálculo alcanzado: decide el orientador")
        return " · ".join(partes)
    reporte = solicitud.get("reporte_auditor")
    for linea in reporte.splitlines() if isinstance(reporte, str) else []:
        if "Resumen:" in linea:
            return linea.split("Resumen:", 1)[1].strip(" *")
    return None


def _mostrar(solicitud: dict[str, Any], escribir: Callable[[str], None], estado: dict | None = None) -> None:
    """La aprobación pendiente con el porqué de cada candidato, compacto.

    Por candidato: traslado y pasaje del solver, una línea de evidencia literal y la explicación del
    Matching. Por vacante: los descartes con su motivo y el resumen del Auditor. Todo sale del estado
    del grafo (`estado`, el del checkpoint); sin él solo se muestra lo que trae la solicitud.
    """
    estado = estado or {}
    oferta = solicitud["oferta"]
    oferta_id = solicitud["oferta_id"]
    escribir(f"\n⏸  Aprobación pendiente · {oferta_id} · {oferta['puesto']} en {oferta['empresa']}")
    if oferta.get("requiere_confirmacion"):
        escribir(f"   Por confirmar con la empresa: {', '.join(oferta['requiere_confirmacion'])}")
    matches = {_campo(m, "perfil_id"): m for m in _lista_corta_de(estado, oferta_id)}
    perfiles = {_campo(p, "id"): p for p in estado.get("perfiles") or []}
    for c in solicitud["candidatos"]:
        brechas = f" · brechas: {', '.join(c['brechas'])}" if c["brechas"] else ""
        ya = f" · postulación: {c['estado_postulacion']}" if c.get("estado_postulacion") else ""
        traslado = _traslado(matches.get(c["perfil_id"]))
        escribir(f"   - {c['nombre']} ({c['perfil_id']}) puntaje {c['puntaje']}{traslado}{brechas}{ya}")
        evidencia = _linea_evidencia(perfiles.get(c["perfil_id"]), _oferta(estado, oferta_id))
        if evidencia:
            escribir(f"     {evidencia}")
        # La cola « | Descartes: …» del solver habla de las otras vacantes del joven; los de esta van abajo
        explicacion = (c.get("explicacion") or "").split(" | Descartes:")[0].strip()
        if explicacion:
            escribir(f"     Matching: {explicacion}")
        for ruta in c.get("rutas_formacion") or []:
            cursos = ", ".join(curso["curso"] for curso in ruta.get("cursos_seleccionados") or [])
            if cursos:
                escribir(f"     Formación sugerida: {cursos}")
    descartes = _descartes(estado, oferta_id, {c["perfil_id"] for c in solicitud["candidatos"]})
    if descartes:
        escribir("   Descartes:")
        for descarte in descartes:
            escribir(f"     - {descarte}")
    resumen = _resumen_auditor(estado, solicitud)
    if resumen:
        escribir(f"   Auditor: {resumen}")
    if solicitud["alertas_auditor"]:
        escribir(f"   Alertas del Auditor: {'; '.join(solicitud['alertas_auditor'])}")
    if solicitud["error"]:
        escribir(f"   ⚠ {solicitud['error']}")
    escribir(f"   Mensaje sugerido: {solicitud['mensaje_sugerido']}")


def _foto_outbox(outbox: Path) -> dict[str, int]:
    """{archivo: mtime} del outbox, para saber qué dejó esta corrida (el CV de una pareja se reescribe)."""
    if not outbox.is_dir():
        return {}
    return {p.name: p.stat().st_mtime_ns for p in outbox.iterdir() if p.is_file()}


def _mostrar_salidas(
    final: dict, base: Path, outbox: Path, antes: dict[str, int], telegram: bool, escribir: Callable[[str], None]
) -> None:
    """Dónde quedó lo que generó Postulación en esta corrida: la base SQLite y los archivos del outbox."""
    if telegram:
        canal = "Telegram activado (--telegram)"
    else:
        canal = "Telegram apagado: los envíos quedan en el outbox (--telegram los envía)"
    escribir(f"\nSalidas · {canal}")
    escribir(f"   Base SQLite (PUENTE_DB): {base.resolve()}")
    nuevos = sorted(n for n, marca in _foto_outbox(outbox).items() if antes.get(n) != marca)
    if nuevos:
        escribir(f"   Outbox (PUENTE_OUTBOX): {outbox.resolve()} · {len(nuevos)} archivo(s) de esta corrida:")
        for nombre in nuevos:
            escribir(f"   - {nombre}")
        return
    escribir(f"   Outbox (PUENTE_OUTBOX): {outbox.resolve()} · sin archivos nuevos en esta corrida")
    if any(d.agente == "postulacion" and d.razon.startswith("Ya postulada") for d in final.get("decisiones") or []):
        escribir("   Esas postulaciones ya estaban enviadas en esa base; para repetir la demo desde cero usa otra PUENTE_DB.")


def _agentes_en_orden(decisiones: list) -> str:
    """Los agentes de las decisiones en orden, con las repeticiones seguidas juntas: a → b → postulacion ×9."""
    tramos: list[list] = []
    for d in decisiones:
        if tramos and tramos[-1][0] == d.agente:
            tramos[-1][1] += 1
        else:
            tramos.append([d.agente, 1])
    return " → ".join(agente if n == 1 else f"{agente} ×{n}" for agente, n in tramos)


def main(argv: list[str] | None = None, leer: Callable[[str], str] = input, escribir: Callable[[str], None] = print) -> dict:
    parser = argparse.ArgumentParser(prog="python -m puente.graph", description="Demo del grafo de Puente (#10)")
    parser.add_argument("--demo", default="yaritza", choices=["yaritza"], help="caso de la demo")
    parser.add_argument("--caso", default=str(CASO_YARITZA), help="JSON del caso")
    parser.add_argument("--checkpoints", default=None, help="archivo SQLite de checkpoints (PUENTE_CHECKPOINTS)")
    parser.add_argument("--thread", default=None, help="thread_id; por defecto <id del caso>-<hora>")
    parser.add_argument(
        "--auto",
        default=None,
        help=(
            "decisiones sin preguntar, p. ej. 'quitar:maria,aprobar,rechazar'; la respuesta de la empresa (#54) es "
            "otro paso: 'empresa_rechaza:yaritza@of-cajero-vista-alegre' o 'empresa_cubierta:of-ayudante-cocina-chorrera'. "
            "Sin --auto, esa misma respuesta se escribe en la pregunta de acción o al terminar el flujo"
        ),
    )
    parser.add_argument(
        "--llm",
        default="grabado",
        choices=["grabado", "claude"],
        help=(
            "grabado: respuestas guardadas en el caso, sin red (por defecto); claude: llamadas reales con "
            "ANTHROPIC_API_KEY en .env vía puente.llm (LLM_MODO=grabar las guarda, LLM_MODO=reproducir las lee sin red)"
        ),
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help=(
            "envía de verdad por Telegram con TELEGRAM_BOT_TOKEN y los chats de .env; sin esta opción nada sale a la red "
            "y el CV, el .ics y los mensajes quedan en PUENTE_OUTBOX (por defecto outbox/)"
        ),
    )
    args = parser.parse_args(argv)
    # Con la salida redirigida en Windows (cp1252) no existen ⏸, ⚠ ni →: se reemplazan en vez de cortar la demo.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    ocultar_trazas_langsmith()

    caso_id, estado = cargar_caso(args.caso)
    estado = limpiar_entrada(estado)  # sin cédula, teléfono ni dirección en el checkpoint
    thread_id = args.thread or f"{caso_id}-{datetime.now(UTC):%Y%m%d%H%M%S}"
    # Después de load_dotenv: PUENTE_DB y PUENTE_OUTBOX pueden venir de .env
    base, outbox = db_por_entorno(), outbox_por_entorno()
    nodos = nodos_fase_2(grabado=args.llm == "grabado", ruta_db=base, outbox=outbox, telegram=args.telegram)
    grafo = construir_grafo(nodos=nodos, checkpointer=checkpointer_sqlite(args.checkpoints))
    config = config_caso(thread_id)
    pasos = [p.strip() for p in args.auto.split(",")] if args.auto else None
    escribir(f"Caso {args.demo} · thread_id={thread_id} · LLM {args.llm}")
    antes = _foto_outbox(outbox)

    final = grafo.invoke(estado, config)
    while True:
        solicitud = aprobacion_pendiente(grafo, config)
        if solicitud is not None:
            _mostrar(solicitud, escribir, grafo.get_state(config).values)
        if pasos and (evento := _evento_automatico(pasos[0])) is not None:
            pasos.pop(0)
            final = _aplicar_evento(grafo, config, evento, escribir)
            continue
        if solicitud is None:
            # Interactivo: la empresa todavía puede responder sobre quien ya se postuló (#54)
            evento = _preguntar_evento_final(grafo.get_state(config).values, leer) if pasos is None else None
            if evento is None:
                break
            final = _aplicar_evento(grafo, config, evento, escribir)
            continue
        if pasos is not None:
            if not pasos:
                escribir("Sin más decisiones automáticas; el caso queda detenido en la aprobación.")
                _mostrar_salidas(final, base, outbox, antes, args.telegram, escribir)
                return final
            respuesta = _respuesta_automatica(pasos.pop(0), solicitud)
            escribir(f"   → {respuesta['accion']}")
        else:
            respuesta = _preguntar_respuesta(solicitud, leer)
            if "evento" in respuesta:
                final = _aplicar_evento(grafo, config, respuesta["evento"], escribir)
                continue
        final = grafo.invoke(Command(resume=respuesta), config)

    escribir("\nFlujo terminado.")
    if final.get("respuesta"):
        escribir(f"Respuesta: {final['respuesta']}")
    if final.get("aprobacion"):
        escribir(f"Última aprobación: {json.dumps(final['aprobacion'], ensure_ascii=False)}")
    escribir("Decisiones: " + _agentes_en_orden(final.get("decisiones", [])))
    # Un agente que falla (por ejemplo, falta una grabación en LLM_MODO=reproducir) no corta el flujo:
    # deja su error en el estado y la demo debe mostrarlo, o el caso parece completo sin serlo.
    errores = final.get("errores") or []
    if errores:
        escribir(f"⚠  Errores ({len(errores)}):")
        for error in errores:
            escribir(f"   - {error}")
    from puente.llm import uso_por_agente

    uso = uso_por_agente()
    if uso:
        llamadas = sum(f["llamadas"] for f in uso.values())
        reproducidas = sum(f["reproducidas"] for f in uso.values())
        costo = sum(f["costo_usd"] for f in uso.values())
        escribir(f"LLM: {llamadas} llamada(s), {reproducidas} reproducida(s), costo estimado US${costo:.4f}")
    _mostrar_salidas(final, base, outbox, antes, args.telegram, escribir)
    return final


if __name__ == "__main__":
    main(sys.argv[1:])
