"""Grafo LangGraph: guardrails, router, agentes, aprobación del orientador con interrupt() y trazas. Issue #10.

Fase 2: los nodos son los agentes reales de main (Mercado #5/#43, Perfil #6/#44, Matching
con el solver CP-SAT #4, Auditor v2 #50 y Formación #14). Postulación sigue como stub
hasta #12. Los stubs de la Fase 1 quedan en `NODOS_FASE_1` para probar la mecánica del grafo.
El LLM de Mercado y Perfil es inyectable (`nodos_fase_2`); los tests y la demo sin conexión
usan respuestas grabadas, nunca la red.

    START → guardrails ─rechazo→ END
                └→ router ─oferta→ mercado ─bloqueada→ alerta_oferta → END
                     │                └→ matching
                     ├─perfil→ perfil → matching
                     └─consulta→ consulta → END
    matching ─sin matches→ sin_matches → END
        └→ auditor ─sesgo (una vez)→ matching, o perfil si el sesgo viene de la lectura del perfil
              └→ formacion → aprobacion (interrupt)
    aprobacion: aprobar/editar → postulacion · quitar → matching · rechazar → END
    postulacion → aprobacion si quedan vacantes por decidir, si no END

Contrato con el tablero (#11):
    grafo = construir_grafo(checkpointer=checkpointer_sqlite())
    config = config_caso(perfil_id)
    grafo.invoke(estado, config)                      # corre hasta el interrupt
    aprobacion_pendiente(grafo, config)              # o grafo.get_state(config)
    grafo.invoke(Command(resume={...}), config)       # ver RESPUESTA_APROBACION

LangSmith se activa solo con LANGSMITH_TRACING=true y LANGSMITH_API_KEY en `.env`; cada nodo
aparece como un run con su nombre (guardrails, router, mercado, perfil, matching, auditor,
formacion, aprobacion, postulacion) bajo el run `puente:<thread_id>`.
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
from puente.agents.perfil import llm_gemini, nodo_perfil
from puente.guardrails import (
    RESPUESTA_FUERA_DE_ALCANCE,
    limpiar_entrada,
    nodo_guardrails,
    ocultar_trazas_langsmith,
)
from puente.rules.discriminacion import esta_bloqueada, revisar_texto
from puente.rules.taxonomia import RUTA_TAXONOMIA, cargar_taxonomia
from puente.schemas import (
    REQUIERE_CONFIRMACION,
    Decision,
    EstadoPuente,
    Match,
    Oferta,
    OfertaExtraida,
    Perfil,
    agregar_decision,
)
from puente.solver.asignacion import ConfigSolver

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


def vacantes_pendientes(estado: dict) -> list[str]:
    """Vacantes con lista corta que el orientador todavía no decidió, en el orden de `ofertas`.

    Ese orden no depende del solver: tras quitar un candidato, el orientador vuelve a ver la misma vacante.
    """
    resueltas = set(estado.get("vacantes_resueltas") or [])
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
        **_decision(estado, "router", f"tipo_entrada={estado.get('tipo_entrada')!r}", tipo, origen),
    }


def ruta_router(estado: dict) -> str:
    return {"oferta": "mercado", "perfil": "perfil"}.get(estado.get("tipo_entrada", ""), "consulta")


def nodo_consulta(estado: dict) -> dict:
    """MVP: las consultas libres no se atienden todavía."""
    return {
        "respuesta": RESPUESTA_FUERA_DE_ALCANCE,
        **_decision(estado, "consulta", "consulta libre", RESPUESTA_FUERA_DE_ALCANCE, "fuera de alcance en el MVP"),
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

    Igual que con Gemini, Perfil verifica cada habilidad contra la taxonomía y la evidencia literal.
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
            return nodo_perfil(estado, llm=llm or llm_gemini, taxonomia=tax)
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
    texto = ", ".join(f"{p} → {o}" for p, o in sorted(excluidos))
    if decisiones and isinstance(decisiones[-1], Decision):
        ultima = decisiones[-1]
        decisiones[-1] = ultima.model_copy(
            update={
                "salida_resumen": f"{len(lista_corta)} candidato(s) en lista corta sin las parejas excluidas",
                "razon": f"{ultima.razon} Parejas excluidas por el orientador, filtradas en el grafo hasta #48: {texto}.",
            }
        )
    return {**cambios, "matches": matches, "lista_corta": lista_corta, "decisiones": decisiones}


def nodo_auditor_grafo(estado: dict) -> dict:
    """Auditor v2 (#50) con los perfiles en la forma que acepta el solver; el estado conserva los originales."""
    vista = {**estado, "perfiles": [_perfil_para_solver(p) for p in estado.get("perfiles") or []]}
    cambios = nodo_auditor(vista)
    return {k: v for k, v in cambios.items() if k not in ("perfiles", "ofertas")}


def nodos_fase_2(
    *,
    llm_perfil: Callable[[str], str] | None = None,
    extraer_oferta: Callable[[Any], Any] | None = None,
    grabado: bool = False,
    taxonomia: dict[str, dict] | None = None,
) -> dict[str, Nodo]:
    """Los agentes reales. Sin `llm_perfil` ni `extraer_oferta` usan Gemini; `grabado=True` usa la
    `respuesta_llm` guardada en cada entrada (demo sin conexión). Postulación sigue como stub (#12)."""
    return {
        "mercado": crear_nodo_mercado_grafo(extraer_oferta, grabado=grabado, taxonomia=taxonomia),
        "perfil": crear_nodo_perfil_grafo(llm_perfil, grabado=grabado, taxonomia=taxonomia),
        "matching": nodo_matching_grafo,
        "auditor": nodo_auditor_grafo,
        "formacion": nodo_formacion,
        "postulacion": stub_postulacion,
    }


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
    rutas = [r for r in estado.get("rutas_formacion") or [] if r.get("oferta_id") == oferta_id]
    candidatos = [
        {
            "perfil_id": m.perfil_id,
            "nombre": _campo(perfiles.get(m.perfil_id), "nombre", m.perfil_id),
            "puntaje": m.puntaje,
            "brechas": list(m.brechas),
            "explicacion": m.explicacion,
            "requiere_confirmacion": list(m.desglose.get(REQUIERE_CONFIRMACION) or []),
            "rutas_formacion": [r for r in rutas if r.get("perfil_id") == m.perfil_id],
        }
        for m in _lista_corta_de(estado, oferta_id)
    ]
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
    """Compila el grafo con los agentes reales (`nodos_fase_2()`, Gemini por defecto).

    `nodos` reemplaza cualquiera de ellos: `nodos_fase_2(grabado=True)` para la demo sin
    conexión, LLM falsos en los tests o `NODOS_FASE_1` para probar solo la mecánica.
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
    grafo.add_conditional_edges(
        "auditor", ruta_post_auditor, {"matching": "matching", "perfil": "perfil", "continuar": "formacion"}
    )
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
    if oferta.get("requiere_confirmacion"):
        escribir(f"   Por confirmar con la empresa: {', '.join(oferta['requiere_confirmacion'])}")
    for c in solicitud["candidatos"]:
        brechas = f" · brechas: {', '.join(c['brechas'])}" if c["brechas"] else ""
        escribir(f"   - {c['nombre']} ({c['perfil_id']}) puntaje {c['puntaje']}{brechas}")
        for ruta in c.get("rutas_formacion") or []:
            cursos = ", ".join(curso["curso"] for curso in ruta.get("cursos_seleccionados") or [])
            if cursos:
                escribir(f"     Formación sugerida: {cursos}")
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
    parser.add_argument("--auto", default=None, help="decisiones sin preguntar, p. ej. 'quitar:maria,aprobar,rechazar'")
    parser.add_argument(
        "--llm",
        default="grabado",
        choices=["grabado", "gemini"],
        help="grabado: respuestas guardadas en el caso, sin red (por defecto); gemini: GOOGLE_API_KEY en .env",
    )
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    ocultar_trazas_langsmith()

    caso_id, estado = cargar_caso(args.caso)
    estado = limpiar_entrada(estado)  # sin cédula, teléfono ni dirección en el checkpoint
    thread_id = args.thread or f"{caso_id}-{datetime.now(UTC):%Y%m%d%H%M%S}"
    nodos = nodos_fase_2(grabado=args.llm == "grabado")
    grafo = construir_grafo(nodos=nodos, checkpointer=checkpointer_sqlite(args.checkpoints))
    config = config_caso(thread_id)
    pasos = [p.strip() for p in args.auto.split(",")] if args.auto else None
    escribir(f"Caso {args.demo} · thread_id={thread_id} · LLM {args.llm}")

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
