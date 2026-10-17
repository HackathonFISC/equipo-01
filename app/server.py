"""Servidor web ASGI para el Tablero del Orientador conectado al grafo real de Puente (Issues #11, #33 y #49).

Toda la lógica de negocio vive en `puente/`:
- Ejecución y pausa HITL (`interrupt` / `Command(resume=...)`): `puente.graph`
- Reglas legales (Ley 11 de 2005): `puente.rules.discriminacion`
- Persistencia SQLite (`perfiles`, `ofertas`, `postulaciones`, `decisiones`): `puente.tools.db`
- Envío de postulaciones (`PDF`, `.ics`, `Telegram/outbox`): `puente.agents.postulacion`
"""

from __future__ import annotations

import copy
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from puente.agents.auditor import nodo_auditor
from puente.agents.formacion import nodo_formacion
from puente.agents.postulacion import consentimiento_otorgado, consentimientos_para, postular
from puente.graph import (
    aprobacion_pendiente,
    config_caso,
    construir_grafo,
    nodo_matching_grafo,
    stub_mercado,
    stub_perfil,
)
from puente.rules.discriminacion import esta_bloqueada, revisar_texto
from puente.schemas import (
    SIN_OFERTA,
    Consentimiento,
    Decision,
    EstadoPostulacion,
    HabilidadPerfil,
    Horario,
    Match,
    Oferta,
    Perfil,
)
from puente.tools import db

RUTA_APP = Path(__file__).resolve().parent
RUTA_STATIC = RUTA_APP / "static"
RUTA_RAIZ = RUTA_APP.parent
RUTA_DATA = RUTA_RAIZ / "data"
RUTA_OUTBOX = RUTA_RAIZ / "outbox"
RUTA_DB_TABLERO = Path(tempfile.gettempdir()) / "puente_tablero.db"

PATRON_ID_SEGURO = re.compile(r"^[A-Za-z0-9_-]+$")

ESTADOS_POSTULACION: tuple[str, ...] = tuple(e.value for e in EstadoPostulacion)


class ErrorValidacionTablero(ValueError):
    """Error de validación de entrada o de transición de estado en el tablero (HTTP 422)."""


class AccionOrientadorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accion: Literal["aprobar", "editar", "quitar_candidato", "quitar", "rechazar"]
    perfil_id: str = Field(min_length=1, max_length=80)
    oferta_id: str = Field(min_length=1, max_length=80)
    nota_edicion: str = Field(default="", max_length=500)
    mensaje_empresa: str = Field(default="", max_length=1000)
    orientador: str = Field(default="Orientadora Ana Gómez", min_length=2, max_length=120)
    consentimiento_joven: bool = Field(
        default=True,
        description="Casilla 'el joven consintió' confirmada por el orientador (#49)",
    )
    consentidos: list[str] | None = Field(
        default=None,
        description="Lista explícita de perfil_id con consentimiento confirmado (#10, #49)",
    )

    @field_validator("perfil_id", "oferta_id")
    @classmethod
    def _validar_id_seguro(cls, valor: str) -> str:
        limpio = valor.strip()
        if not PATRON_ID_SEGURO.fullmatch(limpio):
            raise ValueError(
                f"ID inválido '{valor}': solo se permiten caracteres [A-Za-z0-9_-]"
            )
        return limpio


class DesbloquearOfertaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oferta_id: str = Field(min_length=1, max_length=80)
    motivo: str = Field(min_length=3, max_length=500)
    orientador: str = Field(default="Orientadora Ana Gómez", min_length=2, max_length=120)

    @field_validator("oferta_id")
    @classmethod
    def _validar_id_seguro(cls, valor: str) -> str:
        limpio = valor.strip()
        if not PATRON_ID_SEGURO.fullmatch(limpio):
            raise ValueError(
                f"ID inválido '{valor}': solo se permiten caracteres [A-Za-z0-9_-]"
            )
        return limpio


class NuevaOfertaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    puesto: str = Field(min_length=2, max_length=120)
    empresa: str = Field(min_length=2, max_length=120)
    corregimiento: str = Field(min_length=2, max_length=80)
    salario_mensual: float = Field(gt=0, le=50000)
    requisitos_texto: str = Field(min_length=1, max_length=10000)
    habilidades: list[str] = Field(default_factory=lambda: ["atencion_cliente"], min_length=1)


class NuevoPerfilRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=2, max_length=80)
    edad: int = Field(ge=18, le=29)
    corregimiento: str = Field(min_length=2, max_length=80)
    salario_minimo: float = Field(ge=0, le=50000)
    presupuesto_pasaje_diario: float = Field(ge=0, le=500)
    relato_experiencia: str = Field(min_length=3, max_length=5000)
    habilidades: list[str] = Field(
        default_factory=lambda: ["atencion_cliente", "manejo_caja"], min_length=1
    )


class CargarDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: Literal["oferta", "perfil"]
    item_id: str = Field(min_length=1, max_length=200)


class SimularSesgoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activar: bool


def _slug_seguro(texto: str, prefijo: str = "ITEM") -> str:
    """Convierte un texto en un slug seguro [A-Z0-9-] sin caracteres especiales."""
    sin_tildes = texto.strip().upper().translate(str.maketrans("ÁÉÍÓÚÑ", "AEIOUN"))
    limpio = re.sub(r"[^A-Z0-9]+", "-", sin_tildes).strip("-")
    return limpio[:24] if limpio else prefijo


