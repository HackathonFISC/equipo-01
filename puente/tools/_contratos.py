"""Contratos que usan las herramientas de Postulación (issue #7).

# STUB hasta que se fusione #2

Primero intenta importar los modelos de ``puente.schemas`` (issue #2). Mientras ese
módulo siga vacío, define aquí copias mínimas con los campos del issue #2. Al fusionar
#2 este archivo deja de definir nada y solo reexporta; si #2 nombra distinto algún campo
o el enum de estados, hay que ajustar este archivo y los usos en db.py, pdf.py y
postulacion.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field

try:  # pragma: no cover - rama que se activa cuando #2 esté fusionado
    from puente.schemas import (  # type: ignore[attr-defined]
        Decision,
        EstadoPostulacion,
        Habilidad,
        Horario,
        Match,
        Oferta,
        Perfil,
    )

    USA_SCHEMAS_REALES = True
except ImportError:
    USA_SCHEMAS_REALES = False

    class EstadoPostulacion(str, Enum):
        PERFIL_LISTO = "perfil_listo"
        PRESELECCIONADO = "preseleccionado"
        APROBADO = "aprobado"
        ENVIADO = "enviado"
        ENTREVISTA = "entrevista"
        CONTRATADO = "contratado"
        NO_SELECCIONADO = "no_seleccionado"

    class Horario(BaseModel):
        dias: list[str]
        hora_inicio: str  # "HH:MM"
        hora_fin: str  # "HH:MM"

    class Habilidad(BaseModel):
        skill_id: str
        evidencia: str = ""

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

    class Decision(BaseModel):
        agente: str
        entrada_resumen: str
        salida_resumen: str
        razon: str
        timestamp: datetime = Field(
            default_factory=lambda: datetime.now(timezone(timedelta(hours=-5)))
        )


__all__ = [
    "Decision",
    "EstadoPostulacion",
    "Habilidad",
    "Horario",
    "Match",
    "Oferta",
    "Perfil",
]
