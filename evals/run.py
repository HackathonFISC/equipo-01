"""Runner de evals: uv run python -m evals.run. Issue #13.

Carga evals/casos.yaml, ejecuta cada caso con el ejecutor de su categoría, compara lo observado con lo
esperado (escrito antes de correr), imprime una tabla por categoría con su métrica y su meta, y la guarda en
evals/resultados.md junto con el registro de fallos.

- Los casos de categorías con LLM (extracción de ofertas y perfil sin CV) se corren 3 veces y se reporta la
  tasa de acierto. El LLM es el de `puente.llm` (#45). Por defecto el runner usa LLM_MODO=reproducir con las
  grabaciones de evals/grabaciones, así que corre sin red ni clave. Si falta la grabación de un caso (o la
  clave, en modo vivo o grabar), el caso queda "omitido" con el motivo, no "fallo".
- Cada categoría se mide con un adaptador `_ejecutar_*` escrito contra la API real del agente (#4, #5, #6, #8
  y #9). Si el módulo o la función todavía no está fusionado, el caso queda "pendiente (#N sin fusionar)" y
  no rompe el runner. Una salida con forma inesperada es un error de contrato (TypeError), no un fallo del caso.
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

RUTA_CASOS = Path(__file__).with_name("casos.yaml")
RUTA_RESULTADOS = Path(__file__).with_name("resultados.md")

REPOSITORIO = "HackathonFISC/equipo-01"
ETIQUETA_FALLO = "bug"
HUSO_PANAMA = timezone(timedelta(hours=-5), "America/Panama")  # Panamá no usa horario de verano

# Dónde vive cada función que ejecuta un caso: (módulo, función, issue). Los nombres y las formas de entrada y
# salida son los de las APIs fusionadas de #5 (Mercado y reglas), #6 (Perfil) y #9 (guardrails); si cambian,
# se ajustan aquí y en los adaptadores `_ejecutar_*`, y en ningún otro lugar.
MERCADO_PROCESAR = ("puente.agents.mercado", "procesar_ofertas", 5)  # (entradas, *, extraer, taxonomia) -> Resultado
DISCRIMINACION_REVISAR = ("puente.rules.discriminacion", "revisar_texto", 5)  # (texto) -> list[Hallazgo]
DISCRIMINACION_BLOQUEADA = ("puente.rules.discriminacion", "esta_bloqueada", 5)  # (hallazgos) -> bool
TAXONOMIA_CARGAR = ("puente.rules.taxonomia", "cargar_taxonomia", 5)  # () -> {skill_id: {...}}
TAXONOMIA_NORMALIZAR = ("puente.rules.taxonomia", "normalizar_habilidad", 5)  # (texto, taxonomia) -> skill_id | None
PERFIL_CONSTRUIR = ("puente.agents.perfil", "construir_perfil", 6)  # (entrada, *, taxonomia, normalizar, llm) -> (Perfil, Decision)
PERFIL_LLM = ("puente.agents.perfil", "llm_puente", 6)  # (prompt) -> PerfilExtraido: el LLM real (puente.llm)
GUARDRAILS_EVALUAR = ("puente.guardrails", "evaluar_entrada", 9)  # (entrada) -> list[ResultadoGuardrail]
SOLVER_ASIGNAR = ("puente.solver.asignacion", "asignar", 4)
AUDITOR_AUDITAR = ("puente.agents.auditor", "auditar_lista_corta", 8)
HORARIO_LEER = ("puente.agents.horario", "leer_tirilla", 164)  # (archivo, *, hoy) -> RevisionTirilla (usa puente.llm)
HORARIO_VALIDAR = ("puente.agents.horario", "validar_bloques", 164)  # (bloques) -> ValidacionBloques
HORARIO_CONVERTIR = ("puente.agents.horario", "a_horario_clases", 164)  # (bloques válidos) -> list[Horario]
REDACTOR_PROPONER = ("puente.agents.redactor", "proponer_version", 157)  # (oferta, perfiles, *, redactar, traslados, minutos) -> Propuesta
REDACTOR_PLANTILLA = ("puente.agents.redactor", "redactor_plantilla", 157)  # (texto, cambios) -> TextoRedactado, sin LLM
RAIZ_REPO = Path(__file__).resolve().parent.parent

# Reglas de discriminación -> artículo de la Ley 11 de 2005 (solo las que bloquean) o alerta (las que no).
ARTICULO_POR_REGLA = {"edad": "art. 2", "sexo": "art. 1"}
ALERTA_POR_REGLA = {
    "buena_presencia": "presencia",
    "foto": "foto",
    "estado_civil": "estado_civil",
    "sexo_implicito": "sexo_implicito",  # preferencia de sexo implícita ("cajera", "soltera"): alerta, no bloquea
}

ESTADOS = ("ok", "fallo", "pendiente", "omitido")

# LLM de los evals: puente.llm en modo reproducir, con las grabaciones versionadas junto a los casos (no en data/).
DIR_GRABACIONES = Path(__file__).with_name("grabaciones")
MODO_LLM_POR_DEFECTO = "reproducir"
# Causas de puente.llm.ErrorLLM que significan "no se pudo consultar al LLM", no "el agente se equivocó".
MOTIVO_SIN_LLM = {"grabacion_faltante": "sin grabación", "sin_clave": "sin clave del LLM"}
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


class SinLLM(Exception):
    """El caso necesita al LLM y no hubo respuesta: falta la grabación o la clave. El caso queda omitido."""

    def __init__(self, motivo: str) -> None:
        self.motivo = motivo
        super().__init__(motivo)


def _motivo_sin_llm(error: BaseException | None) -> str | None:
    """Si en la cadena de causas hay un ErrorLLM por grabación o clave faltante, devuelve el motivo."""
    vistos: set[int] = set()
    while error is not None and id(error) not in vistos:
        vistos.add(id(error))
        if isinstance(error, SinLLM):
            return error.motivo
        motivo = MOTIVO_SIN_LLM.get(str(getattr(error, "causa", "") or ""))
        if motivo:
            return motivo
        error = error.__cause__ or error.__context__
    return None


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

    # La demo y los evals deben usar exactamente el mismo catálogo vigente.
    # Un YAML alternativo puede seguir inyectando su taxonomía para pruebas.
    if "taxonomia" not in datos:
        datos["taxonomia"] = _resolver(TAXONOMIA_CARGAR)(
            Path(__file__).resolve().parent.parent / "data" / "taxonomia.csv"
        )
    taxonomia = datos.get("taxonomia")
    if taxonomia is not None:
        if not isinstance(taxonomia, dict) or not taxonomia:
            raise ValueError("casos.yaml: 'taxonomia' debe ser un mapa skill_id -> {nombre_es, sinonimos}")
        for skill_id, info in taxonomia.items():
            if not isinstance(info, dict) or not str(info.get("nombre_es") or "").strip():
                raise ValueError(f"taxonomía {skill_id}: falta nombre_es")
            if not isinstance(info.get("sinonimos", []), list):
                raise ValueError(f"taxonomía {skill_id}: 'sinonimos' debe ser una lista")

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
    """Mapa, modelo Pydantic u objeto con atributos como dict. Cualquier otra forma (lista, tupla, None, texto)
    es un contrato roto de la dependencia: se lanza TypeError para que el caso cuente como fallo con ese
    detalle, en vez de compararlo contra un dict vacío y dar un 'fallo' falso."""
    if isinstance(objeto, dict):
        return objeto
    if hasattr(objeto, "model_dump"):
        return objeto.model_dump()
    if hasattr(objeto, "__dict__"):
        return dict(vars(objeto))
    raise TypeError(f"salida con forma inesperada: {type(objeto).__name__}")


def _sin_tildes(texto: Any) -> str:
    descompuesto = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _norm(texto: Any) -> str:
    """Minúsculas, sin tildes y con espacios colapsados."""
    return " ".join(_sin_tildes(texto).casefold().split())


# Diferencias cosméticas de puesto y empresa que no son un error de extracción (#152): la marca de género
# («Cajero/a», «Mesero(a)») y el sufijo societario («S.A.», «S. de R.L.», «Inc.», «Corp.»).
_MARCA_GENERO = re.compile(r"(?<=\w)(?:/(?:as|os|a|o)|\s?\(\s?(?:as|os|a|o)\s?\))(?!\w)")
_SUFIJO_SOCIETARIO = re.compile(r"(?:[\s,]+(?:s\.?\s?a\.?|s\.?\s?de\s?r\.?\s?l\.?|inc\.?|corp\.?))+\s*$")


def _norm_nombre(texto: Any) -> str:
    """`_norm` sin marca de género ni sufijo societario: «Cajero/a» -> «cajero», «Rio Ficticio S.A.» -> «rio ficticio»."""
    return _SUFIJO_SOCIETARIO.sub("", _MARCA_GENERO.sub("", _norm(texto))).strip()


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
        if "implicit" in texto:
            conocidas.add("sexo_implicito")
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
            e, o = _norm_nombre(esperado[campo]), _norm_nombre(observado.get(campo) or "")
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
    prompt = observado.get("prompt_llm")
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
        # El plan exige quitar cédula, teléfono y dirección ANTES del LLM: el prompt también se revisa.
        if prompt is not None and _norm_literal(dato) in _norm_literal(prompt):
            malos.append(f"PII en el prompt ({dato})")
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


def _franjas(valor: Any) -> list[tuple[tuple[str, ...], str, str]]:
    return sorted(
        (tuple(_a_dict(h).get("dias") or ()), str(_a_dict(h).get("hora_inicio")), str(_a_dict(h).get("hora_fin")))
        for h in _lista(valor or [])
    )


def _diferencias_horario(esperado: dict, observado: dict) -> list[str]:
    """#164: horario_clases idéntico; problemas iguales por (índice, campo); vigencia y excepciones si se esperan."""
    malos = []
    if "horario_clases" in esperado and _franjas(esperado["horario_clases"]) != _franjas(observado.get("horario_clases")):
        malos.append("horario_clases")
    if "problemas" in esperado:
        clave = lambda p: (int(_a_dict(p).get("indice") or 0), str(_a_dict(p).get("campo")))  # noqa: E731
        if sorted(map(clave, esperado["problemas"] or [])) != sorted(map(clave, observado.get("problemas") or [])):
            malos.append("problemas")
    if "vigente_hasta" in esperado and esperado["vigente_hasta"] != observado.get("vigente_hasta"):
        malos.append("vigente_hasta")
    if "excepciones" in esperado:
        rango = lambda e: (str(_a_dict(e).get("desde")), str(_a_dict(e).get("hasta")))  # noqa: E731
        if sorted(map(rango, esperado["excepciones"] or [])) != sorted(map(rango, observado.get("excepciones") or [])):
            malos.append("excepciones")
    return malos


