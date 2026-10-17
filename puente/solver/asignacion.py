"""Asignación jóvenes-vacantes con OR-Tools CP-SAT y restricciones duras. Issues #4 y #48."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

from ortools.sat.python import cp_model
from pydantic import BaseModel, ConfigDict, Field

import puente.schemas as _schemas_mod
from puente.rules.traslados import (
    InfoTraslado,
    indexar_traslados,
    obtener_traslado,
)

SIN_OFERTA_ID = getattr(_schemas_mod, "SIN_OFERTA", "SIN_OFERTA")
PENALIZACION_CONFIRMACION_DEFAULT = int(
    getattr(_schemas_mod, "PENALIZACION_POR_CONFIRMAR", 10)
)


class ConfigSolver(BaseModel):
    """Configuración determinística del solver CP-SAT y sus restricciones duras.

    Los umbrales por defecto (60 min de traslado, 15 % del salario en pasaje, 22 días
    laborables, tope de 2 vacantes por joven y 3 candidatos por plaza) son parámetros
    del orientador documentados en `data/FUENTES.md` como supuesto del equipo (#48).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    max_minutos_traslado: int = Field(
        default=60,
        ge=1,
        description="Tiempo máximo de traslado permitido en minutos (hora pico); parámetro del orientador",
    )
    max_porcentaje_salario_pasaje: float = Field(
        default=0.15,
        gt=0.0,
        le=1.0,
        description="Fracción máxima del salario mensual que puede representar el pasaje mensual; parámetro del orientador",
    )
    dias_laborables: int = Field(
        default=22,
        ge=1,
        le=31,
        alias="dias_laborables_mes",
        description="Días laborables al mes para calcular el pasaje mensual (ida y vuelta × días)",
    )
    max_candidatos_por_vacante: int = Field(
        default=3,
        ge=1,
        alias="k",
        description="Máximo k candidatos asignados por plaza de vacante (lista corta)",
    )
    max_vacantes_por_perfil: int | None = Field(
        default=2,
        ge=1,
        description="Máximo de vacantes a las que puede ser asignado un mismo joven (por defecto 2, #48)",
    )
    peso_cobertura: int = Field(
        default=100,
        ge=1,
        description="Coeficiente entero para el porcentaje de cobertura de habilidades (0..100)",
    )
    peso_penalizacion_minuto: int = Field(
        default=0,
        ge=0,
        description=(
            "Penalización directa por minuto de traslado (0 por defecto: los minutos filtran "
            "según el máximo del joven y desempatan sin restar puntaje entre jóvenes, #48)"
        ),
    )
    penalizacion_por_confirmar: int = Field(
        default=PENALIZACION_CONFIRMACION_DEFAULT,
        ge=0,
        description="Penalización por cada dato no publicado en requiere_confirmacion (#42, #48)",
    )
    permitir_traslado_estimado: bool = Field(
        default=False,
        description=(
            "Si True, cuando falta una pareja en traslados.csv no descarta sino que "
            "marca fuente='estimado' y agrega 'traslado' a requiere_confirmacion"
        ),
    )
    semilla: int = Field(
        default=42, description="Semilla fija para garantizar determinismo en CP-SAT"
    )
    tiempo_limite_segundos: float = Field(
        default=1.8, gt=0.0, description="Tiempo límite de resolución en segundos (< 2 s)"
    )
    incluir_perfiles_sin_vacante: bool = Field(
        default=True,
        description="Si True, incluye un registro Match con oferta_id=SIN_OFERTA y puntaje=0 cuando un perfil no tiene vacante viable",
    )


