"""Contratos compartidos: Oferta, Perfil, Match, Decision y EstadoPuente. Issue #2.

Todos los agentes importan de aquí. Cambiar un campo obliga a revisar a todos los
consumidores, así que se prefiere añadir campos opcionales antes que renombrar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

PATRON_HORA = r"^([01]\d|2[0-3]):[0-5]\d$"
DIAS_VALIDOS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
SIN_OFERTA = "SIN_OFERTA"


def _ahora_utc() -> str:
    return datetime.now(UTC).isoformat()


class EstadoPostulacion(StrEnum):
    PERFIL_LISTO = "perfil_listo"
    PRESELECCIONADO = "preseleccionado"
    APROBADO = "aprobado"
    ENVIADO = "enviado"
    ENTREVISTA = "entrevista"
    CONTRATADO = "contratado"
    NO_SELECCIONADO = "no_seleccionado"


class Horario(BaseModel):
    """Bloque semanal: los mismos días con la misma franja horaria ("HH:MM", 24 h)."""

    dias: list[str] = Field(min_length=1)
    hora_inicio: str = Field(pattern=PATRON_HORA)
    hora_fin: str = Field(pattern=PATRON_HORA)

    @field_validator("dias")
    @classmethod
    def _normalizar_dias(cls, dias: list[str]) -> list[str]:
        """Pasa a minúsculas sin tildes y expande abreviaturas: "Mié" -> "miercoles"."""
        sin_tildes = str.maketrans("áéíóú", "aeiou")
        normalizados = []
        for dia in dias:
            d = dia.strip().lower().translate(sin_tildes)
            normalizados.append(next((n for n in DIAS_VALIDOS if n[:3] == d), d))
        invalidos = [d for d in normalizados if d not in DIAS_VALIDOS]
        if invalidos:
            raise ValueError(f"días no válidos: {invalidos}; usar {DIAS_VALIDOS}")
        return normalizados


def _lista_de_horarios(valor: Any) -> Any:
    """Acepta un solo horario o una lista de ellos."""
    if valor is None:
        return []
    if isinstance(valor, (dict, Horario)):
        return [valor]
    return valor


class Oferta(BaseModel):
    id: str
    puesto: str
    empresa: str
    corregimiento: str
    salario_mensual: float = Field(gt=0)
    horario: Horario
    habilidades: list[str] = Field(default_factory=list, description="skill_id de la taxonomía")
    requisitos_texto: str = ""
    alertas: list[str] = Field(default_factory=list)
    bloqueada: bool = False
    fuente: str = ""


class HabilidadPerfil(BaseModel):
    skill_id: str
    evidencia: str = ""


class Perfil(BaseModel):
    id: str
    nombre: str = Field(description="Nombre ficticio; nunca datos reales")
    genero: str | None = None
    edad: int = Field(ge=15, le=29)
    corregimiento: str
    horario_libre: list[Horario] = Field(default_factory=list)
    horario_clases: list[Horario] = Field(default_factory=list)
    salario_minimo: float = Field(ge=0)
    presupuesto_pasaje_diario: float = Field(ge=0)
    habilidades: list[HabilidadPerfil] = Field(default_factory=list)

    @field_validator("horario_libre", "horario_clases", mode="before")
    @classmethod
    def _horarios_como_lista(cls, valor: Any) -> Any:
        return _lista_de_horarios(valor)


class Desglose(BaseModel):
    """Componentes del puntaje. Acepta claves extra del solver (viable, motivo_descarte, ...)."""

    model_config = ConfigDict(extra="allow")

    cobertura: float = 0.0
    minutos_traslado: float = 0.0
    pasaje_diario: float = 0.0
    penalizacion: float = 0.0

    def __getitem__(self, clave: str) -> Any:
        try:
            return getattr(self, clave)
        except AttributeError as exc:
            raise KeyError(clave) from exc

    def get(self, clave: str, defecto: Any = None) -> Any:
        return getattr(self, clave, defecto)


class Match(BaseModel):
    perfil_id: str
    oferta_id: str = Field(description=f'id de la oferta o "{SIN_OFERTA}"')
    puntaje: int | float = Field(description="Puede ser negativo")
    desglose: Desglose = Field(default_factory=Desglose)
    brechas: list[str] = Field(default_factory=list, description="skill_id que faltan")
    explicacion: str = ""


class Decision(BaseModel):
    agente: str
    entrada_resumen: str
    salida_resumen: str
    razon: str
    timestamp: str = Field(default_factory=_ahora_utc, description="ISO 8601 en UTC")


class EstadoPuente(TypedDict, total=False):
    """Estado del grafo. Sin reductores: cada nodo devuelve la lista completa que quiere dejar."""

    ofertas: list[Oferta]
    perfiles: list[Perfil]
    matches: list[Match]
    lista_corta: list[Match]
    aprobacion: dict[str, Any] | None
    decisiones: list[Decision]
    errores: list[str]
    postulaciones: dict[str, EstadoPostulacion]
    # Entradas y salidas del solver y del auditor (#4, #8)
    traslados: dict[str, Any]
    config_solver: dict[str, Any]
    auditoria: dict[str, Any]
    reporte_auditor: Any
    sesgo_detectado: bool
    devolver_a_matching: bool
    accion_auditor: str
    alertas_auditor: list[str]
    reintentos_auditor: int