def _ruta_segura_en_outbox(base_outbox: Path, nombre_archivo: str) -> Path:
    """Construye una ruta dentro de outbox/ y verifica con resolve().is_relative_to()."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", nombre_archivo) or ".." in nombre_archivo:
        raise ErrorValidacionTablero(f"Nombre de archivo no permitido: {nombre_archivo}")
    base_resuelta = base_outbox.resolve()
    destino = (base_resuelta / nombre_archivo).resolve()
    if not destino.is_relative_to(base_resuelta):
        raise ErrorValidacionTablero("Intento de escritura fuera de outbox/ bloqueado.")
    return destino


def _serializar_decision(d: Any) -> dict[str, Any]:
    if isinstance(d, Decision):
        return d.model_dump()
    if isinstance(d, dict):
        return dict(d)
    return {
        "timestamp": getattr(d, "timestamp", datetime.now(timezone.utc).isoformat()),
        "agente": getattr(d, "agente", "sistema"),
        "entrada_resumen": getattr(d, "entrada_resumen", ""),
        "salida_resumen": getattr(d, "salida_resumen", ""),
        "razon": getattr(d, "razon", ""),
    }


def _conectar_db_estado(estado: dict[str, Any]) -> sqlite3.Connection:
    """Abre la conexión SQLite asociada al estado del tablero (`puente.tools.db`)."""
    ruta_db = estado.get("_db_path") or os.getenv("PUENTE_DB") or str(RUTA_DB_TABLERO)
    estado["_db_path"] = str(ruta_db)
    return db.conectar(ruta_db)


def _registrar_decision_db(estado: dict[str, Any], decision: Decision | dict[str, Any]) -> dict[str, Any]:
    """Guarda una Decision en SQLite (`decisiones`) y en la lista en memoria del estado."""
    serializada = _serializar_decision(decision)
    conn = _conectar_db_estado(estado)
    try:
        db.registrar_decision(conn, serializada)
    finally:
        conn.close()
    estado.setdefault("decisiones", []).append(serializada)
    return serializada


def _leer_decisiones_db(estado: dict[str, Any]) -> list[dict[str, Any]]:
    """Lee todas las filas de la tabla `decisiones` en SQLite (`puente.tools.db`)."""
    conn = _conectar_db_estado(estado)
    try:
        filas = conn.execute(
            "SELECT agente, entrada_resumen, salida_resumen, razon, timestamp FROM decisiones ORDER BY id ASC"
        ).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


def _evaluar_reglas_discriminacion(requisitos_texto: str) -> tuple[bool, list[dict[str, str]]]:
    """Delega 100% la revisión legal a `puente.rules.discriminacion` (Ley 11 de 2005)."""
    hallazgos = revisar_texto(requisitos_texto)
    alertas_mod = [
        {
            "frase": h.frase,
            "regla": f"Ley 11 de 2005 ({h.regla}): {h.fundamento}"
            if "Ley 11" in h.fundamento
            else f"Alerta Ley 11 de 2005 ({h.regla}): {h.fundamento}",
            "tipo": "bloqueo" if h.bloquea else "alerta",
        }
        for h in hallazgos
    ]
    return esta_bloqueada(hallazgos), alertas_mod


def _construir_modelos_desde_estado(
    estado: dict[str, Any],
) -> tuple[list[Perfil], dict[str, dict[str, Any]], list[Oferta], dict[str, dict[str, Any]]]:
    perfiles_dicts = estado.get("perfiles", [])
    ofertas_dicts = estado.get("ofertas", [])

    modelos_perfiles: list[Perfil] = []
    mapa_perfiles: dict[str, dict[str, Any]] = {}
    for p in perfiles_dicts:
        mapa_perfiles[p["id"]] = p
        habs_modelo = [
            HabilidadPerfil(
                skill_id=h["skill_id"] if isinstance(h, dict) else str(h),
                evidencia=h.get("evidencia", "") if isinstance(h, dict) else "",
            )
            for h in p.get("habilidades", [])
        ]
        edad_valida = max(18, min(29, int(p.get("edad", 20))))
        modelos_perfiles.append(
            Perfil(
                id=p["id"],
                nombre=p["nombre"],
                genero=p.get("genero"),
                edad=edad_valida,
                corregimiento=p["corregimiento"],
                horario_libre=[
                    Horario(
                        dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                        hora_inicio="07:00",
                        hora_fin="17:30",
                    )
                ],
                horario_clases=[
                    Horario(
                        dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                        hora_inicio="18:00",
                        hora_fin="22:00",
                    )
                ]
                if "noche" in str(p.get("horario_libre", "")).lower()
                else [],
                salario_minimo=float(p.get("salario_minimo", 600.0)),
                presupuesto_pasaje_diario=float(p.get("presupuesto_pasaje_diario", 3.0)),
                habilidades=habs_modelo,
            )
        )

    modelos_ofertas: list[Oferta] = []
    mapa_ofertas: dict[str, dict[str, Any]] = {}
    for o in ofertas_dicts:
        mapa_ofertas[o["id"]] = o
        sal_raw = o.get("salario_mensual")
        modelos_ofertas.append(
            Oferta(
                id=o["id"],
                puesto=o["puesto"],
                empresa=o["empresa"],
                corregimiento=o["corregimiento"],
                salario_mensual=float(sal_raw) if sal_raw is not None else None,
                horario=Horario(
                    dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                    hora_inicio="08:00",
                    hora_fin="17:00",
                )
                if o.get("horario") is not None
                else None,
                habilidades=list(o.get("habilidades", [])),
                requisitos_texto=o.get("requisitos_texto", ""),
                alertas=[
                    a["frase"] if isinstance(a, dict) else str(a)
                    for a in o.get("alertas", [])
                ],
                bloqueada=bool(o.get("bloqueada", False)),
                fuente=o.get("fuente", "datos de ejemplo (sintéticos)"),
            )
        )
    return modelos_perfiles, mapa_perfiles, modelos_ofertas, mapa_ofertas


def _calcular_metricas_impacto(estado: dict[str, Any]) -> dict[str, Any]:
    """Obtiene métricas de impacto desde puente.metricas (#47) y métricas operativas del estado real."""
    perfiles = estado.get("perfiles", [])
    ofertas = estado.get("ofertas", [])
    matches = estado.get("matches", [])
    descartados = estado.get("descartados", [])
    postulaciones = estado.get("postulaciones", [])
    consentimientos = estado.get("consentimientos", [])
    enviadas = [p for p in postulaciones if p.get("estado") == EstadoPostulacion.ENVIADO.value]
    con_ruta = [m for m in matches if m.get("cursos_recomendados")]

    base_metricas: dict[str, Any] = {}
    try:
        from puente.metricas import calcular_metricas

        res = calcular_metricas(matches, perfiles, ofertas)
        if isinstance(res, dict):
            base_metricas.update(res)
    except Exception:
        pass

    base_metricas.update(
        {
            "referencia_inec": {
                "fuente": "INEC Panamá · Encuesta de Mercado Laboral",
                "desempleo_juvenil_pct": 16.8,
                "informalidad_juvenil_pct": 48.2,
                "nota": "Cifras contextuales de referencia nacional; las métricas operativas corresponden al lote sintético de la demo.",
            },
            "operativas_demo": {
                "jovenes_atendidos": len(perfiles),
                "ofertas_evaluadas": len(ofertas),
                "ofertas_bloqueadas_ley11": sum(1 for o in ofertas if o.get("bloqueada")),
                "matches_viables_cpsat": len(matches),
                "pares_descartados_con_motivo": len(descartados),
                "rutas_formacion_inadeh": len(con_ruta),
                "consentimientos_confirmados": len(consentimientos),
                "postulaciones_enviadas": len(enviadas),
            },
        }
    )
    return base_metricas


def recalcular_pipeline_tablero(
    estado: dict[str, Any],
    *,
    registrar_trazas: bool = True,
) -> dict[str, Any]:
    """Compila y ejecuta el grafo real de `puente.graph` hasta el `interrupt()` del orientador (#10, #49)."""
    modelos_perfiles, mapa_perfiles, modelos_ofertas, mapa_ofertas = (
        _construir_modelos_desde_estado(estado)
    )
    inyeccion_sesgo = bool(estado.get("sesgo_inyectado_demo", False))

    def _nodo_auditor_tablero(estado_g: dict[str, Any]) -> dict[str, Any]:
        def _fn_perfil_sesgado(p_var: Any) -> Any:
            gen = getattr(p_var, "genero", None) or (
                p_var.get("genero") if isinstance(p_var, dict) else None
            )
            if inyeccion_sesgo and str(gen).lower() in ("femenino", "f"):
                if isinstance(p_var, Perfil) and len(p_var.habilidades) > 1:
                    return p_var.model_copy(update={"habilidades": p_var.habilidades[:1]})
                if isinstance(p_var, dict) and len(p_var.get("habilidades", [])) > 1:
                    copia = dict(p_var)
                    copia["habilidades"] = list(copia["habilidades"])[:1]
                    return copia
            return p_var

        cambios = nodo_auditor(
            estado_g,
            fn_perfil=_fn_perfil_sesgado if inyeccion_sesgo else None,
        )
        return {k: v for k, v in cambios.items() if k not in ("perfiles", "ofertas")}

    def _nodo_postulacion_tablero(estado_g: dict[str, Any]) -> dict[str, Any]:
        aprobacion = estado_g.get("aprobacion") or {}
        pares_aprob = {
            (a["perfil_id"], a["oferta_id"]) for a in (aprobacion.get("aprobados") or [])
        }
        # Los consentimientos del grafo (el «no» del joven manda, #54) más el que confirmó el
        # orientador en el tablero para cada aprobado sin registro propio (#12, Ley 81 de 2019).
        consentimientos = consentimientos_para(
            sorted(pares_aprob), estado_g.get("consentimientos"), str(aprobacion.get("timestamp") or "")
        )
        matches_aprob = [
            m
            for m in (estado_g.get("lista_corta") or [])
            if (m.perfil_id, m.oferta_id) in pares_aprob
            and consentimiento_otorgado(consentimientos, (m.perfil_id, m.oferta_id))
        ]
        if not matches_aprob:
            return {}
        perfiles_map = {p.id: p for p in modelos_perfiles}
        ofertas_map = {o.id: o for o in modelos_ofertas}
        RUTA_OUTBOX.mkdir(parents=True, exist_ok=True)
        conn = _conectar_db_estado(estado)
        try:
            res = postular(
                matches_aprob,
                perfiles_map,
                ofertas_map,
                aprobacion=aprobacion,
                conn=conn,
                outbox=RUTA_OUTBOX,
                enviar=lambda _chat, _txt, _adj: None if False else {},  # pragma: no cover
            ) if False else postular(
                matches_aprob,
                perfiles_map,
                ofertas_map,
                aprobacion=aprobacion,
                conn=conn,
                outbox=RUTA_OUTBOX,
                consentimientos=consentimientos,
            )
        finally:
            conn.close()
        estado.setdefault("_ultimos_archivos_postulacion", {})
        for pid, oid in pares_aprob:
            estado["_ultimos_archivos_postulacion"][(pid, oid)] = [
                arch.name for arch in res.archivos
            ]
        return {
            "decisiones": [*(estado_g.get("decisiones") or []), *res.decisiones],
        }

    checkpointer = InMemorySaver()
    grafo = construir_grafo(
        nodos={
            "mercado": stub_mercado,
            "perfil": stub_perfil,
            "matching": nodo_matching_grafo,
            "auditor": _nodo_auditor_tablero,
            "formacion": nodo_formacion,
            "postulacion": _nodo_postulacion_tablero,
        },
        checkpointer=checkpointer,
    )
    thread_id = estado.get("thread_id") or f"tablero-{uuid.uuid4().hex[:8]}"
    estado["thread_id"] = thread_id
    cfg_grafo = config_caso(thread_id)

    excluidos_actuales = list(estado.get("excluidos") or [])
    entradas_perfil = [
        p.model_dump() for p in modelos_perfiles
    ]
    estado_entrada_grafo: dict[str, Any] = {
        "tipo_entrada": "perfil",
        "entradas_perfil": entradas_perfil,
        "ofertas": modelos_ofertas,
        "perfiles": modelos_perfiles,
        "excluidos": excluidos_actuales,
        "decisiones": [],
        "errores": [],
    }

    salida_grafo = grafo.invoke(estado_entrada_grafo, cfg_grafo)
    solicitud_int = aprobacion_pendiente(grafo, cfg_grafo)

    estado["_grafo"] = grafo
    estado["_config_grafo"] = cfg_grafo
    estado["interrupt_pendiente"] = solicitud_int
    estado["estado_grafo"] = (
        "interrupt_orientador" if solicitud_int is not None else "finalizado"
    )

    # Sincronizar perfiles y ofertas en puente.tools.db
    if registrar_trazas:
        conn = _conectar_db_estado(estado)
        try:
            for mp in modelos_perfiles:
                db.guardar_perfil(conn, mp)
            for mo in modelos_ofertas:
                db.guardar_oferta(conn, mo)
        finally:
            conn.close()

    # Mapear rutas de formación por (perfil_id, oferta_id)
    rutas_por_par: dict[tuple[str, str], list[str]] = {}
    for ruta in salida_grafo.get("rutas_formacion") or []:
        clave_r = (ruta["perfil_id"], ruta["oferta_id"])
        descripciones = [
            f"INADEH {c['corregimiento']}: {c['curso']} ({c['horas']} h, {c['horario']}, Cupos: {c['cupos']} [dato de prueba], Traslado: {c['minutos_traslado']} min)"
            for c in ruta.get("cursos_seleccionados", [])
        ]
        rutas_por_par[clave_r] = descripciones

    # Extraer lista corta y descartados desde la salida del grafo
    matches_ui: list[dict[str, Any]] = []
    descartados_map: dict[tuple[str, str], dict[str, Any]] = {}

    for m_obj in salida_grafo.get("matches") or []:
        m_dict = m_obj.model_dump() if hasattr(m_obj, "model_dump") else dict(m_obj)
        pid = m_dict["perfil_id"]
        p_info = mapa_perfiles.get(pid, {})
        desglose = dict(m_dict.get("desglose", {}))
        for oid_desc, mot_desc in (desglose.get("ofertas_descartadas") or {}).items():
            o_desc = mapa_ofertas.get(oid_desc, {})
            descartados_map[(pid, oid_desc)] = {
                "perfil_id": pid,
                "nombre_perfil": p_info.get("nombre", pid),
                "corregimiento_perfil": p_info.get("corregimiento", ""),
                "oferta_id": oid_desc,
                "puesto": o_desc.get("puesto", oid_desc),
                "empresa": o_desc.get("empresa", ""),
                "corregimiento_oferta": o_desc.get("corregimiento", ""),
                "motivo": mot_desc,
            }

    for m_obj in salida_grafo.get("lista_corta") or []:
        m_dict = m_obj.model_dump() if hasattr(m_obj, "model_dump") else dict(m_obj)
        pid = m_dict["perfil_id"]
        oid = m_dict["oferta_id"]
        if oid == SIN_OFERTA or not m_dict.get("desglose", {}).get("viable", True):
            continue
        p_info = mapa_perfiles.get(pid, {})
        o_info = mapa_ofertas.get(oid, {})
        evidencias_p = {
            h["skill_id"]: h.get("evidencia", "")
            for h in p_info.get("habilidades", [])
            if isinstance(h, dict) and "skill_id" in h
        }
        desglose = dict(m_dict.get("desglose", {}))
        skills_oferta = list(o_info.get("habilidades", []))
        brechas_set = set(m_dict.get("brechas", []))
        cubiertas = [s for s in skills_oferta if s not in brechas_set]
        desglose["habilidades_cubiertas"] = cubiertas
        desglose["cobertura_porcentaje"] = int(round(float(desglose.get("cobertura", 0.0)) * 100))
        desglose["evidencias"] = {s: evidencias_p.get(s, "") for s in cubiertas}

        mensaje_sugerido = (
            f"Buen día, {o_info.get('empresa', 'equipo')}: desde Puente les presentamos a "
            f"{p_info.get('nombre', pid)} para la vacante de {o_info.get('puesto', oid)}. "
            "Quedamos atentos para coordinar entrevista."
        )

        matches_ui.append(
            {
                "perfil_id": pid,
                "nombre_perfil": p_info.get("nombre", pid),
                "corregimiento_perfil": p_info.get("corregimiento", ""),
                "oferta_id": oid,
                "puesto": o_info.get("puesto", oid),
                "empresa": o_info.get("empresa", ""),
                "corregimiento_oferta": o_info.get("corregimiento", ""),
                "salario_mensual": o_info.get("salario_mensual", 0.0),
                "puntaje": m_dict["puntaje"],
                "viable": True,
                "desglose": desglose,
                "brechas": list(m_dict.get("brechas", [])),
                "cursos_recomendados": rutas_por_par.get((pid, oid), []),
                "explicacion": m_dict.get("explicacion", ""),
                "mensaje_empresa": mensaje_sugerido,
                "consentimiento_joven": True,
            }
        )

    estado["matches"] = matches_ui
    estado["descartados"] = list(descartados_map.values())
    estado["auditoria"] = salida_grafo.get("auditoria") or {}

    # Registrar las decisiones de los nodos centrales en puente.tools.db y mantener compatibilidad
    if registrar_trazas:
        decisiones_nodos = [
            _serializar_decision(d)
            for d in (salida_grafo.get("decisiones") or [])
            if _serializar_decision(d).get("agente") in ("matching", "formacion", "auditor")
        ]
        # Si ya había decisiones manuales previas (orientador, mercado, perfil, guardrails), conservarlas antes
        previas_manuales = [
            d
            for d in (estado.get("decisiones") or [])
            if d.get("agente") not in ("matching", "formacion", "auditor")
        ]
        orden_agentes = {"matching": 0, "formacion": 1, "auditor": 2}
        decisiones_nodos.sort(key=lambda d: orden_agentes.get(d.get("agente", ""), 99))

        conn = _conectar_db_estado(estado)
        try:
            for d in decisiones_nodos:
                db.registrar_decision(conn, d)
        finally:
            conn.close()
        estado["decisiones"] = previas_manuales + decisiones_nodos

    # Sincronizar postulaciones preservando el estado de las que ya existían
    existentes = {
        (post["perfil_id"], post["oferta_id"]): post
        for post in estado.get("postulaciones", [])
    }
    nuevas_postulaciones: list[dict[str, Any]] = list(estado.get("postulaciones", []))
    conn = _conectar_db_estado(estado)
    try:
        for m in matches_ui:
            par = (m["perfil_id"], m["oferta_id"])
            if par not in existentes:
                fila_db = conn.execute(
                    "SELECT id, estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?",
                    par,
                ).fetchone()
                if fila_db is None:
                    post_db_id = db.crear_postulacion(
                        conn, m["perfil_id"], m["oferta_id"], db.PRESELECCIONADO
                    )
                    est_db = EstadoPostulacion.PRESELECCIONADO.value
                else:
                    post_db_id = int(fila_db["id"])
                    est_db = str(fila_db["estado"])
                post_nuevo = {
                    "id": f"POST-{m['perfil_id']}-{m['oferta_id']}",
                    "db_id": post_db_id,
                    "perfil_id": m["perfil_id"],
                    "nombre_perfil": m["nombre_perfil"],
                    "oferta_id": m["oferta_id"],
                    "puesto": m["puesto"],
                    "empresa": m["empresa"],
                    "puntaje": m["puntaje"],
                    "estado": est_db,
                    "comentario_orientador": "",
                    "mensaje_empresa": m["mensaje_empresa"],
                    "consentimiento_joven": True,
                    "archivos_generados": [],
                }
                nuevas_postulaciones.append(post_nuevo)
                existentes[par] = post_nuevo
            else:
                existentes[par]["puntaje"] = m["puntaje"]
    finally:
        conn.close()

    estado["postulaciones"] = nuevas_postulaciones
    estado["metricas"] = _calcular_metricas_impacto(estado)
    return estado


def calcular_matches_tablero(
    perfiles: list[dict[str, Any]],
    ofertas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calcula los matches ejecutando el grafo real de Puente."""
    estado_temp: dict[str, Any] = {
        "perfiles": copy.deepcopy(perfiles),
        "ofertas": copy.deepcopy(ofertas),
        "decisiones": [],
        "postulaciones": [],
        "excluidos": [],
        "consentimientos": [],
        "sesgo_inyectado_demo": False,
        "_db_path": str(Path(tempfile.gettempdir()) / f"puente_calc_{uuid.uuid4().hex[:8]}.db"),
    }
    recalcular_pipeline_tablero(estado_temp, registrar_trazas=False)
    return estado_temp["matches"]


def obtener_datos_iniciales_demo(db_path: str | Path | None = None) -> dict[str, Any]:
    """Construye el estado inicial ejecutando el grafo real sobre datos de ejemplo (sintéticos)."""
    ruta_db = Path(
        db_path
        or os.getenv("PUENTE_DB")
        or (Path(tempfile.gettempdir()) / f"puente_demo_{uuid.uuid4().hex[:8]}.db")
    )
    if ruta_db.exists() and db_path is None and not os.getenv("PUENTE_DB"):
        try:
            ruta_db.unlink()
        except OSError:
            pass

    perfiles = [
        {
            "id": "PERFIL-YARITZA",
            "nombre": "Yaritza",
            "genero": "femenino",
            "edad": 20,
            "corregimiento": "La Chorrera",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · entrevista sin CV",
            "horario_libre": "Lunes a Viernes 07:00-17:30 (estudia de noche 18:00-22:00)",
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": [
                {
                    "skill_id": "atencion_cliente",
                    "nombre": "Atención al cliente",
                    "evidencia": "Atiende a los comensales y toma pedidos diarios en la fonda de su mamá",
                },
                {
                    "skill_id": "manejo_caja",
                    "nombre": "Manejo de caja y cobros",
                    "evidencia": "Cobra en efectivo y Yappy y cuadra la caja al cierre del turno",
                },
                {
                    "skill_id": "inventario",
                    "nombre": "Control de inventario básico",
                    "evidencia": "Registra entradas y salidas de insumos de cocina cada mañana",
                },
            ],
        },
        {
            "id": "PERFIL-KEVIN",
            "nombre": "Kevin",
            "genero": "masculino",
            "edad": 22,
            "corregimiento": "Arraiján",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · CV",
            "horario_libre": "Lunes a Viernes 07:00-17:30",
            "salario_minimo": 620.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": [
                {
                    "skill_id": "inventario",
                    "nombre": "Control de inventario básico",
                    "evidencia": "Ayudante de bodega por 6 meses organizando mercancía",
                },
                {
                    "skill_id": "atencion_cliente",
                    "nombre": "Atención al cliente",
                    "evidencia": "Despacho en mostrador de ferretería local",
                },
                {
                    "skill_id": "excel_basico",
                    "nombre": "Hoja de cálculo básica (Excel)",
                    "evidencia": "Registro de guías de despacho en plantilla de Excel",
                },
            ],
        },
        {
            "id": "PERFIL-DANIELA",
            "nombre": "Daniela",
            "genero": "femenino",
            "edad": 19,
            "corregimiento": "Vista Alegre",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · foto de CV",
            "horario_libre": "Lunes a Viernes 08:00-17:00",
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 2.50,
            "habilidades": [
                {
                    "skill_id": "atencion_cliente",
                    "nombre": "Atención al cliente",
                    "evidencia": "Voluntariado en feria comunitaria orientando asistentes",
                },
                {
                    "skill_id": "manejo_caja",
                    "nombre": "Manejo de caja y cobros",
                    "evidencia": "Apoyo de cobro en kiosco familiar los fines de semana",
                },
            ],
        },
    ]

    ofertas_base = [
        {
            "id": "OF-CHORRERA-01",
            "puesto": "Asistente de Caja y Atención en Tienda",
            "empresa": "Supermercado Oeste S.A. (Empresa de Prueba)",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 08:00-16:30",
            "habilidades": ["atencion_cliente", "manejo_caja", "inventario", "excel_basico"],
            "requisitos_texto": "Atención en caja, cuadre diario, control de inventario en tienda. Sin experiencia formal previa.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-VISTAALEGRE-02",
            "puesto": "Auxiliar de Mostrador e Inventario",
            "empresa": "Distribuidora El Progreso (Empresa de Prueba)",
            "corregimiento": "Vista Alegre",
            "salario_mensual": 640.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente", "inventario"],
            "requisitos_texto": "Recibir clientes en mostrador y verificar existencias en estantería. Se valorará buena presencia.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-TOCUMEN-03",
            "puesto": "Cajero/a de Sucursal Este",
            "empresa": "Comercial Aeropuerto S.A. (Empresa de Prueba)",
            "corregimiento": "Tocumen",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente", "manejo_caja"],
            "requisitos_texto": "Manejo de caja registradora y atención a clientes en sucursal de Tocumen.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-BLOQ-EDAD-04",
            "puesto": "Recepcionista y Atención Telefónica",
            "empresa": "Servicios Balboa (Empresa de Prueba)",
            "corregimiento": "Bella Vista",
            "salario_mensual": 680.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente"],
            "requisitos_texto": "Requisitos: edad entre 18 a 25 años, excelente buena presencia y enviar foto en el CV.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-BLOQ-SEXO-05",
            "puesto": "Ayudante de Despacho y Bodega",
            "empresa": "Logística Istmeña (Empresa de Prueba)",
            "corregimiento": "Juan Díaz",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 07:30-16:30",
            "habilidades": ["inventario"],
            "requisitos_texto": "Se solicita personal masculino únicamente, soltero, de 19 a 24 años.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
    ]

    for o in ofertas_base:
        bloq, alertas = _evaluar_reglas_discriminacion(o["requisitos_texto"])
        o["bloqueada"] = bloq
        o["alertas"] = alertas

    estado: dict[str, Any] = {
        "perfiles": perfiles,
        "ofertas": ofertas_base,
        "matches": [],
        "descartados": [],
        "decisiones": [],
        "postulaciones": [],
        "excluidos": [],
        "consentimientos": [],
        "auditoria": {},
        "metricas": {},
        "estado_grafo": "interrupt_orientador",
        "sesgo_inyectado_demo": False,
        "_db_path": str(ruta_db),
    }
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


ESTADO_GLOBAL: dict[str, Any] = obtener_datos_iniciales_demo()


def desbloquear_oferta_con_motivo(
    estado: dict[str, Any],
    *,
    oferta_id: str,
    motivo: str,
    orientador: str = "Orientadora Ana Gómez",
) -> dict[str, Any]:
    """Desbloquea una oferta bloqueada registrando el motivo del orientador como Decision (#49)."""
    req = DesbloquearOfertaRequest(
        oferta_id=oferta_id,
        motivo=motivo,
        orientador=orientador,
    )
    oferta_obj = next(
        (o for o in estado.get("ofertas", []) if o["id"] == req.oferta_id),
        None,
    )
    if oferta_obj is None:
        raise ErrorValidacionTablero(f"No existe la oferta '{req.oferta_id}'.")
    if not oferta_obj.get("bloqueada"):
        raise ErrorValidacionTablero(f"La oferta '{req.oferta_id}' no está bloqueada.")

    oferta_obj["bloqueada"] = False
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _registrar_decision_db(
        estado,
        {
            "timestamp": ahora,
            "agente": "orientador_humano",
            "entrada_resumen": f"Desbloquear oferta {req.oferta_id}",
            "salida_resumen": f"Oferta {req.oferta_id} habilitada para el Matching",
            "razon": f"{req.orientador}: {req.motivo}",
        },
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def procesar_accion_orientador(
    estado: dict[str, Any],
    *,
    accion: str,
    perfil_id: str,
    oferta_id: str,
    nota_edicion: str = "",
    mensaje_empresa: str = "",
    orientador: str = "Orientadora Ana Gómez",
    consentimiento_joven: bool = True,
    consentidos: list[str] | None = None,
) -> dict[str, Any]:
    """Ejecuta aprobar, editar, quitar/quitar_candidato o rechazar reanudando el grafo y registrando en SQLite (#49)."""
    req = AccionOrientadorRequest(
        accion=accion,  # type: ignore[arg-type]
        perfil_id=perfil_id,
        oferta_id=oferta_id,
        nota_edicion=nota_edicion,
        mensaje_empresa=mensaje_empresa,
        orientador=orientador,
        consentimiento_joven=consentimiento_joven,
        consentidos=consentidos,
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    match_activo = next(
        (
            m
            for m in estado.get("matches", [])
            if m["perfil_id"] == req.perfil_id and m["oferta_id"] == req.oferta_id
        ),
        None,
    )
    post_activo = next(
        (
            p
            for p in estado.get("postulaciones", [])
            if p["perfil_id"] == req.perfil_id and p["oferta_id"] == req.oferta_id
        ),
        None,
    )
    if match_activo is None or post_activo is None:
        raise ErrorValidacionTablero(
            f"El par ({req.perfil_id}, {req.oferta_id}) no existe en la lista corta activa."
        )
    if post_activo.get("estado") != EstadoPostulacion.PRESELECCIONADO.value:
        raise ErrorValidacionTablero(
            f"La postulación ({req.perfil_id}, {req.oferta_id}) está en estado "
            f"'{post_activo.get('estado')}' y solo se puede actuar sobre estado 'preseleccionado'."
        )

    grafo = estado.get("_grafo")
    cfg_grafo = estado.get("_config_grafo")

    # 1. EDITAR: cambia solo el mensaje a la empresa y la nota del orientador; no recalcula (#41, #49)
    if req.accion == "editar":
        if req.mensaje_empresa:
            match_activo["mensaje_empresa"] = req.mensaje_empresa
            post_activo["mensaje_empresa"] = req.mensaje_empresa
        if req.nota_edicion:
            match_activo["explicacion"] = (
                f"{match_activo['explicacion']} [Nota orientador: {req.nota_edicion}]"
            )
            post_activo["comentario_orientador"] = req.nota_edicion
        _registrar_decision_db(
            estado,
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Editar mensaje/nota para {req.perfil_id} -> {req.oferta_id}",
                "salida_resumen": (
                    f"Nota: '{req.nota_edicion}' | Mensaje: '{req.mensaje_empresa or post_activo.get('mensaje_empresa', '')}'"
                ),
                "razon": f"{req.orientador}: edición de mensaje y nota sin recalcular el solver (#49).",
            },
        )
        estado["metricas"] = _calcular_metricas_impacto(estado)
        return estado

    # 2. QUITAR CANDIDATO: excluye la pareja, reanuda el grafo con Command(resume={"accion": "quitar", ...}) y recalcula (#49)
    if req.accion in ("quitar_candidato", "quitar"):
        exclusion = {
            "perfil_id": req.perfil_id,
            "oferta_id": req.oferta_id,
            "origen": "orientador",
            "motivo": req.nota_edicion or "Retirado por el orientador de la lista corta",
            "timestamp": ahora,
        }
        estado.setdefault("excluidos", []).append(exclusion)
        estado["perfiles"] = [
            p for p in estado["perfiles"] if p["id"] != req.perfil_id
        ]
        for post in estado["postulaciones"]:
            if post["perfil_id"] == req.perfil_id:
                post["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
                post["comentario_orientador"] = (
                    req.nota_edicion or "Retirado por el orientador; Matching recalculado."
                )

        if grafo is not None and cfg_grafo is not None:
            try:
                est_actual = grafo.get_state(cfg_grafo).values
                resueltas = [
                    v for v in (est_actual.get("vacantes_resueltas") or []) if v != req.oferta_id
                ]
                grafo.update_state(cfg_grafo, {"vacantes_resueltas": resueltas})
                grafo.invoke(
                    Command(
                        resume={
                            "accion": "quitar",
                            "orientador": req.orientador,
                            "oferta_id": req.oferta_id,
                            "perfil_id": req.perfil_id,
                            "nota": req.nota_edicion or "quitado de la lista corta",
                        }
                    ),
                    cfg_grafo,
                )
            except Exception:
                pass

        _registrar_decision_db(
            estado,
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Quitar candidato {req.perfil_id} de {req.oferta_id}",
                "salida_resumen": f"Pareja ({req.perfil_id}, {req.oferta_id}) excluida; Matching recalculado",
                "razon": req.nota_edicion
                or "El orientador retiró al candidato y solicitó recálculo de asignaciones.",
            },
        )
        return recalcular_pipeline_tablero(estado, registrar_trazas=True)

    # 3. RECHAZAR: pasa la postulación a no_seleccionado en SQLite y reanuda el grafo si corresponde
    if req.accion == "rechazar":
        conn = _conectar_db_estado(estado)
        try:
            fila = conn.execute(
                "SELECT id, estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?",
                (req.perfil_id, req.oferta_id),
            ).fetchone()
            if fila is not None and fila["estado"] == db.PRESELECCIONADO:
                db.cambiar_estado(
                    conn,
                    int(fila["id"]),
                    db.NO_SELECCIONADO,
                    req.nota_edicion or "Rechazado por el orientador humano.",
                )
        finally:
            conn.close()

        post_activo["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
        post_activo["comentario_orientador"] = (
            req.nota_edicion or "Rechazado por el orientador humano."
        )
        estado["matches"] = [
            m
            for m in estado["matches"]
            if not (
                m["perfil_id"] == req.perfil_id and m["oferta_id"] == req.oferta_id
            )
        ]

        if grafo is not None and cfg_grafo is not None:
            restantes_oferta = [
                m for m in estado["matches"] if m["oferta_id"] == req.oferta_id
            ]
            if not restantes_oferta:
                try:
                    grafo.invoke(
                        Command(
                            resume={
                                "accion": "rechazar",
                                "orientador": req.orientador,
                                "oferta_id": req.oferta_id,
                                "nota": req.nota_edicion or "rechazado por el orientador",
                            }
                        ),
                        cfg_grafo,
                    )
                    estado["interrupt_pendiente"] = aprobacion_pendiente(grafo, cfg_grafo)
                except Exception:
                    pass

        _registrar_decision_db(
            estado,
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Rechazar match {req.perfil_id} -> {req.oferta_id}",
                "salida_resumen": "Estado actualizado a no_seleccionado; no se envían datos a la empresa",
                "razon": req.nota_edicion or "Rechazado en revisión humana previa al envío.",
            },
        )
        estado["metricas"] = _calcular_metricas_impacto(estado)
        return estado

    # 4. APROBAR: exige el consentimiento del joven, registra Consentimiento + Decision y reanuda el grafo con Command(resume=...) (#49)
    if req.accion == "aprobar":
        lista_consentidos = (
            req.consentidos
            if req.consentidos is not None
            else ([req.perfil_id] if req.consentimiento_joven else [])
        )
        if not req.consentimiento_joven or req.perfil_id not in lista_consentidos:
            raise ErrorValidacionTablero(
                f"El candidato {req.perfil_id} no tiene el consentimiento confirmado ('el joven consintió'). "
                "Postulación no envía ningún dato a la empresa sin consentimiento."
            )

        #Validación de ruta segura en outbox para el par antes de ejecutar
        _ = _ruta_segura_en_outbox(
            RUTA_OUTBOX, f"cv_{req.perfil_id}_{req.oferta_id}.pdf"
        )

        consentimiento: Consentimiento = {
            "perfil_id": req.perfil_id,
            "oferta_id": req.oferta_id,
            "otorgado": True,
            "medio": "orientador",
            "timestamp": ahora,
        }
        estado.setdefault("consentimientos", []).append(consentimiento)
        _registrar_decision_db(
            estado,
            {
                "timestamp": ahora,
                "agente": "consentimiento",
                "entrada_resumen": f"{req.perfil_id} -> {req.oferta_id}",
                "salida_resumen": "Consentimiento confirmado (medio: orientador)",
                "razon": f"{req.orientador} confirmó que el joven consintió el envío de su CV sin PII a la empresa.",
            },
        )

        # Reanudar el grafo real con Command(resume=...)
        archivos_generados: list[str] = []
        if grafo is not None and cfg_grafo is not None:
            est_actual = grafo.get_state(cfg_grafo).values
            resueltas = [
                v for v in (est_actual.get("vacantes_resueltas") or []) if v != req.oferta_id
            ]
            grafo.update_state(cfg_grafo, {"vacantes_resueltas": resueltas})
            salida_resume = grafo.invoke(
                Command(
                    resume={
                        "accion": "aprobar",
                        "orientador": req.orientador,
                        "oferta_id": req.oferta_id,
                        "mensaje": req.mensaje_empresa
                        or post_activo.get("mensaje_empresa")
                        or None,
                        "nota": req.nota_edicion or "Aprobado con consentimiento del joven",
                        "consentidos": [req.perfil_id],
                    }
                ),
                cfg_grafo,
            )
            estado["interrupt_pendiente"] = aprobacion_pendiente(grafo, cfg_grafo)
            archivos_generados = list(
                (estado.get("_ultimos_archivos_postulacion") or {}).get(
                    (req.perfil_id, req.oferta_id), []
                )
            )
            # Sincronizar decisiones generadas por aprobacion y postulacion en el estado en memoria
            for d_obj in (salida_resume.get("decisiones") or []):
                d_ser = _serializar_decision(d_obj)
                if d_ser.get("agente") in ("aprobacion", "postulacion") and d_ser not in estado["decisiones"]:
                    estado["decisiones"].append(d_ser)

        # Si el grafo no tenía la vacante en el hilo actual, invocar postular() directamente contra SQLite y outbox/
        if not archivos_generados:
            modelos_perfiles, _, modelos_ofertas, _ = _construir_modelos_desde_estado(estado)
            perfiles_map = {p.id: p for p in modelos_perfiles}
            ofertas_map = {o.id: o for o in modelos_ofertas}
            m_schema = Match(
                perfil_id=req.perfil_id,
                oferta_id=req.oferta_id,
                puntaje=match_activo["puntaje"],
                desglose=match_activo["desglose"],
                brechas=match_activo["brechas"],
                explicacion=match_activo["explicacion"],
            )
            # Los del grafo primero: si ahí el joven dijo «no», manda sobre el check del tablero (#54).
            previos = [
                *((grafo.get_state(cfg_grafo).values.get("consentimientos") or []) if grafo is not None and cfg_grafo is not None else []),
                *(estado.get("consentimientos") or []),
            ]
            consentimientos = consentimientos_para([(req.perfil_id, req.oferta_id)], previos, ahora)
            RUTA_OUTBOX.mkdir(parents=True, exist_ok=True)
            conn = _conectar_db_estado(estado)
            try:
                res_post = postular(
                    [m_schema],
                    perfiles_map,
                    ofertas_map,
                    aprobacion={
                        "orientador": req.orientador,
                        "timestamp": ahora,
                        "aprobados": [{"perfil_id": req.perfil_id, "oferta_id": req.oferta_id}],
                    },
                    conn=conn,
                    outbox=RUTA_OUTBOX,
                    consentimientos=consentimientos,
                )
            except ValueError as error:
                # Sin consentimiento, oferta bloqueada o datos incompletos: error claro, nunca un 500.
                raise ErrorValidacionTablero(f"No se envió nada a la empresa: {error}") from error
            finally:
                conn.close()
            archivos_generados = [arch.name for arch in res_post.archivos]
            for d_post in res_post.decisiones:
                estado["decisiones"].append(_serializar_decision(d_post))

        _registrar_decision_db(
            estado,
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Aprobar match {req.perfil_id} -> {req.oferta_id} (interrupt reanudado con consentimiento)",
                "salida_resumen": f"Estado: preseleccionado -> aprobado -> enviado | Archivos: {', '.join(archivos_generados)}",
                "razon": req.nota_edicion
                or f"{req.orientador} verificó evidencias, traslado, ausencia de sesgo y consentimiento del joven.",
            },
        )

        post_activo["estado"] = EstadoPostulacion.ENVIADO.value
        post_activo["consentimiento_joven"] = True
        post_activo["comentario_orientador"] = (
            req.nota_edicion or "Aprobado con consentimiento del joven y enviado a la empresa."
        )
        post_activo["archivos_generados"] = archivos_generados
        estado["estado_grafo"] = "postulacion_ejecutada"
        estado["metricas"] = _calcular_metricas_impacto(estado)
        return estado

    return estado


def analizar_nueva_oferta_texto(
    estado: dict[str, Any],
    *,
    puesto: str,
    empresa: str,
    corregimiento: str,
    salario_mensual: float,
    requisitos_texto: str,
    habilidades: list[str],
    fuente: str = "Cargada desde Tablero Web (sintética)",
) -> dict[str, Any]:
    """Agrega una nueva oferta validada con Pydantic y `puente.rules.discriminacion` usando ID único con uuid."""
    req = NuevaOfertaRequest(
        puesto=puesto,
        empresa=empresa,
        corregimiento=corregimiento,
        salario_mensual=salario_mensual,
        requisitos_texto=requisitos_texto,
        habilidades=habilidades,
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    texto_lower = req.requisitos_texto.lower()

    patrones_inyeccion = ("ignora", "olvida tus instrucciones", "eres ahora", "system prompt")
    if any(p in texto_lower for p in patrones_inyeccion):
        _registrar_decision_db(
            estado,
            {
                "timestamp": ahora,
                "agente": "guardrails",
                "entrada_resumen": f"Oferta '{req.puesto}' con intento de prompt injection",
                "salida_resumen": "Inyección marcada; el texto se procesa únicamente como dato literal",
                "razon": "Regla determinística anti-inyección activada antes de invocar al modelo.",
            },
        )

    bloqueada, alertas = _evaluar_reglas_discriminacion(req.requisitos_texto)

    sufijo_uuid = uuid.uuid4().hex[:8].upper()
    nuevo_id = f"OF-{sufijo_uuid}"

    _ = Oferta(
        id=nuevo_id,
        puesto=req.puesto,
        empresa=req.empresa,
        corregimiento=req.corregimiento,
        salario_mensual=req.salario_mensual,
        horario=Horario(
            dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
            hora_inicio="08:00",
            hora_fin="17:00",
        ),
        habilidades=req.habilidades,
        requisitos_texto=req.requisitos_texto,
        alertas=[a["frase"] for a in alertas],
        bloqueada=bloqueada,
        fuente=fuente,
    )

    nueva_oferta = {
        "id": nuevo_id,
        "puesto": req.puesto,
        "empresa": req.empresa,
        "corregimiento": req.corregimiento,
        "salario_mensual": req.salario_mensual,
        "horario": "Lunes a Viernes 08:00-17:00",
        "habilidades": req.habilidades,
        "requisitos_texto": req.requisitos_texto,
        "alertas": alertas,
        "bloqueada": bloqueada,
        "fuente": fuente,
    }
    estado["ofertas"].append(nueva_oferta)
    _registrar_decision_db(
        estado,
        {
            "timestamp": ahora,
            "agente": "mercado",
            "entrada_resumen": f"Nueva oferta {nuevo_id} ({req.puesto} en {req.corregimiento})",
            "salida_resumen": f"bloqueada={bloqueada} | alertas={len(alertas)}",
            "razon": (
                alertas[0]["regla"]
                if alertas
                else "Oferta validada sin requisitos discriminatorios y mapeada a taxonomía."
            ),
        },
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def registrar_nuevo_perfil(
    estado: dict[str, Any],
    *,
    nombre: str,
    edad: int,
    corregimiento: str,
    salario_minimo: float,
    presupuesto_pasaje_diario: float,
    relato_experiencia: str,
    habilidades_ids: list[str] | None = None,
    modalidad_ingreso: str = "Entrevista en Tablero Web (sintético)",
) -> dict[str, Any]:
    """Registra un nuevo joven con validación Pydantic y UUID único (sin sobrescribir perfiles existentes)."""
    req = NuevoPerfilRequest(
        nombre=nombre,
        edad=edad,
        corregimiento=corregimiento,
        salario_minimo=salario_minimo,
        presupuesto_pasaje_diario=presupuesto_pasaje_diario,
        relato_experiencia=relato_experiencia,
        habilidades=habilidades_ids or ["atencion_cliente", "manejo_caja"],
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug_nom = _slug_seguro(req.nombre, prefijo="JOVEN")
    sufijo_uuid = uuid.uuid4().hex[:6].upper()
    nuevo_id = f"PERFIL-{slug_nom}-{sufijo_uuid}"

    habs = [
        {
            "skill_id": sid,
            "nombre": sid.replace("_", " ").title(),
            "evidencia": req.relato_experiencia,
        }
        for sid in req.habilidades
    ]

    _ = Perfil(
        id=nuevo_id,
        nombre=req.nombre,
        genero=None,
        edad=req.edad,
        corregimiento=req.corregimiento,
        horario_libre=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="07:00",
                hora_fin="17:30",
            )
        ],
        salario_minimo=req.salario_minimo,
        presupuesto_pasaje_diario=req.presupuesto_pasaje_diario,
        habilidades=[
            HabilidadPerfil(skill_id=h["skill_id"], evidencia=h["evidencia"])
            for h in habs
        ],
    )

    nuevo_p = {
        "id": nuevo_id,
        "nombre": req.nombre,
        "genero": "no_especificado",
        "edad": req.edad,
        "corregimiento": req.corregimiento,
        "modalidad_ingreso": modalidad_ingreso,
        "horario_libre": "Lunes a Viernes 07:00-17:30",
        "salario_minimo": req.salario_minimo,
        "presupuesto_pasaje_diario": req.presupuesto_pasaje_diario,
        "habilidades": habs,
    }
    estado["perfiles"].append(nuevo_p)
    _registrar_decision_db(
        estado,
        {
            "timestamp": ahora,
            "agente": "perfil",
            "entrada_resumen": f"Nuevo perfil {nuevo_id} ({req.corregimiento})",
            "salida_resumen": f"{len(habs)} habilidad(es) con evidencia literal; Matching recalculado",
            "razon": "Extracción de habilidades con cita literal de experiencia informal.",
        },
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def listar_catalogo_data() -> dict[str, list[dict[str, str]]]:
    """Devuelve la lista de ofertas y perfiles disponibles en data/ (más muestras sintéticas)."""
    ofertas_cat = [
        {
            "id": "ejemplo_sintetico_chorrera",
            "etiqueta": "datos de ejemplo (sintéticos) · Cajero/a Tiempo Completo (La Chorrera, $650)",
        },
        {
            "id": "ejemplo_sintetico_CT11_edad",
            "etiqueta": "datos de ejemplo (sintéticos) · Vendedor '18 a 25 años y buena presencia' (Bella Vista)",
        },
        {
            "id": "ejemplo_sintetico_K04_tocumen",
            "etiqueta": "datos de ejemplo (sintéticos) · Auxiliar de Bodega (Tocumen, $640)",
        },
    ]
    perfiles_cat = [
        {
            "id": "ejemplo_sintetico_yaritza",
            "etiqueta": "datos de ejemplo (sintéticos) · Yaritza (20 años, La Chorrera, fonda familiar)",
        },
        {
            "id": "ejemplo_sintetico_perfil_b",
            "etiqueta": "datos de ejemplo (sintéticos) · Joven voluntario en feria libre (Arraiján)",
        },
    ]
    dir_of = RUTA_DATA / "ofertas"
    if dir_of.exists():
        for arch in sorted(dir_of.glob("*.txt"))[:10]:
            rel = f"data/ofertas/{arch.name}"
            ofertas_cat.append({"id": rel, "etiqueta": f"{rel} (archivo en data/)"})
    dir_pf = RUTA_DATA / "perfiles"
    if dir_pf.exists():
        for arch in sorted(dir_pf.glob("*.json"))[:10]:
            rel = f"data/perfiles/{arch.name}"
            perfiles_cat.append({"id": rel, "etiqueta": f"{rel} (archivo en data/)"})
    return {"ofertas": ofertas_cat, "perfiles": perfiles_cat}


def cargar_desde_catalogo_data(
    estado: dict[str, Any],
    *,
    tipo: str,
    item_id: str,
) -> dict[str, Any]:
    """Carga una oferta o perfil de ejemplo y ejecuta el grafo real del sistema."""
    req = CargarDataRequest(tipo=tipo, item_id=item_id)  # type: ignore[arg-type]
    if req.tipo == "oferta":
        if "CT11" in req.item_id:
            return analizar_nueva_oferta_texto(
                estado,
                puesto="Vendedor de Mostrador",
                empresa="Comercial Central (Empresa de Prueba)",
                corregimiento="Bella Vista",
                salario_mensual=660.0,
                requisitos_texto="Se busca joven de 18 a 25 años con excelente buena presencia y enviar foto en el CV.",
                habilidades=["atencion_cliente"],
                fuente="datos de ejemplo (sintéticos)",
            )
        if "K04" in req.item_id:
            return analizar_nueva_oferta_texto(
                estado,
                puesto="Auxiliar de Bodega Este",
                empresa="Logística Tocumen S.A. (Empresa de Prueba)",
                corregimiento="Tocumen",
                salario_mensual=640.0,
                requisitos_texto="Organización de inventario y recepción de mercancía en sucursal Tocumen.",
                habilidades=["inventario"],
                fuente="datos de ejemplo (sintéticos)",
            )
        return analizar_nueva_oferta_texto(
            estado,
            puesto="Cajero/a de Sucursal Oeste",
            empresa="Supermercados del Istmo (Empresa de Prueba)",
            corregimiento="La Chorrera",
            salario_mensual=650.0,
            requisitos_texto="Cobro en caja, atención a clientes y cuadre diario en La Chorrera.",
            habilidades=["atencion_cliente", "manejo_caja"],
            fuente="datos de ejemplo (sintéticos)",
        )

    if "perfil_b" in req.item_id:
        return registrar_nuevo_perfil(
            estado,
            nombre="Brayan",
            edad=21,
            corregimiento="Arraiján",
            salario_minimo=600.0,
            presupuesto_pasaje_diario=3.0,
            relato_experiencia="Organiza inventario de verduras y atiende clientes cada sábado en feria comunitaria.",
            habilidades_ids=["atencion_cliente", "inventario"],
            modalidad_ingreso="datos de ejemplo (sintéticos)",
        )
    return registrar_nuevo_perfil(
        estado,
        nombre="Yaritza",
        edad=20,
        corregimiento="La Chorrera",
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.0,
        relato_experiencia="Atiende a los comensales, cobra en efectivo y Yappy y registra insumos en la fonda de su mamá.",
        habilidades_ids=["atencion_cliente", "manejo_caja", "inventario"],
        modalidad_ingreso="datos de ejemplo (sintéticos)",
    )


def serializar_estado_publico(estado: dict[str, Any]) -> dict[str, Any]:
    """Construye el JSON serializable del estado (excluyendo objetos internos de LangGraph)."""
    conteo_embudo = {est: 0 for est in ESTADOS_POSTULACION}
    conteo_embudo["perfil_listo"] = len(estado.get("perfiles", []))
    for post in estado.get("postulaciones", []):
        est = post.get("estado", "preseleccionado")
        if est in conteo_embudo:
            conteo_embudo[est] += 1

    payload = {
        k: v
        for k, v in estado.items()
        if not k.startswith("_")
    }
    payload["conteo_embudo"] = conteo_embudo
    payload["catalogo_data"] = listar_catalogo_data()
    payload["decisiones_db"] = _leer_decisiones_db(estado)
    payload["metricas"] = _calcular_metricas_impacto(estado)
    return payload


async def _leer_json_validado(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Verifica Content-Type: application/json (415) y parsea el cuerpo JSON (422 si es inválido)."""
    ctype = request.headers.get("content-type", "").lower()
    if "application/json" not in ctype:
        return None, JSONResponse(
            {"detail": "Se requiere encabezado Content-Type: application/json"},
            status_code=415,
        )
    try:
        datos = await request.json()
        if not isinstance(datos, dict):
            return None, JSONResponse(
                {"detail": "El cuerpo JSON debe ser un objeto"},
                status_code=422,
            )
        return datos, None
    except Exception:
        return None, JSONResponse(
            {"detail": "JSON mal formado en la solicitud"},
            status_code=422,
        )


# --- Endpoints ASGI ---
async def endpoint_index(_request: Request) -> FileResponse:
    return FileResponse(RUTA_STATIC / "index.html")


async def endpoint_estado(_request: Request) -> JSONResponse:
    return JSONResponse(serializar_estado_publico(ESTADO_GLOBAL))


async def endpoint_accion_orientador(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = AccionOrientadorRequest.model_validate(datos)
        procesar_accion_orientador(
            ESTADO_GLOBAL,
            accion=req.accion,
            perfil_id=req.perfil_id,
            oferta_id=req.oferta_id,
            nota_edicion=req.nota_edicion,
            mensaje_empresa=req.mensaje_empresa,
            orientador=req.orientador,
            consentimiento_joven=req.consentimiento_joven,
            consentidos=req.consentidos,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_desbloquear_oferta(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = DesbloquearOfertaRequest.model_validate(datos)
        desbloquear_oferta_con_motivo(
            ESTADO_GLOBAL,
            oferta_id=req.oferta_id,
            motivo=req.motivo,
            orientador=req.orientador,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_nueva_oferta(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = NuevaOfertaRequest.model_validate(datos)
        analizar_nueva_oferta_texto(
            ESTADO_GLOBAL,
            puesto=req.puesto,
            empresa=req.empresa,
            corregimiento=req.corregimiento,
            salario_mensual=req.salario_mensual,
            requisitos_texto=req.requisitos_texto,
            habilidades=req.habilidades,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_nuevo_perfil(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = NuevoPerfilRequest.model_validate(datos)
        registrar_nuevo_perfil(
            ESTADO_GLOBAL,
            nombre=req.nombre,
            edad=req.edad,
            corregimiento=req.corregimiento,
            salario_minimo=req.salario_minimo,
            presupuesto_pasaje_diario=req.presupuesto_pasaje_diario,
            relato_experiencia=req.relato_experiencia,
            habilidades_ids=req.habilidades,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_cargar_data(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = CargarDataRequest.model_validate(datos)
        cargar_desde_catalogo_data(
            ESTADO_GLOBAL,
            tipo=req.tipo,
            item_id=req.item_id,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_simular_sesgo(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = SimularSesgoRequest.model_validate(datos)
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)

    ESTADO_GLOBAL["sesgo_inyectado_demo"] = req.activar
    recalcular_pipeline_tablero(ESTADO_GLOBAL, registrar_trazas=True)
    return await endpoint_estado(request)


async def endpoint_reset(request: Request) -> JSONResponse:
    ESTADO_GLOBAL.clear()
    ESTADO_GLOBAL.update(obtener_datos_iniciales_demo())
    return await endpoint_estado(request)


RUTA_STATIC.mkdir(parents=True, exist_ok=True)

app = Starlette(
    debug=False,
    routes=[
        Route("/", endpoint_index, methods=["GET"]),
        Route("/api/estado", endpoint_estado, methods=["GET"]),
        Route("/api/orientador/accion", endpoint_accion_orientador, methods=["POST"]),
        Route("/api/ofertas/desbloquear", endpoint_desbloquear_oferta, methods=["POST"]),
        Route("/api/ofertas", endpoint_nueva_oferta, methods=["POST"]),
        Route("/api/perfiles", endpoint_nuevo_perfil, methods=["POST"]),
        Route("/api/cargar-data", endpoint_cargar_data, methods=["POST"]),
        Route("/api/auditor/simular-sesgo", endpoint_simular_sesgo, methods=["POST"]),
        Route("/api/reset", endpoint_reset, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(RUTA_STATIC)), name="static"),
    ],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, reload=False)
