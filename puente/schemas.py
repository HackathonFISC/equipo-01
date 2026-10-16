"""Contratos compartidos: Oferta, Perfil, Match, Decision y EstadoPuente. Issues #2 y #42.

Todos los agentes importan de aquí. Cambiar un campo obliga a revisar a todos los
consumidores, así que se prefiere añadir campos opcionales antes que renombrar.

Lo que produce el LLM (`OfertaExtraida`, `PerfilExtraido`) es todo opcional y nunca trae
legalidad ni ids: el código arma `Oferta` y `Perfil` después de normalizar y aplicar reglas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Required, TypedDict

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

PATRON_HORA = r"^([01]\d|2[0-3]):[0-5]\d$"
DIAS_VALIDOS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
SIN_OFERTA = "SIN_OFERTA"

# Regla única para lo desconocido (#42): un dato que la oferta no publica no descarta ni aprueba.
# El match lleva en el desglose `requiere_confirmacion` con los campos y el solver (#48) resta
# PENALIZACION_POR_CONFIRMAR por cada uno.
REQUIERE_CONFIRMACION = "requiere_confirmacion"
PENALIZACION_POR_CONFIRMAR = 10

SalarioTipo = Literal["exacto", "comisiones", "no_publicado"]
HorarioTipo = Literal["fijo", "rotativo", "no_publicado"]


def _ahora_utc() -> str:
    return datetime.now(UTC).isoformat()


class EstadoPostulacion(StrEnum):
    PERFIL_LISTO = "perfil_listo"
    PRESELECCIONADO = "preseleccionado"
    APROBADO = "aprobado"
    CONSENTIDO = "consentido"
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
    salario_mensual: float | None = Field(None, gt=0, description="None si la oferta no lo publica")
    salario_tipo: SalarioTipo | None = Field(None, description="Si falta: exacto con salario, no_publicado sin él")
    horario: Horario | None = None
    horario_tipo: HorarioTipo | None = Field(None, description="Si falta: fijo con horario, no_publicado sin él")
    plazas: int = Field(1, ge=1)
    franjas_entrevista: list[Horario] = Field(default_factory=list)
    habilidades: list[str] = Field(default_factory=list, description="skill_id de la taxonomía")
    requisitos_texto: str = ""
    alertas: list[str] = Field(default_factory=list)
    bloqueada: bool = False
    fuente: str = ""

    @field_validator("franjas_entrevista", mode="before")
    @classmethod
    def _franjas_como_lista(cls, valor: Any) -> Any:
        return _lista_de_horarios(valor)

    @model_validator(mode="after")
    def _tipos_coherentes(self) -> Oferta:
        """Completa salario_tipo y horario_tipo y rechaza combinaciones contradictorias."""
        if self.salario_tipo is None:
            self.salario_tipo = "no_publicado" if self.salario_mensual is None else "exacto"
        if self.salario_tipo == "exacto" and self.salario_mensual is None:
            raise ValueError('salario_tipo="exacto" exige salario_mensual')
        if self.salario_tipo == "no_publicado" and self.salario_mensual is not None:
            raise ValueError('salario_tipo="no_publicado" no admite salario_mensual')
        if self.horario_tipo is None:
            self.horario_tipo = "no_publicado" if self.horario is None else "fijo"
        if self.horario_tipo == "fijo" and self.horario is None:
            raise ValueError('horario_tipo="fijo" exige horario')
        if self.horario_tipo == "no_publicado" and self.horario is not None:
            raise ValueError('horario_tipo="no_publicado" no admite horario')
        return self

    def por_confirmar(self) -> list[str]:
        """Campos que el solver no puede verificar y que van a `requiere_confirmacion` del desglose."""
        faltan = []
        if self.salario_mensual is None:
            faltan.append("salario")
        if self.horario is None:
            faltan.append("horario")
        if not self.corregimiento.strip():
            faltan.append("corregimiento")
        return faltan


class HabilidadPerfil(BaseModel):
    skill_id: str
    evidencia: str = ""


class Perfil(BaseModel):
    id: str
    nombre: str = Field(description="Nombre ficticio; nunca datos reales")
    genero: str | None = None
    edad: int = Field(ge=18, le=29, description="Menores fuera de la demo (#15)")
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


CLAVES_DESGLOSE = ("cobertura", "minutos_traslado", "pasaje_diario", "penalizacion")


def _validar_desglose(desglose: dict[str, Any]) -> dict[str, Any]:
    """Exige las claves numéricas del puntaje; las demás (viable, motivo_descarte, ...) pasan tal cual."""
    faltan = [c for c in CLAVES_DESGLOSE if c not in desglose]
    if faltan:
        raise ValueError(f"al desglose le faltan claves: {faltan}")
    no_numericas = [
        c for c in CLAVES_DESGLOSE if isinstance(desglose[c], bool) or not isinstance(desglose[c], (int, float))
    ]
    if no_numericas:
        raise ValueError(f"claves del desglose que deben ser numéricas: {no_numericas}")
    por_confirmar = desglose.get(REQUIERE_CONFIRMACION, [])
    if not isinstance(por_confirmar, list) or not all(isinstance(c, str) for c in por_confirmar):
        raise ValueError(f"{REQUIERE_CONFIRMACION} debe ser una lista de nombres de campo")
    return desglose


# Es un dict y no un modelo para que `isinstance(desglose, dict)` y `.get()` funcionen en todos los agentes.
Desglose = Annotated[dict[str, Any], AfterValidator(_validar_desglose)]


class Match(BaseModel):
    perfil_id: str
    oferta_id: str = Field(description=f'id de la oferta o "{SIN_OFERTA}"')
    puntaje: int | float = Field(description="Puede ser negativo")
    desglose: Desglose
    brechas: list[str] = Field(default_factory=list, description="skill_id que faltan")
    explicacion: str = ""


class Decision(BaseModel):
    agente: str
    entrada_resumen: str
    salida_resumen: str
    razon: str
    timestamp: str = Field(default_factory=_ahora_utc, description="ISO 8601 en UTC")


def agregar_decision(estado: EstadoPuente | dict[str, Any], decision: Decision | dict[str, Any]) -> dict:
    """Devuelve `{"decisiones": anteriores + [decision]}` sin tocar la lista del estado.

    El grafo no tiene reductores: un nodo que devuelve solo su decisión borra el historial.
    """
    nueva = decision if isinstance(decision, Decision) else Decision.model_validate(decision)
    return {"decisiones": list(estado.get("decisiones") or []) + [nueva]}


# --- Salida del LLM (#42). Todo opcional: lo que no aparece queda en None, nunca se inventa. ---


class HorarioExtraido(BaseModel):
    """Horario tal como lo lee el LLM; el código lo valida y normaliza a `Horario`."""

    dias: list[str] = Field(default_factory=list, description="Días en español, p. ej. lunes")
    hora_inicio: str | None = Field(None, description="HH:MM en 24 h")
    hora_fin: str | None = Field(None, description="HH:MM en 24 h")


class OfertaExtraida(BaseModel):
    """Lo único que el LLM produce de una oferta. Sin id, legalidad ni alertas: eso lo decide el código."""

    puesto: str | None = None
    empresa: str | None = None
    corregimiento: str | None = Field(None, description="Corregimiento de Panamá; null si la oferta es vaga")
    ubicacion_texto: str | None = Field(None, description="La ubicación copiada literal, p. ej. 'Área de Costa del Este'")
    salario_mensual: float | None = Field(None, description="Salario mensual en balboas; null si no aparece")
    salario_tipo: SalarioTipo | None = Field(None, description="no_publicado si la oferta no da cifra")
    horario: HorarioExtraido | None = None
    horario_tipo: HorarioTipo | None = Field(None, description="'Tiempo Completo' sin horas es no_publicado")
    plazas: int | None = None
    franjas_entrevista: list[HorarioExtraido] = Field(default_factory=list)
    habilidades: list[str] = Field(default_factory=list, description="Habilidades pedidas, en español y breves")
    requisitos_texto: str | None = Field(None, description="Requisitos copiados literalmente")
    transcripcion_literal: str | None = Field(None, description="Imagen o PDF: transcripción literal y completa")


class PerfilExtraido(BaseModel):
    """Lo único que el LLM produce de un CV o una entrevista. Sin id ni nombre: la PII no pasa por el LLM."""

    habilidades: list[HabilidadPerfil] = Field(default_factory=list, description="Con evidencia literal")
    edad: int | None = None
    corregimiento: str | None = None
    ubicacion_texto: str | None = None
    salario_minimo: float | None = None
    horario_libre: list[HorarioExtraido] = Field(default_factory=list)
    horario_clases: list[HorarioExtraido] = Field(default_factory=list)
    transcripcion_literal: str | None = Field(None, description="CV en imagen o PDF: transcripción literal")


# --- Entradas y registros del estado (#42) ---


class TipoEntrada(StrEnum):
    """Lo que lee el router del grafo (#10)."""

    OFERTA = "oferta"
    PERFIL = "perfil"
    CONSULTA = "consulta"
    EVENTO_EMPRESA = "evento_empresa"


class EntradaPerfil(TypedDict, total=False):
    """Forma que documenta `construir_perfil` (#6, #39): `id` y uno de cv_texto, cv_ruta o respuestas.

    Los datos del formulario son opcionales y mandan sobre lo que extraiga el LLM.
    """

    id: Required[str]
    cv_texto: str
    cv_ruta: str
    respuestas: list[str]
    nombre: str
    genero: str
    edad: int
    corregimiento: str
    horario_libre: list[dict[str, Any]] | dict[str, Any]
    horario_clases: list[dict[str, Any]] | dict[str, Any]
    salario_minimo: float
    presupuesto_pasaje_diario: float


# Texto pegado, ruta a jpg/png/pdf, o dict con `texto` o `ruta` y `fuente` opcional (#5, #37).
OfertaCruda = str | dict[str, Any]


class Exclusion(BaseModel):
    """Pareja perfil–oferta que el solver no debe volver a proponer."""

    perfil_id: str
    oferta_id: str
    origen: Literal["orientador", "empresa"]
    motivo: str = ""
    timestamp: str = Field(default_factory=_ahora_utc)


class Consentimiento(BaseModel):
    """El joven acepta (o no) que su perfil se envíe a una empresa. Va entre aprobado y enviado."""

    perfil_id: str
    oferta_id: str
    otorgado: bool
    medio: str = Field("", description="telegram, presencial, ...")
    timestamp: str = Field(default_factory=_ahora_utc)


class EstadoPuente(TypedDict, total=False):
    """Estado del grafo. Sin reductores: cada nodo devuelve la lista completa que quiere dejar.

    LangGraph descarta sin error las claves que no están aquí: toda clave que escriba un nodo se declara.
    """

    tipo_entrada: TipoEntrada
    ofertas_crudas: list[OfertaCruda]
    entradas_perfil: list[EntradaPerfil]

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
    # Formación, orientador, postulación y métricas (#14, #11, #7, #42)
    rutas_formacion: list[dict[str, Any]]
    excluidos: list[Exclusion]
    consentimientos: list[Consentimiento]
    metricas: dict[str, Any]