class MatchFallback(BaseModel):
    """Contrato de respaldo para Match cuando puente.schemas aún no ha sido fusionado (#2)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    perfil_id: str
    oferta_id: str = ""
    puntaje: int | float = 0
    desglose: dict[str, Any] = Field(default_factory=dict)
    brechas: list[str] = Field(default_factory=list)
    explicacion: str = ""


def _construir_match(
    *,
    perfil_id: str,
    oferta_id: str,
    puntaje: int,
    desglose: dict[str, Any],
    brechas: list[str],
    explicacion: str,
) -> Any:
    """Instancia `puente.schemas.Match` (#2, #42) o `MatchFallback` en su defecto."""
    cls_match = getattr(_schemas_mod, "Match", None)
    if cls_match is not None and isinstance(cls_match, type) and issubclass(cls_match, BaseModel):
        try:
            return cls_match(
                perfil_id=perfil_id,
                oferta_id=oferta_id,
                puntaje=puntaje,
                desglose=desglose,
                brechas=brechas,
                explicacion=explicacion,
            )
        except Exception:
            req_conf = desglose.get("requiere_confirmacion", [])
            if not isinstance(req_conf, list):
                req_conf = []
            desglose_basico = {
                "cobertura": desglose.get("cobertura", 0.0),
                "minutos_traslado": desglose.get("minutos_traslado", 0),
                "pasaje_diario": desglose.get("pasaje_diario", 0.0),
                "penalizacion": desglose.get("penalizacion", 0),
                "requiere_confirmacion": req_conf,
            }
            return cls_match(
                perfil_id=perfil_id,
                oferta_id=oferta_id,
                puntaje=puntaje,
                desglose=desglose_basico,
                brechas=brechas,
                explicacion=explicacion,
            )
    return MatchFallback(
        perfil_id=perfil_id,
        oferta_id=oferta_id,
        puntaje=puntaje,
        desglose=desglose,
        brechas=brechas,
        explicacion=explicacion,
    )


def _get_field(obj: Any, *names: str, default: Any = None) -> Any:
    """Obtiene un atributo o clave de un objeto Pydantic o diccionario probando varios nombres."""
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            val = getattr(obj, name)
            if val is not None:
                return val
    return default


def _has_explicit_none(obj: Any, *names: str) -> bool:
    """Devuelve True si el objeto o dict tiene explícitamente alguno de los campos en None."""
    if isinstance(obj, dict):
        return any(name in obj and obj[name] is None for name in names)
    return any(hasattr(obj, name) and getattr(obj, name) is None for name in names)


def _normalizar_texto(texto: str) -> str:
    texto = str(texto).strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def extraer_skill_ids_perfil(perfil: Any) -> list[str]:
    """Extrae la lista normalizada de skill_id de un perfil (acepta list[str], list[dict] o list[BaseModel])."""
    raw = _get_field(perfil, "habilidades", "skills", "skill_ids", default=[])
    resultado: list[str] = []
    if isinstance(raw, dict):
        raw = list(raw.keys())
    for item in raw or []:
        if isinstance(item, str):
            sid = item.strip()
        elif isinstance(item, dict):
            sid = str(item.get("skill_id") or item.get("id") or item.get("nombre") or "").strip()
        else:
            sid = str(
                getattr(item, "skill_id", None)
                or getattr(item, "id", None)
                or getattr(item, "nombre", "")
            ).strip()
        if sid and sid not in resultado:
            resultado.append(sid)
    return resultado


def extraer_skill_ids_oferta(oferta: Any) -> list[str]:
    """Extrae la lista normalizada de skill_id requeridos por una oferta."""
    raw = _get_field(oferta, "habilidades", "skills", "skill_ids", default=[])
    resultado: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            sid = item.strip()
        elif isinstance(item, dict):
            sid = str(item.get("skill_id") or item.get("id") or item.get("nombre") or "").strip()
        else:
            sid = str(
                getattr(item, "skill_id", None)
                or getattr(item, "id", None)
                or getattr(item, "nombre", "")
            ).strip()
        if sid and sid not in resultado:
            resultado.append(sid)
    return resultado


_MAPA_DIAS: dict[str, set[str]] = {
    "l": {"lunes"},
    "lu": {"lunes"},
    "lun": {"lunes"},
    "lunes": {"lunes"},
    "m": {"martes"},
    "ma": {"martes"},
    "mar": {"martes"},
    "martes": {"martes"},
    "x": {"miercoles"},
    "mi": {"miercoles"},
    "mie": {"miercoles"},
    "miercoles": {"miercoles"},
    "j": {"jueves"},
    "ju": {"jueves"},
    "jue": {"jueves"},
    "jueves": {"jueves"},
    "v": {"viernes"},
    "vi": {"viernes"},
    "vie": {"viernes"},
    "viernes": {"viernes"},
    "s": {"sabado"},
    "sa": {"sabado"},
    "sab": {"sabado"},
    "sabado": {"sabado"},
    "d": {"domingo"},
    "do": {"domingo"},
    "dom": {"domingo"},
    "domingo": {"domingo"},
    "l-v": {"lunes", "martes", "miercoles", "jueves", "viernes"},
    "lunes a viernes": {"lunes", "martes", "miercoles", "jueves", "viernes"},
    "l-s": {"lunes", "martes", "miercoles", "jueves", "viernes", "sabado"},
    "lunes a sabado": {"lunes", "martes", "miercoles", "jueves", "viernes", "sabado"},
    "todos": {"lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"},
}


def _parsear_minutos_dia(valor: Any) -> int | None:
    """Convierte '08:00', '17:30', 8 o 8.5 en minutos desde medianoche (0..1440)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        if 0 <= valor <= 24:
            return int(round(float(valor) * 60))
        return int(valor)
    texto = str(valor).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", texto)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m_num = re.match(r"^(\d{1,2})$", texto)
    if m_num:
        return int(m_num.group(1)) * 60
    return None


def _formatear_hora(minutos: int) -> str:
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def _extraer_dias(raw_dias: Any) -> set[str]:
    """Normaliza una lista o cadena de días a un conjunto canónico."""
    todos_habiles = {"lunes", "martes", "miercoles", "jueves", "viernes"}
    if not raw_dias:
        return todos_habiles
    if isinstance(raw_dias, str):
        norm = _normalizar_texto(raw_dias)
        if norm in _MAPA_DIAS:
            return set(_MAPA_DIAS[norm])
        partes = [p.strip() for p in re.split(r"[,;/]+", norm) if p.strip()]
        dias: set[str] = set()
        for p in partes:
            dias.update(_MAPA_DIAS.get(p, {p}))
        return dias or todos_habiles
    if isinstance(raw_dias, (list, tuple, set)):
        dias = set()
        for item in raw_dias:
            norm = _normalizar_texto(str(item))
            dias.update(_MAPA_DIAS.get(norm, {norm}))
        return dias or todos_habiles
    return todos_habiles


def _extraer_ventana_horario(raw: Any) -> dict[str, Any]:
    """Extrae {dias, inicio, fin, etiqueta} de un objeto/dict/string de horario."""
    todos_habiles = {"lunes", "martes", "miercoles", "jueves", "viernes"}
    if raw is None:
        return {"dias": todos_habiles, "inicio": 8 * 60, "fin": 17 * 60, "etiqueta": "08:00-17:00"}

    if isinstance(raw, str):
        norm = _normalizar_texto(raw)
        m = re.search(r"(\d{1,2}:\d{2})\s*(?:-|a|hasta)\s*(\d{1,2}:\d{2})", norm)
        inicio = _parsear_minutos_dia(m.group(1)) if m else None
        fin = _parsear_minutos_dia(m.group(2)) if m else None

        if inicio is None or fin is None:
            if "nocturn" in norm or "noche" in norm:
                inicio, fin = 18 * 60, 22 * 60
            elif "manana" in norm and "tarde" in norm:
                inicio, fin = 7 * 60, 18 * 60
            elif "diurn" in norm or "dia" in norm:
                inicio, fin = 7 * 60, 17 * 60 + 30
            elif "manana" in norm or "matutin" in norm:
                inicio, fin = 7 * 60, 13 * 60
            elif "tarde" in norm or "vespertin" in norm:
                inicio, fin = 13 * 60, 18 * 60
            else:
                inicio, fin = 6 * 60, 22 * 60

        dias = todos_habiles
        if "l-s" in norm or "sabado" in norm:
            dias = todos_habiles | {"sabado"}
        return {
            "dias": dias,
            "inicio": inicio,
            "fin": fin,
            "etiqueta": f"{_formatear_hora(inicio)}-{_formatear_hora(fin)}",
        }

    dias_raw = _get_field(raw, "dias", "days", default=None)
    inicio_raw = _get_field(raw, "hora_inicio", "inicio", "start", default="08:00")
    fin_raw = _get_field(raw, "hora_fin", "fin", "end", default="17:00")
    inicio = _parsear_minutos_dia(inicio_raw)
    fin = _parsear_minutos_dia(fin_raw)
    if inicio is None:
        inicio = 8 * 60
    if fin is None:
        fin = 17 * 60
    dias = _extraer_dias(dias_raw)
    return {
        "dias": dias,
        "inicio": inicio,
        "fin": fin,
        "etiqueta": f"{_formatear_hora(inicio)}-{_formatear_hora(fin)}",
    }


def verificar_compatibilidad_horario(perfil: Any, oferta: Any) -> tuple[bool, str | None]:
    """Verifica que el horario de la oferta no cruce con las clases y caiga dentro del horario libre."""
    horario_oferta_raw = _get_field(oferta, "horario", default=None)
    if horario_oferta_raw is None:
        return True, None
    ventana_oferta = _extraer_ventana_horario(horario_oferta_raw)

    # 1. Revisar si el perfil declara explícitamente horario_clases / clases
    clases_raw = _get_field(perfil, "horario_clases", "clases", default=None)
    if clases_raw is not None:
        lista_clases = clases_raw if isinstance(clases_raw, list) else [clases_raw]
        for clase in lista_clases:
            v_clase = _extraer_ventana_horario(clase)
            dias_comunes = ventana_oferta["dias"] & v_clase["dias"]
            if dias_comunes:
                if max(ventana_oferta["inicio"], v_clase["inicio"]) < min(
                    ventana_oferta["fin"], v_clase["fin"]
                ):
                    return (
                        False,
                        f"horario oferta ({ventana_oferta['etiqueta']}) cruza con clases ({v_clase['etiqueta']})",
                    )

    # 2. Revisar horario_libre del perfil
    libre_raw = _get_field(perfil, "horario_libre", "disponibilidad", "horario", default=None)
    if not libre_raw:
        return True, None

    if isinstance(libre_raw, str):
        norm = _normalizar_texto(libre_raw)
        if ("estudia de noche" in norm or "clases de noche" in norm or "nocturn" in norm) and "libre" not in norm:
            if ventana_oferta["fin"] > 18 * 60:
                return (
                    False,
                    f"horario oferta ({ventana_oferta['etiqueta']}) cruza con clases nocturnas (>= 18:00)",
                )
            if "diurn" in norm or "estudia de noche" in norm or "clases de noche" in norm:
                return True, None
        if ("estudia de manana" in norm or "clases de manana" in norm) and ventana_oferta["inicio"] < 12 * 60:
            return (
                False,
                f"horario oferta ({ventana_oferta['etiqueta']}) cruza con clases matutinas (< 12:00)",
            )

    ventanas_libres = libre_raw if isinstance(libre_raw, list) else [libre_raw]
    for vl_raw in ventanas_libres:
        if isinstance(vl_raw, dict) and "clases" in vl_raw and vl_raw["clases"]:
            v_clase = _extraer_ventana_horario(vl_raw["clases"])
            if (ventana_oferta["dias"] & v_clase["dias"]) and max(
                ventana_oferta["inicio"], v_clase["inicio"]
            ) < min(ventana_oferta["fin"], v_clase["fin"]):
                return (
                    False,
                    f"horario oferta ({ventana_oferta['etiqueta']}) cruza con clases ({v_clase['etiqueta']})",
                )

        vl = _extraer_ventana_horario(vl_raw)
        dias_faltantes = ventana_oferta["dias"] - vl["dias"]
        cabe_en_horas = (
            ventana_oferta["inicio"] >= vl["inicio"] and ventana_oferta["fin"] <= vl["fin"]
        )
        if not dias_faltantes and cabe_en_horas:
            return True, None

    primera_vl = _extraer_ventana_horario(ventanas_libres[0])
    if ventana_oferta["inicio"] < primera_vl["inicio"] or ventana_oferta["fin"] > primera_vl["fin"]:
        return (
            False,
            f"horario oferta ({ventana_oferta['etiqueta']}) fuera de horario libre ({primera_vl['etiqueta']})",
        )
    return (
        False,
        f"días de oferta ({sorted(ventana_oferta['dias'])}) fuera de días disponibles ({sorted(primera_vl['dias'])})",
    )


def _campos_por_confirmar_oferta(oferta: Any) -> list[str]:
    """Determina qué campos desconocidos de la oferta requieren confirmación (#42, #48)."""
    faltan: list[str] = []

    salario_raw = _get_field(oferta, "salario_mensual", "salario", default=None)
    salario_tipo = _get_field(oferta, "salario_tipo", default=None)
    if salario_raw is None or salario_tipo == "no_publicado":
        faltan.append("salario")

    horario_raw = _get_field(oferta, "horario", default=None)
    horario_tipo = _get_field(oferta, "horario_tipo", default=None)
    horario_explicito_none = _has_explicit_none(oferta, "horario")
    if (
        horario_tipo in ("no_publicado", "rotativo")
        or isinstance(oferta, getattr(_schemas_mod, "Oferta", ())) and horario_raw is None
        or (horario_explicito_none and horario_raw is None)
    ):
        faltan.append("horario")

    corregimiento = str(_get_field(oferta, "corregimiento", default="") or "").strip()
    if not corregimiento:
        faltan.append("corregimiento")

    return faltan


def evaluar_factibilidad_par(
    perfil: Any,
    oferta: Any,
    indice_traslados: dict[tuple[str, str], InfoTraslado],
    config: ConfigSolver,
) -> dict[str, Any]:
    """Evalúa todas las restricciones duras y calcula métricas para el par (perfil, oferta).

    Regla de lo desconocido (#42, #48):
    - Si `salario_mensual` es `None` (`salario_tipo="no_publicado"`), no descarta por salario
      ni por el 15 % de salario en pasaje; agrega `"salario"` a `requiere_confirmacion` y resta
      `config.penalizacion_por_confirmar` puntos.
    - Si `horario` es `None` en una `Oferta` o `horario_tipo="no_publicado"`, no descarta por
      horario; agrega `"horario"` a `requiere_confirmacion` y resta `config.penalizacion_por_confirmar`.
    - Los minutos de traslado filtran según el máximo del joven (`perfil.max_minutos_traslado`
      o `config.max_minutos_traslado`) y desempatan, pero no restan puntaje entre jóvenes
      (`peso_penalizacion_minuto = 0` por defecto).
    """
    perfil_id = str(_get_field(perfil, "id", "perfil_id", default="perfil"))
    oferta_id = str(_get_field(oferta, "id", "oferta_id", default="oferta"))
    origen = str(_get_field(perfil, "corregimiento", default="") or "").strip()
    destino = str(_get_field(oferta, "corregimiento", default="") or "").strip()

    salario_raw = _get_field(oferta, "salario_mensual", "salario", default=None)
    salario_tipo = _get_field(oferta, "salario_tipo", default=None)
    salario_conocido = salario_raw is not None and salario_tipo != "no_publicado"
    salario_oferta = float(salario_raw) if salario_conocido else None

    salario_min_perfil = float(_get_field(perfil, "salario_minimo", default=0.0))
    presupuesto_diario = float(
        _get_field(perfil, "presupuesto_pasaje_diario", "presupuesto_diario", default=999.0)
    )
    max_minutos_joven = int(
        _get_field(perfil, "max_minutos_traslado", default=config.max_minutos_traslado)
    )
    bloqueada = bool(_get_field(oferta, "bloqueada", default=False))

    motivos: list[str] = []
    requiere_confirmacion: list[str] = _campos_por_confirmar_oferta(oferta)

    if bloqueada:
        motivos.append("oferta bloqueada por reglas de discriminación")

    info_traslado = obtener_traslado(indice_traslados, origen, destino) if (origen and destino) else None
    fuente_traslado = "desconocida"
    if info_traslado is None:
        if config.permitir_traslado_estimado:
            minutos_traslado = config.max_minutos_traslado
            pasaje_diario = min(presupuesto_diario, 2.00)
            pasaje_mensual = round(pasaje_diario * config.dias_laborables, 2)
            fuente_traslado = "estimado"
            if "traslado" not in requiere_confirmacion:
                requiere_confirmacion.append("traslado")
        else:
            minutos_traslado = 999
            pasaje_diario = 999.0
            pasaje_mensual = 999.0 * config.dias_laborables
            fuente_traslado = "estimado"
            motivos.append(f"sin dato de traslado ({origen} -> {destino})")
    else:
        minutos_traslado = int(info_traslado.minutos_hora_pico)
        pasaje_diario = info_traslado.pasaje_diario_usd
        pasaje_mensual = info_traslado.pasaje_mensual_usd(dias_laborables=config.dias_laborables)
        fuente_traslado = str(getattr(info_traslado, "fuente", "") or "medido")
        if fuente_traslado == "estimado" and "traslado" not in requiere_confirmacion:
            requiere_confirmacion.append("traslado")

        # 1. Restricción dura: traslado <= máximo del joven (por defecto 60 min)
        if minutos_traslado > max_minutos_joven:
            motivos.append(f"traslado {minutos_traslado} min > {max_minutos_joven}")

        # 2. Restricción dura: pasaje mensual <= 15 % del salario mensual (solo si el salario es conocido)
        if salario_oferta is not None:
            limite_salario_pasaje = round(
                salario_oferta * config.max_porcentaje_salario_pasaje, 2
            )
            if round(pasaje_mensual * 100) > round(limite_salario_pasaje * 100):
                pct_entero = int(round(config.max_porcentaje_salario_pasaje * 100))
                motivos.append(
                    f"pasaje mensual ${pasaje_mensual:.2f} > {pct_entero}% del salario (${limite_salario_pasaje:.2f})"
                )

        # 3. Restricción dura: pasaje <= presupuesto del perfil
        presupuesto_mensual = round(presupuesto_diario * config.dias_laborables, 2)
        if round(pasaje_diario * 100) > round(presupuesto_diario * 100):
            motivos.append(
                f"pasaje diario ${pasaje_diario:.2f} (mensual ${pasaje_mensual:.2f}) > presupuesto ${presupuesto_diario:.2f}/día (${presupuesto_mensual:.2f}/mes)"
            )

    # 4. Restricción dura: horario sin cruce con clases (si el horario de la oferta está publicado)
    if "horario" not in requiere_confirmacion or _get_field(oferta, "horario", default=None) is not None:
        horario_ok, motivo_horario = verificar_compatibilidad_horario(perfil, oferta)
        if not horario_ok and motivo_horario:
            motivos.append(motivo_horario)

    # 5. Restricción dura: salario >= salario mínimo del perfil (solo si el salario de la oferta es conocido)
    if salario_oferta is not None:
        if round(salario_oferta * 100) < round(salario_min_perfil * 100):
            motivos.append(
                f"salario ${salario_oferta:.2f} < salario mínimo del perfil (${salario_min_perfil:.2f})"
            )

    # Cálculo determinístico de cobertura de habilidades y brechas
    skills_perfil = set(extraer_skill_ids_perfil(perfil))
    skills_oferta = extraer_skill_ids_oferta(oferta)

    if skills_oferta:
        cubiertas = [s for s in skills_oferta if s in skills_perfil]
        brechas = [s for s in skills_oferta if s not in skills_perfil]
        cobertura = len(cubiertas) / len(skills_oferta)
        if not cubiertas:
            motivos.append(
                f"ninguna habilidad requerida en común (brechas: {', '.join(brechas)})"
            )
    else:
        cubiertas = []
        brechas = []
        cobertura = 1.0

    cobertura_entera = int(round(cobertura * config.peso_cobertura))
    penalizacion_confirmacion = len(requiere_confirmacion) * config.penalizacion_por_confirmar
    penalizacion_traslado = int(minutos_traslado * config.peso_penalizacion_minuto)
    penalizacion = penalizacion_confirmacion + penalizacion_traslado
    puntaje = cobertura_entera - penalizacion

    viable = len(motivos) == 0
    motivo_str = "; ".join(motivos) if motivos else None

    return {
        "perfil_id": perfil_id,
        "oferta_id": oferta_id,
        "origen": origen,
        "destino": destino,
        "viable": viable,
        "motivos": motivos,
        "motivo_str": motivo_str,
        "cobertura": round(cobertura, 4),
        "cobertura_entera": cobertura_entera,
        "cubiertas": cubiertas,
        "brechas": brechas,
        "minutos_traslado": minutos_traslado,
        "pasaje_diario": pasaje_diario,
        "pasaje_mensual": round(pasaje_mensual, 2),
        "fuente_traslado": fuente_traslado,
        "requiere_confirmacion": list(requiere_confirmacion),
        "penalizacion_confirmacion": penalizacion_confirmacion,
        "penalizacion": penalizacion,
        "puntaje": puntaje,
    }


def _normalizar_config(config: ConfigSolver | dict[str, Any] | None) -> ConfigSolver:
    if config is None:
        return ConfigSolver()
    if isinstance(config, ConfigSolver):
        return config
    if isinstance(config, dict):
        return ConfigSolver(**config)
    return ConfigSolver()


def _normalizar_mapa_excluidos(excluidos: Any) -> dict[tuple[str, str], str]:
    """Convierte una colección de exclusiones en {(perfil_id, oferta_id): motivo}."""
    mapa: dict[tuple[str, str], str] = {}
    if not excluidos:
        return mapa
    for item in excluidos:
        if isinstance(item, tuple) and len(item) >= 2:
            pid, oid = str(item[0]), str(item[1])
            motivo = str(item[2]) if len(item) >= 3 and item[2] else "excluido por el orientador o la empresa"
            mapa[(pid, oid)] = motivo
        elif isinstance(item, dict):
            pid = str(item.get("perfil_id", ""))
            oid = str(item.get("oferta_id", ""))
            origen_exc = str(item.get("origen", "orientador"))
            motivo = str(item.get("motivo") or f"excluido por {origen_exc}")
            if pid and oid:
                mapa[(pid, oid)] = motivo
        else:
            pid = str(getattr(item, "perfil_id", ""))
            oid = str(getattr(item, "oferta_id", ""))
            motivo = str(getattr(item, "motivo", "") or "excluido por el orientador o la empresa")
            if pid and oid:
                mapa[(pid, oid)] = motivo
    return mapa


def _normalizar_parejas_fijas(parejas_fijas: Any) -> set[tuple[str, str]]:
    """Convierte una colección de parejas fijas en set[(perfil_id, oferta_id)]."""
    conjunto: set[tuple[str, str]] = set()
    if not parejas_fijas:
        return conjunto
    for item in parejas_fijas:
        if isinstance(item, tuple) and len(item) >= 2:
            conjunto.add((str(item[0]), str(item[1])))
        elif isinstance(item, dict):
            pid = str(item.get("perfil_id", ""))
            oid = str(item.get("oferta_id", ""))
            if pid and oid:
                conjunto.add((pid, oid))
        else:
            pid = str(getattr(item, "perfil_id", ""))
            oid = str(getattr(item, "oferta_id", ""))
            if pid and oid:
                conjunto.add((pid, oid))
    return conjunto


def _cupo_vacante(oferta: Any, cfg: ConfigSolver) -> int:
    """Calcula el cupo de lista corta de una oferta respetando `Oferta.plazas` (#42, #48)."""
    cupo_explicito = _get_field(oferta, "cupo_lista_corta", "max_candidatos", default=None)
    if cupo_explicito is not None:
        return max(1, int(cupo_explicito))
    plazas = max(1, int(_get_field(oferta, "plazas", default=1) or 1))
    return cfg.max_candidatos_por_vacante * plazas


def _formatear_explicacion_match(
    ev: dict[str, Any],
    descartes_map: dict[str, str],
) -> str:
    """Plantilla determinística sobre el desglose del match, sin llamadas al LLM (#48)."""
    resumen_descartes = (
        " | Descartes: "
        + "; ".join(f"{oid} ({mot})" for oid, mot in descartes_map.items())
        if descartes_map
        else ""
    )
    req_conf = ev.get("requiere_confirmacion") or []
    nota_confirmacion = (
        f", requiere confirmación ({', '.join(req_conf)}, -{ev['penalizacion_confirmacion']} pts)"
        if req_conf
        else ""
    )
    return (
        f"Asignado a {ev['oferta_id']} ({ev['destino']}): "
        f"cobertura {ev['cobertura_entera']}% ({len(ev['cubiertas'])}/{len(ev['cubiertas']) + len(ev['brechas'])} habs), "
        f"traslado {ev['minutos_traslado']} min, "
        f"pasaje ${ev['pasaje_diario']:.2f}/día (${ev['pasaje_mensual']:.2f}/mes)"
        f"{nota_confirmacion}, "
        f"puntaje={ev['puntaje']}.{resumen_descartes}"
    )


def asignar(
    perfiles: Sequence[Any],
    ofertas: Sequence[Any],
    traslados: Any = None,
    config: ConfigSolver | dict[str, Any] | None = None,
    *,
    excluidos: Any = None,
    parejas_excluidas: Any = None,
    parejas_fijas: Any = None,
) -> list[Any]:
    """Asigna jóvenes a vacantes con OR-Tools CP-SAT respetando todas las restricciones duras.

    - Nunca realiza llamadas al LLM.
    - Determinístico: num_workers=1 y semilla fija.
    - Respeta `max_vacantes_por_perfil` (2 por defecto) y `Oferta.plazas` (#42, #48).
    - Los minutos de traslado filtran según el máximo del joven y desempatan entre vacantes
      del mismo joven sin restar puntaje entre jóvenes distintos (#48).
    - Las parejas en `excluidos` no se asignan; las parejas en `parejas_fijas` quedan fijas.
    - Si un perfil no tiene vacante viable, devuelve un Match con `oferta_id=SIN_OFERTA` y el motivo.
    """
    cfg = _normalizar_config(config)
    indice_traslados = indexar_traslados(traslados)

    if not perfiles:
        return []

    mapa_excluidos = _normalizar_mapa_excluidos(excluidos or parejas_excluidas)
    set_fijas = _normalizar_parejas_fijas(parejas_fijas)

    evaluaciones: dict[tuple[int, int], dict[str, Any]] = {}
    descartes_por_perfil: dict[str, dict[str, str]] = {}

    for i, perfil in enumerate(perfiles):
        pid = str(_get_field(perfil, "id", "perfil_id", default=f"P{i+1:02d}"))
        descartes_por_perfil[pid] = {}
        for j, oferta in enumerate(ofertas):
            oid = str(_get_field(oferta, "id", "oferta_id", default=f"O{j+1:02d}"))
            ev = evaluar_factibilidad_par(perfil, oferta, indice_traslados, cfg)
            if (pid, oid) in mapa_excluidos:
                motivo_exc = mapa_excluidos[(pid, oid)]
                ev["viable"] = False
                ev["motivos"] = [*ev["motivos"], motivo_exc]
                ev["motivo_str"] = "; ".join(ev["motivos"])
            evaluaciones[(i, j)] = ev
            if not ev["viable"] and ev["motivo_str"]:
                descartes_por_perfil[pid][oid] = ev["motivo_str"]

    model = cp_model.CpModel()
    x_vars: dict[tuple[int, int], cp_model.IntVar] = {}

    for i, perfil in enumerate(perfiles):
        pid = str(_get_field(perfil, "id", "perfil_id", default=f"P{i+1:02d}"))
        for j, oferta in enumerate(ofertas):
            oid = str(_get_field(oferta, "id", "oferta_id", default=f"O{j+1:02d}"))
            ev = evaluaciones[(i, j)]
            var = model.NewBoolVar(f"x_{i}_{j}")
            x_vars[(i, j)] = var
            if (pid, oid) in set_fijas and (pid, oid) not in mapa_excluidos:
                model.Add(var == 1)
                ev["viable"] = True
            elif not ev["viable"]:
                model.Add(var == 0)

    # Restricción dura de capacidad por vacante: máximo k * plazas candidatos por vacante
    for j, oferta in enumerate(ofertas):
        cupo_j = _cupo_vacante(oferta, cfg)
        model.Add(
            sum(x_vars[(i, j)] for i in range(len(perfiles))) <= cupo_j
        )

    # Restricción de máximo de vacantes por perfil (por defecto 2, #48)
    if cfg.max_vacantes_por_perfil is not None:
        for i in range(len(perfiles)):
            fijas_i = sum(
                1
                for j, oferta in enumerate(ofertas)
                if (
                    str(_get_field(perfiles[i], "id", "perfil_id", default=f"P{i+1:02d}")),
                    str(_get_field(oferta, "id", "oferta_id", default=f"O{j+1:02d}")),
                )
                in set_fijas
            )
            tope_i = max(cfg.max_vacantes_por_perfil, fijas_i)
            model.Add(
                sum(x_vars[(i, j)] for j in range(len(ofertas))) <= tope_i
            )

    # Desempate relativo por traslado dentro de cada joven:
    # Para cada joven i, su vacante viable más cercana tiene delta_traslado = 0.
    # Así, dos jóvenes de distintos corregimientos (ej. La Chorrera vs Bella Vista)
    # compiten en igualdad de condiciones por sus habilidades, pero para un mismo joven
    # ante vacantes de igual puntaje se prefiere la más cercana (#48).
    min_traslado_por_joven: dict[int, int] = {}
    for i in range(len(perfiles)):
        viables_i = [
            evaluaciones[(i, j)]["minutos_traslado"]
            for j in range(len(ofertas))
            if evaluaciones[(i, j)]["viable"]
        ]
        min_traslado_por_joven[i] = min(viables_i) if viables_i else 0

    bono_factibilidad = 100_000
    escala_puntaje = 100
    terminos_objetivo = []
    for (i, j), var in x_vars.items():
        ev = evaluaciones[(i, j)]
        if ev["viable"]:
            delta_traslado = max(
                0, min(99, ev["minutos_traslado"] - min_traslado_por_joven.get(i, 0))
            )
            coef_entero = int(
                bono_factibilidad + ev["puntaje"] * escala_puntaje - delta_traslado
            )
            terminos_objetivo.append(coef_entero * var)

    if terminos_objetivo:
        model.Maximize(sum(terminos_objetivo))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = cfg.semilla
    solver.parameters.max_time_in_seconds = cfg.tiempo_limite_segundos

    status = solver.Solve(model)

    resultados: list[Any] = []
    perfiles_con_asignacion: set[int] = set()

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for i, perfil in enumerate(perfiles):
            pid = str(_get_field(perfil, "id", "perfil_id", default=f"P{i+1:02d}"))
            matches_perfil: list[tuple[int, dict[str, Any]]] = []
            for j in range(len(ofertas)):
                if solver.Value(x_vars[(i, j)]) == 1:
                    matches_perfil.append((j, evaluaciones[(i, j)]))

            matches_perfil.sort(
                key=lambda item: (
                    -item[1]["puntaje"],
                    item[1]["minutos_traslado"],
                    item[1]["oferta_id"],
                )
            )

            for j_idx, ev in matches_perfil:
                perfiles_con_asignacion.add(i)
                descartes_map = descartes_por_perfil.get(pid, {})
                explicacion = _formatear_explicacion_match(ev, descartes_map)
                plazas_oferta = max(
                    1, int(_get_field(ofertas[j_idx], "plazas", default=1) or 1)
                )
                desglose = {
                    "cobertura": ev["cobertura"],
                    "minutos_traslado": ev["minutos_traslado"],
                    "pasaje_diario": ev["pasaje_diario"],
                    "pasaje_mensual": ev["pasaje_mensual"],
                    "penalizacion": ev["penalizacion"],
                    "requiere_confirmacion": list(ev["requiere_confirmacion"]),
                    "fuente_traslado": ev["fuente_traslado"],
                    "plazas": plazas_oferta,
                    "viable": True,
                    "motivo_descarte": None,
                    "ofertas_descartadas": descartes_map,
                }
                resultados.append(
                    _construir_match(
                        perfil_id=ev["perfil_id"],
                        oferta_id=ev["oferta_id"],
                        puntaje=ev["puntaje"],
                        desglose=desglose,
                        brechas=ev["brechas"],
                        explicacion=explicacion,
                    )
                )

    if cfg.incluir_perfiles_sin_vacante:
        for i, perfil in enumerate(perfiles):
            if i in perfiles_con_asignacion:
                continue
            pid = str(_get_field(perfil, "id", "perfil_id", default=f"P{i+1:02d}"))
            descartes_map = descartes_por_perfil.get(pid, {})

            if not ofertas:
                resultados.append(
                    _construir_match(
                        perfil_id=pid,
                        oferta_id=SIN_OFERTA_ID,
                        puntaje=0,
                        desglose={
                            "cobertura": 0.0,
                            "minutos_traslado": 0,
                            "pasaje_diario": 0.0,
                            "pasaje_mensual": 0.0,
                            "penalizacion": 0,
                            "requiere_confirmacion": [],
                            "viable": False,
                            "motivo_descarte": "no hay ofertas disponibles",
                            "ofertas_descartadas": {},
                        },
                        brechas=[],
                        explicacion="Sin vacante viable: no hay ofertas disponibles.",
                    )
                )
                continue

            ev_ref = evaluaciones[(i, 0)]
            if descartes_map:
                detalle_motivos = "; ".join(
                    f"{oid}: {mot}" for oid, mot in descartes_map.items()
                )
                motivo_principal = f"Sin vacante viable ({detalle_motivos})"
            else:
                motivo_principal = (
                    f"Sin vacante asignada por límite de cupo k={cfg.max_candidatos_por_vacante}"
                )

            desglose_no_viable = {
                "cobertura": ev_ref["cobertura"],
                "minutos_traslado": ev_ref["minutos_traslado"],
                "pasaje_diario": ev_ref["pasaje_diario"],
                "pasaje_mensual": ev_ref["pasaje_mensual"],
                "penalizacion": ev_ref["penalizacion"],
                "requiere_confirmacion": [],
                "viable": False,
                "motivo_descarte": motivo_principal,
                "ofertas_descartadas": descartes_map,
            }
            resultados.append(
                _construir_match(
                    perfil_id=pid,
                    oferta_id=SIN_OFERTA_ID,
                    puntaje=0,
                    desglose=desglose_no_viable,
                    brechas=ev_ref["brechas"],
                    explicacion=motivo_principal,
                )
            )

    return resultados


def asignar_orden_simple(
    perfiles: Sequence[Any],
    ofertas: Sequence[Any],
    traslados: Any = None,
    config: ConfigSolver | dict[str, Any] | None = None,
) -> list[Any]:
    """Línea base voraz (ordenar por vacante y tomar los mejores respetando topes) para comparar contra CP-SAT (#48)."""
    cfg = _normalizar_config(config)
    indice_traslados = indexar_traslados(traslados)
    if not perfiles or not ofertas:
        return []

    conteo_por_perfil: dict[int, int] = {i: 0 for i in range(len(perfiles))}
    seleccionados: list[tuple[int, int, dict[str, Any]]] = []

    for j, oferta in enumerate(ofertas):
        cupo_j = _cupo_vacante(oferta, cfg)
        candidatos_j: list[tuple[int, dict[str, Any]]] = []
        for i, perfil in enumerate(perfiles):
            if (
                cfg.max_vacantes_por_perfil is not None
                and conteo_por_perfil[i] >= cfg.max_vacantes_por_perfil
            ):
                continue
            ev = evaluar_factibilidad_par(perfil, oferta, indice_traslados, cfg)
            if ev["viable"]:
                candidatos_j.append((i, ev))

        candidatos_j.sort(
            key=lambda item: (
                -item[1]["puntaje"],
                item[1]["minutos_traslado"],
                item[1]["perfil_id"],
            )
        )
        for i_elegido, ev_elegido in candidatos_j[:cupo_j]:
            conteo_por_perfil[i_elegido] += 1
            seleccionados.append((i_elegido, j, ev_elegido))

    resultados: list[Any] = []
    for _, _, ev in seleccionados:
        resultados.append(
            _construir_match(
                perfil_id=ev["perfil_id"],
                oferta_id=ev["oferta_id"],
                puntaje=ev["puntaje"],
                desglose={
                    "cobertura": ev["cobertura"],
                    "minutos_traslado": ev["minutos_traslado"],
                    "pasaje_diario": ev["pasaje_diario"],
                    "pasaje_mensual": ev["pasaje_mensual"],
                    "penalizacion": ev["penalizacion"],
                    "requiere_confirmacion": list(ev["requiere_confirmacion"]),
                    "viable": True,
                    "motivo_descarte": None,
                },
                brechas=ev["brechas"],
                explicacion=_formatear_explicacion_match(ev, {}),
            )
        )
    return resultados