def _diferencias_redactor(esperado: dict, observado: dict) -> list[str]:
    """#157: bloqueo antes y después, hallazgos de revisar_texto sobre lo propuesto, cambios por (regla, frase) y
    viables por id iguales a lo esperado. Del texto solo se exige lo que debe contener: lo redacta la plantilla o
    el LLM, la legalidad la mide revisar_texto."""
    malos = []
    for campo in ("bloqueada_antes", "bloqueada_despues"):
        if campo in esperado and bool(esperado[campo]) != bool(observado.get(campo)):
            malos.append(campo)
    if "hallazgos_despues" in esperado and sorted(map(_norm, _planos(esperado["hallazgos_despues"]))) != sorted(
        map(_norm, _planos(observado.get("hallazgos_despues")))
    ):
        malos.append("hallazgos_despues")
    if "cambios" in esperado:
        clave = lambda c: (str(_a_dict(c).get("regla")), _norm(_a_dict(c).get("frase")))  # noqa: E731
        if sorted(map(clave, _planos(esperado["cambios"]))) != sorted(map(clave, _planos(observado.get("cambios")))):
            malos.append("cambios")
    for campo in ("viables_antes", "viables_despues", "cumplen_filtro_original"):
        if campo in esperado and sorted(map(str, _planos(esperado[campo]))) != sorted(map(str, _planos(observado.get(campo)))):
            malos.append(campo)
    for campo in ("redactado_por", "max_minutos_traslado"):
        if campo in esperado and str(esperado[campo]) != str(observado.get(campo)):
            malos.append(campo)
    if "nota_contiene" in esperado and _norm(esperado["nota_contiene"]) not in _norm(observado.get("nota_redaccion") or ""):
        malos.append("nota_redaccion")
    texto = _norm(observado.get("texto_nuevo") or "")
    malos += [f"texto sin «{f}»" for f in _planos(esperado.get("texto_contiene")) if _norm(f) not in texto]
    if esperado.get("decision_pendiente") and not observado.get("decision_pendiente"):
        malos.append("decision_pendiente")
    return malos


