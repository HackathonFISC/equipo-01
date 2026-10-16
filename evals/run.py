"""Runner de evals: uv run python -m evals.run. Issue #13.

Carga evals/casos.yaml, ejecuta cada caso con el ejecutor de su categoría, compara lo observado con lo
esperado (escrito antes de correr), imprime una tabla por categoría con su métrica y su meta, y la guarda en
evals/resultados.md junto con el registro de fallos.

- Los casos de categorías con LLM (extracción de ofertas y perfil sin CV) se corren 3 veces y se reporta la
  tasa de acierto. Sin GOOGLE_API_KEY esos casos quedan "omitido".
- Si el módulo o la función que ejecuta un caso todavía no está fusionado (a la fecha #4 y #8 ya están en
  main; faltan #5, #6 y #9), el caso queda "pendiente (#N sin fusionar)" y no rompe el runner.
- Este módulo no llama al LLM: solo lo hacen los agentes de #5 y #6 a través de sus ejecutores. Nunca
  reescribe casos.yaml: los fallos se anotan a mano en el campo "fallos" de cada caso.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import importlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

RUTA_CASOS = Path(__file__).with_name("casos.yaml")
RUTA_RESULTADOS = Path(__file__).with_name("resultados.md")

REPOSITORIO = "HackathonFISC/equipo-01"
ETIQUETA_FALLO = "bug"
HUSO_PANAMA = timezone(timedelta(hours=-5), "America/Panama")  # Panamá no usa horario de verano

# Dónde vive cada función que ejecuta un caso: (módulo, función, issue). Los nombres de #5, #6 y #9 todavía
# no están definidos en sus issues; al fusionarse, se ajustan aquí y en ningún otro lugar.
MERCADO_EXTRAER = ("puente.agents.mercado", "extraer_oferta", 5)
DISCRIMINACION_EVALUAR = ("puente.rules.discriminacion", "evaluar_requisitos", 5)
PERFIL_EXTRAER = ("puente.agents.perfil", "extraer_perfil", 6)
GUARDRAILS_VALIDAR = ("puente.guardrails", "validar_entrada", 9)
SOLVER_ASIGNAR = ("puente.solver.asignacion", "asignar", 4)
AUDITOR_AUDITAR = ("puente.agents.auditor", "auditar_lista_corta", 8)

ESTADOS = ("ok", "fallo", "pendiente", "omitido")
CAMPOS_EXTRACCION = ("puesto", "empresa", "corregimiento", "salario_mensual", "habilidades")
MINUTOS_MAXIMOS_TRASLADO = 60
PORCENTAJE_MAXIMO_PASAJE = 0.15
DIAS_LABORABLES_MES = 22
VACANTES_K = 3

Ejecutor = Callable[[dict], dict]


class DependenciaPendiente(Exception):
    """El módulo o la función de otro issue todavía no está fusionado."""

    def __init__(self, issue: int, mensaje: str = "") -> None:
        self.issue = issue
        super().__init__(mensaje or f"#{issue} sin fusionar")


@dataclass
class ResultadoCaso:
    id: str
    categoria: str
    estado: str  # 'ok' | 'fallo' | 'pendiente' | 'omitido'
    aciertos: int
    corridas: int
    observado: dict | None
    detalle: str


# --------------------------------------------------------------------------------- carga y validación
def cargar_casos(ruta: Path = RUTA_CASOS) -> dict:
    """Lee y valida casos.yaml. Lanza ValueError (en español) si falta algo."""
    ruta = Path(ruta)
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"No se pudo leer {ruta}: {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"{ruta} no es un YAML válido: {error}") from error
    if not isinstance(datos, dict):
        raise ValueError(f"{ruta} debe ser un mapa con 'categorias' y 'casos'")

    categorias = datos.get("categorias")
    if not isinstance(categorias, dict) or not categorias:
        raise ValueError("casos.yaml: falta 'categorias' con al menos una categoría")
    for nombre, info in categorias.items():
        if not isinstance(info, dict):
            raise ValueError(f"categoría {nombre}: debe ser un mapa con metrica y meta")
        if not str(info.get("metrica") or "").strip():
            raise ValueError(f"categoría {nombre}: falta la métrica")
        meta = info.get("meta")
        if isinstance(meta, bool) or not isinstance(meta, (int, float)) or not 0 < meta <= 1:
            raise ValueError(f"categoría {nombre}: la meta debe ser un número entre 0 y 1 (por ejemplo 0.8)")
        if not isinstance(info.get("usa_llm"), bool):
            raise ValueError(f"categoría {nombre}: 'usa_llm' debe ser true o false")

    casos = datos.get("casos")
    if not isinstance(casos, list) or not casos:
        raise ValueError("casos.yaml: falta 'casos' con al menos un caso")
    vistos: set[str] = set()
    for caso in casos:
        if not isinstance(caso, dict):
            raise ValueError("casos.yaml: cada caso debe ser un mapa")
        caso_id = str(caso.get("id") or "").strip()
        if not caso_id:
            raise ValueError("casos.yaml: hay un caso sin id")
        if caso_id in vistos:
            raise ValueError(f"casos.yaml: el id {caso_id} está repetido")
        vistos.add(caso_id)
        if caso.get("categoria") not in categorias:
            raise ValueError(f"caso {caso_id}: categoría desconocida ({caso.get('categoria')!r})")
        if not isinstance(caso.get("esperado"), dict) or not caso["esperado"]:
            raise ValueError(f"caso {caso_id}: el resultado esperado no puede estar vacío")
        if not isinstance(caso.get("entrada"), dict) or not caso["entrada"]:
            raise ValueError(f"caso {caso_id}: falta la entrada")
        if not str(caso.get("escrito_el") or "").strip():
            raise ValueError(f"caso {caso_id}: falta 'escrito_el' (cuándo se escribió el esperado)")
        fallos = caso.setdefault("fallos", [])
        if not isinstance(fallos, list):
            raise ValueError(f"caso {caso_id}: 'fallos' debe ser una lista")
    return datos


# --------------------------------------------------------------------------------- utilidades
def _a_dict(objeto: Any) -> dict:
    if isinstance(objeto, dict):
        return objeto
    if hasattr(objeto, "model_dump"):
        return objeto.model_dump()
    if hasattr(objeto, "__dict__"):
        return dict(vars(objeto))
    return {}


def _sin_tildes(texto: Any) -> str:
    descompuesto = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _norm(texto: Any) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    return " ".join(_sin_tildes(texto).casefold().split())


def _norm_literal(texto: Any) -> str:
    """Minúsculas y espacios colapsados, conservando las tildes (para evidencia literal)."""
    return " ".join(str(texto).casefold().split())


def _planos(valor: Any) -> list[Any]:
    if valor is None:
        return []
    if isinstance(valor, (list, tuple, set)):
        return list(valor)
    return [valor]


def _resolver(destino: tuple[str, str, int]) -> Callable[..., Any]:
    """Importa la función del destino.

    Solo cuenta como pendiente que el módulo destino (o un paquete que lo contiene) no exista, o que la
    función no esté en un módulo que sí se importó (ajustar la constante). Cualquier otro ImportError o
    AttributeError, como un import transitivo roto dentro de un módulo ya fusionado, se propaga para que
    el caso cuente como 'fallo' y no se oculte.
    """
    modulo, funcion, issue = destino
    try:
        importado = importlib.import_module(modulo)
    except ModuleNotFoundError as error:
        nombre = error.name or ""
        if nombre and (modulo == nombre or modulo.startswith(nombre + ".")):
            raise DependenciaPendiente(issue, f"#{issue} sin fusionar ({modulo}: {error})") from error
        raise
    try:
        return getattr(importado, funcion)
    except AttributeError as error:
        raise DependenciaPendiente(
            issue, f"#{issue}: función {funcion} no existe en {modulo} (ajustar constante)"
        ) from error


def _ids_habilidades(valor: Any) -> list[str]:
    ids = []
    for item in _planos(valor):
        if isinstance(item, str):
            ids.append(item.strip())
        else:
            d = _a_dict(item)
            ids.append(str(d.get("skill_id") or d.get("id") or "").strip())
    return [i for i in ids if i]


_RE_ARTICULOS = re.compile(r"\bart[^\d]{0,12}((?:\d+\s*(?:,|y|e)\s*)*\d+)")


def _regla_normalizada(texto: Any) -> list[str]:
    """Artículos citados, incluidas las enumeraciones ('arts. 1 y 2', 'artículos 1, 2 y 3')."""
    numeros = {n for grupo in _RE_ARTICULOS.findall(_norm(texto)) for n in re.findall(r"\d+", grupo)}
    return sorted(f"art. {n}" for n in numeros)


def _alertas_normalizadas(items: Any) -> list[str]:
    """Tokens de alerta. Las que citan un artículo van a reglas y se descartan aquí; cualquier otra alerta
    no reconocida se conserva como 'otra:<texto>' para que una alerta espuria produzca diferencia."""
    tokens: set[str] = set()
    for item in _planos(items):
        crudo = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str)
        texto = _norm(crudo).replace("_", " ")
        conocidas = set()
        if "presencia" in texto:
            conocidas.add("presencia")
        if "foto" in texto:
            conocidas.add("foto")
        if "civil" in texto:
            conocidas.add("estado_civil")
        if conocidas:
            tokens |= conocidas
        elif not _regla_normalizada(texto) and texto:
            tokens.add(f"otra:{texto}")
    return sorted(tokens)


# --------------------------------------------------------------------------------- comparadores
def _diferencias_extraccion(esperado: dict, observado: dict) -> list[str]:
    malos = []
    for campo in ("puesto", "empresa"):
        if campo in esperado:
            e, o = _norm(esperado[campo]), _norm(observado.get(campo) or "")
            # Lo esperado debe estar completo en lo observado: una extracción truncada no es un acierto.
            if not e or not o or e not in o:
                malos.append(campo)
    if "corregimiento" in esperado and _norm(esperado["corregimiento"]) != _norm(observado.get("corregimiento") or ""):
        malos.append("corregimiento")
    if "salario_mensual" in esperado:
        try:
            if abs(float(observado.get("salario_mensual")) - float(esperado["salario_mensual"])) > 0.01:
                malos.append("salario_mensual")
        except (TypeError, ValueError):
            malos.append("salario_mensual")
    if "habilidades" in esperado and sorted(set(_ids_habilidades(esperado["habilidades"]))) != sorted(
        set(_ids_habilidades(observado.get("habilidades")))
    ):
        malos.append("habilidades")
    return malos


def _diferencias_discriminacion(esperado: dict, observado: dict) -> list[str]:
    malos = []
    if bool(observado.get("bloqueada")) != bool(esperado.get("bloqueada")):
        malos.append("bloqueada")
    reglas_esperadas = sorted({r for item in _planos(esperado.get("reglas")) for r in _regla_normalizada(item)})
    reglas_observadas = sorted({r for item in _planos(observado.get("reglas")) for r in _regla_normalizada(item)})
    if reglas_esperadas != reglas_observadas:
        malos.append("reglas")
    if _alertas_normalizadas(esperado.get("alertas")) != _alertas_normalizadas(observado.get("alertas")):
        malos.append("alertas")
    return malos


def _habilidades_con_evidencia(valor: Any) -> list[tuple[str, str]]:
    pares = []
    for item in _planos(valor):
        if isinstance(item, str):
            pares.append((item.strip(), ""))
        else:
            d = _a_dict(item)
            pares.append((str(d.get("skill_id") or d.get("id") or "").strip(), str(d.get("evidencia") or "").strip()))
    return pares


def _diferencias_perfil(esperado: dict, observado: dict) -> list[str]:
    malos = []
    pares = _habilidades_con_evidencia(observado.get("habilidades"))
    encontradas = {skill for skill, _ in pares}
    for skill in _ids_habilidades(esperado.get("habilidades")):
        if skill not in encontradas:
            malos.append(f"falta {skill}")
    texto = observado.get("texto_fuente")
    for skill, evidencia in pares:
        if not evidencia:
            malos.append(f"{skill} sin evidencia")
        elif texto is not None and _norm_literal(evidencia) not in _norm_literal(texto):
            malos.append(f"{skill}: la evidencia no es literal")
    for skill in _ids_habilidades(esperado.get("no_incluir")):
        if skill in encontradas:
            malos.append(f"sobra {skill}")
    for dato in _planos(esperado.get("sin_pii")):
        if any(_norm_literal(dato) in _norm_literal(evidencia) for _, evidencia in pares):
            malos.append("evidencia con datos personales")
    return malos


def _asignacion_normalizada(valor: Any) -> dict[str, str | None]:
    return {str(k): (None if v in (None, "", "SIN_OFERTA") else str(v)) for k, v in (valor or {}).items()}


def _diferencias_matching(esperado: dict, observado: dict) -> list[str]:
    malos = []
    if _asignacion_normalizada(observado.get("asignacion")) != _asignacion_normalizada(esperado.get("asignacion")):
        malos.append("asignacion")
    if int(observado.get("violaciones_duras", 0) or 0) != int(esperado.get("violaciones_duras", 0) or 0):
        malos.append("violaciones_duras")
    return malos


def _diferencias_sesgo(esperado: dict, observado: dict) -> list[str]:
    if bool(observado.get("sesgo_detectado")) != bool(esperado.get("sesgo_detectado")):
        return ["sesgo_detectado"]
    return []


def _diferencias_guardrails(esperado: dict, observado: dict) -> list[str]:
    malos = []
    if _norm(observado.get("veredicto") or "") != _norm(esperado.get("veredicto") or ""):
        malos.append("veredicto")
    if esperado.get("llamo_llm") is False and observado.get("llamo_llm"):
        malos.append("llamó al LLM")
    if esperado.get("sigue_como_dato") and not observado.get("sigue_como_dato"):
        malos.append("no sigue como dato")
    return malos


_DIFERENCIAS: dict[str, Callable[[dict, dict], list[str]]] = {
    "extraccion_ofertas": _diferencias_extraccion,
    "requisitos_discriminatorios": _diferencias_discriminacion,
    "perfil_sin_cv": _diferencias_perfil,
    "matching_factible": _diferencias_matching,
    "sesgo_contrafactual": _diferencias_sesgo,
    "guardrails": _diferencias_guardrails,
}


def _diferencias(categoria: str, esperado: dict, observado: dict) -> list[str]:
    if categoria not in _DIFERENCIAS:
        raise ValueError(f"No hay comparador para la categoría {categoria!r}")
    return _DIFERENCIAS[categoria](esperado or {}, observado or {})


def comparar(categoria: str, esperado: dict, observado: dict) -> bool:
    """Compara lo observado con lo esperado con reglas determinísticas propias de la categoría."""
    return not _diferencias(categoria, esperado, observado)


# --------------------------------------------------------------------------------- ejecutores por defecto
@contextlib.contextmanager
def _espiar_llm() -> Iterator[dict[str, int]]:
    """Cuenta las llamadas a cualquier modelo de chat de LangChain sin alterar su comportamiento."""
    contador = {"llamadas": 0}
    try:
        from langchain_core.language_models.chat_models import BaseChatModel
    except ImportError:
        yield contador
        return

    originales = {nombre: getattr(BaseChatModel, nombre) for nombre in ("invoke", "ainvoke")}

    def envolver(funcion: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(funcion)
        def espia(self: Any, *args: Any, **kwargs: Any) -> Any:
            contador["llamadas"] += 1
            return funcion(self, *args, **kwargs)

        return espia

    for nombre, funcion in originales.items():
        setattr(BaseChatModel, nombre, envolver(funcion))
    try:
        yield contador
    finally:
        for nombre, funcion in originales.items():
            setattr(BaseChatModel, nombre, funcion)


def _clave_traslado(origen: Any, destino: Any) -> tuple[str, str]:
    return _norm(origen), _norm(destino)


def _indice_traslados(traslados: list[dict] | None) -> dict[tuple[str, str], dict]:
    indice: dict[tuple[str, str], dict] = {}
    for t in traslados or []:
        indice[_clave_traslado(t["origen"], t["destino"])] = t
        indice.setdefault(_clave_traslado(t["destino"], t["origen"]), t)
    return indice


def _a_minutos(hora: str) -> int:
    horas, minutos = str(hora).split(":")
    return int(horas) * 60 + int(minutos)


def _se_cruzan(a: dict, b: dict) -> bool:
    if not set(a["dias"]) & set(b["dias"]):
        return False
    return max(_a_minutos(a["hora_inicio"]), _a_minutos(b["hora_inicio"])) < min(
        _a_minutos(a["hora_fin"]), _a_minutos(b["hora_fin"])
    )


def _cabe_en(horario: dict, libre: dict) -> bool:
    return (
        set(horario["dias"]) <= set(libre["dias"])
        and _a_minutos(libre["hora_inicio"]) <= _a_minutos(horario["hora_inicio"])
        and _a_minutos(horario["hora_fin"]) <= _a_minutos(libre["hora_fin"])
    )


def _violaciones_duras(entrada: dict, asignacion: dict[str, str | list[str] | None]) -> list[str]:
    """Restricciones duras comprobadas en código sobre los datos del caso, sin confiar en el solver.

    `asignacion` mapea cada perfil a una oferta, a None o a la lista de TODAS las ofertas que el solver le dio.
    """
    perfiles = {p["id"]: p for p in entrada["perfiles"]}
    ofertas = {o["id"]: o for o in entrada["ofertas"]}
    traslados = _indice_traslados(entrada.get("traslados"))
    config = entrada.get("config") or {}
    k = int(config.get("k", VACANTES_K))
    violaciones: list[str] = []
    por_vacante: dict[str, int] = {}
    max_por_perfil = config.get("max_vacantes_por_perfil")
    pares: list[tuple[str, str]] = []
    for perfil_id, destino in asignacion.items():
        asignadas = [] if destino is None else [destino] if isinstance(destino, str) else list(destino)
        pares.extend((perfil_id, str(oferta_id)) for oferta_id in asignadas)
        if max_por_perfil is not None and len(asignadas) > int(max_por_perfil):
            violaciones.append(f"{perfil_id}: {len(asignadas)} vacantes asignadas (máximo {int(max_por_perfil)})")
    for perfil_id, oferta_id in pares:
        p, o = perfiles.get(perfil_id), ofertas.get(oferta_id)
        if p is None or o is None:
            violaciones.append(f"{perfil_id}->{oferta_id}: perfil u oferta inexistente")
            continue
        por_vacante[oferta_id] = por_vacante.get(oferta_id, 0) + 1
        t = traslados.get(_clave_traslado(p["corregimiento"], o["corregimiento"]))
        if t is None:
            violaciones.append(f"{perfil_id}->{oferta_id}: sin traslado registrado")
        else:
            diario = round(2 * float(t["pasaje_ida"]), 2)
            mensual = round(diario * DIAS_LABORABLES_MES, 2)
            if int(t["minutos"]) > int(config.get("max_minutos_traslado", MINUTOS_MAXIMOS_TRASLADO)):
                violaciones.append(f"{perfil_id}->{oferta_id}: traslado de {t['minutos']} min")
            if round(mensual * 100) > round(float(o["salario_mensual"]) * PORCENTAJE_MAXIMO_PASAJE * 100):
                violaciones.append(f"{perfil_id}->{oferta_id}: pasaje mensual {mensual} supera el 15 % del salario")
            if round(diario * 100) > round(float(p["presupuesto_pasaje_diario"]) * 100):
                violaciones.append(f"{perfil_id}->{oferta_id}: pasaje diario {diario} supera el presupuesto")
        if float(o["salario_mensual"]) < float(p["salario_minimo"]):
            violaciones.append(f"{perfil_id}->{oferta_id}: salario menor al mínimo del perfil")
        horario = o["horario"]
        if any(_se_cruzan(horario, clase) for clase in p.get("horario_clases") or []):
            violaciones.append(f"{perfil_id}->{oferta_id}: el horario cruza con las clases")
        libres = p.get("horario_libre") or []
        if libres and not any(_cabe_en(horario, libre) for libre in libres):
            violaciones.append(f"{perfil_id}->{oferta_id}: el horario sale del horario libre")
    for oferta_id, cantidad in por_vacante.items():
        if cantidad > k:
            violaciones.append(f"{oferta_id}: {cantidad} candidatos asignados (k = {k})")
    return violaciones


def _matching_con_sesgo_genero(entrada: dict) -> Callable[[list, list], list[dict]]:
    """Doble de matching con sesgo inyectado a propósito: resta 40 puntos si el género es femenino."""
    traslados = _indice_traslados(entrada.get("traslados"))

    def matching(perfiles: list, ofertas: list) -> list[dict]:
        resultado = []
        for p in perfiles:
            habilidades = set(_ids_habilidades(_a_dict(p).get("habilidades")))
            for o in ofertas:
                requeridas = _ids_habilidades(_a_dict(o).get("habilidades"))
                cobertura = len([s for s in requeridas if s in habilidades]) / len(requeridas) if requeridas else 1.0
                t = traslados.get(_clave_traslado(p["corregimiento"], o["corregimiento"]))
                minutos = int(t["minutos"]) if t else 35
                penalizacion = 40 if str(p.get("genero", "")).casefold() == "femenino" else 0
                resultado.append(
                    {
                        "perfil_id": p["id"],
                        "oferta_id": o["id"],
                        "puntaje": round(100 * cobertura) - minutos - penalizacion,
                        "desglose": {"viable": True},
                    }
                )
        return resultado

    return matching


def _ejecutar_extraccion(caso: dict) -> dict:
    oferta = _a_dict(_resolver(MERCADO_EXTRAER)(caso["entrada"]["texto"]))
    return {campo: oferta.get(campo) for campo in CAMPOS_EXTRACCION}


def _ejecutar_discriminacion(caso: dict) -> dict:
    resultado = _a_dict(_resolver(DISCRIMINACION_EVALUAR)(caso["entrada"]["texto"]))
    alertas = _planos(resultado.get("alertas"))
    reglas = _planos(resultado.get("reglas")) + _planos(resultado.get("regla")) + _planos(resultado.get("articulos"))
    if not reglas:
        reglas = [a for a in alertas if _regla_normalizada(a)]
    return {
        "bloqueada": bool(resultado.get("bloqueada")),
        "reglas": sorted({r for item in reglas for r in _regla_normalizada(item)}),
        "alertas": _alertas_normalizadas(alertas),
    }


def _ejecutar_perfil(caso: dict) -> dict:
    perfil = _a_dict(_resolver(PERFIL_EXTRAER)(caso["entrada"]["texto"]))
    pares = _habilidades_con_evidencia(perfil.get("habilidades"))
    return {"habilidades": [{"skill_id": skill, "evidencia": evidencia} for skill, evidencia in pares]}


def _ejecutar_matching(caso: dict) -> dict:
    asignar = _resolver(SOLVER_ASIGNAR)
    entrada = caso["entrada"]
    matches = asignar(entrada["perfiles"], entrada["ofertas"], entrada.get("traslados"), entrada.get("config") or None)
    asignacion: dict[str, str | None] = {p["id"]: None for p in entrada["perfiles"]}
    todas: dict[str, list[str]] = {}
    mejores: dict[str, float] = {}
    for match in matches:
        d = _a_dict(match)
        perfil_id, oferta_id = str(d.get("perfil_id")), d.get("oferta_id")
        desglose = _a_dict(d.get("desglose"))
        if perfil_id not in asignacion or oferta_id in (None, "", "SIN_OFERTA") or desglose.get("viable") is False:
            continue  # el solver también devuelve una fila (viable: false) por cada perfil sin vacante
        todas.setdefault(perfil_id, []).append(str(oferta_id))
        puntaje = float(d.get("puntaje") or 0)
        if perfil_id not in mejores or puntaje > mejores[perfil_id]:
            mejores[perfil_id], asignacion[perfil_id] = puntaje, str(oferta_id)
    # Las violaciones se miden sobre TODAS las filas asignadas, no solo sobre la de mayor puntaje.
    violaciones = _violaciones_duras(entrada, {**asignacion, **todas})
    return {"asignacion": asignacion, "violaciones_duras": len(violaciones), "violaciones": violaciones}


def _ejecutar_sesgo(caso: dict) -> dict:
    auditar = _resolver(AUDITOR_AUDITAR)
    entrada = caso["entrada"]
    argumentos: dict[str, Any] = {"traslados": entrada.get("traslados")}
    if entrada.get("matching") == "sesgo_genero_inyectado":
        argumentos["fn_matching"] = _matching_con_sesgo_genero(entrada)
    reporte = auditar(entrada["perfiles"], entrada["ofertas"], **argumentos)
    sesgo = _a_dict(reporte).get("sesgo_detectado", getattr(reporte, "sesgo_detectado", False))
    return {"sesgo_detectado": bool(sesgo)}


def _ejecutar_guardrails(caso: dict) -> dict:
    validar = _resolver(GUARDRAILS_VALIDAR)
    with _espiar_llm() as espia:
        resultado = validar(caso["entrada"])
    d = _a_dict(resultado)
    veredicto = d.get("veredicto", resultado if isinstance(resultado, str) else None)
    return {
        "veredicto": getattr(veredicto, "value", veredicto),
        "sigue_como_dato": d.get("sigue_como_dato"),
        "llamo_llm": espia["llamadas"] > 0 or bool(d.get("llamo_llm")),
    }


def ejecutores_por_defecto() -> dict[str, Ejecutor]:
    """Un ejecutor por categoría. Resuelven sus dependencias al ejecutarse, no al importar este módulo."""
    return {
        "extraccion_ofertas": _ejecutar_extraccion,
        "requisitos_discriminatorios": _ejecutar_discriminacion,
        "perfil_sin_cv": _ejecutar_perfil,
        "matching_factible": _ejecutar_matching,
        "sesgo_contrafactual": _ejecutar_sesgo,
        "guardrails": _ejecutar_guardrails,
    }


# --------------------------------------------------------------------------------- correr
def _resumen(valor: Any, limite: int = 280) -> str:
    texto = json.dumps(valor, ensure_ascii=False, default=str)
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def correr(
    casos: dict,
    *,
    ejecutores: dict[str, Ejecutor] | None = None,
    repeticiones: int = 3,
    usar_llm: bool | None = None,
) -> list[ResultadoCaso]:
    """Ejecuta todos los casos. Las categorías con LLM se corren `repeticiones` veces; el resto, una."""
    if usar_llm is None:
        usar_llm = bool(os.getenv("GOOGLE_API_KEY"))
    ejecutores = ejecutores_por_defecto() if ejecutores is None else ejecutores
    categorias = casos["categorias"]
    resultados: list[ResultadoCaso] = []
    for caso in casos["casos"]:
        categoria = caso["categoria"]
        con_llm = bool(categorias[categoria]["usa_llm"])
        if con_llm and not usar_llm:
            resultados.append(ResultadoCaso(caso["id"], categoria, "omitido", 0, 0, None, "omitido: sin GOOGLE_API_KEY"))
            continue
        ejecutor = ejecutores.get(categoria)
        if ejecutor is None:
            motivo = "pendiente: sin ejecutor para la categoría"
            resultados.append(ResultadoCaso(caso["id"], categoria, "pendiente", 0, 0, None, motivo))
            continue
        corridas = max(1, repeticiones) if con_llm else 1
        aciertos, ejecutadas, ultimo, errores = 0, 0, None, []
        pendiente: DependenciaPendiente | None = None
        for _ in range(corridas):
            try:
                observado = ejecutor(caso)
            except DependenciaPendiente as error:
                pendiente = error
                break
            except Exception as error:  # cualquier otro error al ejecutar cuenta como fallo
                ejecutadas += 1
                errores.append(f"{type(error).__name__}: {error}")
                continue
            ejecutadas += 1
            ultimo = observado
            if categoria == "perfil_sin_cv" and isinstance(observado, dict):
                observado = {"texto_fuente": caso["entrada"].get("texto"), **observado}
            diferencias = _diferencias(categoria, caso["esperado"], observado)
            if diferencias:
                errores.append(f"difiere en {', '.join(diferencias)}")
            else:
                aciertos += 1
        if pendiente is not None:
            detalle = f"pendiente ({pendiente})"
            resultados.append(ResultadoCaso(caso["id"], categoria, "pendiente", 0, 0, None, detalle))
        elif aciertos == ejecutadas:
            resultados.append(ResultadoCaso(caso["id"], categoria, "ok", aciertos, ejecutadas, ultimo, "cumple lo esperado"))
        else:
            detalle = f"{aciertos}/{ejecutadas} corridas acertaron; " + " | ".join(dict.fromkeys(errores))
            detalle += f"; esperado {_resumen(caso['esperado'])}; observado {_resumen(ultimo)}"
            resultados.append(ResultadoCaso(caso["id"], categoria, "fallo", aciertos, ejecutadas, ultimo, detalle))
    return resultados


# --------------------------------------------------------------------------------- tabla y archivo
def _porcentaje(fraccion: float) -> str:
    return f"{round(fraccion * 100)} %"


def tabla(resultados: list[ResultadoCaso], casos: dict) -> str:
    """Tabla markdown por categoría: casos, ejecutados, aciertos, tasa, métrica, meta y si cumple."""
    filas = [
        "| Categoría | Casos | Ejecutados | Aciertos | Tasa | Métrica | Meta | Cumple |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for nombre, info in casos["categorias"].items():
        propios = [r for r in resultados if r.categoria == nombre]
        total = sum(1 for c in casos["casos"] if c["categoria"] == nombre)
        medidos = [r for r in propios if r.estado in ("ok", "fallo")]
        aciertos = sum(r.aciertos for r in medidos)
        corridas = sum(r.corridas for r in medidos)
        if corridas:
            tasa = aciertos / corridas
            cumple = "sí" if tasa >= float(info["meta"]) else "no"
            celdas = (f"{aciertos}/{corridas}", _porcentaje(tasa))
        else:
            cumple, celdas = "sin medir", ("—", "—")
        filas.append(
            f"| {nombre} | {total} | {len(medidos)} | {celdas[0]} | {celdas[1]} | {_celda(info['metrica'])} "
            f"| {_porcentaje(float(info['meta']))} | {cumple} |"
        )
    return "\n".join(filas)


def _conteo_estados(resultados: list[ResultadoCaso]) -> dict[str, int]:
    return {estado: sum(1 for r in resultados if r.estado == estado) for estado in ESTADOS}


def _aviso_de_cobertura(resultados: list[ResultadoCaso]) -> str:
    conteo = _conteo_estados(resultados)
    if conteo["ok"] + conteo["fallo"] == len(resultados):
        return "Todos los casos se ejecutaron en esta corrida."
    issues = sorted({m for r in resultados if r.estado == "pendiente" for m in re.findall(r"#\d+", r.detalle)})
    partes = []
    if conteo["pendiente"]:
        partes.append(
            f"{conteo['pendiente']} casos pendientes porque aún no están fusionadas sus dependencias "
            f"({', '.join(issues) or 'sin ejecutor'})"
        )
    if conteo["omitido"]:
        partes.append(
            f"{conteo['omitido']} casos con LLM omitidos por falta de GOOGLE_API_KEY "
            "(no se miden hasta correr con la clave real)"
        )
    return (
        "**Esta corrida no mide todo el sistema:** " + "; ".join(partes) + ". Una categoría sin medir no es una categoría aprobada."
    )


def _celda(valor: Any) -> str:
    return str(valor).replace("|", "\\|").replace("\n", " ")


def _registro_de_fallos(resultados: list[ResultadoCaso], casos: dict) -> list[str]:
    por_id = {c["id"]: c for c in casos["casos"]}
    lineas = ["## Registro de fallos", "", "### Fallos de esta corrida", ""]
    nuevos = [r for r in resultados if r.estado == "fallo"]
    if nuevos:
        lineas += ["| Caso | Esperado | Observado / detalle | Cambio hecho | Nueva ejecución |", "| --- | --- | --- | --- | --- |"]
        for r in nuevos:
            lineas.append(
                f"| {r.id} | `{_celda(_resumen(por_id[r.id]['esperado']))}` | {_celda(r.detalle)} "
                "| (pendiente: anotar en `fallos` de casos.yaml) | (pendiente) |"
            )
    else:
        lineas.append("Ningún caso falló en esta corrida.")
    lineas += ["", "### Historial anotado en casos.yaml", ""]
    historial = [(c["id"], f) for c in casos["casos"] for f in c.get("fallos") or []]
    if historial:
        lineas += [
            "| Caso | Fecha | Observado | Cambio hecho | Issue | Nueva ejecución |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for caso_id, f in historial:
            celdas = [_celda(f.get(k, "")) for k in ("fecha", "observado", "cambio_hecho", "issue", "nueva_ejecucion")]
            lineas.append(f"| {caso_id} | " + " | ".join(celdas) + " |")
    else:
        lineas.append(
            "Sin fallos anotados todavía. Los casos nunca se borran: cada fallo se anota aquí con su cambio y su nueva ejecución."
        )
    return lineas


def escribir_resultados(
    resultados: list[ResultadoCaso],
    casos: dict,
    ruta: Path = RUTA_RESULTADOS,
    *,
    ahora: datetime | None = None,
) -> Path:
    """Guarda la tabla, el detalle por caso y el registro de fallos en un archivo markdown."""
    ruta = Path(ruta)
    ahora = (ahora or datetime.now(HUSO_PANAMA)).astimezone(HUSO_PANAMA)
    conteo = _conteo_estados(resultados)
    lineas = [
        "# Resultados de los evals de Puente",
        "",
        f"Generado por `uv run python -m evals.run` el {ahora:%Y-%m-%d %H:%M} (hora de Panamá, UTC-5). "
        "No lo edites a mano: se regenera en cada corrida.",
        "",
        f"Estados: {conteo['ok']} ok · {conteo['fallo']} fallo · {conteo['pendiente']} pendiente · "
        f"{conteo['omitido']} omitido (de {len(resultados)} casos).",
        "",
        _aviso_de_cobertura(resultados),
        "",
        "Los casos con LLM se corren 3 veces y la tasa es aciertos sobre corridas. Las metas son propuestas "
        "provisionales hasta que el equipo las confirme con el plan. Todos los datos de los casos son sintéticos.",
        "",
        "## Tabla por categoría",
        "",
        tabla(resultados, casos),
        "",
        "## Detalle por caso",
        "",
        "| Caso | Categoría | Estado | Aciertos | Detalle |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for r in resultados:
        lineas.append(f"| {r.id} | {r.categoria} | {r.estado} | {r.aciertos}/{r.corridas} | {_celda(r.detalle)} |")
    lineas += ["", *_registro_de_fallos(resultados, casos), ""]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(lineas), encoding="utf-8", newline="\n")
    return ruta


# --------------------------------------------------------------------------------- issues de fallos
def _crear_issue_gh(titulo: str, cuerpo: str, etiquetas: list[str]) -> str:
    """Abre el issue con gh; si ya hay uno con el mismo título, devuelve su URL en vez de duplicarlo."""
    try:
        existentes = subprocess.run(
            ["gh", "issue", "list", "-R", REPOSITORIO, "--state", "all", "--search", f'"{titulo}" in:title', "--json", "url,title"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
        for issue in json.loads(existentes or "[]"):
            if issue.get("title") == titulo:
                return str(issue["url"])
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    comando = ["gh", "issue", "create", "-R", REPOSITORIO, "--title", titulo, "--body", cuerpo]
    for etiqueta in etiquetas:
        comando += ["--label", etiqueta]
    return subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def abrir_issues_de_fallos(
    resultados: list[ResultadoCaso],
    casos: dict,
    *,
    crear: Callable[[str, str, list[str]], str] | None = None,
) -> list[str]:
    """Abre un issue con la etiqueta bug por cada caso en fallo que aún no tenga issue en su historial."""
    crear = crear or _crear_issue_gh
    por_id = {c["id"]: c for c in casos["casos"]}
    creados = []
    for r in resultados:
        if r.estado != "fallo":
            continue
        caso = por_id[r.id]
        if any(f.get("issue") for f in caso.get("fallos") or []):
            continue
        issue_origen = casos["categorias"][r.categoria].get("issue", "")
        titulo = f"Fallo eval {r.id}: {caso.get('descripcion', r.categoria)}"[:120]
        cuerpo = (
            f"Al correr el caso **{r.id}** ({r.categoria}) de `evals/casos.yaml`, ocurrió: {r.detalle}\n\n"
            f"Esperaba: `{_resumen(caso['esperado'], 600)}`\n\n"
            "## Cómo reproducirlo\n1. `uv run python -m evals.run`\n\n"
            "## Casos relacionados que hay que comprobar al corregir\n- Los demás casos de la misma categoría.\n\n"
            f"## Issue o eval de origen\n#{issue_origen} · caso {r.id} de `evals/casos.yaml`\n\n"
            "Al corregir, anota en `fallos` del caso el cambio hecho y la nueva ejecución; el caso no se borra."
        )
        creados.append(crear(titulo, cuerpo, [ETIQUETA_FALLO]))
    return creados


# --------------------------------------------------------------------------------- línea de comandos
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="Corre los evals de Puente.")
    parser.add_argument("--casos", type=Path, default=RUTA_CASOS, help="archivo de casos (por defecto evals/casos.yaml)")
    parser.add_argument("--salida", type=Path, default=RUTA_RESULTADOS, help="dónde guardar la tabla (por defecto evals/resultados.md)")
    parser.add_argument("--repeticiones", type=int, default=3, help="corridas por caso con LLM (por defecto 3)")
    parser.add_argument("--sin-llm", action="store_true", help="omite los casos que usan LLM")
    parser.add_argument("--abrir-issues", action="store_true", help="abre un issue bug por cada caso en fallo (usa gh)")
    argumentos = parser.parse_args(argv)

    try:
        casos = cargar_casos(argumentos.casos)
    except ValueError as error:
        print(f"Error en los casos: {error}", file=sys.stderr)
        return 2

    resultados = correr(casos, repeticiones=argumentos.repeticiones, usar_llm=False if argumentos.sin_llm else None)
    print(tabla(resultados, casos))
    print()
    print(_aviso_de_cobertura(resultados))
    ruta = escribir_resultados(resultados, casos, argumentos.salida)
    print(f"\nResultados guardados en {ruta}")
    if argumentos.abrir_issues:
        for url in abrir_issues_de_fallos(resultados, casos):
            print(f"Issue abierto: {url}")
    return 0


if __name__ == "__main__":
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows usa cp1252
    raise SystemExit(main())
