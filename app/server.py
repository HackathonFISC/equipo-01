"""Servidor web ASGI para el Tablero del Orientador conectado al grafo real de Puente (Issues #11, #33 y #49).

Toda la lógica de negocio vive en `puente/`:
- Ejecución y pausa HITL (`interrupt` / `Command(resume=...)`): `puente.graph`
- Reglas legales (Ley 11 de 2005): `puente.rules.discriminacion`
- Persistencia SQLite (`perfiles`, `ofertas`, `postulaciones`, `decisiones`): `puente.tools.db`
- Envío de postulaciones (`PDF`, `.ics`, `Telegram/outbox`): `puente.agents.postulacion`
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import dataclasses
import puente.rules.traslados as _mod_traslados
from puente.agents import mercado
from puente.agents.auditor import nodo_auditor
from puente.agents.formacion import nodo_formacion
from puente.agents.postulacion import (
    agendar_entrevistas,
    consentimiento_otorgado,
    consentimientos_para,
    decisiones_de_agenda,
    postular,
)
from puente.tools.calendario import ZONA_PANAMA
from puente.graph import (
    aprobacion_pendiente,
    checkpointer_sqlite,
    config_caso,
    construir_grafo,
    negaron_consentimiento,
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
from puente.solver.asignacion import ConfigSolver
from puente.solver.turnos import proponer_cobertura
from puente.tools import db

# Vista del joven (#52): imports en un grupo propio para no tocar los del tablero (#49/#99).
import contextlib
import csv
import hmac
import logging
import secrets
import time
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import StrictBool, TypeAdapter
from starlette.concurrency import run_in_threadpool

# Lista corta ciega de la empresa (#167): su lógica vive en app/empresa.py; aquí solo rutas y anuncio de códigos.
from app import empresa
from puente.agents.perfil import (
    MAX_PREGUNTAS,
    PREGUNTAS_ENTREVISTA,
    construir_perfil,
    detectar_corregimiento,
    entrada_entrevista,
    llm_puente,
)
from puente.graph import cargar_taxonomia_grafo
from puente.guardrails import EDAD_MAX, EDAD_MIN, evaluar_entrada, limpiar_entrada
from puente.rules.taxonomia import normalizar_habilidad
from puente.schemas import DIAS_VALIDOS, EntradaPerfil

def es_modo_demo_publica() -> bool:
    """True cuando el servidor corre en Vercel (`VERCEL=1`) o con `PUENTE_DEMO_PUBLICA=1` (#161)."""
    return bool(
        os.getenv("VERCEL", "").strip() in ("1", "true", "True")
        or os.getenv("PUENTE_DEMO_PUBLICA", "").strip() in ("1", "true", "True")
    )


if es_modo_demo_publica():
    os.environ["LLM_MODO"] = "reproducir"


class MiddlewareDemoPublicaReadOnly(BaseHTTPMiddleware):
    """En modo demo pública (Vercel / PUENTE_DEMO_PUBLICA=1), bloquea mutaciones (POST, PUT, DELETE, PATCH) con 403 (#161)."""

    async def dispatch(self, request: Request, call_next):
        if es_modo_demo_publica() and request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse(
                {
                    "detail": "Modo demostración pública en Vercel: el servidor opera en solo lectura para preservar la demo del hackathon (#161).",
                    "modo_demo_publica": True,
                },
                status_code=403,
            )
        return await call_next(request)

from app import joven_fase2  # Vista del joven, fase 2 (#166): la lógica vive en app/joven_fase2.py

RUTA_APP = Path(__file__).resolve().parent
RUTA_STATIC = RUTA_APP / "static"
RUTA_RAIZ = RUTA_APP.parent
RUTA_DATA = RUTA_RAIZ / "data"
RUTA_OUTBOX = (
    Path(tempfile.gettempdir()) / "puente_outbox"
    if es_modo_demo_publica()
    else RUTA_RAIZ / "outbox"
)
# Después del condicional del #178: así, también en la demo pública, PUENTE_OUTBOX manda.
_RUTA_OUTBOX_POR_DEFECTO = RUTA_OUTBOX


def _ruta_outbox() -> Path:
    """Carpeta del CV, el .ics y los mensajes: PUENTE_OUTBOX si está definida, como PUENTE_DB.

    Se lee en cada llamada. Si alguien fijó ``RUTA_OUTBOX`` a mano (las pruebas), manda esa;
    sin ninguna de las dos, la de siempre (``outbox/`` del repo, o la temporal en la demo pública).
    """
    if RUTA_OUTBOX != _RUTA_OUTBOX_POR_DEFECTO:
        return RUTA_OUTBOX
    return Path(os.getenv("PUENTE_OUTBOX") or RUTA_OUTBOX)
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
    consentimiento_joven: bool | None = Field(
        default=None,
        description="Casilla 'el joven consintió' confirmada explícitamente por el orientador (#49, #124)",
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


class AprobarCoberturaTurnosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oferta_id: str = Field(min_length=1, max_length=80)
    consentimiento_manana: bool | None = None
    consentimiento_tarde: bool | None = None
    orientador: str = Field(default="Orientadora Ana Gómez", min_length=2, max_length=120)

    @field_validator("oferta_id")
    @classmethod
    def _validar_id_seguro(cls, valor: str) -> str:
        limpio = valor.strip()
        if not PATRON_ID_SEGURO.fullmatch(limpio):
            raise ValueError(f"ID inválido '{valor}': solo se permiten caracteres [A-Za-z0-9_-]")
        return limpio


class UmbralesSolverRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    max_minutos_traslado: int | None = Field(default=None, ge=10, le=240)
    max_porcentaje_salario_pasaje: float | None = Field(default=None, gt=0.0, le=1.0)
    max_porcentaje_pasaje: float | None = Field(default=None, gt=0.0, le=100.0)
    max_candidatos_por_vacante: int | None = Field(default=None, ge=1, le=20, alias="k")
    max_vacantes_por_perfil: int | None = Field(default=None, ge=1, le=20)
    orientador: str = Field(default="Orientadora Ana Gómez", min_length=2, max_length=120)


class EventoEmpresaTableroRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    tipo: Literal["rechaza", "cubierta", "acepta"]
    oferta_id: str = Field(min_length=1, max_length=80)
    perfil_id: str | None = Field(default=None, max_length=80)
    nota: str = Field(default="", max_length=500)
    empresa: str = Field(default="Empresa", min_length=2, max_length=120)

    @field_validator("oferta_id")
    @classmethod
    def _validar_id_seguro(cls, valor: str) -> str:
        limpio = valor.strip()
        if not PATRON_ID_SEGURO.fullmatch(limpio):
            raise ValueError(f"ID inválido '{valor}': solo se permiten caracteres [A-Za-z0-9_-]")
        return limpio


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


def _ruta_checkpoints_para_db(ruta_db: str | Path) -> Path:
    """Archivo SQLite propio para el checkpointer de LangGraph (aparte de puente.db, #122)."""
    env_cp = os.getenv("PUENTE_CHECKPOINTS")
    if env_cp:
        return Path(env_cp).resolve()
    p = Path(ruta_db).resolve()
    return p.with_name(f"{p.stem}_checkpoints.db")


def _asegurar_tabla_tablero_estado(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tablero_estado (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            estado_json TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _guardar_estado_tablero_db(estado: dict[str, Any]) -> None:
    """Persiste el estado del tablero en SQLite (`tablero_estado`) para sobrevivir reinicios (#122)."""
    if not estado.get("_persistir_tablero", True):
        return
    serializable = {
        k: v
        for k, v in estado.items()
        if not k.startswith("_") or k == "_tokens_joven"
    }
    conn = _conectar_db_estado(estado)
    try:
        _asegurar_tabla_tablero_estado(conn)
        conn.execute(
            """
            INSERT INTO tablero_estado (id, estado_json, actualizado_en)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                estado_json = excluded.estado_json,
                actualizado_en = excluded.actualizado_en
            """,
            (
                json.dumps(serializable, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _cargar_estado_tablero_db(ruta_db: Path) -> dict[str, Any] | None:
    """Recupera el estado persistido en `tablero_estado` si la base ya existe (#122)."""
    if not ruta_db.exists():
        return None
    conn = db.conectar(ruta_db)
    try:
        _asegurar_tabla_tablero_estado(conn)
        fila = conn.execute(
            "SELECT estado_json FROM tablero_estado WHERE id = 1"
        ).fetchone()
        if fila is None or not fila["estado_json"]:
            return None
        cargado = json.loads(str(fila["estado_json"]))
        if not isinstance(cargado, dict) or not cargado.get("perfiles"):
            return None
        cargado["_db_path"] = str(ruta_db)
        return cargado
    except Exception:
        return None
    finally:
        conn.close()


def _limpiar_db_y_checkpoints(ruta_db: Path) -> None:
    """Limpia todas las tablas de SQLite y del checkpointer para un reset limpio (#122)."""
    if ruta_db.exists():
        conn = db.conectar(ruta_db)
        try:
            _asegurar_tabla_tablero_estado(conn)
            conn.execute("PRAGMA foreign_keys = OFF;")
            tablas = [
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for tabla in tablas:
                try:
                    conn.execute(f'DELETE FROM "{tabla}"')
                except sqlite3.OperationalError:
                    pass
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON;")
        finally:
            conn.close()
    ruta_cp = _ruta_checkpoints_para_db(ruta_db)
    if ruta_cp.exists():
        cp = checkpointer_sqlite(ruta_cp)
        try:
            tablas_cp = [
                str(r[0])
                for r in cp.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for t_cp in tablas_cp:
                cp.conn.execute(f'DELETE FROM "{t_cp}"')
            cp.conn.commit()
        except Exception:
            pass


def _registrar_decision_db(estado: dict[str, Any], decision: Decision | dict[str, Any]) -> dict[str, Any]:
    """Guarda una Decision en SQLite (`decisiones`) y en la lista en memoria del estado."""
    serializada = _serializar_decision(decision)
    conn = _conectar_db_estado(estado)
    try:
        db.registrar_decision(conn, serializada)
        if serializada.get("agente") == "orientador_humano":
            copia_orientador = {**serializada, "agente": "orientador"}
            db.registrar_decision(conn, copia_orientador)
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


def _ultimo_consentimiento(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> dict[str, Any] | None:
    """El registro más reciente de la pareja en `consentimientos` (el joven puede cambiar de opinión)."""
    return next(
        (
            c
            for c in reversed(estado.get("consentimientos") or [])
            if c.get("perfil_id") == perfil_id and c.get("oferta_id") == oferta_id
        ),
        None,
    )


def _consentimientos_combinados(estado: dict[str, Any]) -> list[dict[str, Any]]:
    """Los consentimientos del grafo y luego los del tablero que el grafo aún no tiene (#52, #54).

    Es la única lista que leen el tablero, el grafo y `postular()`: en las tres manda el último
    registro de cada pareja.
    """
    del_grafo: list[dict[str, Any]] = []
    grafo, cfg = estado.get("_grafo"), estado.get("_config_grafo")
    if grafo is not None and cfg is not None:
        try:
            del_grafo = [dict(c) for c in grafo.get_state(cfg).values.get("consentimientos") or []]
        except Exception:
            del_grafo = []
    return del_grafo + [dict(c) for c in estado.get("consentimientos") or [] if dict(c) not in del_grafo]


def _consentimiento_vigente(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> bool:
    """True solo si existe un registro explícito de la pareja y el último es «sí» (#124, #52)."""
    propios = [
        c
        for c in _consentimientos_combinados(estado)
        if c.get("perfil_id") == perfil_id and c.get("oferta_id") == oferta_id
    ]
    return bool(propios and propios[-1].get("otorgado"))


def _consentimiento_rechazado_por_joven(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> bool:
    """True si el último registro de la pareja en consentimientos es un «no» (#52, #54)."""
    propios = [
        c
        for c in _consentimientos_combinados(estado)
        if c.get("perfil_id") == perfil_id and c.get("oferta_id") == oferta_id
    ]
    return bool(propios and not propios[-1].get("otorgado"))


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
        # Los bloques del formulario de /joven (#52) mandan; el texto queda de respaldo para los demás perfiles.
        bloques_libre = [Horario.model_validate(h) for h in p.get("horario_libre_bloques") or []]
        bloques_clases = p.get("horario_clases_bloques")
        modelos_perfiles.append(
            Perfil(
                id=p["id"],
                nombre=p["nombre"],
                genero=p.get("genero"),
                edad=edad_valida,
                corregimiento=p["corregimiento"],
                horario_libre=bloques_libre
                or [
                    Horario(
                        dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                        hora_inicio="07:00",
                        hora_fin="17:30",
                    )
                ],
                horario_clases=[Horario.model_validate(h) for h in bloques_clases]
                if bloques_clases is not None
                else [
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
                "fecha": "agosto de 2026",
                "desocupacion_nacional_pct": 8.5,
                "desocupacion_20_24_pct": 22.6,
                "desocupacion_25_29_pct": 14.4,
                "informalidad_no_agricola_pct": 45.8,
                "fuente_desocupacion_url": "https://www.inec.gob.pa/archivos/P0579518620260930152328Cuadro%205.pdf",
                "fuente_informalidad_url": "https://www.inec.gob.pa/archivos/P0862619320260930153357Bolet%C3%ADn%20de%20la%20Encuesta%20de%20Mercado%20Laboral%20-%20Agosto%202026.pdf",
                "nota": "Cifras contextuales de referencia nacional; las métricas operativas corresponden al lote sintético de la demo.",
            },
            "operativas_demo": {
                "jovenes_atendidos": len(perfiles),
                "ofertas_evaluadas": len(ofertas),
                "ofertas_bloqueadas_ley11": sum(1 for o in ofertas if o.get("bloqueada")),
                "matches_viables_cpsat": len(matches),
                "pares_descartados_con_motivo": len(descartados),
                "rutas_formacion_inadeh": len(con_ruta),
                # Parejas cuyo último registro es un «sí»: un «no» del joven (#52) no cuenta.
                "consentimientos_confirmados": sum(
                    1 for otorgado in {(c["perfil_id"], c["oferta_id"]): c.get("otorgado") for c in consentimientos}.values() if otorgado
                ),
                "postulaciones_enviadas": len(enviadas),
            },
        }
    )
    return base_metricas


# Trazas honestas (#193): lo que hace el código del tablero (stubs, altas desde el formulario o el catálogo)
# queda a nombre de «tablero». «mercado» y «perfil» solo aparecen cuando corre su agente de `puente/agents`
# (la vista del joven sí usa el agente Perfil). Las acciones de personas siguen como «orientador_humano».
AGENTE_TABLERO = "tablero"


def _stub_del_tablero(stub: Callable[[dict], dict], agente: str) -> Callable[[dict], dict]:
    """El stub del grafo para el nodo `agente`, con sus Decisiones a nombre de «tablero» (#193).

    En el tablero las ofertas y los perfiles ya llegan estructurados: el stub solo los valida y el agente
    Mercado o Perfil no corre. Llamar a los agentes reales pediría LLM o una grabación por cada texto nuevo.
    """

    def nodo(estado_g: dict[str, Any]) -> dict[str, Any]:
        cambios = stub(estado_g)
        previas = len(estado_g.get("decisiones") or [])
        decisiones = list(cambios.get("decisiones") or [])
        nuevas = [Decision.model_validate(_serializar_decision(d)) for d in decisiones[previas:]]
        decisiones[previas:] = [
            d.model_copy(
                update={
                    "agente": AGENTE_TABLERO,
                    "razon": f"Stub del nodo {agente} en el tablero; el agente {agente} no corrió. {d.razon}",
                }
            )
            for d in nuevas
        ]
        return {**cambios, "decisiones": decisiones}

    return nodo


def _compilar_grafo_tablero(
    estado: dict[str, Any],
    modelos_perfiles: list[Perfil],
    modelos_ofertas: list[Oferta],
) -> tuple[Any, dict[str, Any]]:
    """Compila el grafo del tablero respaldado por `checkpointer_sqlite` (#122, #168)."""
    inyeccion_sesgo = bool(estado.get("sesgo_inyectado_demo", False))

    def _inyectar_umbrales_grafo(estado_g: dict[str, Any]) -> dict[str, Any]:
        if not estado.get("umbrales_editados_orientador"):
            return estado_g
        cfg_dict = dict(estado.get("config_solver") or {})
        cfg_obj = ConfigSolver(**cfg_dict)
        return {**estado_g, "config_solver": cfg_obj}

    def _nodo_matching_tablero(estado_g: dict[str, Any]) -> dict[str, Any]:
        return nodo_matching_grafo(_inyectar_umbrales_grafo(estado_g))

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
            _inyectar_umbrales_grafo(estado_g),
            fn_perfil=_fn_perfil_sesgado if inyeccion_sesgo else None,
        )
        return {k: v for k, v in cambios.items() if k not in ("perfiles", "ofertas")}

    def _nodo_postulacion_tablero(estado_g: dict[str, Any]) -> dict[str, Any]:
        aprobacion = estado_g.get("aprobacion") or {}
        # El «no» del joven (#52) manda: esa pareja no se envía aunque venga en `aprobados` (#109).
        pares_aprob = {
            (a["perfil_id"], a["oferta_id"])
            for a in (aprobacion.get("aprobados") or [])
            if a["perfil_id"] not in negaron_consentimiento(estado_g, a["oferta_id"])
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
        matches_aprob = [m for m in matches_aprob if m.perfil_id in perfiles_map and m.oferta_id in ofertas_map]
        _ruta_outbox().mkdir(parents=True, exist_ok=True)
        conn = _conectar_db_estado(estado)
        try:
            # Cada candidato en su propia franja, con la agenda del nodo de Postulación (#186).
            matches_aprob, inicios, avisos = agendar_entrevistas(
                matches_aprob, perfiles_map, ofertas_map, conn, desde=datetime.now(ZONA_PANAMA)
            )
            # Hora de respaldo «por confirmar» o sin ninguna hora: la misma Decision que deja el nodo.
            de_agenda = decisiones_de_agenda(avisos)
            for decision in de_agenda:
                db.registrar_decision(conn, decision)
            # La ruta de respaldo vuelve a agendar si el grafo no generó archivos: que no repita estas.
            estado.setdefault("_agenda_registrada", set()).update(par for par, _, _ in avisos)
            if not matches_aprob:
                return {"decisiones": [*(estado_g.get("decisiones") or []), *de_agenda]} if de_agenda else {}
            res = postular(
                matches_aprob,
                perfiles_map,
                ofertas_map,
                aprobacion=aprobacion,
                conn=conn,
                outbox=_ruta_outbox(),
                consentimientos=consentimientos,
                # El mensaje que editó el orientador llega a la empresa, cegado (#187).
                mensaje_orientador=str(aprobacion.get("mensaje") or ""),
                inicio_entrevista=lambda m: inicios[(m.perfil_id, m.oferta_id)],
            )
        finally:
            conn.close()
        estado.setdefault("_ultimos_archivos_postulacion", {})
        for pid, oid in pares_aprob:
            estado["_ultimos_archivos_postulacion"][(pid, oid)] = [
                arch.name for arch in res.archivos
            ]
        return {
            "decisiones": [*(estado_g.get("decisiones") or []), *de_agenda, *res.decisiones],
        }

    ruta_db = estado.get("_db_path") or os.getenv("PUENTE_DB") or str(RUTA_DB_TABLERO)
    estado["_db_path"] = str(ruta_db)
    ruta_cp = _ruta_checkpoints_para_db(ruta_db)
    checkpointer = checkpointer_sqlite(ruta_cp)
    grafo = construir_grafo(
        nodos={
            "mercado": _stub_del_tablero(stub_mercado, "mercado"),
            "perfil": _stub_del_tablero(stub_perfil, "perfil"),
            "matching": _nodo_matching_tablero,
            "auditor": _nodo_auditor_tablero,
            "formacion": nodo_formacion,
            "postulacion": _nodo_postulacion_tablero,
        },
        checkpointer=checkpointer,
    )
    thread_id = estado.get("thread_id") or os.getenv("PUENTE_THREAD_ID") or "tablero-principal"
    estado["thread_id"] = thread_id
    cfg_grafo = config_caso(thread_id)
    return grafo, cfg_grafo


def _explicar_jovenes_sin_opcion(
    estado: dict[str, Any],
    modelos_ofertas: list[Oferta],
    matches_ui: list[dict[str, Any]],
    descartados_map: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Por qué cada joven sin opciones en la lista corta no tiene ninguna (#245).

    El tablero decía siempre «Ninguna oferta cumple sus restricciones», también cuando las vacantes que sí las
    cumplen ya llenaron su cupo. Aquí se distingue con lo que el tablero ya tiene, sin tocar el solver: una
    oferta es viable para el joven si no está entre sus descartes (traslado, pasaje, salario, horario, habilidades
    o Ley 11) ni excluida por el orientador o la empresa. Si hay vacantes viables y todas tienen ya su cupo (k por
    plaza) lleno, la causa es el cupo; si no hay ninguna viable, son las restricciones.
    """
    k = ConfigSolver(**dict(estado.get("config_solver") or {})).max_candidatos_por_vacante
    plazas = {o.id: o.plazas for o in modelos_ofertas}
    asignados: dict[str, int] = {}
    for m in matches_ui:
        asignados[m["oferta_id"]] = asignados.get(m["oferta_id"], 0) + 1
    con_opcion = {m["perfil_id"] for m in matches_ui}
    excluidos = {(e.get("perfil_id"), e.get("oferta_id")) for e in estado.get("excluidos") or []}
    ofertas = estado.get("ofertas") or []

    explicaciones: dict[str, dict[str, Any]] = {}
    for perfil in estado.get("perfiles") or []:
        pid = perfil["id"]
        if pid in con_opcion:
            continue
        if not ofertas:
            explicaciones[pid] = {
                "causa": "sin_ofertas",
                "titulo": "No hay ofertas cargadas",
                "texto": "Carga una oferta para calcular su lista corta.",
                "ofertas": [],
            }
            continue
        viables = [
            o for o in ofertas
            if not o.get("bloqueada") and (pid, o["id"]) not in descartados_map and (pid, o["id"]) not in excluidos
        ]
        if not viables:
            explicaciones[pid] = {
                "causa": "restricciones",
                "titulo": "Ninguna oferta cumple sus restricciones",
                "texto": "Traslado, pasaje, salario, horario, habilidades o la Ley 11 dejan fuera todas las vacantes.",
                "ofertas": [],
            }
            continue
        llenas = [o for o in viables if asignados.get(o["id"], 0) >= k * plazas.get(o["id"], 1)]
        resumen = [{"oferta_id": o["id"], "puesto": o.get("puesto", o["id"]), "empresa": o.get("empresa", "")} for o in viables]
        nombres = "; ".join(f"{o['puesto']} ({o['empresa']})" if o["empresa"] else o["puesto"] for o in resumen)
        cuantas = f"{len(viables)} vacante{'s' if len(viables) != 1 else ''}"
        if len(llenas) == len(viables):
            explicaciones[pid] = {
                "causa": "cupo",
                "titulo": f"Cumple las restricciones de {cuantas}, pero su cupo está lleno",
                "texto": (
                    f"Cada vacante admite {k} candidato{'s' if k != 1 else ''} por plaza y ya los ocupan otros "
                    f"jóvenes: {nombres}."
                ),
                "ofertas": resumen,
            }
        else:
            explicaciones[pid] = {
                "causa": "sin_asignar",
                "titulo": f"Cumple las restricciones de {cuantas}, pero el solver no le asignó ninguna",
                "texto": f"Vacantes que cumple: {nombres}. El desglose del solver tiene el detalle.",
                "ofertas": resumen,
            }
    return explicaciones


def recalcular_pipeline_tablero(
    estado: dict[str, Any],
    *,
    registrar_trazas: bool = True,
) -> dict[str, Any]:
    """Compila y ejecuta el grafo real de `puente.graph` hasta el `interrupt()` del orientador (#10, #49)."""
    modelos_perfiles, mapa_perfiles, modelos_ofertas, mapa_ofertas = (
        _construir_modelos_desde_estado(estado)
    )
    grafo, cfg_grafo = _compilar_grafo_tablero(estado, modelos_perfiles, modelos_ofertas)

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
        # Lo que el joven decidió en /joven (#52) llega al grafo: `negaron_consentimiento` (#109) lo lee.
        "consentimientos": list(estado.get("consentimientos") or []),
        "vacantes_resueltas": [],
        "reintentos_auditor": 0,
        "devolver_a_matching": False,
        "aprobacion": None,
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
                "consentimiento_joven": _consentimiento_vigente(estado, pid, oid),
            }
        )

    estado["matches"] = matches_ui
    estado["descartados"] = list(descartados_map.values())
    estado["sin_opcion"] = _explicar_jovenes_sin_opcion(estado, modelos_ofertas, matches_ui, descartados_map)
    estado["auditoria"] = salida_grafo.get("auditoria") or {}

    # Cobertura por turnos (#165): solo para ofertas activas sin candidato individual de turno completo
    ofertas_con_match_individual = {m["oferta_id"] for m in matches_ui}
    ofertas_sin_match = [
        o for o in modelos_ofertas if o.id not in ofertas_con_match_individual and not o.bloqueada
    ]
    coberturas_objs = proponer_cobertura(
        ofertas_sin_match,
        modelos_perfiles,
        config=estado.get("config_solver"),
        excluidos=excluidos_actuales,
    )
    coberturas_ui: list[dict[str, Any]] = []
    for cob in coberturas_objs:
        cob_dict = cob.model_dump()
        for seg in cob_dict.get("segmentos", []):
            seg["consentimiento_joven"] = _consentimiento_vigente(
                estado, seg["perfil_id"], cob_dict["oferta_id"]
            )
        coberturas_ui.append(cob_dict)
    estado["coberturas_turnos"] = coberturas_ui

    # Registrar las decisiones de los nodos centrales en puente.tools.db en orden cronológico real (#124)
    if registrar_trazas:
        decisiones_nodos = [
            _serializar_decision(d)
            for d in (salida_grafo.get("decisiones") or [])
            if _serializar_decision(d).get("agente") in ("matching", "formacion", "auditor")
        ]
        if coberturas_ui:
            resumen_cob = "; ".join(
                f"{c['oferta_id']}: {c['segmentos'][0]['perfil_id']} ({c['segmentos'][0]['inicio']}-{c['segmentos'][0]['fin']}) + "
                f"{c['segmentos'][1]['perfil_id']} ({c['segmentos'][1]['inicio']}-{c['segmentos'][1]['fin']})"
                for c in coberturas_ui
            )
            decisiones_nodos.append(
                _serializar_decision(
                    Decision(
                        agente="cobertura_turnos",
                        entrada_resumen=f"{len(ofertas_sin_match)} vacante(s) sin candidato de turno completo",
                        salida_resumen=f"{len(coberturas_ui)} dupla(s) de cobertura por turnos: {resumen_cob}",
                        razon=(
                            "Cobertura por turnos determinística con CP-SAT (#165): ningún joven cubre la jornada "
                            "completa sin cruzar con clases, pero la dupla complementaria cubre mañana y tarde."
                        ),
                        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                )
            )
        # Si ya había decisiones manuales previas (orientador, tablero, guardrails), conservarlas antes
        previas_manuales = [
            d
            for d in (estado.get("decisiones") or [])
            if d.get("agente") not in ("matching", "formacion", "auditor", "cobertura_turnos")
        ]

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
                    "consentimiento_joven": m["consentimiento_joven"],
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
    if registrar_trazas:
        _guardar_estado_tablero_db(estado)
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
        "_persistir_tablero": False,
        "_db_path": str(Path(tempfile.gettempdir()) / f"puente_calc_{uuid.uuid4().hex[:8]}.db"),
    }
    recalcular_pipeline_tablero(estado_temp, registrar_trazas=False)
    return estado_temp["matches"]


def obtener_datos_iniciales_demo(
    db_path: str | Path | None = None,
    *,
    reiniciar: bool = False,
) -> dict[str, Any]:
    """Construye o recupera el estado del tablero sobre SQLite y `checkpointer_sqlite` (#122)."""
    ruta_db = Path(
        db_path
        or os.getenv("PUENTE_DB")
        or RUTA_DB_TABLERO
    ).resolve()

    if reiniciar:
        _limpiar_db_y_checkpoints(ruta_db)
    else:
        estado_previo = _cargar_estado_tablero_db(ruta_db)
        if estado_previo is not None:
            modelos_perfiles, _, modelos_ofertas, _ = _construir_modelos_desde_estado(estado_previo)
            grafo, cfg_grafo = _compilar_grafo_tablero(
                estado_previo, modelos_perfiles, modelos_ofertas
            )
            estado_previo["_grafo"] = grafo
            estado_previo["_config_grafo"] = cfg_grafo
            solicitud_int = aprobacion_pendiente(grafo, cfg_grafo)
            if solicitud_int is not None:
                estado_previo["interrupt_pendiente"] = solicitud_int
            estado_previo["metricas"] = _calcular_metricas_impacto(estado_previo)
            return estado_previo

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
            "presupuesto_pasaje_diario": 5.00,
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
            "salario_mensual": 700.0,
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
    consentimiento_joven: bool | None = None,
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
    # Nota y mensaje son texto libre del orientador: sin cédula, teléfono ni dirección exacta antes de
    # guardarlos en la explicación, la postulación o `decisiones`, en cualquier acción (#213).
    req = req.model_copy(
        update=limpiar_entrada({"nota_edicion": req.nota_edicion, "mensaje_empresa": req.mensaje_empresa})
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
        _guardar_estado_tablero_db(estado)
        return estado

    # 2. QUITAR CANDIDATO: excluye solo el par (perfil_id, oferta_id), reanuda el grafo y recalcula (#49, #124)
    if req.accion in ("quitar_candidato", "quitar"):
        exclusion = {
            "perfil_id": req.perfil_id,
            "oferta_id": req.oferta_id,
            "origen": "orientador",
            "motivo": req.nota_edicion or "Retirado por el orientador de la lista corta",
            "timestamp": ahora,
        }
        estado.setdefault("excluidos", []).append(exclusion)

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
                    req.nota_edicion or "Retirado de esta vacante por el orientador; Matching recalculado.",
                )
        finally:
            conn.close()

        for post in estado["postulaciones"]:
            if post["perfil_id"] == req.perfil_id and post["oferta_id"] == req.oferta_id:
                post["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
                post["comentario_orientador"] = (
                    req.nota_edicion or "Retirado de esta vacante por el orientador; Matching recalculado."
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
                or "El orientador retiró al candidato de esta vacante y solicitó recálculo de asignaciones.",
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
        _guardar_estado_tablero_db(estado)
        return estado

    # 4. APROBAR: exige el consentimiento explícito del joven o del orientador, registra Consentimiento + Decision y reanuda el grafo (#49, #124)
    if req.accion == "aprobar":
        # Con sesgo del Auditor el caso volvió a Matching: la API frena igual que el botón, 422 sin efectos (#189).
        if (estado.get("auditoria") or {}).get("sesgo_detectado"):
            raise ErrorValidacionTablero(
                "El Auditor detectó sesgo en este lote: cambiar el nombre o el género altera el resultado. "
                "No se puede aprobar hasta que el caso vuelva a pasar el Auditor sin sesgo; no se envió nada."
            )
        # El «no» del joven en /joven (#52) manda sobre la casilla del orientador: 422 sin efectos.
        if _consentimiento_rechazado_por_joven(estado, req.perfil_id, req.oferta_id):
            raise ErrorValidacionTablero(
                f"{req.perfil_id} negó su consentimiento: no acepta que su CV llegue a {req.oferta_id}. "
                "Postulación no envía nada; el joven puede cambiar su decisión en /joven."
            )
        tiene_consentimiento = (
            req.consentimiento_joven is True
            or (
                req.consentimiento_joven is None
                and _consentimiento_vigente(estado, req.perfil_id, req.oferta_id)
            )
        )
        lista_consentidos = (
            req.consentidos
            if req.consentidos is not None
            else ([req.perfil_id] if tiene_consentimiento else [])
        )
        if not tiene_consentimiento or req.perfil_id not in lista_consentidos:
            raise ErrorValidacionTablero(
                f"El candidato {req.perfil_id} no tiene el consentimiento confirmado ('el joven consintió'). "
                "Postulación no envía ningún dato a la empresa sin consentimiento."
            )

        # Validación de ruta segura en outbox para el par antes de ejecutar
        _ = _ruta_segura_en_outbox(
            _ruta_outbox(), f"cv_{req.perfil_id}_{req.oferta_id}.pdf"
        )

        # El orientador confirma solo si el joven no dejó su propio registro (el «sí» de /joven queda con su medio).
        if _ultimo_consentimiento(estado, req.perfil_id, req.oferta_id) is None:
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
            grafo.update_state(
                cfg_grafo,
                {
                    "vacantes_resueltas": resueltas,
                    # Combinados: no pisa un «no» que ya estuviera en el grafo (#54).
                    "consentimientos": _consentimientos_combinados(estado),
                },
            )
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
            # Los del grafo primero y luego los del tablero; manda el último registro de la pareja (#52, #54).
            previos = _consentimientos_combinados(estado)
            consentimientos = consentimientos_para([(req.perfil_id, req.oferta_id)], previos, ahora)
            _ruta_outbox().mkdir(parents=True, exist_ok=True)
            conn = _conectar_db_estado(estado)
            try:
                if req.perfil_id not in perfiles_map or req.oferta_id not in ofertas_map:
                    raise ValueError(f"falta el perfil {req.perfil_id} o la oferta {req.oferta_id}")
                # Misma agenda que el nodo: no cruza otras entrevistas de la empresa ni del joven (#186).
                con_fecha, inicios, avisos = agendar_entrevistas(
                    [m_schema], perfiles_map, ofertas_map, conn, desde=datetime.now(ZONA_PANAMA)
                )
                ya_registradas = estado.get("_agenda_registrada") or set()
                for decision in decisiones_de_agenda([a for a in avisos if a[0] not in ya_registradas]):
                    db.registrar_decision(conn, decision)
                    estado["decisiones"].append(_serializar_decision(decision))
                if not con_fecha:
                    raise ValueError(f"sin franja compatible ({avisos[0][1] if avisos else 'sin hora posible'})")
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
                    outbox=_ruta_outbox(),
                    consentimientos=consentimientos,
                    # El mensaje que editó el orientador llega a la empresa, cegado (#187).
                    mensaje_orientador=req.mensaje_empresa or post_activo.get("mensaje_empresa") or "",
                    inicio_entrevista=lambda m: inicios[(m.perfil_id, m.oferta_id)],
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
        match_activo["consentimiento_joven"] = True
        post_activo["comentario_orientador"] = (
            req.nota_edicion or "Aprobado con consentimiento del joven y enviado a la empresa."
        )
        post_activo["archivos_generados"] = archivos_generados
        estado["estado_grafo"] = "postulacion_ejecutada"
        estado["metricas"] = _calcular_metricas_impacto(estado)
        _guardar_estado_tablero_db(estado)
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
    """Agrega una nueva oferta tras pasar por `limpiar_entrada`, `evaluar_entrada` y `puente.rules.discriminacion` (#120)."""
    req = NuevaOfertaRequest(
        puesto=puesto,
        empresa=empresa,
        corregimiento=corregimiento,
        salario_mensual=salario_mensual,
        requisitos_texto=requisitos_texto,
        habilidades=habilidades,
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Limpieza de PII (cédula y teléfono en ofertas) y guardrails reales (#9, #120)
    limpio_oferta = limpiar_entrada(
        {
            "ofertas": {
                "puesto": req.puesto.strip(),
                "empresa": req.empresa.strip(),
                "requisitos_texto": req.requisitos_texto,
            }
        }
    )["ofertas"]
    puesto_limpio = str(limpio_oferta["puesto"])
    empresa_limpia = str(limpio_oferta["empresa"])
    requisitos_limpios = str(limpio_oferta["requisitos_texto"])

    resultados_gr = evaluar_entrada(
        {"texto": f"{puesto_limpio}\n{requisitos_limpios}", "salario": req.salario_mensual}
    )
    for r_gr in resultados_gr:
        _registrar_decision_db(estado, r_gr.decision)
    rechazo_gr = next((r for r in resultados_gr if not r.ok), None)
    if rechazo_gr is not None:
        raise ErrorValidacionTablero(rechazo_gr.respuesta or rechazo_gr.motivo)

    bloqueada, alertas = _evaluar_reglas_discriminacion(requisitos_limpios)

    sufijo_uuid = uuid.uuid4().hex[:8].upper()
    nuevo_id = f"OF-{sufijo_uuid}"

    _ = Oferta(
        id=nuevo_id,
        puesto=puesto_limpio,
        empresa=empresa_limpia,
        corregimiento=req.corregimiento,
        salario_mensual=req.salario_mensual,
        horario=Horario(
            dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
            hora_inicio="08:00",
            hora_fin="17:00",
        ),
        habilidades=req.habilidades,
        requisitos_texto=requisitos_limpios,
        alertas=[a["frase"] for a in alertas],
        bloqueada=bloqueada,
        fuente=fuente,
    )

    nueva_oferta = {
        "id": nuevo_id,
        "puesto": puesto_limpio,
        "empresa": empresa_limpia,
        "corregimiento": req.corregimiento,
        "salario_mensual": req.salario_mensual,
        "horario": "Lunes a Viernes 08:00-17:00",
        "habilidades": req.habilidades,
        "requisitos_texto": requisitos_limpios,
        "alertas": alertas,
        "bloqueada": bloqueada,
        "fuente": fuente,
    }
    estado["ofertas"].append(nueva_oferta)
    _registrar_decision_db(
        estado,
        {
            "timestamp": ahora,
            # El tablero revisa la oferta con las reglas en código; el agente Mercado (LLM) no corre aquí (#193).
            "agente": AGENTE_TABLERO,
            "entrada_resumen": f"Nueva oferta {nuevo_id} ({puesto_limpio} en {req.corregimiento})",
            "salida_resumen": f"bloqueada={bloqueada} | alertas={len(alertas)}",
            "razon": (
                (alertas[0]["regla"] if alertas else "Reglas de la Ley 11 de 2005 en código: sin requisitos discriminatorios")
                + " · campos y habilidades tal como llegaron del formulario o del catálogo, sin el agente Mercado"
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
    """Registra un nuevo joven tras limpiar PII (`limpiar_entrada`) y evaluar `guardrails` (#9, #120)."""
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

    # Quitar cédula, teléfono y dirección exacta antes de guardar en estado, SQLite o decisiones (#120)
    entrada_limpia = limpiar_entrada(
        {
            "nombre": req.nombre.strip(),
            "relato_experiencia": req.relato_experiencia,
            "corregimiento": req.corregimiento,
        }
    )
    nombre_limpio = str(entrada_limpia["nombre"]).strip() or "Joven"
    relato_limpio = str(entrada_limpia["relato_experiencia"])

    resultados_gr = evaluar_entrada({"texto": relato_limpio, "edad": req.edad})
    for r_gr in resultados_gr:
        _registrar_decision_db(estado, r_gr.decision)
    rechazo_gr = next((r for r in resultados_gr if not r.ok), None)
    if rechazo_gr is not None:
        raise ErrorValidacionTablero(rechazo_gr.respuesta or rechazo_gr.motivo)

    slug_nom = _slug_seguro(nombre_limpio, prefijo="JOVEN")
    sufijo_uuid = uuid.uuid4().hex[:6].upper()
    nuevo_id = f"PERFIL-{slug_nom}-{sufijo_uuid}"

    habs = [
        {
            "skill_id": sid,
            "nombre": sid.replace("_", " ").title(),
            "evidencia": relato_limpio,
        }
        for sid in req.habilidades
    ]

    _ = Perfil(
        id=nuevo_id,
        nombre=nombre_limpio,
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
        "nombre": nombre_limpio,
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
            # El tablero arma el perfil con lo que llegó; el agente Perfil (LLM) no corre aquí (#193).
            "agente": AGENTE_TABLERO,
            "entrada_resumen": f"Nuevo perfil {nuevo_id} ({req.corregimiento})",
            "salida_resumen": f"{len(habs)} habilidad(es) registradas; Matching recalculado",
            "razon": (
                "Habilidades tal como llegaron del formulario o del catálogo, cada una con el relato sin PII "
                "como evidencia; sin el agente Perfil, que no extrajo ni verificó nada."
            ),
        },
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def listar_catalogo_data() -> dict[str, list[dict[str, str]]]:
    """Ofertas y perfiles de data/ (se leen de verdad, #188) más ejemplos sintéticos rotulados como tales."""
    ofertas_cat = [
        {
            "id": "ejemplo_sintetico_chorrera",
            "etiqueta": "ejemplo sintético (no es un archivo) · Cajero/a Tiempo Completo (La Chorrera, $650)",
        },
        {
            "id": "ejemplo_sintetico_CT11_edad",
            "etiqueta": "ejemplo sintético (no es un archivo) · Vendedor '18 a 25 años y buena presencia' (Bella Vista)",
        },
        {
            "id": "ejemplo_sintetico_K04_tocumen",
            "etiqueta": "ejemplo sintético (no es un archivo) · Auxiliar de Bodega (Tocumen, $640)",
        },
    ]
    perfiles_cat = [
        {
            "id": "ejemplo_sintetico_yaritza",
            "etiqueta": "ejemplo sintético (no es un archivo) · Yaritza (20 años, La Chorrera, fonda familiar)",
        },
        {
            "id": "ejemplo_sintetico_perfil_b",
            "etiqueta": "ejemplo sintético (no es un archivo) · Joven voluntario en feria libre (Arraiján)",
        },
    ]
    dir_of = RUTA_DATA / "ofertas"
    if dir_of.exists():
        for arch in sorted(dir_of.glob("*.txt")):  # todas: con [:10] quedaban fuera las de Konzerta (#188)
            rel = f"data/ofertas/{arch.name}"
            ofertas_cat.append({"id": rel, "etiqueta": f"{rel} · anuncio real, lo lee Mercado"})
    dir_pf = RUTA_DATA / "perfiles"
    if dir_pf.exists():
        for arch in sorted(dir_pf.glob("*.json")):
            rel = f"data/perfiles/{arch.name}"
            perfiles_cat.append({"id": rel, "etiqueta": f"{rel} · perfil sintético del archivo"})
    return {"ofertas": ofertas_cat, "perfiles": perfiles_cat}


def cargar_desde_catalogo_data(
    estado: dict[str, Any],
    *,
    tipo: str,
    item_id: str,
) -> dict[str, Any]:
    """Carga una oferta o perfil validando que `item_id` pertenezca al catálogo permitido (#120)."""
    req = CargarDataRequest(tipo=tipo, item_id=item_id)  # type: ignore[arg-type]
    catalogo = listar_catalogo_data()
    clave_cat = "ofertas" if req.tipo == "oferta" else "perfiles"
    ids_permitidos = {item["id"] for item in catalogo.get(clave_cat, [])}
    if ".." in req.item_id or "\\" in req.item_id or req.item_id not in ids_permitidos:
        raise ErrorValidacionTablero(
            f"El item_id '{req.item_id}' no existe en el catálogo permitido de '{req.tipo}'."
        )

    if req.item_id.startswith("data/ofertas/"):
        return _cargar_oferta_desde_archivo(estado, RUTA_DATA / "ofertas" / Path(req.item_id).name)
    if req.item_id.startswith("data/perfiles/"):
        return _cargar_perfil_desde_archivo(estado, RUTA_DATA / "perfiles" / Path(req.item_id).name)

    # Ejemplos sintéticos escritos aquí (no son archivos de data/; el catálogo los rotula así).
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


def cargar_escenario_cobertura_turnos(estado: dict[str, Any]) -> dict[str, Any]:
    """Carga la oferta de turno completo 08:00-17:00 en La Chorrera y dos estudiantes complementarios (#165)."""
    ids_perfiles = {p["id"] for p in estado.get("perfiles", [])}
    perfiles_turnos = [
        {
            "id": "PERFIL-SOFIA-MANANA",
            "nombre": "Sofía (estudia tarde)",
            "genero": "femenino",
            "edad": 20,
            "corregimiento": "La Chorrera",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · turno matutino",
            "horario_libre": "Lunes a Viernes 07:30-12:45 (clases 13:30-18:00)",
            "horario_libre_bloques": [
                {
                    "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                    "hora_inicio": "07:30",
                    "hora_fin": "12:45",
                }
            ],
            "horario_clases_bloques": [
                {
                    "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                    "hora_inicio": "13:30",
                    "hora_fin": "18:00",
                }
            ],
            "salario_minimo": 300.0,
            "presupuesto_pasaje_diario": 2.50,
            "habilidades": [
                {
                    "skill_id": "facturacion",
                    "nombre": "Facturación y cobro",
                    "evidencia": "Emisión de facturas y recibos en librería familiar por las mañanas",
                },
                {
                    "skill_id": "control_calidad",
                    "nombre": "Control de calidad",
                    "evidencia": "Revisión de mercancía recibida antes de abrir mostrador",
                },
            ],
        },
        {
            "id": "PERFIL-MATEO-TARDE",
            "nombre": "Mateo (estudia mañana)",
            "genero": "masculino",
            "edad": 21,
            "corregimiento": "La Chorrera",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · turno vespertino",
            "horario_libre": "Lunes a Viernes 12:15-17:30 (clases 07:30-11:45)",
            "horario_libre_bloques": [
                {
                    "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                    "hora_inicio": "12:15",
                    "hora_fin": "17:30",
                }
            ],
            "horario_clases_bloques": [
                {
                    "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                    "hora_inicio": "07:30",
                    "hora_fin": "11:45",
                }
            ],
            "salario_minimo": 300.0,
            "presupuesto_pasaje_diario": 2.50,
            "habilidades": [
                {
                    "skill_id": "control_calidad",
                    "nombre": "Control de calidad",
                    "evidencia": "Verificación de lotes y fechas de vencimiento en abarrotería por las tardes",
                },
                {
                    "skill_id": "facturacion",
                    "nombre": "Facturación y cobro",
                    "evidencia": "Cierre de facturación vespertina en negocio barrial",
                },
            ],
        },
    ]
    for p_nuevo in perfiles_turnos:
        if p_nuevo["id"] not in ids_perfiles:
            estado["perfiles"].append(p_nuevo)

    ids_ofertas = {o["id"] for o in estado.get("ofertas", [])}
    if "OF-CHORRERA-TURNO-01" not in ids_ofertas:
        estado["ofertas"].append(
            {
                "id": "OF-CHORRERA-TURNO-01",
                "puesto": "Auxiliar de Facturación y Control de Calidad (Jornada Completa)",
                "empresa": "Farmacia Oeste S.A. (Empresa de Prueba)",
                "corregimiento": "La Chorrera",
                "salario_mensual": 650.0,
                "horario": "Lunes a Viernes 08:00-17:00",
                "habilidades": ["facturacion", "control_calidad"],
                "requisitos_texto": "Jornada completa de 08:00 a 17:00 en La Chorrera para facturación y control de calidad.",
                "fuente": "datos de ejemplo (sintéticos)",
                "bloqueada": False,
                "alertas": [],
            }
        )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def aprobar_cobertura_turnos(
    estado: dict[str, Any],
    *,
    oferta_id: str,
    consentimiento_manana: bool | None = None,
    consentimiento_tarde: bool | None = None,
    orientador: str = "Orientadora Ana Gómez",
) -> dict[str, Any]:
    """Aprueba una propuesta de cobertura por turnos creando las dos postulaciones con consentimiento de cada joven (#165)."""
    req = AprobarCoberturaTurnosRequest(
        oferta_id=oferta_id,
        consentimiento_manana=consentimiento_manana,
        consentimiento_tarde=consentimiento_tarde,
        orientador=orientador,
    )
    coberturas = estado.get("coberturas_turnos") or []
    cob = next((c for c in coberturas if c.get("oferta_id") == req.oferta_id), None)
    if cob is None or len(cob.get("segmentos", [])) < 2:
        raise ErrorValidacionTablero(
            f"No existe propuesta de cobertura por turnos para la oferta '{req.oferta_id}'."
        )
    seg_m, seg_t = cob["segmentos"][0], cob["segmentos"][1]
    pid_m, pid_t = seg_m["perfil_id"], seg_t["perfil_id"]

    if _consentimiento_rechazado_por_joven(estado, pid_m, req.oferta_id) or _consentimiento_rechazado_por_joven(
        estado, pid_t, req.oferta_id
    ):
        raise ErrorValidacionTablero(
            "Uno de los jóvenes indicó que no acepta enviar su CV a esta vacante; la cobertura no se puede aprobar."
        )

    ok_m = req.consentimiento_manana is True or (
        req.consentimiento_manana is None and _consentimiento_vigente(estado, pid_m, req.oferta_id)
    )
    ok_t = req.consentimiento_tarde is True or (
        req.consentimiento_tarde is None and _consentimiento_vigente(estado, pid_t, req.oferta_id)
    )
    if not ok_m or not ok_t:
        raise ErrorValidacionTablero(
            "Se requiere confirmar el consentimiento explícito de ambos jóvenes (mañana y tarde) antes de aprobar la cobertura por turnos (#165)."
        )

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for pid, seg in ((pid_m, seg_m), (pid_t, seg_t)):
        if not _consentimiento_vigente(estado, pid, req.oferta_id):
            estado.setdefault("consentimientos", []).append(
                {
                    "perfil_id": pid,
                    "oferta_id": req.oferta_id,
                    "otorgado": True,
                    "medio": "orientador_tablero",
                    "timestamp": ahora,
                }
            )
        seg["consentimiento_joven"] = True

    # Construir modelos desde estado para invocar al agente de postulación real (#165)
    modelos_perfiles, _, modelos_ofertas, _ = _construir_modelos_desde_estado(estado)
    perfiles_map = {p.id: p for p in modelos_perfiles}
    ofertas_map = {o.id: o for o in modelos_ofertas}

    for p_raw in estado.get("perfiles", []):
        if p_raw.get("id") in (pid_m, pid_t) and p_raw.get("id") not in perfiles_map:
            perfiles_map[p_raw["id"]] = Perfil.model_validate(p_raw)
    for o_raw in estado.get("ofertas", []):
        if o_raw.get("id") == req.oferta_id and o_raw.get("id") not in ofertas_map:
            ofertas_map[o_raw["id"]] = Oferta.model_validate(o_raw)

    def _desglose_segmento(seg_info: dict[str, Any]) -> dict[str, Any]:
        d = dict(seg_info.get("desglose") or {})
        d.setdefault("cobertura", 1.0)
        d.setdefault("minutos_traslado", 0)
        d.setdefault("pasaje_diario", 0.0)
        d.setdefault("penalizacion", 0)
        return d

    matches_turnos = [
        Match(
            perfil_id=pid_m,
            oferta_id=req.oferta_id,
            puntaje=seg_m.get("puntaje", 80),
            desglose=_desglose_segmento(seg_m),
            brechas=list(seg_m.get("brechas") or []),
            explicacion=f"Cobertura por turnos ({seg_m['inicio']}-{seg_m['fin']}) aprobada por {req.orientador}",
        ),
        Match(
            perfil_id=pid_t,
            oferta_id=req.oferta_id,
            puntaje=seg_t.get("puntaje", 80),
            desglose=_desglose_segmento(seg_t),
            brechas=list(seg_t.get("brechas") or []),
            explicacion=f"Cobertura por turnos ({seg_t['inicio']}-{seg_t['fin']}) aprobada por {req.orientador}",
        ),
    ]

    aprobacion = {
        "orientador": req.orientador,
        "timestamp": ahora,
        "aprobados": [
            {"perfil_id": pid_m, "oferta_id": req.oferta_id},
            {"perfil_id": pid_t, "oferta_id": req.oferta_id},
        ],
    }

    previos = _consentimientos_combinados(estado)
    consentimientos = consentimientos_para(
        [(pid_m, req.oferta_id), (pid_t, req.oferta_id)],
        previos,
        ahora,
    )
    _ruta_outbox().mkdir(parents=True, exist_ok=True)
    conn = _conectar_db_estado(estado)
    try:
        res_post = postular(
            matches_turnos,
            perfiles_map,
            ofertas_map,
            aprobacion=aprobacion,
            conn=conn,
            outbox=_ruta_outbox(),
            consentimientos=consentimientos,
            mensaje_orientador=f"Propuesta de cobertura compartida por turnos ({seg_m['inicio']}-{seg_m['fin']} y {seg_t['inicio']}-{seg_t['fin']}) aprobada por {req.orientador}.",
        )
    except ValueError as error:
        raise ErrorValidacionTablero(f"No se envió la cobertura por turnos a la empresa: {error}") from error
    finally:
        conn.close()

    for d_post in res_post.decisiones:
        d_ser = _serializar_decision(d_post)
        if d_ser not in estado["decisiones"]:
            estado["decisiones"].append(d_ser)

    archivos_nombres = [arch.name for arch in res_post.archivos]

    for idx, seg in enumerate((seg_m, seg_t)):
        pid = seg["perfil_id"]
        archivos_perfil = [
            arch.name for arch in res_post.archivos
            if pid in arch.name or req.oferta_id in arch.name
        ] or archivos_nombres

        post_existente = _buscar_postulacion(estado, pid, req.oferta_id)
        db_id = res_post.postulaciones[idx] if idx < len(res_post.postulaciones) else None
        if post_existente is None:
            estado.setdefault("postulaciones", []).append(
                {
                    "id": f"POST-{pid}-{req.oferta_id}",
                    "db_id": db_id,
                    "perfil_id": pid,
                    "nombre_perfil": seg.get("nombre_perfil", pid),
                    "oferta_id": req.oferta_id,
                    "puesto": f"{cob.get('puesto', req.oferta_id)} · Turno {seg['inicio']}-{seg['fin']}",
                    "empresa": cob.get("empresa", ""),
                    "puntaje": seg.get("puntaje", 0),
                    "estado": EstadoPostulacion.ENVIADO.value,
                    "comentario_orientador": f"Cobertura por turnos ({seg['inicio']}-{seg['fin']}) aprobada por {req.orientador}",
                    "mensaje_empresa": cob.get("explicacion", ""),
                    "consentimiento_joven": True,
                    "archivos_generados": archivos_perfil,
                }
            )
        else:
            if db_id is not None:
                post_existente["db_id"] = db_id
            post_existente["estado"] = EstadoPostulacion.ENVIADO.value
            post_existente["consentimiento_joven"] = True
            post_existente["comentario_orientador"] = f"Cobertura por turnos ({seg['inicio']}-{seg['fin']}) aprobada por {req.orientador}"
            post_existente["archivos_generados"] = archivos_perfil

    _registrar_decision_db(
        estado,
        Decision(
            agente="orientador",
            entrada_resumen=f"cobertura_turnos -> {req.oferta_id} ({pid_m} + {pid_t})",
            salida_resumen=(
                f"Cobertura por turnos aprobada por {req.orientador}: "
                f"{pid_m} ({seg_m['inicio']}-{seg_m['fin']}) y {pid_t} ({seg_t['inicio']}-{seg_t['fin']})"
            ),
            razon=cob.get("motivo", "Aprobación de cobertura por turnos con consentimiento de ambos jóvenes."),
            timestamp=ahora,
        ),
    )
    estado["metricas"] = _calcular_metricas_impacto(estado)
    _guardar_estado_tablero_db(estado)
    return estado


def actualizar_umbrales_solver(
    estado: dict[str, Any],
    *,
    max_minutos_traslado: int | None = None,
    max_porcentaje_salario_pasaje: float | None = None,
    max_porcentaje_pasaje: float | None = None,
    max_candidatos_por_vacante: int | None = None,
    max_vacantes_por_perfil: int | None = None,
    orientador: str = "Orientadora Ana Gómez",
) -> dict[str, Any]:
    """Actualiza los umbrales editables del solver CP-SAT, registra la Decision y recalcula (#168)."""
    req = UmbralesSolverRequest(
        max_minutos_traslado=max_minutos_traslado,
        max_porcentaje_salario_pasaje=max_porcentaje_salario_pasaje,
        max_porcentaje_pasaje=max_porcentaje_pasaje,
        max_candidatos_por_vacante=max_candidatos_por_vacante,
        max_vacantes_por_perfil=max_vacantes_por_perfil,
        orientador=orientador,
    )
    cfg_actual = dict(
        estado.get("config_solver")
        or {
            "max_minutos_traslado": 60,
            "max_porcentaje_salario_pasaje": 0.15,
            "max_candidatos_por_vacante": 3,
            "max_vacantes_por_perfil": 2,
        }
    )
    explicitos = set(estado.get("umbrales_explicitos") or [])
    cambios_txt: list[str] = []

    if req.max_minutos_traslado is not None:
        ant = cfg_actual.get("max_minutos_traslado", 60)
        cfg_actual["max_minutos_traslado"] = int(req.max_minutos_traslado)
        explicitos.add("max_minutos_traslado")
        cambios_txt.append(f"max_minutos_traslado: {ant} -> {req.max_minutos_traslado} min")

    pct_val = req.max_porcentaje_salario_pasaje
    if pct_val is None and req.max_porcentaje_pasaje is not None:
        pct_val = (
            req.max_porcentaje_pasaje / 100.0
            if req.max_porcentaje_pasaje > 1.0
            else req.max_porcentaje_pasaje
        )
    if pct_val is not None:
        ant = cfg_actual.get("max_porcentaje_salario_pasaje", 0.15)
        cfg_actual["max_porcentaje_salario_pasaje"] = round(float(pct_val), 4)
        explicitos.add("max_porcentaje_salario_pasaje")
        cambios_txt.append(
            f"max_porcentaje_salario_pasaje: {int(round(float(ant) * 100))}% -> {int(round(float(pct_val) * 100))}%"
        )

    if req.max_candidatos_por_vacante is not None:
        ant = cfg_actual.get("max_candidatos_por_vacante", 3)
        cfg_actual["max_candidatos_por_vacante"] = int(req.max_candidatos_por_vacante)
        explicitos.add("max_candidatos_por_vacante")
        cambios_txt.append(f"k_por_vacante: {ant} -> {req.max_candidatos_por_vacante}")

    if req.max_vacantes_por_perfil is not None:
        ant = cfg_actual.get("max_vacantes_por_perfil", 2)
        cfg_actual["max_vacantes_por_perfil"] = int(req.max_vacantes_por_perfil)
        explicitos.add("max_vacantes_por_perfil")
        cambios_txt.append(f"max_vacantes_por_perfil: {ant} -> {req.max_vacantes_por_perfil}")

    if not cambios_txt:
        raise ErrorValidacionTablero("Debe indicar al menos un umbral del solver para actualizar.")

    estado["config_solver"] = cfg_actual
    estado["umbrales_editados_orientador"] = True
    estado["umbrales_explicitos"] = sorted(explicitos)

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _registrar_decision_db(
        estado,
        Decision(
            agente="orientador",
            entrada_resumen=f"umbrales_solver ({req.orientador})",
            salida_resumen=f"Umbrales del solver actualizados: {', '.join(cambios_txt)}",
            razon=f"El orientador ({req.orientador}) ajustó los parámetros determinísticos de ConfigSolver y recalculó el matching.",
            timestamp=ahora,
        ),
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def registrar_evento_empresa_tablero(
    estado: dict[str, Any],
    *,
    tipo: Literal["rechaza", "cubierta", "acepta"],
    oferta_id: str,
    perfil_id: str | None = None,
    nota: str = "",
    empresa: str = "Empresa",
) -> dict[str, Any]:
    """Registra una acción desde la vista de empresa con `agente='empresa'` y recalcula si aplica (#168, #54)."""
    req = EventoEmpresaTableroRequest(
        tipo=tipo,
        oferta_id=oferta_id,
        perfil_id=perfil_id,
        nota=nota,
        empresa=empresa,
    )
    oferta = next((o for o in estado.get("ofertas", []) if o.get("id") == req.oferta_id), None)
    if oferta is None:
        raise ErrorValidacionTablero(f"No existe la oferta '{req.oferta_id}'.")

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nombre_emp = oferta.get("empresa") or req.empresa
    def _actualizar_post_sql(conn_db: sqlite3.Connection, post_id: int, nuevo_est: str, motivo_txt: str) -> None:
        try:
            db.cambiar_estado(conn_db, post_id, nuevo_est, motivo_txt)
        except ValueError as exc:
            raise ErrorValidacionTablero(f"Transición no permitida para la postulación: {exc}") from exc

    conn = _conectar_db_estado(estado)
    try:
        if req.tipo == "rechaza" and req.perfil_id:
            post = _buscar_postulacion(estado, req.perfil_id, req.oferta_id)
            if post is not None:
                _actualizar_post_sql(
                    conn,
                    int(post["db_id"]),
                    db.NO_SELECCIONADO,
                    req.nota or f"Rechazado por {nombre_emp}",
                )
                post["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
            excl = estado.setdefault("excluidos", [])
            if not any(
                e.get("perfil_id") == req.perfil_id and e.get("oferta_id") == req.oferta_id
                for e in excl
            ):
                excl.append(
                    {
                        "perfil_id": req.perfil_id,
                        "oferta_id": req.oferta_id,
                        "origen": "empresa",
                        "motivo": req.nota or f"Rechazado por {nombre_emp}",
                    }
                )
        elif req.tipo == "acepta" and req.perfil_id:
            post = _buscar_postulacion(estado, req.perfil_id, req.oferta_id)
            if post is not None:
                _actualizar_post_sql(
                    conn,
                    int(post["db_id"]),
                    db.ENTREVISTA,
                    req.nota or f"Citado a entrevista por {nombre_emp}",
                )
                post["estado"] = EstadoPostulacion.ENTREVISTA.value
        elif req.tipo == "cubierta":
            for post in estado.get("postulaciones", []):
                if post.get("oferta_id") == req.oferta_id:
                    nuevo_est = (
                        EstadoPostulacion.CONTRATADO.value
                        if req.perfil_id and post.get("perfil_id") == req.perfil_id
                        else EstadoPostulacion.NO_SELECCIONADO.value
                    )
                    _actualizar_post_sql(
                        conn,
                        int(post["db_id"]),
                        nuevo_est,
                        req.nota or f"Vacante cubierta en {nombre_emp}",
                    )
                    post["estado"] = nuevo_est
    finally:
        conn.close()

    _registrar_decision_db(
        estado,
        Decision(
            agente="empresa",
            entrada_resumen=f"{nombre_emp} -> {req.oferta_id}" + (f" ({req.perfil_id})" if req.perfil_id else ""),
            salida_resumen=f"Respuesta de empresa ({req.tipo}) sobre {req.oferta_id}",
            razon=req.nota or f"Acción '{req.tipo}' registrada en la vista de empresa por {nombre_emp}.",
            timestamp=ahora,
        ),
    )
    if req.tipo in ("rechaza", "cubierta"):
        return recalcular_pipeline_tablero(estado, registrar_trazas=True)
    estado["metricas"] = _calcular_metricas_impacto(estado)
    _guardar_estado_tablero_db(estado)
    return estado


def obtener_panel_operacion(estado: dict[str, Any]) -> dict[str, Any]:
    """Construye el panel de operación admin: uso/costo del LLM, modo, grabaciones, canales y umbrales (#168)."""
    uso_agentes: dict[str, Any] = {}
    uso_tot: dict[str, Any] = {
        "llamadas": 0,
        "tokens_entrada": 0,
        "tokens_salida": 0,
        "tokens_cache_lectura": 0,
        "tokens_cache_escritura": 0,
        "costo_usd": 0.0,
    }
    try:
        from puente.llm import uso_por_agente, uso_total

        uso_agentes = uso_por_agente()
        uso_tot = uso_total()
    except Exception:
        pass

    dir_grab_data = RUTA_DATA / "grabaciones"
    dir_grab_evals = RUTA_RAIZ / "evals" / "grabaciones"
    n_grab_data = len(list(dir_grab_data.glob("*.json"))) if dir_grab_data.exists() else 0
    n_grab_evals = len(list(dir_grab_evals.glob("*.json"))) if dir_grab_evals.exists() else 0

    cfg_solver = dict(
        estado.get("config_solver")
        or {
            "max_minutos_traslado": 60,
            "max_porcentaje_salario_pasaje": 0.15,
            "max_candidatos_por_vacante": 3,
            "max_vacantes_por_perfil": 2,
        }
    )
    return {
        "llm_modo": os.getenv("LLM_MODO", "reproducir").strip() or "reproducir",
        "uso_por_agente": uso_agentes,
        "uso_total": uso_tot,
        "grabaciones_disponibles": {
            "data_grabaciones": n_grab_data,
            "evals_grabaciones": n_grab_evals,
            "total": n_grab_data + n_grab_evals,
        },
        "telegram_estado": "activo" if os.getenv("TELEGRAM_BOT_TOKEN") else "outbox_local",
        "whatsapp_estado": "activo" if os.getenv("TWILIO_AUTH_TOKEN") else "sandbox_inactivo",
        "umbrales_solver": cfg_solver,
        "roles_sistema": [
            {"rol": "admin", "nombre": "Admin (Orientador)", "ruta": "/", "agente": "orientador"},
            {"rol": "aplicante", "nombre": "Aplicante (Joven)", "ruta": "/joven", "agente": "joven"},
            {"rol": "empresa", "nombre": "Empresa", "ruta": "/empresa", "agente": "empresa"},
        ],
    }


def _rechazo_guardrails(estado: dict[str, Any], datos: dict[str, Any]) -> str | None:
    """Guardrails (#9) sobre lo que llega de un archivo de data/; deja sus Decisiones y devuelve el rechazo, si hay."""
    resultados = evaluar_entrada(datos)
    for resultado in resultados:
        _registrar_decision_db(estado, resultado.decision)
    rechazo = next((r for r in resultados if not r.ok), None)
    return (rechazo.respuesta or rechazo.motivo) if rechazo else None


def _cargar_oferta_desde_archivo(estado: dict[str, Any], ruta: Path) -> dict[str, Any]:
    """Lee un anuncio de data/ofertas con el agente Mercado (puente.llm; en reproducir, las grabaciones de #139)."""
    rel = f"data/ofertas/{ruta.name}"
    # Sin cédula ni teléfono antes de Mercado y del tablero; la dirección de la empresa se queda (#95, #188).
    texto = limpiar_entrada({"ofertas": ruta.read_text(encoding="utf-8")})["ofertas"]
    rechazo = _rechazo_guardrails(estado, {"texto": texto})
    if rechazo:
        raise ErrorValidacionTablero(rechazo)
    resultado = mercado.procesar_ofertas([{"texto": texto, "fuente": rel}])
    for decision in resultado.decisiones:
        _registrar_decision_db(estado, decision)
    if not resultado.ofertas:
        motivo = resultado.errores[0] if resultado.errores else "sin detalle"
        raise ErrorValidacionTablero(f"Mercado no pudo leer {rel}: {motivo}")
    oferta = resultado.ofertas[0]
    nuevo_id = oferta.id.upper()  # el id de Mercado sale del texto: el mismo archivo da el mismo id
    if any(o.get("id") == nuevo_id for o in estado.get("ofertas", [])):
        raise ErrorValidacionTablero(f"{rel} ya está en el tablero como {nuevo_id}.")
    bloqueada, alertas = _evaluar_reglas_discriminacion(texto)  # Ley 11 sobre el texto del anuncio, no un resumen
    estado["ofertas"].append(
        {
            "id": nuevo_id,
            "puesto": oferta.puesto,
            "empresa": oferta.empresa,
            "corregimiento": oferta.corregimiento,
            "salario_mensual": oferta.salario_mensual,
            "horario": _texto_horarios([oferta.horario]) if oferta.horario else None,
            "habilidades": oferta.habilidades,
            "requisitos_texto": oferta.requisitos_texto,
            "alertas": alertas,
            "bloqueada": bloqueada,
            "fuente": rel,
        }
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def _cargar_perfil_desde_archivo(estado: dict[str, Any], ruta: Path) -> dict[str, Any]:
    """Suma al tablero un perfil sintético de data/perfiles tal como está en el archivo (#188)."""
    rel = f"data/perfiles/{ruta.name}"
    try:
        # Todo texto del archivo (nombre, evidencias) sin cédula, teléfono ni dirección exacta, como cualquier
        # otra entrada; el corregimiento se conserva (#188).
        perfil = Perfil.model_validate(limpiar_entrada(json.loads(ruta.read_text(encoding="utf-8"))))
    except ValueError as exc:
        raise ErrorValidacionTablero(f"{rel} no es un perfil válido: {exc}") from exc
    perfil = perfil.model_copy(update={"id": f"PERFIL-DATA-{perfil.id}"})
    if _buscar_perfil(estado, perfil.id) is not None:
        raise ErrorValidacionTablero(f"{rel} ya está en el tablero como {perfil.id}.")
    evidencias = " ".join(h.evidencia for h in perfil.habilidades)
    rechazo = _rechazo_guardrails(estado, {"texto": evidencias, "edad": perfil.edad})
    if rechazo:
        raise ErrorValidacionTablero(rechazo)
    estado["perfiles"].append(_perfil_para_tablero(perfil, f"{rel} (sintético)", cargar_taxonomia_grafo()))
    _registrar_decision_db(
        estado,
        {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agente": "perfil",
            "entrada_resumen": f"Perfil {perfil.id} desde {rel}",
            "salida_resumen": f"{len(perfil.habilidades)} habilidad(es) con su evidencia del archivo; Matching recalculado",
            "razon": "Perfil sintético de data/ cargado tal cual, con sus horarios y evidencias.",
        },
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)

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
        # `entradas_perfil` (#52) trae las respuestas en texto libre de cada joven: no sale.
        if not k.startswith("_") and k != "entradas_perfil"
    }
    es_publica = es_modo_demo_publica()
    payload["conteo_embudo"] = conteo_embudo
    payload["catalogo_data"] = listar_catalogo_data()
    payload["decisiones_db"] = _leer_decisiones_db(estado)
    payload["metricas"] = _calcular_metricas_impacto(estado)
    payload["operacion"] = obtener_panel_operacion(estado)
    payload["modo_demo_publica"] = es_publica
    payload["aviso_demo_publica"] = (
        "Demo pública de solo lectura (estado efímero): corre con LLM_MODO=reproducir sin claves y el estado se reinicia entre invocaciones."
        if es_publica
        else None
    )
    return payload


async def _leer_json_validado(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Verifica Content-Type: application/json (415) y parsea el cuerpo JSON (422 si es inválido)."""
    # Media type exacto: `text/plain; x=application/json` no dispara el preflight del navegador (CSRF, #52).
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        return None, JSONResponse(
            {"detail": "Se requiere encabezado Content-Type: application/json"},
            status_code=415,
        )
    sitio = request.headers.get("sec-fetch-site")
    origen = request.headers.get("origin")
    if (sitio is not None and sitio == "cross-site") or (
        sitio is None and origen and urlsplit(origen).netloc != request.headers.get("host", "")
    ):
        return None, JSONResponse({"detail": "Solicitud desde otro sitio rechazada"}, status_code=403)
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
    ruta_actual = ESTADO_GLOBAL.get("_db_path")
    ESTADO_GLOBAL.clear()
    ESTADO_GLOBAL.update(obtener_datos_iniciales_demo(db_path=ruta_actual, reiniciar=True))
    anunciar_codigos_joven(ESTADO_GLOBAL)  # #52: códigos nuevos de /joven en la consola
    empresa.anunciar_codigos_empresa(ESTADO_GLOBAL)  # #167: y los de /empresa/lista-corta
    _guardar_estado_tablero_db(ESTADO_GLOBAL)
    return await endpoint_estado(request)


async def endpoint_cobertura_turnos_demo(request: Request) -> JSONResponse:
    _, err_resp = await _leer_json_validado(request)
    if err_resp is not None:
        return err_resp
    cargar_escenario_cobertura_turnos(ESTADO_GLOBAL)
    anunciar_codigos_joven(ESTADO_GLOBAL)
    return await endpoint_estado(request)


async def endpoint_aprobar_cobertura_turnos(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = AprobarCoberturaTurnosRequest.model_validate(datos)
        aprobar_cobertura_turnos(
            ESTADO_GLOBAL,
            oferta_id=req.oferta_id,
            consentimiento_manana=req.consentimiento_manana,
            consentimiento_tarde=req.consentimiento_tarde,
            orientador=req.orientador,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_umbrales_solver(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = UmbralesSolverRequest.model_validate(datos)
        actualizar_umbrales_solver(
            ESTADO_GLOBAL,
            max_minutos_traslado=req.max_minutos_traslado,
            max_porcentaje_salario_pasaje=req.max_porcentaje_salario_pasaje,
            max_porcentaje_pasaje=req.max_porcentaje_pasaje,
            max_candidatos_por_vacante=req.max_candidatos_por_vacante,
            max_vacantes_por_perfil=req.max_vacantes_por_perfil,
            orientador=req.orientador,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_admin_operacion(_request: Request) -> JSONResponse:
    return JSONResponse(obtener_panel_operacion(ESTADO_GLOBAL))


async def endpoint_empresa_pagina(_request: Request) -> FileResponse:
    return FileResponse(RUTA_STATIC / "empresa.html")


async def endpoint_empresa_postulaciones(_request: Request) -> JSONResponse:
    """Devuelve las postulaciones enviadas a la empresa sin exponer datos internos del orientador (#168)."""
    postulaciones_empresa = [
        {
            "id": p.get("id"),
            "perfil_id": p.get("perfil_id"),
            "nombre_perfil": p.get("nombre_perfil"),
            "oferta_id": p.get("oferta_id"),
            "puesto": p.get("puesto"),
            "empresa": p.get("empresa"),
            "estado": p.get("estado"),
        }
        for p in ESTADO_GLOBAL.get("postulaciones", [])
        if p.get("estado") in (db.ENVIADO, db.ENTREVISTA, db.CONTRATADO, db.NO_SELECCIONADO)
    ]
    return JSONResponse({"postulaciones": postulaciones_empresa})


async def endpoint_empresa_evento(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = EventoEmpresaTableroRequest.model_validate(datos)
        if req.perfil_id and not _buscar_perfil(ESTADO_GLOBAL, req.perfil_id):
            return JSONResponse({"detail": f"Perfil '{req.perfil_id}' no encontrado."}, status_code=404)
        registrar_evento_empresa_tablero(
            ESTADO_GLOBAL,
            tipo=req.tipo,
            oferta_id=req.oferta_id,
            perfil_id=req.perfil_id,
            nota=req.nota,
            empresa=req.empresa,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse({
        "ok": True,
        "tipo": req.tipo,
        "oferta_id": req.oferta_id,
        "perfil_id": req.perfil_id,
    })


# --- Vista del joven (#52, fase 1) ---
# El joven llena el formulario fijo y la entrevista del agente Perfil (#6), decide si consiente cada
# envío y ve su resultado. Solo se agregan funciones: las del tablero (#49/#99) quedan igual.
# El mensaje para el joven lo arma Postulación (#12, PR #84); esta vista no redacta texto propio.

MEDIO_CONSENTIMIENTO_JOVEN = "joven_web"
MAX_CARACTERES_RESPUESTA = 2000
# El consentimiento se da antes del envío: con la pareja en la lista corta o ya aprobada.
ESTADOS_ADMITEN_CONSENTIMIENTO = (
    EstadoPostulacion.PRESELECCIONADO.value,
    EstadoPostulacion.APROBADO.value,
)
ESTADOS_ENVIADOS = (
    EstadoPostulacion.ENVIADO.value,
    EstadoPostulacion.ENTREVISTA.value,
    EstadoPostulacion.CONTRATADO.value,
)
ESTADOS_CERRADOS = (*ESTADOS_ENVIADOS, EstadoPostulacion.NO_SELECCIONADO.value)
NOMBRES_DIAS = {"miercoles": "miércoles", "sabado": "sábado"}

# Perfil (#6) extrae las habilidades con este LLM a través de `puente.llm` (#45); las pruebas lo
# cambian por uno falso. El LLM solo propone: el código verifica taxonomía y evidencia literal.
llm_perfil_joven: Callable[[str], Any] = llm_puente

_ADAPTADOR_ENTRADA_PERFIL = TypeAdapter(EntradaPerfil)
_ADAPTADOR_CONSENTIMIENTO = TypeAdapter(Consentimiento)


def _id_seguro(valor: str) -> str:
    """Misma regla que `_validar_id_seguro` del tablero: solo [A-Za-z0-9_-]."""
    limpio = str(valor).strip()
    if not limpio or len(limpio) > 80 or not PATRON_ID_SEGURO.fullmatch(limpio):
        raise ValueError(f"ID inválido '{valor}': solo se permiten caracteres [A-Za-z0-9_-]")
    return limpio


# Credencial de cada joven: el id sale en /api/estado, así que no basta. Un token por perfil,
# guardado en `_tokens_joven` (las claves con "_" no se serializan) y que solo ve el joven.
ENCABEZADO_TOKEN_JOVEN = "x-token-joven"
_registro = logging.getLogger("uvicorn.error")


class CredencialJovenInvalida(PermissionError):
    pass


def _crear_token_joven(estado: dict[str, Any], perfil_id: str) -> str:
    token = secrets.token_urlsafe(24)
    estado.setdefault("_tokens_joven", {})[perfil_id] = token
    return token


def anunciar_codigos_joven(estado: dict[str, Any]) -> dict[str, str]:
    """Crea el token de los perfiles que no lo tienen (los semilla) y escribe su código en la consola.

    Solo quien corre el servidor (el orientador) ve esos códigos; no salen por ninguna ruta HTTP.
    """
    tokens = estado.setdefault("_tokens_joven", {})
    for p in estado.get("perfiles", []):
        if p.get("id") and p["id"] not in tokens:
            tokens[p["id"]] = secrets.token_urlsafe(24)
            _registro.info("Vista del joven · %s (%s): /joven#codigo=%s.%s", p["id"], p.get("nombre", ""), p["id"], tokens[p["id"]])
    return tokens


def verificar_token_joven(estado: dict[str, Any], perfil_id: str, token: str | None) -> None:
    esperado = (estado.get("_tokens_joven") or {}).get(perfil_id)
    if not token or esperado is None or not hmac.compare_digest(token.encode(), esperado.encode()):
        raise CredencialJovenInvalida(
            "Falta tu código completo o no es correcto. Escribe el código que te dio Puente al terminar la entrevista."
        )


# El registro llama al LLM: tope de tamaño del cuerpo y de envíos por minuto desde una misma IP.
MAX_BYTES_CUERPO_JOVEN = 64 * 1024
LIMITE_PERFILES_POR_MINUTO = 10
_envios_perfil: dict[str, list[float]] = {}


def _cuerpo_demasiado_grande(request: Request, cuerpo: bytes) -> bool:
    declarado = request.headers.get("content-length", "0")
    return len(cuerpo) > MAX_BYTES_CUERPO_JOVEN or (declarado.isdigit() and int(declarado) > MAX_BYTES_CUERPO_JOVEN)


def _dentro_del_limite(request: Request) -> bool:
    ip = request.client.host if request.client else "desconocida"
    ahora = time.monotonic()
    recientes = [t for t in _envios_perfil.get(ip, []) if ahora - t < 60]
    dentro = len(recientes) < LIMITE_PERFILES_POR_MINUTO
    if dentro:
        recientes.append(ahora)
    _envios_perfil[ip] = recientes
    return dentro


def _token_de(request: Request, *, permitir_query: bool = False) -> str | None:
    token = request.headers.get(ENCABEZADO_TOKEN_JOVEN)
    if not token and permitir_query:  # el enlace del .ics es un <a href>: no lleva encabezados
        token = request.query_params.get("token")
    return token


class PerfilJovenRequest(BaseModel):
    """Formulario fijo (#6, #39) y respuestas de la entrevista que manda la vista del joven."""

    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(default="", max_length=80, description="Nombre o apodo; opcional")
    edad: int = Field(ge=EDAD_MIN, le=EDAD_MAX)
    corregimiento: str = Field(min_length=2, max_length=80)
    horario_clases: list[Horario] = Field(default_factory=list, max_length=3)
    horario_libre: list[Horario] = Field(default_factory=list, max_length=3)
    salario_minimo: float = Field(ge=0, le=50000)
    presupuesto_pasaje_diario: float = Field(ge=0, le=500)
    respuestas: list[Annotated[str, Field(max_length=MAX_CARACTERES_RESPUESTA)]] = Field(
        min_length=1, max_length=MAX_PREGUNTAS
    )

    @field_validator("respuestas")
    @classmethod
    def _al_menos_una_respuesta(cls, respuestas: list[str]) -> list[str]:
        limpias = [r.strip() for r in respuestas]
        if not any(len(r) >= 3 for r in limpias):
            raise ValueError("responde al menos una pregunta de la entrevista")
        return limpias


class ConsentimientoJovenRequest(BaseModel):
    """La casilla de consentimiento de una vacante de la lista corta (#42, #12)."""

    model_config = ConfigDict(extra="forbid")

    perfil_id: str = Field(min_length=1, max_length=80)
    oferta_id: str = Field(min_length=1, max_length=80)
    otorgado: StrictBool

    @field_validator("perfil_id", "oferta_id")
    @classmethod
    def _validar_id_seguro(cls, valor: str) -> str:
        return _id_seguro(valor)


def corregimientos_con_traslado() -> list[str]:
    """Corregimientos de origen en `data/traslados.csv`: con otro, el Matching no tiene traslado.

    Solo lee la lista; los minutos y el pasaje los calcula el solver (#4).
    """
    ruta = RUTA_DATA / "traslados.csv"
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8", newline="") as archivo:
        return sorted({(f.get("origen") or "").strip() for f in csv.DictReader(archivo)} - {""})


def _texto_dias(dias: list[str]) -> str:
    indices = sorted({DIAS_VALIDOS.index(d) for d in dias if d in DIAS_VALIDOS})
    nombres = [NOMBRES_DIAS.get(DIAS_VALIDOS[i], DIAS_VALIDOS[i]) for i in indices]
    if len(indices) > 2 and indices == list(range(indices[0], indices[-1] + 1)):
        return f"{nombres[0]} a {nombres[-1]}"
    return ", ".join(nombres)


def _texto_horarios(horarios: list[Horario]) -> str:
    return "; ".join(f"{_texto_dias(h.dias)} {h.hora_inicio}-{h.hora_fin}" for h in horarios)


def _horario_para_tablero(perfil: Perfil) -> str:
    """Texto de horario del perfil en el tablero, solo para leer.

    El Matching usa los bloques `horario_libre_bloques` y `horario_clases_bloques` del perfil.
    """
    libre = _texto_horarios(perfil.horario_libre) or "sin horario indicado"
    if not perfil.horario_clases:
        return f"{libre} (sin clases)"
    clases = _texto_horarios(perfil.horario_clases)
    if any(h.hora_fin > "18:00" for h in perfil.horario_clases):
        return f"{libre} (estudia de noche: {clases})"
    return f"{libre} (clases: {clases})"


def preparar_entrada_joven(req: PerfilJovenRequest) -> dict[str, Any]:
    """Un elemento de `entradas_perfil` (forma de #39/#42) sin cédula, teléfono ni dirección.

    El corregimiento sale de la lista con traslado y el id lo pone el servidor, nunca el joven.
    """
    corregimiento = detectar_corregimiento(req.corregimiento, corregimientos_con_traslado())
    if corregimiento is None:
        raise ErrorValidacionTablero(
            "Corregimiento sin datos de traslado: elige uno de la lista."
        )
    nombre = str(limpiar_entrada(req.nombre.strip())) or "Joven"
    # Sin el nombre: el id va en la URL, en el localStorage y en /api/estado.
    perfil_id = f"PERFIL-JOVEN-{uuid.uuid4().hex[:8].upper()}"
    formulario = {
        "nombre": nombre,
        "edad": req.edad,
        "corregimiento": corregimiento,
        "horario_clases": [h.model_dump() for h in req.horario_clases],
        "horario_libre": [h.model_dump() for h in req.horario_libre],
        "salario_minimo": req.salario_minimo,
        "presupuesto_pasaje_diario": req.presupuesto_pasaje_diario,
    }
    entrada = limpiar_entrada(entrada_entrevista(perfil_id, formulario, req.respuestas))
    entrada["id"] = perfil_id
    return dict(_ADAPTADOR_ENTRADA_PERFIL.validate_python(entrada))


def revisar_guardrails_joven(estado: dict[str, Any], entrada: dict[str, Any]) -> str | None:
    """Guardrails (#9) sobre las respuestas ya limpias; deja sus Decisiones y devuelve el rechazo, si hay."""
    texto = "\n".join(entrada.get("respuestas") or [])
    resultados = evaluar_entrada({"texto": texto, "edad": entrada.get("edad")})
    for resultado in resultados:
        _registrar_decision_db(estado, resultado.decision)
    rechazo = next((r for r in resultados if not r.ok), None)
    return (rechazo.respuesta or rechazo.motivo) if rechazo else None


def registrar_perfil_joven(
    estado: dict[str, Any],
    entrada: dict[str, Any],
    perfil: Perfil,
    decision: Decision,
    taxonomia: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Suma al joven al tablero y recalcula el grafo.

    No usa `registrar_nuevo_perfil` porque ese fija el horario y recibe los skill_id del
    orientador; aquí las habilidades vienen verificadas por el agente Perfil (#6).
    """
    estado.setdefault("entradas_perfil", []).append(entrada)
    estado["perfiles"].append(
        _perfil_para_tablero(perfil, "Vista del joven: formulario fijo y entrevista (#52)", taxonomia or {})
    )
    _registrar_decision_db(estado, decision)
    _registrar_decision_db(
        estado,
        Decision(
            agente="joven",
            entrada_resumen=f"registro_perfil ({perfil.id})",
            salida_resumen=f"Perfil {perfil.id} enviado desde /joven ({len(perfil.habilidades)} habilidades)",
            razon="El joven completó el formulario y entrevista en la vista de aplicante (/joven).",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def _perfil_para_tablero(perfil: Perfil, modalidad_ingreso: str, taxonomia: dict[str, dict]) -> dict[str, Any]:
    """El perfil como lo guarda el tablero, con sus horarios y evidencias reales (#52, #188)."""
    return {
        "id": perfil.id,
        "nombre": perfil.nombre,
        "genero": perfil.genero or "no_especificado",
        "edad": perfil.edad,
        "corregimiento": perfil.corregimiento,
        "modalidad_ingreso": modalidad_ingreso,
        "horario_libre": _horario_para_tablero(perfil),
        # Lo que el Matching usa de verdad (#4): los bloques del perfil, no el texto de arriba.
        "horario_libre_bloques": [h.model_dump() for h in perfil.horario_libre],
        "horario_clases_bloques": [h.model_dump() for h in perfil.horario_clases],
        "salario_minimo": perfil.salario_minimo,
        "presupuesto_pasaje_diario": perfil.presupuesto_pasaje_diario,
        "habilidades": [
            {
                "skill_id": h.skill_id,
                "nombre": taxonomia.get(h.skill_id, {}).get("nombre_es", h.skill_id),
                "evidencia": h.evidencia,
            }
            for h in perfil.habilidades
        ],
    }


def _buscar_perfil(estado: dict[str, Any], perfil_id: str) -> dict[str, Any] | None:
    return next((p for p in estado.get("perfiles", []) if p.get("id") == perfil_id), None)


def _buscar_postulacion(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> dict[str, Any] | None:
    return next(
        (
            p
            for p in estado.get("postulaciones", [])
            if p.get("perfil_id") == perfil_id and p.get("oferta_id") == oferta_id
        ),
        None,
    )


def _en_lista_corta(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> bool:
    return any(
        m.get("perfil_id") == perfil_id and m.get("oferta_id") == oferta_id
        for m in estado.get("matches", [])
    )


def _estado_consentimiento(registro: dict[str, Any] | None) -> str:
    if registro is None:
        return "pendiente"
    return EstadoPostulacion.CONSENTIDO.value if registro.get("otorgado") else "no_consentido"


def registrar_consentimiento_joven(
    estado: dict[str, Any],
    *,
    perfil_id: str,
    oferta_id: str,
    otorgado: bool,
) -> dict[str, Any]:
    """Casilla del joven: un `Consentimiento` en `consentimientos` y su Decision, como en el tablero (#49).

    Es el mismo registro que deja hoy la aprobación del orientador, con medio "joven_web". El
    estado de la postulación en el tablero no se mueve: el orientador solo actúa sobre
    "preseleccionado" y SQLite todavía no tiene "consentido" (#84). El joven ve "consentido".
    """
    req = ConsentimientoJovenRequest(perfil_id=perfil_id, oferta_id=oferta_id, otorgado=otorgado)
    if _buscar_perfil(estado, req.perfil_id) is None:
        raise LookupError(f"No existe el perfil '{req.perfil_id}'.")
    post = _buscar_postulacion(estado, req.perfil_id, req.oferta_id)
    if post is None or not _en_lista_corta(estado, req.perfil_id, req.oferta_id):
        raise ErrorValidacionTablero(f"La vacante '{req.oferta_id}' no está en tu lista corta.")
    if post.get("estado") not in ESTADOS_ADMITEN_CONSENTIMIENTO:
        raise ErrorValidacionTablero(
            f"Tu postulación a '{req.oferta_id}' está en '{post.get('estado')}': "
            "el consentimiento se da antes del envío."
        )
    previo = _ultimo_consentimiento(estado, req.perfil_id, req.oferta_id)
    if previo is not None and bool(previo.get("otorgado")) == req.otorgado:
        return estado  # misma respuesta que la anterior: no se duplica el registro

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    consentimiento = _ADAPTADOR_CONSENTIMIENTO.validate_python(
        {
            "perfil_id": req.perfil_id,
            "oferta_id": req.oferta_id,
            "otorgado": req.otorgado,
            "medio": MEDIO_CONSENTIMIENTO_JOVEN,
            "timestamp": ahora,
        }
    )
    estado.setdefault("consentimientos", []).append(dict(consentimiento))
    empresa = post.get("empresa") or "la empresa"
    _registrar_decision_db(
        estado,
        Decision(
            agente="consentimiento",
            entrada_resumen=f"{req.perfil_id} -> {req.oferta_id}",
            salida_resumen=(
                f"Consentimiento confirmado (medio: {MEDIO_CONSENTIMIENTO_JOVEN})"
                if req.otorgado
                else f"Consentimiento negado (medio: {MEDIO_CONSENTIMIENTO_JOVEN}): el CV no se envía"
            ),
            razon=(
                f"El joven marcó en la vista del joven que acepta que su CV, sin cédula, teléfono ni dirección, llegue a {empresa}."
                if req.otorgado
                else f"El joven indicó en la vista del joven que no acepta que su CV llegue a {empresa}."
            ),
            timestamp=ahora,
        ),
    )
    _registrar_decision_db(
        estado,
        Decision(
            agente="joven",
            entrada_resumen=f"{req.perfil_id} -> {req.oferta_id}",
            salida_resumen=f"Consentimiento {'otorgado' if req.otorgado else 'negado'} desde /joven",
            razon=f"Decisión de consentimiento registrada por el joven sobre la vacante {req.oferta_id} ({empresa}).",
            timestamp=ahora,
        ),
    )
    post["consentimiento_joven"] = req.otorgado
    post["consentimiento_medio"] = MEDIO_CONSENTIMIENTO_JOVEN
    for m in estado.get("matches", []):
        if m.get("perfil_id") == req.perfil_id and m.get("oferta_id") == req.oferta_id:
            m["consentimiento_joven"] = req.otorgado
    estado["metricas"] = _calcular_metricas_impacto(estado)
    _guardar_estado_tablero_db(estado)
    return estado


def _rutas_formacion_de(estado: dict[str, Any], perfil_id: str) -> list[dict[str, Any]]:
    """`rutas_formacion` que dejó Formación (#14) en el estado del grafo, solo las del joven."""
    grafo, config = estado.get("_grafo"), estado.get("_config_grafo")
    if grafo is None or config is None:
        return []
    try:
        rutas = grafo.get_state(config).values.get("rutas_formacion") or []
    except Exception:
        return []
    return [dict(r) for r in rutas if isinstance(r, dict) and r.get("perfil_id") == perfil_id]


def resultado_joven(estado: dict[str, Any], perfil_id: str) -> dict[str, Any]:
    """Lo que ve el joven: lista corta con estado y consentimiento, descartes con motivo y formación.

    Son los datos estructurados del tablero; ningún texto se redacta aquí.
    """
    perfil = _buscar_perfil(estado, perfil_id)
    if perfil is None:
        raise LookupError(f"No existe el perfil '{perfil_id}'.")
    ofertas = {o["id"]: o for o in estado.get("ofertas", [])}
    matches = {m["oferta_id"]: m for m in estado.get("matches", []) if m.get("perfil_id") == perfil_id}

    vacantes: list[dict[str, Any]] = []
    for post in estado.get("postulaciones", []):
        if post.get("perfil_id") != perfil_id:
            continue
        oferta_id = post["oferta_id"]
        match = matches.get(oferta_id)
        estado_post = post.get("estado", EstadoPostulacion.PRESELECCIONADO.value)
        if match is None and estado_post not in ESTADOS_CERRADOS:
            continue  # ya no está en la lista corta tras un recálculo
        consentimiento = _estado_consentimiento(_ultimo_consentimiento(estado, perfil_id, oferta_id))
        if estado_post in ESTADOS_CERRADOS or consentimiento != EstadoPostulacion.CONSENTIDO.value:
            estado_visible = estado_post
        else:
            estado_visible = EstadoPostulacion.CONSENTIDO.value
        desglose = (match or {}).get("desglose") or {}
        oferta = ofertas.get(oferta_id, {})
        vacantes.append(
            {
                "oferta_id": oferta_id,
                "puesto": post.get("puesto") or oferta.get("puesto", oferta_id),
                "empresa": post.get("empresa") or oferta.get("empresa", ""),
                "corregimiento": oferta.get("corregimiento", ""),
                "salario_mensual": oferta.get("salario_mensual"),
                "minutos_traslado": desglose.get("minutos_traslado"),
                "pasaje_diario": desglose.get("pasaje_diario"),
                "habilidades_cubiertas": list(desglose.get("habilidades_cubiertas") or []),
                "brechas": list((match or {}).get("brechas") or []),
                "estado": estado_visible,
                "estado_postulacion": estado_post,
                "consentimiento": consentimiento,
                "puede_consentir": match is not None and estado_post in ESTADOS_ADMITEN_CONSENTIMIENTO,
                "enviada": estado_post in ESTADOS_ENVIADOS,
                "ics": next(
                    (a for a in post.get("archivos_generados") or [] if str(a).endswith(".ics")),
                    None,
                ),
            }
        )

    descartes = [
        {
            "oferta_id": d.get("oferta_id"),
            "puesto": d.get("puesto"),
            "empresa": d.get("empresa"),
            "corregimiento": d.get("corregimiento_oferta"),
            "motivo": d.get("motivo"),
        }
        for d in estado.get("descartados", [])
        if d.get("perfil_id") == perfil_id
    ]

    rutas = _rutas_formacion_de(estado, perfil_id)
    taxonomia = cargar_taxonomia_grafo()
    ids_habilidades = {h.get("skill_id") for h in perfil.get("habilidades", []) if isinstance(h, dict)}
    for v in vacantes:
        ids_habilidades.update(v["habilidades_cubiertas"], v["brechas"])
    for r in rutas:
        ids_habilidades.update(r.get("brechas_iniciales") or [])

    return {
        "perfil": {
            "id": perfil["id"],
            "nombre": perfil.get("nombre", ""),
            "edad": perfil.get("edad"),
            "corregimiento": perfil.get("corregimiento", ""),
            "horario": perfil.get("horario_libre", ""),
            "salario_minimo": perfil.get("salario_minimo"),
            "presupuesto_pasaje_diario": perfil.get("presupuesto_pasaje_diario"),
            "modalidad_ingreso": perfil.get("modalidad_ingreso", ""),
            "habilidades": [
                {"skill_id": h.get("skill_id"), "evidencia": h.get("evidencia", "")}
                for h in perfil.get("habilidades", [])
                if isinstance(h, dict)
            ],
        },
        "nombres_habilidades": {
            sid: taxonomia.get(sid, {}).get("nombre_es", sid) for sid in sorted(i for i in ids_habilidades if i)
        },
        "vacantes": vacantes,
        "descartes": descartes,
        "rutas_formacion": rutas,
        # TODO(#12/#84): el mensaje para el joven lo arma Postulación (#12). Cuando #84 esté en main,
        # este campo lleva ese texto; esta vista no redacta un mensaje propio.
        "mensaje": None,
    }


async def endpoint_joven_pagina(_request: Request) -> FileResponse:
    return FileResponse(RUTA_STATIC / "joven.html")


async def endpoint_joven_formulario(_request: Request) -> JSONResponse:
    """Lo que la página necesita para armar el formulario fijo y la entrevista de #6."""
    return JSONResponse(
        {
            "preguntas": list(PREGUNTAS_ENTREVISTA)[:MAX_PREGUNTAS],
            "max_preguntas": MAX_PREGUNTAS,
            "max_caracteres_respuesta": MAX_CARACTERES_RESPUESTA,
            "corregimientos": corregimientos_con_traslado(),
            "dias": list(DIAS_VALIDOS),
            "edad_min": EDAD_MIN,
            "edad_max": EDAD_MAX,
        }
    )


async def endpoint_joven_perfil(request: Request) -> JSONResponse:
    if _cuerpo_demasiado_grande(request, await request.body()):
        return JSONResponse({"detail": "La solicitud es demasiado grande."}, status_code=413)
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = PerfilJovenRequest.model_validate(datos)
        entrada = preparar_entrada_joven(req)
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    rechazo = revisar_guardrails_joven(ESTADO_GLOBAL, entrada)
    if rechazo:
        return JSONResponse({"detail": rechazo}, status_code=422)
    if not _dentro_del_limite(request):  # cuenta solo los envíos que llegarían al LLM
        return JSONResponse(
            {"detail": "Recibimos muchas entrevistas seguidas. Espera un minuto y vuelve a intentarlo."},
            status_code=429,
        )

    taxonomia = cargar_taxonomia_grafo()
    try:
        # El LLM puede tardar: corre fuera del bucle de eventos. El estado se toca después, aquí.
        perfil, decision = await run_in_threadpool(
            construir_perfil,
            entrada,
            taxonomia=taxonomia,
            normalizar=normalizar_habilidad,
            llm=llm_perfil_joven,
        )
    except ValueError as exc:
        return JSONResponse({"detail": f"No se pudo armar tu perfil: {exc}"}, status_code=422)
    except (RuntimeError, OSError):  # ErrorLLM es un RuntimeError
        # El detalle (proveedor, modelo, ruta de la grabación) queda en la consola, no en la respuesta.
        _registro.exception("Vista del joven: el agente Perfil falló")
        return JSONResponse(
            {
                "detail": (
                    "El agente Perfil no pudo leer tu entrevista ahora. Avísale a tu orientador/a: "
                    "la entrevista puede seguir por terminal."
                )
            },
            status_code=503,
        )
    registrar_perfil_joven(ESTADO_GLOBAL, entrada, perfil, decision, taxonomia)
    token = _crear_token_joven(ESTADO_GLOBAL, perfil.id)
    # El token sale solo aquí: con él el joven vuelve a ver su resultado y decide su consentimiento.
    return JSONResponse({**resultado_joven(ESTADO_GLOBAL, perfil.id), "token": token}, status_code=201)


async def endpoint_joven_consentimiento(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = ConsentimientoJovenRequest.model_validate(datos)
        verificar_token_joven(ESTADO_GLOBAL, req.perfil_id, _token_de(request))
        registrar_consentimiento_joven(
            ESTADO_GLOBAL,
            perfil_id=req.perfil_id,
            oferta_id=req.oferta_id,
            otorgado=req.otorgado,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except CredencialJovenInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    return JSONResponse(resultado_joven(ESTADO_GLOBAL, req.perfil_id))


async def endpoint_joven_resultado(request: Request) -> JSONResponse:
    try:
        perfil_id = _id_seguro(request.path_params["perfil_id"])
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    try:
        verificar_token_joven(ESTADO_GLOBAL, perfil_id, _token_de(request))
        return JSONResponse(resultado_joven(ESTADO_GLOBAL, perfil_id))
    except CredencialJovenInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)


async def endpoint_joven_ics(request: Request) -> FileResponse | JSONResponse:
    """El .ics de la entrevista propuesta, solo si la postulación ya se envió."""
    try:
        perfil_id = _id_seguro(request.path_params["perfil_id"])
        oferta_id = _id_seguro(request.path_params["oferta_id"])
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    try:
        verificar_token_joven(ESTADO_GLOBAL, perfil_id, _token_de(request, permitir_query=True))
    except CredencialJovenInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    post = _buscar_postulacion(ESTADO_GLOBAL, perfil_id, oferta_id)
    nombre = None
    if post is not None and post.get("estado") in ESTADOS_ENVIADOS:
        nombre = next(
            (a for a in post.get("archivos_generados") or [] if str(a).endswith(".ics")), None
        )
    if nombre is None:
        return JSONResponse({"detail": "Todavía no hay entrevista propuesta para esta vacante."}, status_code=404)
    try:
        ruta = _ruta_segura_en_outbox(_ruta_outbox(), nombre)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    if not ruta.is_file():
        return JSONResponse({"detail": "No se encontró el archivo .ics."}, status_code=404)
    return FileResponse(ruta, media_type="text/calendar", filename=nombre)


# --- Ingesta multicanal en el tablero (#170): oferta y CV por PDF o foto ---------------------
# Un solo flujo para los dos: tamaño declarado antes de leer, `validar_archivo` (5 MB, tipo y firma
# de bytes; audio con su mensaje), nombre de archivo generado por el servidor, carpeta temporal que
# se borra al terminar y agentes reales (no los stubs del tablero). Cada rechazo deja su Decision.
from starlette.datastructures import UploadFile

from puente.agents.mercado import Resultado as ResultadoMercado
from puente.agents.mercado import procesar_ofertas
from puente.agents.perfil import extraer_texto_cv
from puente.guardrails import EXTENSIONES_PERMITIDAS, MAX_BYTES_ARCHIVO, ResultadoGuardrail, validar_archivo

try:
    from pypdf.errors import PyPdfError
except ImportError:  # pragma: no cover
    class PyPdfError(Exception):
        pass

# El nombre entra en el prompt de Mercado y por tanto en la clave de la grabación (#45): fijo por
# tipo, para que el mismo archivo dé la misma clave venga de donde venga (cámara, PDF, otro equipo).
NOMBRE_SUBIDA_OFERTA = "oferta_subida"
FUENTE_SUBIDA_OFERTA = "Subida en el tablero del orientador (foto o PDF)"
MARGEN_MULTIPART = 64 * 1024  # campos y cabeceras del formulario además del archivo
MIN_CARACTERES_CV = 20
_envios_subida: dict[str, list[float]] = {}


class ErrorSubida(Exception):
    """Rechazo de una subida con su código HTTP y el mensaje que ve el orientador."""

    def __init__(self, mensaje: str, status: int = 422) -> None:
        super().__init__(mensaje)
        self.status = status


def _extension_declarada(nombre: str | None) -> str:
    """Solo la extensión del nombre que manda el cliente; el resto (que puede traer PII) se descarta."""
    sufijo = Path(str(nombre or "")).suffix.lower()
    return sufijo if re.fullmatch(r"\.[a-z0-9]{1,5}", sufijo) else ""


def _mensaje_rechazo(resultado: ResultadoGuardrail) -> str:
    return resultado.respuesta or f"Archivo rechazado: {resultado.motivo}."


def _subida_de_otro_sitio(request: Request) -> bool:
    """Un multipart no dispara el preflight del navegador: misma defensa CSRF que `_leer_json_validado`."""
    sitio = request.headers.get("sec-fetch-site")
    origen = request.headers.get("origin")
    return (sitio is not None and sitio == "cross-site") or (
        sitio is None and bool(origen) and urlsplit(origen).netloc != request.headers.get("host", "")
    )


def _subida_dentro_del_limite(request: Request) -> bool:
    ip = request.client.host if request.client else "desconocida"
    ahora = time.monotonic()
    recientes = [t for t in _envios_subida.get(ip, []) if ahora - t < 60]
    dentro = len(recientes) < LIMITE_PERFILES_POR_MINUTO
    if dentro:
        recientes.append(ahora)
    _envios_subida[ip] = recientes
    return dentro


async def leer_archivo_subido(request: Request, estado: dict[str, Any]) -> tuple[str, bytes, dict[str, str]]:
    """(tipo `pdf|jpg|png`, bytes, campos de texto) de un multipart con el campo `archivo`.

    El tamaño declarado se mira antes de leer el cuerpo: un archivo de 50 MB se rechaza sin cargarlo.
    Lanza `ErrorSubida`; los rechazos de guardrails quedan como Decision en SQLite.
    """
    if _subida_de_otro_sitio(request):
        raise ErrorSubida("Solicitud desde otro sitio rechazada", 403)
    if not _subida_dentro_del_limite(request):
        raise ErrorSubida("Muchas subidas seguidas. Espera un minuto y vuelve a intentarlo.", 429)
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "multipart/form-data":
        raise ErrorSubida("Sube el archivo como multipart/form-data en el campo «archivo».", 415)
    declarado = request.headers.get("content-length", "")
    if not declarado or not declarado.isdigit():
        raise ErrorSubida("Falta el encabezado Content-Length.", 411)
    if int(declarado) > MAX_BYTES_ARCHIVO + MARGEN_MULTIPART:
        rechazo = validar_archivo("subida", int(declarado))
        _registrar_decision_db(estado, rechazo.decision)
        raise ErrorSubida(_mensaje_rechazo(rechazo), 413)
    try:
        formulario = await request.form(max_files=1, max_fields=12)
    except Exception as exc:  # multipart mal formado o con más archivos de la cuenta
        raise ErrorSubida("El formulario de subida no es válido.", 422) from exc
    try:
        archivo = formulario.get("archivo")
        if not isinstance(archivo, UploadFile):
            raise ErrorSubida("Falta el archivo en el campo «archivo».", 422)
        contenido = await archivo.read(MAX_BYTES_ARCHIVO + 1)
        extension = _extension_declarada(archivo.filename)
        campos = {
            k: str(v)[:200] for k, v in formulario.multi_items() if k != "archivo" and isinstance(v, str)
        }
    finally:
        await formulario.close()
    # El nombre que ve guardrails lo pone el servidor: el del cliente podría traer una cédula.
    resultado = validar_archivo(f"subida{extension}", len(contenido), contenido)
    _registrar_decision_db(estado, resultado.decision)
    if not resultado.ok:
        raise ErrorSubida(_mensaje_rechazo(resultado), 413 if len(contenido) > MAX_BYTES_ARCHIVO else 422)
    return EXTENSIONES_PERMITIDAS[extension], contenido, campos


def extraer_oferta_de_archivo(tipo: str, contenido: bytes) -> ResultadoMercado:
    """Agente Mercado real sobre el archivo, en una carpeta temporal que se borra al salir.

    PDF con capa de texto: Mercado lo lee con pypdf. Foto o PDF escaneado: va al LLM como bloque nativo
    (una oferta no trae datos de personas) y las reglas corren sobre la transcripción literal.
    """
    with tempfile.TemporaryDirectory(prefix="puente_oferta_") as carpeta:
        ruta = Path(carpeta) / f"{NOMBRE_SUBIDA_OFERTA}.{tipo}"
        ruta.write_bytes(contenido)
        return procesar_ofertas([{"ruta": str(ruta), "fuente": FUENTE_SUBIDA_OFERTA}])


def leer_texto_de_cv(tipo: str, contenido: bytes) -> str:
    """Texto del CV con pypdf o OCR local. El archivo (con la cédula) nunca va al LLM y se borra al salir."""
    with tempfile.TemporaryDirectory(prefix="puente_cv_") as carpeta:
        ruta = Path(carpeta) / f"cv_subido.{tipo}"
        ruta.write_bytes(contenido)
        try:
            return extraer_texto_cv(ruta)
        except (PyPdfError, ValueError) as exc:
            raise ErrorSubida("El archivo PDF no se pudo leer o está dañado.", 422) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            if tipo == "pdf":
                raise ErrorSubida("El archivo PDF no se pudo leer o está dañado.", 422) from exc
            raise


def _alerta_para_tablero(alerta: str) -> dict[str, str]:
    """`bloqueo · edad · límite de edad, Ley 11 de 2005 art. 2 · «frase»` -> {frase, regla, tipo}."""
    partes = alerta.split(" · ")
    frase = re.search(r"«(.+?)»", alerta)
    return {
        "frase": frase.group(1) if frase else alerta,
        "regla": " · ".join(partes[1:-1]) if len(partes) >= 3 else alerta,
        "tipo": "bloqueo" if partes[0] == "bloqueo" else "alerta",
    }


def registrar_oferta_subida(estado: dict[str, Any], resultado: ResultadoMercado) -> dict[str, Any]:
    """Suma al tablero la oferta que extrajo Mercado de una foto o PDF y recalcula el grafo."""
    for decision in resultado.decisiones:
        _registrar_decision_db(estado, decision)
    if not resultado.ofertas:
        sin_grabacion = any("grabacion_faltante" in e for e in resultado.errores)
        if sin_grabacion:
            raise ErrorSubida(
                "No hay respuesta grabada del modelo para este archivo (LLM_MODO=reproducir). "
                "En la demo sin red usa tests/fixtures/volante_sintetico.jpg; con clave, LLM_MODO=vivo.",
                503,
            )
        raise ErrorSubida("El agente Mercado no pudo extraer una oferta de este archivo; revisa la traza.", 422)

    oferta = resultado.ofertas[0]
    limpio = limpiar_entrada(
        {"puesto": oferta.puesto, "empresa": oferta.empresa, "requisitos_texto": oferta.requisitos_texto}
    )
    resultados_gr = evaluar_entrada(
        {"texto": f"{limpio['puesto']}\n{limpio['requisitos_texto']}", "salario": oferta.salario_mensual}
    )
    for r_gr in resultados_gr:
        _registrar_decision_db(estado, r_gr.decision)
    rechazo = next((r for r in resultados_gr if not r.ok), None)
    if rechazo is not None:
        raise ErrorSubida(rechazo.respuesta or rechazo.motivo, 422)

    oferta_id = oferta.id.upper()
    if any(o.get("id") == oferta_id for o in estado.get("ofertas", [])):
        raise ErrorSubida(f"Esta oferta ya está en el tablero ({oferta_id}).", 409)
    alertas = [_alerta_para_tablero(a) for a in oferta.alertas]
    nueva = {
        "id": oferta_id,
        "puesto": str(limpio["puesto"]),
        "empresa": str(limpio["empresa"]),
        "corregimiento": oferta.corregimiento,
        "salario_mensual": oferta.salario_mensual,
        "horario": _texto_horarios([oferta.horario]) if oferta.horario else None,
        "habilidades": list(oferta.habilidades),
        "requisitos_texto": str(limpio["requisitos_texto"]),
        "alertas": alertas,
        "bloqueada": oferta.bloqueada,
        "fuente": FUENTE_SUBIDA_OFERTA,
    }
    estado["ofertas"].append(nueva)
    _registrar_decision_db(
        estado,
        Decision(
            agente="orientador",
            entrada_resumen=f"subida_oferta ({oferta_id})",
            salida_resumen=f"{oferta_id} · {'bloqueada' if oferta.bloqueada else 'publicable'} · {len(alertas)} alertas",
            razon="El orientador subió una foto o PDF de oferta; Mercado la extrajo y las reglas de la Ley 11 la revisaron.",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    recalcular_pipeline_tablero(estado, registrar_trazas=True)
    return {"tipo": "oferta", "oferta": nueva}


def _numero(campos: dict[str, str], clave: str, por_defecto: float) -> float:
    valor = (campos.get(clave) or "").strip()
    try:
        return float(valor) if valor else por_defecto
    except ValueError as exc:
        raise ErrorSubida(f"El campo «{clave}» debe ser un número.", 422) from exc


def preparar_entrada_cv(estado: dict[str, Any], texto_cv: str, campos: dict[str, str]) -> dict[str, Any]:
    """Entrada de `construir_perfil` desde el texto del CV y el formulario fijo; guardrails antes del LLM."""
    if len("".join(texto_cv.split())) < MIN_CARACTERES_CV:
        raise ErrorSubida(
            "El archivo no tiene texto legible (PDF escaneado o foto borrosa). Sube un PDF con texto "
            "o registra al joven con la entrevista.",
            422,
        )
    try:
        edad = int(_numero(campos, "edad", -1))
    except (TypeError, ValueError) as exc:
        raise ErrorSubida("La edad debe ser un número.", 422) from exc
    corregimiento_form = (campos.get("corregimiento") or "").strip()
    corregimiento = detectar_corregimiento(corregimiento_form or texto_cv, corregimientos_con_traslado())
    if corregimiento is None:
        raise ErrorSubida("Corregimiento sin datos de traslado: elige uno de la lista.", 422)
    # Guardrails sobre el texto ya sin cédula ni teléfono: inyección, fuera de alcance y rango de edad.
    resultados_gr = evaluar_entrada({"texto": str(limpiar_entrada(texto_cv)), "edad": edad})
    for r_gr in resultados_gr:
        _registrar_decision_db(estado, r_gr.decision)
    rechazo = next((r for r in resultados_gr if not r.ok), None)
    if rechazo is not None:
        raise ErrorSubida(rechazo.respuesta or rechazo.motivo, 422)
    clases = (
        [{"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "18:00", "hora_fin": "22:00"}]
        if campos.get("estudia_noche") in ("1", "true", "on")
        else []
    )
    return {
        "id": f"PERFIL-CV-{uuid.uuid4().hex[:8].upper()}",
        # El texto crudo solo vive en esta llamada: `construir_perfil` le quita la PII antes del LLM.
        "cv_texto": texto_cv,
        "nombre": str(limpiar_entrada((campos.get("nombre") or "").strip()))[:80] or "Joven",
        "edad": edad,
        "corregimiento": corregimiento,
        "horario_libre": [
            {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "07:00", "hora_fin": "17:30"}
        ],
        "horario_clases": clases,
        "salario_minimo": _numero(campos, "salario_minimo", 600.0),
        "presupuesto_pasaje_diario": _numero(campos, "presupuesto_pasaje_diario", 3.0),
    }


def registrar_perfil_desde_cv(
    estado: dict[str, Any], perfil: Perfil, decision: Decision, taxonomia: dict[str, dict]
) -> dict[str, Any]:
    """Suma el perfil al tablero sin guardar el CV ni su texto (ni en `entradas_perfil`)."""
    nuevo = {
        "id": perfil.id,
        "nombre": perfil.nombre,
        "genero": perfil.genero or "no_especificado",
        "edad": perfil.edad,
        "corregimiento": perfil.corregimiento,
        "modalidad_ingreso": "CV subido en el tablero (PDF o foto, #170)",
        "horario_libre": _horario_para_tablero(perfil),
        "horario_libre_bloques": [h.model_dump() for h in perfil.horario_libre],
        "horario_clases_bloques": [h.model_dump() for h in perfil.horario_clases],
        "salario_minimo": perfil.salario_minimo,
        "presupuesto_pasaje_diario": perfil.presupuesto_pasaje_diario,
        "habilidades": [
            {
                "skill_id": h.skill_id,
                "nombre": taxonomia.get(h.skill_id, {}).get("nombre_es", h.skill_id),
                "evidencia": h.evidencia,
            }
            for h in perfil.habilidades
        ],
    }
    estado["perfiles"].append(nuevo)
    _registrar_decision_db(estado, decision)
    _registrar_decision_db(
        estado,
        Decision(
            agente="orientador",
            entrada_resumen=f"subida_cv ({perfil.id})",
            salida_resumen=f"Perfil {perfil.id} desde CV ({len(perfil.habilidades)} habilidades con evidencia)",
            razon="El orientador subió un CV; la PII se quitó antes del LLM y el archivo temporal se borró.",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    recalcular_pipeline_tablero(estado, registrar_trazas=True)
    return {"tipo": "perfil", "perfil": nuevo}


def _respuesta_subida(exc: ErrorSubida) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


async def endpoint_oferta_archivo(request: Request) -> JSONResponse:
    try:
        tipo, contenido, _campos = await leer_archivo_subido(request, ESTADO_GLOBAL)
        resultado = await run_in_threadpool(extraer_oferta_de_archivo, tipo, contenido)
        subida = registrar_oferta_subida(ESTADO_GLOBAL, resultado)
    except ErrorSubida as exc:
        return _respuesta_subida(exc)
    return JSONResponse({**serializar_estado_publico(ESTADO_GLOBAL), "subida": subida})


async def endpoint_perfil_archivo(request: Request) -> JSONResponse:
    try:
        tipo, contenido, campos = await leer_archivo_subido(request, ESTADO_GLOBAL)
        try:
            texto_cv = await run_in_threadpool(leer_texto_de_cv, tipo, contenido)
        except ErrorSubida:
            raise
        except RuntimeError as exc:  # foto sin OCR local instalado: nunca se manda la imagen al LLM
            raise ErrorSubida(
                "Este equipo no tiene OCR local para leer fotos de CV (la foto no se envía al modelo "
                "porque trae la cédula). Sube el CV en PDF o registra al joven con la entrevista.",
                422,
            ) from exc
        except Exception as exc:
            if tipo == "pdf":
                raise ErrorSubida("El archivo PDF no se pudo leer o está dañado.", 422) from exc
            raise
        entrada = preparar_entrada_cv(ESTADO_GLOBAL, texto_cv, campos)
        taxonomia = cargar_taxonomia_grafo()
        try:
            perfil, decision = await run_in_threadpool(
                construir_perfil, entrada, taxonomia=taxonomia, normalizar=normalizar_habilidad, llm=llm_perfil_joven
            )
        except ValueError as exc:
            raise ErrorSubida(f"No se pudo armar el perfil: {limpiar_entrada(str(exc))}", 422) from exc
        except (RuntimeError, OSError) as exc:  # ErrorLLM es un RuntimeError
            _registro.exception("Subida de CV: el agente Perfil falló")
            if getattr(exc, "causa", "") == "grabacion_faltante":
                raise ErrorSubida(
                    "No hay respuesta grabada del modelo para este CV (LLM_MODO=reproducir). En la demo sin "
                    "red usa tests/fixtures/cv_sintetico.pdf; con clave, LLM_MODO=vivo.",
                    503,
                ) from exc
            raise ErrorSubida("El agente Perfil no pudo leer el CV ahora; revisa la consola del servidor.", 503) from exc
        finally:
            entrada.pop("cv_texto", None)
        subida = registrar_perfil_desde_cv(ESTADO_GLOBAL, perfil, decision, taxonomia)
    except ErrorSubida as exc:
        return _respuesta_subida(exc)
    return JSONResponse({**serializar_estado_publico(ESTADO_GLOBAL), "subida": subida})


RUTA_STATIC.mkdir(parents=True, exist_ok=True)


@contextlib.asynccontextmanager
async def _ciclo_de_vida(_app: Starlette):
    anunciar_codigos_joven(ESTADO_GLOBAL)
    empresa.anunciar_codigos_empresa(ESTADO_GLOBAL)  # #167
    yield


app = Starlette(
    debug=False,
    lifespan=_ciclo_de_vida,
    middleware=[Middleware(MiddlewareDemoPublicaReadOnly)],
    routes=[
        Route("/", endpoint_index, methods=["GET"]),
        Route("/api/estado", endpoint_estado, methods=["GET"]),
        Route("/api/orientador/accion", endpoint_accion_orientador, methods=["POST"]),
        Route("/api/ofertas/desbloquear", endpoint_desbloquear_oferta, methods=["POST"]),
        Route("/api/ofertas", endpoint_nueva_oferta, methods=["POST"]),
        Route("/api/ofertas/archivo", endpoint_oferta_archivo, methods=["POST"]),
        Route("/api/perfiles", endpoint_nuevo_perfil, methods=["POST"]),
        Route("/api/perfiles/archivo", endpoint_perfil_archivo, methods=["POST"]),
        Route("/api/cargar-data", endpoint_cargar_data, methods=["POST"]),
        Route("/api/auditor/simular-sesgo", endpoint_simular_sesgo, methods=["POST"]),
        Route("/api/reset", endpoint_reset, methods=["POST"]),
        Route("/api/orientador/cobertura-turnos/demo", endpoint_cobertura_turnos_demo, methods=["POST"]),
        Route("/api/orientador/cobertura-turnos/aprobar", endpoint_aprobar_cobertura_turnos, methods=["POST"]),
        Route("/api/orientador/umbrales", endpoint_umbrales_solver, methods=["POST"]),
        Route("/api/admin/operacion", endpoint_admin_operacion, methods=["GET"]),
        Route("/empresa", endpoint_empresa_pagina, methods=["GET"]),
        Route("/api/empresa/postulaciones", endpoint_empresa_postulaciones, methods=["GET"]),
        Route("/api/empresa/evento", endpoint_empresa_evento, methods=["POST"]),
        # Vista del joven (#52)
        Route("/joven", endpoint_joven_pagina, methods=["GET"]),
        Route("/api/joven/formulario", endpoint_joven_formulario, methods=["GET"]),
        Route("/api/joven/perfil", endpoint_joven_perfil, methods=["POST"]),
        Route("/api/joven/consentimiento", endpoint_joven_consentimiento, methods=["POST"]),
        Route("/api/joven/{perfil_id}/resultado", endpoint_joven_resultado, methods=["GET"]),
        Route("/api/joven/{perfil_id}/ics/{oferta_id}", endpoint_joven_ics, methods=["GET"]),
        # Vista del joven, fase 2 (#166)
        Route("/api/joven/entrevista", joven_fase2.endpoint_iniciar_entrevista, methods=["POST"]),
        Route("/api/joven/{perfil_id}/entrevista", joven_fase2.endpoint_entrevista, methods=["GET", "POST"]),
        Route("/api/joven/{perfil_id}/entrevista/terminar", joven_fase2.endpoint_terminar_entrevista, methods=["POST"]),
        Route("/api/joven/{perfil_id}/tirilla", joven_fase2.endpoint_tirilla, methods=["POST"]),
        Route("/api/joven/{perfil_id}/tirilla/confirmar", joven_fase2.endpoint_confirmar_tirilla, methods=["POST"]),
        Route("/api/joven/{perfil_id}/postulaciones", joven_fase2.endpoint_postulaciones, methods=["GET"]),
        # Lista corta ciega de la empresa (#167)
        Route("/empresa/lista-corta", empresa.endpoint_pagina, methods=["GET"]),
        Route("/api/empresa/{empresa_id}/lista-corta", empresa.endpoint_lista, methods=["GET"]),
        Route("/api/empresa/{empresa_id}/responder", empresa.endpoint_responder, methods=["POST"]),
        Route("/api/empresa/{empresa_id}/ics/{oferta_id}/{ref}", empresa.endpoint_ics, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(RUTA_STATIC)), name="static"),
    ],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, reload=False)