_DIFERENCIAS: dict[str, Callable[[dict, dict], list[str]]] = {
    "extraccion_ofertas": _diferencias_extraccion,
    "requisitos_discriminatorios": _diferencias_discriminacion,
    "perfil_sin_cv": _diferencias_perfil,
    "matching_factible": _diferencias_matching,
    "sesgo_contrafactual": _diferencias_sesgo,
    "guardrails": _diferencias_guardrails,
    "horario_tirilla": _diferencias_horario,
    "horario_validacion": _diferencias_horario,
    "redactor_puente": _diferencias_redactor,
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


def _lista(valor: Any) -> list:
    """Exige una lista o tupla; cualquier otra forma es un contrato roto de la dependencia."""
    if not isinstance(valor, (list, tuple)):
        raise TypeError(f"salida con forma inesperada: {type(valor).__name__}")
    return list(valor)


def _ejecutar_extraccion(caso: dict, *, taxonomia: dict | None = None, extraer: Callable[..., Any] | None = None) -> dict:
    """#5: procesar_ofertas([texto]) -> Resultado con .ofertas; se mide la primera Oferta.

    Sin `extraer` usa el extractor real (puente.llm); sin `taxonomia`, la de data/taxonomia.csv. Mercado atrapa
    los errores del extractor y los deja como texto en `errores` con la causa entre paréntesis; si la causa es una
    grabación o una clave faltante, el caso no se pudo medir (SinLLM), no falló.
    """
    procesar = _resolver(MERCADO_PROCESAR)
    argumentos: dict[str, Any] = {}
    if extraer is not None:
        argumentos["extraer"] = extraer
    if taxonomia is not None:
        argumentos["taxonomia"] = taxonomia
    resultado = _a_dict(procesar([caso["entrada"]["texto"]], **argumentos))
    ofertas = _lista(resultado.get("ofertas"))
    if not ofertas:
        errores = [str(e) for e in _planos(resultado.get("errores"))]
        for causa, motivo in MOTIVO_SIN_LLM.items():
            if any(f"({causa})" in error for error in errores):
                raise SinLLM(motivo)
        raise ValueError(f"mercado no devolvió ninguna oferta ({'; '.join(errores) or 'sin detalle'})")
    oferta = _a_dict(ofertas[0])
    return {campo: oferta.get(campo) for campo in CAMPOS_EXTRACCION}


def _ejecutar_discriminacion(caso: dict) -> dict:
    """#5: revisar_texto(texto) -> list[Hallazgo]; esta_bloqueada(hallazgos) decide el bloqueo.

    Cada hallazgo trae `regla` (edad, sexo, buena_presencia, foto, estado_civil): edad y sexo se traducen al
    artículo de la Ley 11 de 2005 que bloquea; las demás son alertas.
    """
    hallazgos = _lista(_resolver(DISCRIMINACION_REVISAR)(caso["entrada"]["texto"]))
    bloqueada = bool(_resolver(DISCRIMINACION_BLOQUEADA)(hallazgos))
    reglas: set[str] = set()
    alertas: set[str] = set()
    for hallazgo in hallazgos:
        regla = str(_a_dict(hallazgo).get("regla") or "")
        if regla in ARTICULO_POR_REGLA:
            reglas.add(ARTICULO_POR_REGLA[regla])
        else:
            alertas.add(ALERTA_POR_REGLA.get(regla, f"otra:{regla}"))
    return {"bloqueada": bloqueada, "reglas": sorted(reglas), "alertas": sorted(alertas)}


def _ejecutar_perfil(caso: dict, *, taxonomia: dict | None = None, llm: Callable[[str], str] | None = None) -> dict:
    """#6: construir_perfil(entrada, taxonomia=, normalizar=, llm=) -> (Perfil, Decision).

    La entrevista entra como `respuestas` (hasta 5, en el orden de PREGUNTAS_ENTREVISTA) para que el agente corra
    en modo entrevista y no en modo CV; `entrada.formulario` aporta los datos que el agente exige y que no salen
    del texto (edad, corregimiento). Sin `llm` usa puente.llm; sin `taxonomia`, la de data/taxonomia.csv.
    Un ErrorLLM por grabación o clave faltante sube tal cual y `correr` lo marca como omitido.

    Devuelve además `prompt_llm`: lo que recibió el LLM (ya sin PII), para medir que no se filtren datos personales.
    """
    construir = _resolver(PERFIL_CONSTRUIR)
    normalizar = _resolver(TAXONOMIA_NORMALIZAR)
    if taxonomia is None:
        taxonomia = _resolver(TAXONOMIA_CARGAR)()
    respuestas = [str(r) for r in caso["entrada"]["respuestas"]]
    entrada = {**(caso["entrada"].get("formulario") or {}), "id": caso["id"], "respuestas": respuestas}
    base = llm if llm is not None else _resolver(PERFIL_LLM)
    prompts: list[str] = []

    def llm_espiado(prompt: str) -> str:
        prompts.append(prompt)
        return base(prompt)

    argumentos: dict[str, Any] = {"taxonomia": taxonomia, "normalizar": normalizar, "llm": llm_espiado}
    resultado = construir(entrada, **argumentos)
    if not isinstance(resultado, tuple) or not resultado:
        raise TypeError(f"salida con forma inesperada: {type(resultado).__name__}")
    pares = _habilidades_con_evidencia(_a_dict(resultado[0]).get("habilidades"))
    return {
        "habilidades": [{"skill_id": skill, "evidencia": evidencia} for skill, evidencia in pares],
        "prompt_llm": "\n".join(prompts),
    }


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
        desglose = _a_dict(d.get("desglose") or {})
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
    """#9: evaluar_entrada(entrada) -> list[ResultadoGuardrail] (ok, marcas, motivo, respuesta).

    Un caso de tipo 'archivo' entra como {'archivo': {nombre, tamano_bytes}}; uno de texto, con sus claves
    (texto, edad, salario). Veredicto: ok=False con respuesta fija -> fuera_de_alcance; ok=False sin ella ->
    rechazar; solo marcas (inyección, alcance dudoso) -> marcar; nada de eso -> aceptar. El texto marcado sigue
    como dato mientras no haya rechazo.
    """
    evaluar = _resolver(GUARDRAILS_EVALUAR)
    datos = {clave: valor for clave, valor in caso["entrada"].items() if clave != "tipo"}
    entrada = {"archivo": datos} if caso["entrada"].get("tipo") == "archivo" else datos
    with _espiar_llm() as espia:
        resultados = _lista(evaluar(entrada))
    items = [_a_dict(r) for r in resultados]
    if any("ok" not in d for d in items):
        raise TypeError("salida con forma inesperada: ResultadoGuardrail sin 'ok'")
    rechazos = [d for d in items if not d["ok"]]
    marcas = [m for d in items for m in _planos(d.get("marcas"))]
    if rechazos:
        veredicto = "fuera_de_alcance" if any(d.get("respuesta") for d in rechazos) else "rechazar"
    else:
        veredicto = "marcar" if marcas else "aceptar"
    return {"veredicto": veredicto, "sigue_como_dato": not rechazos, "llamo_llm": espia["llamadas"] > 0}


def _ejecutar_horario_tirilla(caso: dict) -> dict:
    """#164: leer_tirilla(ruta, hoy=) -> RevisionTirilla. Claude con visión vía puente.llm (grabación en evals/grabaciones);
    una grabación o clave faltante sube como ErrorLLM y el caso queda omitido."""
    entrada = caso["entrada"]
    argumentos: dict[str, Any] = {"hoy": date.fromisoformat(str(entrada["hoy"]))} if entrada.get("hoy") else {}
    revision = _resolver(HORARIO_LEER)(RAIZ_REPO / entrada["ruta"], **argumentos)
    datos = _a_dict(revision.como_dict() if hasattr(revision, "como_dict") else revision)
    return {c: datos.get(c) for c in ("horario_clases", "problemas", "vigente_hasta", "excepciones", "confianza")}


def _ejecutar_horario_validacion(caso: dict) -> dict:
    """#164: validar_bloques(bloques) -> ValidacionBloques; a_horario_clases(válidos) -> list[Horario]. Sin LLM."""
    validacion = _resolver(HORARIO_VALIDAR)(caso["entrada"]["bloques"])
    horario = _resolver(HORARIO_CONVERTIR)(validacion.bloques)
    return {
        "problemas": [{"indice": p.indice, "campo": p.campo, "motivo": p.motivo} for p in validacion.problemas],
        "horario_clases": [_a_dict(h) for h in _lista(horario)],
    }


def _ejecutar_redactor(caso: dict) -> dict:
    """#157: proponer_version(oferta, perfiles, redactar=, traslados=, minutos=) -> Propuesta. Sin LLM real ni red:
    redacta la plantilla del código o, si el caso trae `llm_falso`, un redactor falso que devuelve ese texto (el
    código lo verifica igual que al LLM). Los hallazgos de después se miden aparte con revisar_texto sobre el puesto
    y el texto propuestos; los viables son los ids que contó el solver."""
    proponer = _resolver(REDACTOR_PROPONER)
    revisar = _resolver(DISCRIMINACION_REVISAR)
    entrada = caso["entrada"]
    if "llm_falso" in entrada:
        texto_llm = str(entrada["llm_falso"])
        redactar: Callable[..., Any] = lambda _texto, _cambios: texto_llm  # noqa: E731
    else:
        redactar = _resolver(REDACTOR_PLANTILLA)
    oferta = entrada.get("oferta") or json.loads((RAIZ_REPO / entrada["oferta_ruta"]).read_text(encoding="utf-8"))
    argumentos: dict[str, Any] = {"redactar": redactar, "traslados": entrada.get("traslados")}
    if entrada.get("minutos") is not None:
        argumentos["minutos"] = int(entrada["minutos"])
    propuesta = _a_dict(proponer(oferta, list(entrada.get("perfiles") or []), **argumentos))
    propuesto = "\n".join(str(t) for t in (propuesta.get("puesto_nuevo"), propuesta.get("texto_nuevo")) if t)
    hallazgos = [_a_dict(h) for h in _lista(revisar(propuesto))]
    decision = _a_dict(propuesta.get("decision") or {})
    return {
        "viables": f"{propuesta.get('viables_antes')} → {propuesta.get('viables_despues')}",
        "bloqueada_antes": propuesta.get("bloqueada_antes"),
        "bloqueada_despues": propuesta.get("bloqueada_despues"),
        "hallazgos_despues": [f"{h.get('regla')} «{h.get('frase')}»" for h in hallazgos],
        "cambios": [{"regla": c.get("regla"), "frase": c.get("frase")} for c in map(_a_dict, _lista(propuesta.get("cambios")))],
        "viables_antes": _lista(propuesta.get("viables_antes_ids")),
        "viables_despues": _lista(propuesta.get("viables_despues_ids")),
        "cumplen_filtro_original": _lista(propuesta.get("cumplen_filtro_original")),
        "redactado_por": propuesta.get("redactado_por"),
        "nota_redaccion": propuesta.get("nota_redaccion"),
        "max_minutos_traslado": propuesta.get("max_minutos_traslado"),
        "texto_nuevo": propuesta.get("texto_nuevo"),
        "decision_pendiente": "pendiente del orientador" in str(decision.get("salida_resumen") or ""),
    }


def ejecutores_por_defecto(
    taxonomia: dict | None = None,
    *,
    extraer: Callable[..., Any] | None = None,
    llm: Callable[[str], str] | None = None,
) -> dict[str, Ejecutor]:
    """Un ejecutor por categoría. Resuelven sus dependencias al ejecutarse, no al importar este módulo.

    `taxonomia` es la de casos.yaml (en línea); `extraer` y `llm` sustituyen al LLM real de puente.llm (pruebas).
    """
    return {
        "extraccion_ofertas": functools.partial(_ejecutar_extraccion, taxonomia=taxonomia, extraer=extraer),
        "requisitos_discriminatorios": _ejecutar_discriminacion,
        "perfil_sin_cv": functools.partial(_ejecutar_perfil, taxonomia=taxonomia, llm=llm),
        "matching_factible": _ejecutar_matching,
        "sesgo_contrafactual": _ejecutar_sesgo,
        "guardrails": _ejecutar_guardrails,
        "horario_tirilla": _ejecutar_horario_tirilla,
        "horario_validacion": _ejecutar_horario_validacion,
        "redactor_puente": _ejecutar_redactor,
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
    """Ejecuta todos los casos. Las categorías con LLM se corren `repeticiones` veces; el resto, una.

    `usar_llm=False` (--sin-llm) omite las categorías con LLM sin llamar a su ejecutor. Si no, se ejecutan con
    puente.llm, y un caso que no obtiene respuesta del LLM (grabación o clave faltante) queda "omitido".
    """
    usar_llm = True if usar_llm is None else usar_llm
    ejecutores = ejecutores_por_defecto(casos.get("taxonomia")) if ejecutores is None else ejecutores
    categorias = casos["categorias"]
    resultados: list[ResultadoCaso] = []
    for caso in casos["casos"]:
        categoria = caso["categoria"]
        con_llm = bool(categorias[categoria]["usa_llm"])
        if con_llm and not usar_llm:
            resultados.append(ResultadoCaso(caso["id"], categoria, "omitido", 0, 0, None, "omitido (--sin-llm)"))
            continue
        ejecutor = ejecutores.get(categoria)
        if ejecutor is None:
            motivo = "pendiente: sin ejecutor para la categoría"
            resultados.append(ResultadoCaso(caso["id"], categoria, "pendiente", 0, 0, None, motivo))
            continue
        corridas = max(1, repeticiones) if con_llm else 1
        aciertos, ejecutadas, ultimo, errores = 0, 0, None, []
        pendiente: DependenciaPendiente | None = None
        sin_llm: str | None = None
        for _ in range(corridas):
            try:
                observado = ejecutor(caso)
                if not isinstance(observado, dict):
                    raise TypeError(f"salida con forma inesperada: {type(observado).__name__}")
            except DependenciaPendiente as error:
                pendiente = error
                break
            except Exception as error:  # cualquier otro error al ejecutar cuenta como fallo
                sin_llm = _motivo_sin_llm(error) if con_llm else None
                if sin_llm:
                    break
                ejecutadas += 1
                errores.append(f"{type(error).__name__}: {error}")
                continue
            ejecutadas += 1
            ultimo = observado
            if categoria == "perfil_sin_cv" and isinstance(observado, dict):
                fuente = "\n".join(str(r) for r in caso["entrada"].get("respuestas") or [])
                observado = {"texto_fuente": fuente, **observado}
            diferencias = _diferencias(categoria, caso["esperado"], observado)
            if diferencias:
                errores.append(f"difiere en {', '.join(diferencias)}")
            else:
                aciertos += 1
        if pendiente is not None:
            detalle = f"pendiente ({pendiente})"
            resultados.append(ResultadoCaso(caso["id"], categoria, "pendiente", 0, 0, None, detalle))
        elif sin_llm is not None:
            resultados.append(ResultadoCaso(caso["id"], categoria, "omitido", 0, 0, None, f"omitido ({sin_llm})"))
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
    """Tabla markdown por categoría: casos, ejecutados, aciertos, tasa, métrica, meta y si cumple.

    "Cumple" se decide con los casos medidos (ok o fallo). Si se midieron todos: "sí" o "no" según la meta. Si
    algún caso quedó pendiente u omitido: "parcial" si lo medido alcanza la meta y "no (parcial)" si no la
    alcanza; ninguno de los dos aprueba la categoría. "sin medir" si no se midió ninguno.
    """
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
            alcanza = tasa >= float(info["meta"])
            if len(medidos) == total:
                cumple = "sí" if alcanza else "no"
            else:
                cumple = "parcial" if alcanza else "no (parcial)"
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
        motivos = sorted({r.detalle.removeprefix("omitido").strip(" ()") for r in resultados if r.estado == "omitido"})
        partes.append(
            f"{conteo['omitido']} casos con LLM omitidos ({'; '.join(motivos)}); no se miden hasta grabar sus "
            "respuestas con `LLM_MODO=grabar` y la clave del LLM (ver evals/grabaciones/README.md)"
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


def _nota_taxonomia(casos: dict) -> str:
    return "Taxonomía usada en esta corrida: `data/taxonomia.csv`, el mismo catálogo que usa la demo."


def _nota_llm() -> str:
    """Qué LLM respondió en esta corrida, leído del entorno que usa puente.llm (sin leer ni mostrar claves)."""
    modo = (os.getenv("LLM_MODO") or "vivo").strip().lower()
    directorio = (os.getenv("LLM_DIR_GRABACIONES") or "").strip() or "data/grabaciones (por defecto de puente.llm)"
    with contextlib.suppress(ValueError):
        directorio = Path(directorio).resolve().relative_to(Path(__file__).resolve().parent.parent).as_posix()
    if modo == "reproducir":
        return f"LLM de esta corrida: `puente.llm` en modo reproducir, con las grabaciones de `{directorio}`."
    return f"LLM de esta corrida: `puente.llm` en modo {modo} (llamadas reales al modelo)."


@contextlib.contextmanager
def _entorno_llm(modo: str = MODO_LLM_POR_DEFECTO) -> Iterator[None]:
    """Los evals solo hacen llamadas reales cuando la CLI pide vivo o grabar explícitamente."""
    with contextlib.suppress(Exception):  # igual que puente.llm: .env sin pisar el entorno; si falta o es ilegible, se sigue
        from dotenv import load_dotenv

        load_dotenv(override=False)
    por_defecto = {"LLM_MODO": MODO_LLM_POR_DEFECTO, "LLM_DIR_GRABACIONES": str(DIR_GRABACIONES)}
    previos = {nombre: os.environ.get(nombre) for nombre in por_defecto}
    os.environ["LLM_MODO"] = modo
    if not (os.environ.get("LLM_DIR_GRABACIONES") or "").strip():
        os.environ["LLM_DIR_GRABACIONES"] = por_defecto["LLM_DIR_GRABACIONES"]
    try:
        yield
    finally:
        for nombre, valor in previos.items():
            if valor is None:
                os.environ.pop(nombre, None)
            else:
                os.environ[nombre] = valor


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
        (
            "En modo reproducir cada caso con LLM usa una sola grabación; no se mide variación entre corridas. "
            if (os.getenv("LLM_MODO") or "").strip().lower() == "reproducir"
            else "Los casos con LLM se repiten y la tasa es aciertos sobre corridas. "
        ) + "Las metas son propuestas provisionales hasta que el equipo las confirme con el plan. "
        "Todos los datos de los casos son sintéticos.",
        "",
        _nota_taxonomia(casos),
        "",
        _nota_llm(),
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
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--vivo", action="store_true", help="autoriza llamadas reales al LLM en esta corrida")
    modo.add_argument("--grabar", action="store_true", help="autoriza llamadas reales y graba las respuestas")
    parser.add_argument("--abrir-issues", action="store_true", help="abre un issue bug por cada caso en fallo (usa gh)")
    argumentos = parser.parse_args(argv)

    try:
        casos = cargar_casos(argumentos.casos)
    except ValueError as error:
        print(f"Error en los casos: {error}", file=sys.stderr)
        return 2

    modo_llm = "vivo" if argumentos.vivo else "grabar" if argumentos.grabar else MODO_LLM_POR_DEFECTO
    with _entorno_llm(modo_llm):
        # Reproducir tres veces el mismo hash no aporta tres muestras independientes.
        repeticiones = 1 if modo_llm == "reproducir" else argumentos.repeticiones
        resultados = correr(casos, repeticiones=repeticiones, usar_llm=not argumentos.sin_llm)
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
