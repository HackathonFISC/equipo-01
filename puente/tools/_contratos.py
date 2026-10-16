"""Contratos que usan las herramientas de Postulación (issue #7).

# STUB hasta que se fusione #2

Cada nombre se importa por separado de ``puente.schemas`` (issue #2): si #2 define
``Perfil`` pero todavía no ``Habilidad``, se usa el ``Perfil`` real y solo ``Habilidad``
queda como copia mínima. Así, cuando #2 defina un contrato, este archivo deja de
sustituirlo sin avisar. ``STUBS_EN_USO`` lista los nombres que siguen siendo copia y
``USA_SCHEMAS_REALES`` es verdadero solo cuando ya no queda ninguno. Si #2 nombra distinto
algún campo o el enum de estados, hay que ajustar los usos en db.py, pdf.py y postulacion.py.

Los lectores de habilidades (``skill_id_de``, ``evidencia_de``) aceptan un modelo o un
dict, porque el sistema real puede pasar cualquiera de los dos.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field

import puente.schemas as _schemas

_log = logging.getLogger(__name__)
_stubs: list[str] = []

if hasattr(_schemas, "EstadoPostulacion"):
    EstadoPostulacion = _schemas.EstadoPostulacion
else:
    _stubs.append("EstadoPostulacion")

    class EstadoPostulacion(str, Enum):
        PERFIL_LISTO = "perfil_listo"
        PRESELECCIONADO = "preseleccionado"
        APROBADO = "aprobado"
        ENVIADO = "enviado"
        ENTREVISTA = "entrevista"
        CONTRATADO = "contratado"
        NO_SELECCIONADO = "no_seleccionado"


if hasattr(_schemas, "Horario"):
    Horario = _schemas.Horario
else:
    _stubs.append("Horario")

    class Horario(BaseModel):
        dias: list[str]
        hora_inicio: str  # "HH:MM"
        hora_fin: str  # "HH:MM"


if hasattr(_schemas, "Habilidad"):
    Habilidad = _schemas.Habilidad
else:
    _stubs.append("Habilidad")

    class Habilidad(BaseModel):
        skill_id: str
        evidencia: str = ""


if hasattr(_schemas, "Perfil"):
    Perfil = _schemas.Perfil
else:
    _stubs.append("Perfil")

    class Perfil(BaseModel):
        id: str
        nombre: str  # nombre ficticio
        genero: str | None = None
        edad: int = Field(ge=15, le=29)
        corregimiento: str
        horario_libre: list[Horario] = Field(default_factory=list)
        salario_minimo: float = 0
        presupuesto_pasaje_diario: float = 0
        habilidades: list[Habilidad] = Field(default_factory=list)


if hasattr(_schemas, "Oferta"):
    Oferta = _schemas.Oferta
else:
    _stubs.append("Oferta")

    class Oferta(BaseModel):
        id: str
        puesto: str
        empresa: str
        corregimiento: str
        salario_mensual: float = Field(gt=0)
        horario: Horario
        habilidades: list[str] = Field(default_factory=list)  # skill_id
        requisitos_texto: str = ""
        alertas: list[str] = Field(default_factory=list)
        bloqueada: bool = False
        fuente: str = ""


if hasattr(_schemas, "Match"):
    Match = _schemas.Match
else:
    _stubs.append("Match")

    class Desglose(BaseModel):
        cobertura: float = 0
        minutos_traslado: float = 0
        pasaje_diario: float = 0
        penalizacion: float = 0

    class Match(BaseModel):
        perfil_id: str
        oferta_id: str
        puntaje: float = 0
        desglose: Desglose = Field(default_factory=Desglose)
        brechas: list[str] = Field(default_factory=list)
        explicacion: str = ""


if hasattr(_schemas, "Decision"):
    Decision = _schemas.Decision
else:
    _stubs.append("Decision")

    class Decision(BaseModel):
        agente: str
        entrada_resumen: str
        salida_resumen: str
        razon: str
        timestamp: datetime = Field(
            default_factory=lambda: datetime.now(timezone(timedelta(hours=-5)))
        )


STUBS_EN_USO: tuple[str, ...] = tuple(_stubs)
USA_SCHEMAS_REALES = not STUBS_EN_USO
if STUBS_EN_USO:
    _log.info("Contratos de #7 en copia mínima (faltan en puente.schemas): %s", ", ".join(STUBS_EN_USO))


def _campo(objeto, nombre: str, defecto=""):
    """Lee un campo de un modelo o de un dict."""
    if isinstance(objeto, dict):
        return objeto.get(nombre, defecto)
    return getattr(objeto, nombre, defecto)


def skill_id_de(habilidad) -> str:
    """``skill_id`` de una habilidad, sea modelo o dict."""
    return str(_campo(habilidad, "skill_id") or "")


def evidencia_de(habilidad) -> str:
    """Frase que evidencia la habilidad (vacía si no hay), sea modelo o dict."""
    return str(_campo(habilidad, "evidencia") or "")


__all__ = [
    "Decision",
    "EstadoPostulacion",
    "Habilidad",
    "Horario",
    "Match",
    "Oferta",
    "Perfil",
    "STUBS_EN_USO",
    "USA_SCHEMAS_REALES",
    "evidencia_de",
    "skill_id_de",
]
