"""Validación de entradas, detección de inyección, fuera de alcance y limpieza de PII. Issues #9 y #56.

Todo es determinístico, sin LLM: se corre antes de gastar una sola llamada al modelo.

- Archivos: solo pdf, jpg o png de hasta 5 MB. El tamaño se revisa antes que nada, así
  un archivo de 50 MB se rechaza sin leer su contenido ni llamar al LLM. Las notas de voz
  (.ogg, .mp3, .m4a, .wav) se rechazan con un mensaje propio hasta que exista #55.
- Texto: hasta 10,000 caracteres y no vacío.
- Rangos: edad entre 18 y 29 (los menores quedan fuera de la demo), salario mayor que 0.
- Inyección: se marca y el texto sigue como dato; nunca se ejecuta como instrucción.
  Las palabras clave solo marcan: la defensa real es estructural (salida estructurada,
  validación con Pydantic y decisiones en código).
- Fuera de alcance: respuesta fija y registro. Si hay duda, se deja pasar y se marca.
- PII: `limpiar_entrada` quita cédula, teléfono y dirección exacta antes de `graph.invoke`,
  para que el dato crudo no llegue al checkpointer, a `decisiones`, al LLM ni a LangSmith.

Cada verificación devuelve un `ResultadoGuardrail` con su `Decision`, y `nodo_guardrails`
junta todas en la clave `decisiones` del estado.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puente.agents.perfil import quitar_pii
from puente.schemas import Decision, EstadoPuente

AGENTE = "guardrails"

MAX_BYTES_ARCHIVO = 5 * 1024 * 1024
MAX_CARACTERES_TEXTO = 10_000
EDAD_MIN, EDAD_MAX = 18, 29
EXTENSIONES_PERMITIDAS = {".pdf": "pdf", ".jpg": "jpg", ".jpeg": "jpg", ".png": "png"}
# Primeros bytes de cada formato: evita que un ejecutable renombrado a .pdf pase.
FIRMAS_ARCHIVO = {
    "pdf": (b"%PDF",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
}

# Notas de voz: se rechazan con un mensaje claro hasta que #55 las transcriba.
EXTENSIONES_AUDIO = {".ogg", ".mp3", ".m4a", ".wav"}
MENSAJE_AUDIO = "Todavía no aceptamos notas de voz; escribe tu mensaje o sube un pdf, jpg o png."

RESPUESTA_FUERA_DE_ALCANCE = "Solo atiendo empleo y formación."

# Rutas de archivo: se dejan intactas para no romper la lectura; el texto que sale de ellas
# lo limpia el agente que lo extrae (Perfil quita la PII antes del LLM).
CLAVES_SIN_LIMPIAR = frozenset({"cv_ruta", "ruta"})

# Las reglas se aplican sobre el texto normalizado: minúsculas, sin acentos, espacios simples.
REGLAS_INYECCION: dict[str, re.Pattern[str]] = {
    "ignora": re.compile(r"\bignor(a|e|en|ar)\b"),
    "olvida_instrucciones": re.compile(
        r"\b(olvida|olvidate|olviden|forget)\b.{0,30}\b(instrucciones|reglas|indicaciones|instructions|rules)\b"
    ),
    "eres_ahora": re.compile(
        r"\b(eres ahora|ahora eres|you are now|actua como|act as)\b"
    ),
    "system_prompt": re.compile(
        r"\b(system ?prompt|prompt del sistema|prompt de sistema)\b"
    ),
}

# Palabras que ubican el texto dentro de empleo y formación. Se buscan como prefijo de palabra.
PALABRAS_EN_ALCANCE = (
    "empleo",
    "emplea",
    "trabaj",
    "vacante",
    "oferta",
    "puesto",
    "empresa",
    "contrat",
    "salario",
    "sueldo",
    "pago",
    "paga",
    "jornada",
    "turno",
    "horario",
    "entrevista",
    "postul",
    "aplicar",
    "curriculum",
    "hoja de vida",
    "cv",
    "experiencia",
    "habilidad",
    "destreza",
    "perfil",
    "oficio",
    "curso",
    "formacion",
    "capacita",
    "taller",
    "beca",
    "estudi",
    "aprend",
    "pasantia",
    "practica",
    "certifica",
    "diploma",
    "carrera",
    "colegio",
    "universidad",
    "inadeh",
    "fonda",
    "atencion al cliente",
    "cajer",
    "mesero",
    "vendedor",
    "ayudante",
    "asistente",
    "chofer",
    "conductor",
    "cocin",
    "limpieza",
    "seguridad",
    "bodega",
    "traslado",
    "pasaje",
    "corregimiento",
    "edad",
    "anos",
)
# Temas claramente ajenos. Solo cuentan si no aparece ninguna palabra en alcance.
PALABRAS_FUERA_DE_ALCANCE = (
    "receta",
    "chiste",
    "poema",
    "cancion",
    "futbol",
    "beisbol",
    "partido de",
    "pelicula",
    "serie de",
    "horoscopo",
    "clima",
    "pronostico",
    "loteria",
    "apuesta",
    "criptomoneda",
    "bitcoin",
    "elecciones",
    "presidente",
    "politic",
    "religion",
    "videojuego",
    "novia",
    "novio",
    "tarea de matematica",
    "ecuacion",
    "capital de",
    "hackear",
    "contrasena",
    "bomba",
    "droga",
)


@dataclass
class ResultadoGuardrail:
    """`ok=False` detiene el flujo; `marcas` solo avisa (inyección, alcance dudoso)."""

    ok: bool
    motivo: str
    decision: Decision
    marcas: list[str] = field(default_factory=list)
    respuesta: str | None = None


def _decidir(
    ok: bool, motivo: str, entrada: str, salida: str, **extra: Any
) -> ResultadoGuardrail:
    decision = Decision(
        agente=AGENTE, entrada_resumen=entrada, salida_resumen=salida, razon=motivo
    )
    return ResultadoGuardrail(ok=ok, motivo=motivo, decision=decision, **extra)


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos ni caracteres invisibles, con espacios simples."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_marcas = "".join(
        c
        for c in descompuesto
        if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
    )
    return re.sub(r"\s+", " ", sin_marcas.casefold()).strip()


def _resumen(texto: str, largo: int = 80) -> str:
    plano = re.sub(r"\s+", " ", texto).strip()
    return plano if len(plano) <= largo else plano[: largo - 1] + "…"


def validar_archivo(
    nombre: str, tamano_bytes: int, contenido: bytes | None = None
) -> ResultadoGuardrail:
    """Acepta pdf, jpg o png de hasta 5 MB. Mira el tamaño declarado antes de tocar el contenido."""
    entrada = f"archivo {nombre!r} de {tamano_bytes} bytes"
    extension = Path(nombre).suffix.lower()
    if extension in EXTENSIONES_AUDIO:
        return _decidir(
            False, MENSAJE_AUDIO, entrada, "rechazado", respuesta=MENSAJE_AUDIO
        )
    if tamano_bytes > MAX_BYTES_ARCHIVO:
        mb = tamano_bytes / (1024 * 1024)
        return _decidir(
            False,
            f"archivo de {mb:.1f} MB supera el límite de 5 MB",
            entrada,
            "rechazado",
        )
    if tamano_bytes <= 0:
        return _decidir(False, "archivo vacío", entrada, "rechazado")
    tipo = EXTENSIONES_PERMITIDAS.get(extension)
    if tipo is None:
        return _decidir(
            False,
            f"tipo {extension or 'sin extensión'!r} no permitido; solo pdf, jpg o png",
            entrada,
            "rechazado",
        )
    if contenido is not None:
        if len(contenido) > MAX_BYTES_ARCHIVO:
            return _decidir(
                False,
                "el contenido real supera el límite de 5 MB",
                entrada,
                "rechazado",
            )
        if not contenido.startswith(FIRMAS_ARCHIVO[tipo]):
            return _decidir(
                False, f"el contenido no corresponde a un {tipo}", entrada, "rechazado"
            )
    return _decidir(True, f"archivo {tipo} dentro del límite", entrada, "aceptado")


def validar_texto(texto: str) -> ResultadoGuardrail:
    """Acepta texto no vacío de hasta 10,000 caracteres."""
    entrada = f"texto de {len(texto)} caracteres"
    if not texto.strip():
        return _decidir(False, "texto vacío", entrada, "rechazado")
    if len(texto) > MAX_CARACTERES_TEXTO:
        return _decidir(
            False,
            f"texto de {len(texto)} caracteres supera el límite de 10,000",
            entrada,
            "rechazado",
        )
    return _decidir(True, "texto dentro del límite", entrada, "aceptado")


def _es_numero(valor: Any) -> bool:
    """Número finito; descarta bool, NaN e infinito."""
    return (
        isinstance(valor, (int, float))
        and not isinstance(valor, bool)
        and math.isfinite(valor)
    )


def validar_rangos(edad: Any = None, salario: Any = None) -> ResultadoGuardrail:
    """Edad entera entre 18 y 29 y salario mayor que 0. Los valores ausentes no se validan."""
    entrada = f"edad={edad!r}, salario={salario!r}"
    problemas = []
    if edad is not None and not (
        _es_numero(edad) and float(edad).is_integer() and EDAD_MIN <= edad <= EDAD_MAX
    ):
        problemas.append(f"edad {edad!r} fuera del rango {EDAD_MIN}–{EDAD_MAX}")
    if salario is not None and not (_es_numero(salario) and salario > 0):
        problemas.append(f"salario {salario!r} debe ser mayor que 0")
    if problemas:
        return _decidir(False, "; ".join(problemas), entrada, "rechazado")
    return _decidir(True, "rangos válidos", entrada, "aceptado")


def detectar_inyeccion(texto: str) -> list[str]:
    """Nombres de las reglas de inyección que coinciden; lista vacía si ninguna."""
    normal = normalizar(texto)
    return [
        nombre for nombre, patron in REGLAS_INYECCION.items() if patron.search(normal)
    ]


def _contiene(normal: str, palabras: tuple[str, ...]) -> list[str]:
    return [p for p in palabras if re.search(rf"\b{re.escape(p)}", normal)]


def fuera_de_alcance(texto: str) -> bool | None:
    """True si el texto es claramente ajeno a empleo y formación, False si está en alcance,
    None si no se puede saber (se deja pasar y se marca)."""
    normal = normalizar(texto)
    if _contiene(normal, PALABRAS_EN_ALCANCE):
        return False
    if _contiene(normal, PALABRAS_FUERA_DE_ALCANCE):
        return True
    return None


def revisar_texto(texto: str) -> list[ResultadoGuardrail]:
    """Longitud, inyección y alcance de un texto, en ese orden. Se detiene en el primer rechazo."""
    resultados = [validar_texto(texto)]
    if not resultados[0].ok:
        return resultados
    resumen = _resumen(texto)

    reglas = detectar_inyeccion(texto)
    if reglas:
        resultados.append(
            _decidir(
                True,
                f"posible inyección ({', '.join(reglas)}); el texto sigue como dato, no como instrucción",
                resumen,
                "marcado",
                marcas=[f"inyeccion:{r}" for r in reglas],
            )
        )

    alcance = fuera_de_alcance(texto)
    if alcance is True:
        resultados.append(
            _decidir(
                False,
                "fuera de alcance: no trata de empleo ni formación",
                resumen,
                RESPUESTA_FUERA_DE_ALCANCE,
                respuesta=RESPUESTA_FUERA_DE_ALCANCE,
            )
        )
    elif alcance is None:
        resultados.append(
            _decidir(
                True,
                "alcance dudoso: sin palabras de empleo ni formación; se deja pasar",
                resumen,
                "marcado",
                marcas=["alcance_dudoso"],
            )
        )
    return resultados


def evaluar_entrada(entrada: dict[str, Any]) -> list[ResultadoGuardrail]:
    """Revisa una entrada cruda con claves opcionales `archivo` ({nombre, tamano_bytes, contenido}),
    `texto`, `edad` y `salario`. Los archivos van primero: si se rechaza uno, no se mira el resto."""
    resultados: list[ResultadoGuardrail] = []
    archivo = entrada.get("archivo")
    if archivo is not None:
        contenido = archivo.get("contenido")
        tamano = archivo.get("tamano_bytes")
        if tamano is None:
            tamano = len(contenido) if contenido is not None else 0
        resultados.append(validar_archivo(archivo.get("nombre", ""), tamano, contenido))
        if not resultados[-1].ok:
            return resultados
    if entrada.get("edad") is not None or entrada.get("salario") is not None:
        resultados.append(validar_rangos(entrada.get("edad"), entrada.get("salario")))
    if entrada.get("texto") is not None:
        resultados.extend(revisar_texto(entrada["texto"]))
    if not resultados:
        resultados.append(
            _decidir(False, "entrada vacía", "sin archivo, texto ni datos", "rechazado")
        )
    return resultados


def limpiar_entrada(valor: Any) -> Any:
    """Copia de `valor` con cédula, teléfono y dirección exacta quitados de cada texto.

    Recorre dicts, listas y tuplas; los bytes, números y objetos se dejan igual, y las claves
    de `CLAVES_SIN_LIMPIAR` (rutas) también. El grafo (#10) la llama sobre el estado inicial
    antes de `graph.invoke`, así el dato crudo nunca se guarda en el checkpoint.
    """
    if isinstance(valor, str):
        return quitar_pii(valor)
    if isinstance(valor, dict):
        return {
            k: v if k in CLAVES_SIN_LIMPIAR else limpiar_entrada(v)
            for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return type(valor)(limpiar_entrada(v) for v in valor)
    return valor


def ocultar_trazas_langsmith() -> None:
    """Pide a LangSmith que no guarde entradas ni salidas de las trazas. Respeta un valor
    ya puesto en `.env` o en el entorno."""
    os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "true")
    os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "true")


def nodo_guardrails(estado: EstadoPuente) -> dict:
    """Nodo del grafo. Lee `estado["entrada"]` y devuelve las listas completas de `decisiones`
    y `errores`, más `guardrails` con el veredicto para el router:
    {"ok", "marcas", "motivos", "respuesta"}. Un rechazo deja una línea "guardrails: ..." en errores.

    Vuelve a limpiar la entrada por si el grafo no lo hizo: los resúmenes de `decisiones`
    nunca llevan PII."""
    entrada = limpiar_entrada(estado.get("entrada") or {})  # type: ignore[call-overload]
    resultados = evaluar_entrada(entrada)
    rechazos = [r for r in resultados if not r.ok]
    respuesta = next((r.respuesta for r in resultados if r.respuesta), None)
    return {
        "decisiones": [
            *estado.get("decisiones", []),
            *(r.decision for r in resultados),
        ],
        "errores": [
            *estado.get("errores", []),
            *(f"{AGENTE}: {r.motivo}" for r in rechazos),
        ],
        "guardrails": {
            "ok": not rechazos,
            "marcas": [m for r in resultados for m in r.marcas],
            "motivos": [r.motivo for r in rechazos],
            "respuesta": respuesta,
        },
    }
