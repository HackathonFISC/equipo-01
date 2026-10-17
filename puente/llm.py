"""Capa común de LLM: la única puerta al modelo para todos los agentes. Issue #45.

Ningún agente instancia un cliente por su cuenta: llama a `generar_estructurado` (salida validada con un
esquema Pydantic) o a `generar_texto`. El proveedor principal es Claude (SDK `anthropic`); Gemini queda de
respaldo y se elige por entorno, sin tocar código.

Configuración por variables de entorno, sin tocar código. Se leen en cada llamada, pero `.env` se carga una sola vez
por proceso y el cliente queda en caché: tras editar `.env`, reinicie el tablero o la CLI.
- `LLM_PROVEEDOR`: `anthropic` (por defecto) o `gemini`.
- `PUENTE_MODELO_LLM`: `claude-opus-5-5` con anthropic, `gemini-2.5-flash` con gemini.
- `LLM_MODO`: `vivo` (por defecto), `grabar` (llama y guarda la respuesta) o `reproducir` (solo lee grabaciones;
  sin red ni claves, es el modo del CI y de la demo congelada).
- `LLM_DIR_GRABACIONES`: carpeta de grabaciones (por defecto `data/grabaciones`).
- `ANTHROPIC_WORKSPACE_ID` (opcional): workspace al que se carga el gasto, para claves que no son de un workspace.
- `LLM_PROVEEDOR_RESPALDO` y `LLM_MODELO_RESPALDO` (opcionales): un solo reintento si el modelo rechaza (refusal).
- `PUENTE_LLM_FALLBACKS` (`false` por defecto): respaldo del servidor de Anthropic (beta). Sin verificar con clave.
- `PUENTE_LLM_TOPE_USD`: límite estimado por proceso para llamadas reales; se comprueba antes y después de cada
  respuesta. El costo exacto de una llamada solo se conoce cuando el proveedor devuelve su uso.

La salida estructurada usa `messages.create` con `output_config.format` y valida el JSON aquí, después de revisar
`stop_reason`: así un corte por `max_tokens` o un rechazo con texto no llegan como un error de esquema. El esquema que
se envía es el de `anthropic.transform_schema`, simplificado por `esquema_para_api`: todo obligatorio, `$defs` en línea,
sin `title` ni `default`. La API limita la suma de parámetros opcionales (24) y con unión (16) de los esquemas
estrictos, y además el tamaño de la gramática compilada; con todo opcional, `OfertaExtraida` daba HTTP 400 «Schema is
too complex» (#137). Si aun así la API rechaza el esquema por complejidad, se pide en modo no estricto: el esquema va en
el sistema y la respuesta se valida con el modelo Pydantic. Una salida que no valida se pide una vez más.

Reglas del API de Claude Opus 5.5 que este módulo respeta: sin `temperature`/`top_p`/`top_k`, sin `tool_choice`
forzado, sin `thinking` (el razonamiento no se apaga), sin prefill; el esfuerzo va en `output_config`.

Todo error llega como `ErrorLLM` con un mensaje en español; los nodos lo capturan y lo pasan a `estado["errores"]`.
Nunca se registra, imprime ni guarda la clave de API: el SDK la lee del entorno.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import os
import re
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Ruta, o (mime, bytes) para archivos que no están en disco.
Archivo = Path | str | tuple[str, bytes]

PROVEEDORES = ("anthropic", "gemini")
MODOS = ("vivo", "grabar", "reproducir")
MODELO_POR_DEFECTO = {"anthropic": "claude-opus-5-5", "gemini": "gemini-2.5-flash"}
RAIZ_REPO = Path(__file__).resolve().parent.parent
DIR_GRABACIONES_POR_DEFECTO = RAIZ_REPO / "data" / "grabaciones"

# US$ por millón de tokens (entrada, salida). Modelos sin precio conocido cuentan costo 0.
PRECIOS: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
}

# Respaldo del servidor ante rechazos (beta). Solo se verificó que la firma de `client.beta.messages.create`
# acepta `fallbacks` y `betas`; que funcione con salida estructurada NO está comprobado con clave real.
# Por eso queda apagado: se activa con PUENTE_LLM_FALLBACKS=true una vez que un humano lo verifique.
FALLBACKS_SERVIDOR_ACTIVO = False
BETA_FALLBACKS = "server-side-fallback-2026-07-01"

# Cortos para no colgar la demo: en el peor caso (2 intentos de 40 s) un nodo espera menos de 90 s.
TIMEOUT_SEGUNDOS = 40.0
TIMEOUT_CONEXION_SEGUNDOS = 5.0
MAX_REINTENTOS = 1

MIME_IMAGEN = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MIME_IMAGENES_VALIDOS = frozenset(MIME_IMAGEN.values())
MIME_PDF = "application/pdf"


class ErrorLLM(RuntimeError):
    """Fallo de la capa de LLM, con el mensaje listo para mostrar al orientador.

    `causa`: refusal, max_tokens, timeout, conexion, limite, estado_http, salida_invalida, sin_salida,
    sin_clave, grabacion_faltante, grabacion_invalida, proveedor_desconocido, modo_desconocido, archivo, mime, esquema,
    error_proveedor.
    """

    def __init__(self, mensaje: str, *, causa: str = "error_proveedor", proveedor: str = "", modelo: str = "", agente: str = ""):
        super().__init__(mensaje)
        self.causa = causa
        self.proveedor = proveedor
        self.modelo = modelo
        self.agente = agente


# ---------------------------------------------------------------- configuración


def _modelo_para(proveedor: str, valor: str | None) -> str:
    """Modelo pedido, o el de por defecto del proveedor si falta o es de la otra familia (claude con gemini y al revés)."""
    modelo = (valor or "").strip()
    familia_ajena = "claude" if proveedor == "gemini" else "gemini"
    if not modelo or modelo.lower().startswith(familia_ajena):
        return MODELO_POR_DEFECTO[proveedor]
    return modelo


def configuracion() -> dict[str, str]:
    """Configuración vigente leída del entorno. Valida proveedor y modo."""
    _cargar_dotenv()  # antes de cualquier os.getenv: el .env rige también la primera llamada (tablero, CLI)
    proveedor = (os.getenv("LLM_PROVEEDOR") or "anthropic").strip().lower()
    if proveedor not in PROVEEDORES:
        raise ErrorLLM(
            f"LLM_PROVEEDOR='{proveedor}' no es válido; use uno de: {', '.join(PROVEEDORES)}.",
            causa="proveedor_desconocido",
            proveedor=proveedor,
        )
    modo = (os.getenv("LLM_MODO") or "vivo").strip().lower()
    if modo not in MODOS:
        raise ErrorLLM(
            f"LLM_MODO='{modo}' no es válido; use uno de: {', '.join(MODOS)}.", causa="modo_desconocido", proveedor=proveedor
        )
    modelo = _modelo_para(proveedor, os.getenv("PUENTE_MODELO_LLM"))
    directorio = (os.getenv("LLM_DIR_GRABACIONES") or "").strip() or str(DIR_GRABACIONES_POR_DEFECTO)
    return {"proveedor": proveedor, "modelo": modelo, "modo": modo, "directorio": directorio}


def _respaldo(proveedor_principal: str) -> tuple[str, str] | None:
    """(proveedor, modelo) de respaldo ante un refusal, o None si no hay o es el mismo proveedor."""
    proveedor = (os.getenv("LLM_PROVEEDOR_RESPALDO") or "").strip().lower()
    if not proveedor or proveedor == proveedor_principal:
        return None
    if proveedor not in PROVEEDORES:
        raise ErrorLLM(
            f"LLM_PROVEEDOR_RESPALDO='{proveedor}' no es válido; use uno de: {', '.join(PROVEEDORES)}.",
            causa="proveedor_desconocido",
            proveedor=proveedor,
        )
    return proveedor, _modelo_para(proveedor, os.getenv("LLM_MODELO_RESPALDO"))


def _fallbacks_servidor() -> bool:
    valor = os.getenv("PUENTE_LLM_FALLBACKS")
    if valor is None or not valor.strip():
        return FALLBACKS_SERVIDOR_ACTIVO
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes"}


# ---------------------------------------------------------------- uso y costo

_candado = threading.Lock()
_uso: dict[str, dict[str, Any]] = {}


def _costo(modelo: str, entrada: int, salida: int) -> float:
    precio_entrada, precio_salida = PRECIOS.get(modelo, (0.0, 0.0))
    return entrada * precio_entrada / 1e6 + salida * precio_salida / 1e6


def _tope_usd() -> float | None:
    valor = (os.getenv("PUENTE_LLM_TOPE_USD") or "").strip()
    if not valor:
        return None
    try:
        tope = float(valor)
    except ValueError as exc:
        raise ErrorLLM("PUENTE_LLM_TOPE_USD debe ser un número positivo.", causa="configuracion") from exc
    if not math.isfinite(tope) or tope <= 0:
        raise ErrorLLM("PUENTE_LLM_TOPE_USD debe ser un número positivo.", causa="configuracion")
    return tope


def _verificar_tope(agente: str, modelo: str) -> None:
    tope = _tope_usd()
    if tope is None:
        return
    with _candado:
        gastado = sum(fila["costo_usd"] for fila in _uso.values())
    if gastado >= tope:
        raise ErrorLLM(
            f"Se alcanzó el tope estimado de US${tope:.2f} de esta sesión; no se llama al modelo.",
            causa="tope_gasto", modelo=modelo, agente=agente,
        )


def _registrar(agente: str, modelo: str, entrada: int, salida: int, *, reproducida: bool = False) -> None:
    """Suma una llamada al contador del agente. Reproducir cuenta la llamada con tokens y costo 0."""
    with _candado:
        fila = _uso.setdefault(
            agente, {"llamadas": 0, "reproducidas": 0, "tokens_entrada": 0, "tokens_salida": 0, "costo_usd": 0.0}
        )
        fila["llamadas"] += 1
        if reproducida:
            fila["reproducidas"] += 1
            return
        fila["tokens_entrada"] += entrada
        fila["tokens_salida"] += salida
        fila["costo_usd"] += _costo(modelo, entrada, salida)
        tope = _tope_usd()
        if tope is not None and sum(f["costo_usd"] for f in _uso.values()) > tope:
            raise ErrorLLM(
                f"La última llamada superó el tope estimado de US${tope:.2f} de esta sesión.",
                causa="tope_gasto", modelo=modelo, agente=agente,
            )


def uso_por_agente() -> dict[str, dict[str, Any]]:
    """Copia del contador: por agente, llamadas, reproducidas, tokens de entrada y salida y costo estimado en US$."""
    with _candado:
        return {agente: dict(fila) for agente, fila in _uso.items()}


def reiniciar_uso() -> None:
    with _candado:
        _uso.clear()


# ---------------------------------------------------------------- archivos


def _leer_archivos(archivos: Sequence[Archivo] | None, *, proveedor: str, modelo: str, agente: str) -> list[tuple[str, bytes]]:
    """Resuelve cada archivo a (mime, bytes) y rechaza lo que el modelo no recibe (audio u otros)."""
    lista = list(archivos or [])
    contexto = {"proveedor": proveedor, "modelo": modelo, "agente": agente}
    resueltos: list[tuple[str, bytes]] = []
    for i, archivo in enumerate(lista, start=1):
        # El nombre del archivo puede traer datos personales (CV_Nombre_Apellido.pdf): los mensajes usan la posición.
        cual = f"archivo {i} de {len(lista)}"
        if isinstance(archivo, tuple):
            if len(archivo) != 2 or not isinstance(archivo[1], (bytes, bytearray, memoryview)):
                raise ErrorLLM(
                    f"El {cual} para el agente {agente} debe ser una ruta o una tupla (mime, bytes).",
                    causa="archivo",
                    **contexto,
                )
            mime, datos = str(archivo[0]).lower(), bytes(archivo[1])
        else:
            try:
                ruta = Path(archivo)
                sufijo = ruta.suffix.lower()
                mime = MIME_IMAGEN.get(sufijo) or (MIME_PDF if sufijo == ".pdf" else (mimetypes.guess_type(ruta.name)[0] or ""))
                datos = ruta.read_bytes()
            except (OSError, TypeError, ValueError) as exc:
                raise ErrorLLM(
                    f"No se pudo leer el {cual} para el agente {agente}: {type(exc).__name__}.",
                    causa="archivo",
                    **contexto,
                ) from exc
        if mime not in MIME_IMAGENES_VALIDOS and mime != MIME_PDF:
            motivo = "Claude no recibe audio, ver #55" if mime.startswith("audio/") else "solo se aceptan imágenes y PDF"
            raise ErrorLLM(
                f"El {cual} es de tipo '{mime or 'desconocido'}', no admitido para el agente {agente}: {motivo}.",
                causa="mime",
                **contexto,
            )
        resueltos.append((mime, datos))
    return resueltos


def _bloques_anthropic(prompt: str, archivos: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    """Archivos primero (image/document en base64) y el texto al final, como pide la API."""
    bloques: list[dict[str, Any]] = []
    for mime, datos in archivos:
        tipo = "document" if mime == MIME_PDF else "image"
        bloques.append(
            {"type": tipo, "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(datos).decode("ascii")}}
        )
    bloques.append({"type": "text", "text": prompt})
    return bloques


def _mensajes_gemini(prompt: str, sistema: str | None, archivos: list[tuple[str, bytes]]) -> list[tuple[str, Any]]:
    mensajes: list[tuple[str, Any]] = []
    if sistema:
        mensajes.append(("system", sistema))
    if not archivos:
        mensajes.append(("human", prompt))
        return mensajes
    contenido: list[dict[str, Any]] = []
    for mime, datos in archivos:
        codificado = base64.b64encode(datos).decode("ascii")
        if mime == MIME_PDF:
            contenido.append({"type": "file", "source_type": "base64", "mime_type": mime, "data": codificado})
        else:
            contenido.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{codificado}"}})
    contenido.append({"type": "text", "text": prompt})
    mensajes.append(("human", contenido))
    return mensajes


# ---------------------------------------------------------------- grabaciones


def _normalizar(texto: str | None) -> str:
    """Fin de línea único para que el hash sea igual en Windows y Linux."""
    return (texto or "").replace("\r\n", "\n").replace("\r", "\n")


def clave_grabacion(
    proveedor: str,
    modelo: str,
    sistema: str | None,
    prompt: str,
    esquema: type[BaseModel] | None,
    archivos_bytes: Sequence[bytes],
) -> str:
    """sha256 hexadecimal de proveedor + modelo + sistema + prompt + esquema + bytes de los archivos."""
    carga = {
        "proveedor": proveedor,
        "modelo": modelo,
        "sistema": _normalizar(sistema),
        "prompt": _normalizar(prompt),
        "esquema": esquema.model_json_schema() if esquema is not None else None,
        "archivos": [hashlib.sha256(datos).hexdigest() for datos in archivos_bytes],
    }
    serializado = json.dumps(carga, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _ruta_grabacion(directorio: str, clave: str) -> Path:
    return Path(directorio) / f"{clave}.json"


def _ruta_visible(ruta: Path) -> str:
    """Ruta para mensajes: relativa al repo si está dentro; si no, solo el nombre (sin la ruta absoluta local)."""
    try:
        return ruta.resolve().relative_to(RAIZ_REPO).as_posix()
    except (OSError, ValueError):
        return ruta.name


def _guardar_grabacion(
    directorio: str,
    clave: str,
    *,
    proveedor: str,
    modelo: str,
    esquema: type[BaseModel] | None,
    agente: str,
    salida: Any,
    entrada_tokens: int,
    salida_tokens: int,
    atendido_por: dict[str, str] | None,
) -> None:
    registro: dict[str, Any] = {
        "hash": clave,
        "proveedor": proveedor,
        "modelo": modelo,
        "tipo": "estructurado" if esquema is not None else "texto",
        "agente": agente,
        "esquema": esquema.__name__ if esquema is not None else None,
        "salida": salida.model_dump(mode="json") if esquema is not None else salida,
        "uso": {"entrada": entrada_tokens, "salida": salida_tokens},
        "creado": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if atendido_por:
        registro["atendido_por"] = atendido_por
    ruta = _ruta_grabacion(directorio, clave)
    temporal = ruta.with_suffix(".json.tmp")
    try:
        texto = json.dumps(registro, ensure_ascii=False, indent=2) + "\n"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal.write_text(texto, encoding="utf-8", newline="\n")
        temporal.replace(ruta)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass
        raise ErrorLLM(
            f"No se pudo guardar la grabación {clave}.json para el agente {agente} ({type(exc).__name__}). "
            "La respuesta ya se cobró: revise LLM_DIR_GRABACIONES y vuelva a grabar.",
            causa="grabacion_invalida",
            proveedor=proveedor,
            modelo=modelo,
            agente=agente,
        ) from exc


def _leer_grabacion(directorio: str, clave: str, esquema: type[T] | None, *, proveedor: str, modelo: str, agente: str) -> Any:
    ruta = _ruta_grabacion(directorio, clave)
    contexto = {"proveedor": proveedor, "modelo": modelo, "agente": agente}
    if not ruta.is_file():
        raise ErrorLLM(
            f"Falta la grabación {clave} para el agente {agente} ({proveedor}/{modelo}). Esperada en {_ruta_visible(ruta)}. "
            "Grábela con LLM_MODO=grabar y una clave, o use LLM_MODO=vivo.",
            causa="grabacion_faltante",
            **contexto,
        )
    try:
        registro = json.loads(ruta.read_text(encoding="utf-8"))
        salida = registro["salida"]
        if esquema is not None:
            return esquema.model_validate(salida)
        if not isinstance(salida, str):
            raise TypeError("la salida grabada de un texto debe ser una cadena")
        return salida
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        raise ErrorLLM(
            f"La grabación {_ruta_visible(ruta)} está dañada o no coincide con el esquema ({type(exc).__name__}).",
            causa="grabacion_invalida",
            **contexto,
        ) from exc


# ---------------------------------------------------------------- proveedores

_clientes: dict[str, Any] = {}
_dotenv_cargado = False
_CLAVES = {"anthropic": ("ANTHROPIC_API_KEY",), "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY")}


def _cargar_dotenv() -> None:
    """Carga `.env` una sola vez sin pisar lo que ya esté en el entorno (los puntos de entrada no lo hacen)."""
    global _dotenv_cargado
    if _dotenv_cargado:
        return
    _dotenv_cargado = True
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:  # noqa: BLE001 - un .env ilegible no debe tumbar la llamada; faltará la clave y se avisa
        pass


def _cliente_por_defecto(proveedor: str, modelo: str, agente: str = "") -> Any:
    """Cliente real, creado de forma perezosa. Nunca se llama en modo reproducir.

    Comprueba que haya clave (sin leerla ni mostrarla) para devolver un ErrorLLM claro y no una excepción del SDK.
    """
    _cargar_dotenv()
    nombres = _CLAVES[proveedor]
    if not any((os.getenv(nombre) or "").strip() for nombre in nombres):
        raise ErrorLLM(
            f"Falta {nombres[0]} en .env para el agente {agente or 'desconocido'} ({proveedor}/{modelo}); "
            "use LLM_MODO=reproducir para la demo sin red.",
            causa="sin_clave",
            proveedor=proveedor,
            modelo=modelo,
            agente=agente,
        )
    with _candado:
        if proveedor == "anthropic":
            if "anthropic" not in _clientes:
                import anthropic

                # Una clave que no es de un workspace (sk-ant-usr-...) exige el encabezado del workspace al que se carga el gasto.
                espacio = (os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip()
                _clientes["anthropic"] = anthropic.Anthropic(
                    max_retries=MAX_REINTENTOS,
                    timeout=anthropic.Timeout(TIMEOUT_SEGUNDOS, connect=TIMEOUT_CONEXION_SEGUNDOS),
                    default_headers={"anthropic-workspace-id": espacio} if espacio else None,
                )
            return _clientes["anthropic"]
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=modelo, temperature=0, max_retries=MAX_REINTENTOS, timeout=TIMEOUT_SEGUNDOS)


def _error_anthropic(exc: Exception, proveedor: str, modelo: str, agente: str) -> ErrorLLM:
    """Traduce los errores tipados del SDK, de lo más específico a lo general."""
    import anthropic

    contexto = {"proveedor": proveedor, "modelo": modelo, "agente": agente}
    base = f"({proveedor}/{modelo}, agente {agente})"
    if isinstance(exc, anthropic.APITimeoutError):
        return ErrorLLM(f"El modelo no respondió a tiempo {base}. Intente de nuevo.", causa="timeout", **contexto)
    if isinstance(exc, anthropic.RateLimitError):
        return ErrorLLM(f"Se alcanzó el límite de uso del modelo {base}. Espere un momento e intente de nuevo.", causa="limite", **contexto)
    if isinstance(exc, anthropic.APIStatusError):
        pista = " Revise ANTHROPIC_API_KEY." if exc.status_code in (401, 403) else ""
        return ErrorLLM(f"El modelo respondió con error HTTP {exc.status_code} {base}.{pista}", causa="estado_http", **contexto)
    if isinstance(exc, anthropic.APIConnectionError):
        return ErrorLLM(f"No hubo conexión con el modelo {base}. Revise la red.", causa="conexion", **contexto)
    return ErrorLLM(f"Error inesperado del modelo {base}: {type(exc).__name__}.", causa="error_proveedor", **contexto)


def _revisar_stop_reason(respuesta: Any, proveedor: str, modelo: str, agente: str) -> None:
    """Antes de leer o validar el contenido: refusal y max_tokens son errores, no respuestas."""
    motivo = getattr(respuesta, "stop_reason", None)
    contexto = {"proveedor": proveedor, "modelo": modelo, "agente": agente}
    if motivo == "refusal":
        detalle = getattr(respuesta, "stop_details", None)
        categoria = getattr(detalle, "category", None)
        explicacion = getattr(detalle, "explanation", None)
        extra = f" Categoría: {categoria}." if categoria else ""
        extra += f" {explicacion}" if explicacion else ""
        raise ErrorLLM(f"Claude rechazó la solicitud ({proveedor}/{modelo}, agente {agente}).{extra}", causa="refusal", **contexto)
    if motivo == "max_tokens":
        raise ErrorLLM(
            f"La respuesta del modelo se cortó por límite de tokens ({proveedor}/{modelo}, agente {agente}); "
            "suba max_tokens o acorte la entrada.",
            causa="max_tokens",
            **contexto,
        )


def _uso_anthropic(respuesta: Any) -> tuple[int, int]:
    uso = getattr(respuesta, "usage", None)
    entrada = sum(
        int(getattr(uso, nombre, 0) or 0)
        for nombre in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return entrada, int(getattr(uso, "output_tokens", 0) or 0)


# ---------------------------------------------------------------- esquema para la salida estructurada (#137)

_RE_DEFAULT_EN_DESCRIPCION = re.compile(r"\s*\{default: [^{}]*\}\s*$")
INSTRUCCION_JSON_LIBRE = (
    "Responde únicamente con un objeto JSON válido que cumpla este JSON Schema, sin texto antes ni después y sin "
    "bloques de código. Incluye todas las propiedades; usa null cuando el dato no aparece.\n{esquema}"
)


class _EsquemaDemasiadoComplejo(Exception):
    """La API rechazó el esquema estricto por complejidad: se pide otra vez en modo no estricto."""


def esquema_para_api(esquema: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema que se envía en `output_config.format`, más simple que el de `anthropic.transform_schema`.

    - Todas las propiedades van en `required`: la API cuenta cada propiedad opcional contra su límite (24 por
      pedido) y cada una multiplica la gramática. Los campos que pueden faltar siguen admitiendo null, así que el
      modelo los manda como null y Pydantic aplica el mismo contrato que antes.
    - Los `$defs` van en línea (sin `$ref`), y se quitan `title`, `default` y la nota «{default: ...}» que
      `transform_schema` agrega a la descripción.

    No cambia el contrato de `generar_estructurado`: la respuesta se sigue validando con el modelo Pydantic.
    """
    import anthropic

    base = anthropic.transform_schema(esquema)
    definiciones = base.pop("$defs", {})

    def limpiar(nodo: Any, pila: tuple[str, ...] = ()) -> Any:
        if isinstance(nodo, list):
            return [limpiar(n, pila) for n in nodo]
        if not isinstance(nodo, dict):
            return nodo
        if "$ref" in nodo:
            nombre = str(nodo["$ref"]).rsplit("/", 1)[-1]
            if nombre in pila or nombre not in definiciones:
                raise ValueError(f"referencia no soportada en el esquema: {nodo['$ref']}")
            return limpiar(definiciones[nombre], (*pila, nombre))
        limpio: dict[str, Any] = {}
        for clave, valor in nodo.items():
            if clave in ("title", "default"):
                continue
            if clave == "description" and isinstance(valor, str):
                valor = _RE_DEFAULT_EN_DESCRIPCION.sub("", valor)
                if not valor:
                    continue
            if clave == "properties" and isinstance(valor, dict):
                limpio[clave] = {nombre: limpiar(prop, pila) for nombre, prop in valor.items()}
                continue
            limpio[clave] = limpiar(valor, pila)
        if isinstance(limpio.get("properties"), dict):
            limpio["required"] = list(limpio["properties"])
            limpio["additionalProperties"] = False
        return limpio

    return limpiar(base)


def _es_esquema_complejo(exc: Exception) -> bool:
    """HTTP 400 por un esquema que la API no puede compilar («Schema is too complex for compilation»)."""
    cuerpo = getattr(exc, "body", None)
    mensaje = str(getattr(exc, "message", "") or exc)
    if isinstance(cuerpo, dict):
        error = cuerpo.get("error")
        mensaje += " " + str(error.get("message", "") if isinstance(error, dict) else error or "")
    mensaje = mensaje.lower()
    return "schema" in mensaje and ("complex" in mensaje or "compil" in mensaje)


def _json_de_texto(texto: str) -> str:
    """En modo no estricto el modelo puede envolver el JSON en un bloque de código: se toma el objeto JSON."""
    inicio, fin = texto.find("{"), texto.rfind("}")
    return texto[inicio : fin + 1] if 0 <= inicio < fin else texto


def _llamar_anthropic(
    cliente: Any,
    *,
    modelo: str,
    prompt: str,
    esquema: type[T] | None,
    archivos: list[tuple[str, bytes]],
    sistema: str | None,
    agente: str,
    effort: str,
    max_tokens: int,
    cache_sistema: bool,
) -> tuple[Any, int, int]:
    """Una llamada con salida estructurada, con dos reintentos acotados (#137).

    1. Si la API rechaza el esquema estricto por complejidad, se pide otra vez sin `output_config.format`: el esquema
       va en el sistema y la respuesta se valida igual con Pydantic.
    2. Si la respuesta no cumple el esquema, se pide una vez más en el mismo modo; si vuelve a fallar, `salida_invalida`.
    """
    argumentos_comunes = {
        "modelo": modelo,
        "prompt": prompt,
        "esquema": esquema,
        "archivos": archivos,
        "sistema": sistema,
        "agente": agente,
        "effort": effort,
        "max_tokens": max_tokens,
        "cache_sistema": cache_sistema,
    }
    estricto = True
    reintento_por_salida = False
    while True:
        try:
            return _pedir_anthropic(cliente, estricto=estricto, **argumentos_comunes)
        except _EsquemaDemasiadoComplejo:
            estricto = False
        except ErrorLLM as error:
            if esquema is None or error.causa != "salida_invalida" or reintento_por_salida:
                raise
            reintento_por_salida = True


def _pedir_anthropic(
    cliente: Any,
    *,
    estricto: bool,
    modelo: str,
    prompt: str,
    esquema: type[T] | None,
    archivos: list[tuple[str, bytes]],
    sistema: str | None,
    agente: str,
    effort: str,
    max_tokens: int,
    cache_sistema: bool,
) -> tuple[Any, int, int]:
    import anthropic

    config_salida: dict[str, Any] = {"effort": effort}
    texto_sistema = sistema
    if esquema is not None:
        try:
            esquema_api = esquema_para_api(esquema)
        except Exception as exc:  # noqa: BLE001 - un esquema que no se puede simplificar no debe salir como error crudo
            raise ErrorLLM(
                f"El esquema {esquema.__name__} no se puede usar con el modelo (agente {agente}): {type(exc).__name__}.",
                causa="esquema",
                proveedor="anthropic",
                modelo=modelo,
                agente=agente,
            ) from exc
        if estricto:
            config_salida["format"] = {"type": "json_schema", "schema": esquema_api}
        else:
            instruccion = INSTRUCCION_JSON_LIBRE.format(esquema=json.dumps(esquema_api, ensure_ascii=False))
            texto_sistema = f"{sistema}\n\n{instruccion}" if sistema else instruccion
    argumentos: dict[str, Any] = {
        "model": modelo,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": _bloques_anthropic(prompt, archivos)}],
        "output_config": config_salida,
    }
    if texto_sistema:
        argumentos["system"] = (
            [{"type": "text", "text": texto_sistema, "cache_control": {"type": "ephemeral"}}] if cache_sistema else texto_sistema
        )
    servicio = cliente.messages
    if _fallbacks_servidor():
        argumentos["fallbacks"] = "default"
        argumentos["betas"] = [BETA_FALLBACKS]
        servicio = cliente.beta.messages
    # Se usa create y no parse: parse valida el JSON dentro de la llamada y un corte por max_tokens o un rechazo
    # con texto llegarían como ValidationError, sin dejar leer stop_reason. Aquí se lee primero y se valida después.
    try:
        respuesta = servicio.create(**argumentos)
    except ErrorLLM:
        raise
    except anthropic.BadRequestError as exc:
        if estricto and esquema is not None and _es_esquema_complejo(exc):
            raise _EsquemaDemasiadoComplejo() from exc
        raise _error_anthropic(exc, "anthropic", modelo, agente) from exc
    except anthropic.AnthropicError as exc:
        raise _error_anthropic(exc, "anthropic", modelo, agente) from exc
    # El uso se cuenta en cuanto llega la respuesta, antes de revisar stop_reason o validar: un corte por max_tokens,
    # un rechazo o una salida inválida también consumen tokens y cuestan dinero. Aquí y solo aquí, para no contar dos veces.
    entrada, salida = _uso_anthropic(respuesta)
    _registrar(agente, modelo, entrada, salida)
    _revisar_stop_reason(respuesta, "anthropic", modelo, agente)
    bloques = getattr(respuesta, "content", None) or []
    texto = "".join(getattr(b, "text", "") for b in bloques if getattr(b, "type", None) == "text")
    if esquema is None:
        return texto, entrada, salida
    contexto = {"proveedor": "anthropic", "modelo": modelo, "agente": agente}
    if not texto.strip():
        raise ErrorLLM(f"El modelo no devolvió una salida estructurada ({modelo}, agente {agente}).", causa="sin_salida", **contexto)
    try:
        return esquema.model_validate_json(texto if estricto else _json_de_texto(texto)), entrada, salida
    except ValidationError as exc:
        raise ErrorLLM(
            f"La respuesta del modelo no cumple el esquema {esquema.__name__} ({modelo}, agente {agente}).",
            causa="salida_invalida",
            **contexto,
        ) from exc


def _texto_gemini(contenido: Any) -> str:
    if isinstance(contenido, str):
        return contenido
    partes = []
    for parte in contenido or []:
        partes.append(parte if isinstance(parte, str) else (parte.get("text", "") if isinstance(parte, dict) else ""))
    return "".join(partes)


def _llamar_gemini(
    cliente: Any,
    *,
    modelo: str,
    prompt: str,
    esquema: type[T] | None,
    archivos: list[tuple[str, bytes]],
    sistema: str | None,
    agente: str,
) -> tuple[Any, int, int]:
    mensajes = _mensajes_gemini(prompt, sistema, archivos)
    contexto = {"proveedor": "gemini", "modelo": modelo, "agente": agente}
    try:
        if esquema is not None:
            resultado = cliente.with_structured_output(esquema).invoke(mensajes)
            validado = resultado if isinstance(resultado, esquema) else esquema.model_validate(resultado)
            _registrar(agente, modelo, 0, 0)  # la salida estructurada de Gemini no trae uso: se cuenta la llamada
            return validado, 0, 0
        respuesta = cliente.invoke(mensajes)
    except ErrorLLM:
        raise
    except ValidationError as exc:
        raise ErrorLLM(
            f"La respuesta de Gemini no cumple el esquema {esquema.__name__ if esquema else ''} ({modelo}, agente {agente}).",
            causa="salida_invalida",
            **contexto,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del proveedor se reporta sin colgar el grafo
        raise ErrorLLM(
            f"Error de Gemini ({modelo}, agente {agente}): {type(exc).__name__}.", causa="error_proveedor", **contexto
        ) from exc
    metadatos = getattr(respuesta, "usage_metadata", None) or {}
    entrada = int(metadatos.get("input_tokens", 0) or 0) if isinstance(metadatos, dict) else 0
    salida = int(metadatos.get("output_tokens", 0) or 0) if isinstance(metadatos, dict) else 0
    _registrar(agente, modelo, entrada, salida)
    return _texto_gemini(getattr(respuesta, "content", "")), entrada, salida


# ---------------------------------------------------------------- despacho


def _despachar(
    prompt: str,
    esquema: type[T] | None,
    archivos: Sequence[Archivo] | None,
    *,
    sistema: str | None,
    agente: str,
    effort: str,
    max_tokens: int,
    cliente: Any | None,
    cliente_respaldo: Any | None,
    cache_sistema: bool,
) -> Any:
    cfg = configuracion()
    proveedor, modelo, modo, directorio = cfg["proveedor"], cfg["modelo"], cfg["modo"], cfg["directorio"]
    datos = _leer_archivos(archivos, proveedor=proveedor, modelo=modelo, agente=agente)
    bytes_archivos = [contenido for _, contenido in datos]
    try:
        clave = clave_grabacion(proveedor, modelo, sistema, prompt, esquema, bytes_archivos)
    except Exception as exc:  # noqa: BLE001 - un esquema que no da JSON Schema no debe salir como error crudo
        raise ErrorLLM(
            f"El esquema {getattr(esquema, '__name__', esquema)!s} no se puede usar con el modelo (agente {agente}): "
            f"{type(exc).__name__}.",
            causa="esquema",
            proveedor=proveedor,
            modelo=modelo,
            agente=agente,
        ) from exc

    if modo == "reproducir":
        resultado = _leer_grabacion(directorio, clave, esquema, proveedor=proveedor, modelo=modelo, agente=agente)
        _registrar(agente, modelo, 0, 0, reproducida=True)
        return resultado

    def intentar(prov: str, mod: str, cli: Any | None) -> tuple[Any, int, int]:
        try:
            _verificar_tope(agente, mod)
            cli = cli if cli is not None else _cliente_por_defecto(prov, mod, agente)
            if prov == "anthropic":
                return _llamar_anthropic(
                    cli,
                    modelo=mod,
                    prompt=prompt,
                    esquema=esquema,
                    archivos=datos,
                    sistema=sistema,
                    agente=agente,
                    effort=effort,
                    max_tokens=max_tokens,
                    cache_sistema=cache_sistema,
                )
            return _llamar_gemini(cli, modelo=mod, prompt=prompt, esquema=esquema, archivos=datos, sistema=sistema, agente=agente)
        except ErrorLLM:
            raise
        except Exception as exc:  # noqa: BLE001 - todo error llega como ErrorLLM, sin datos del entorno ni de la clave
            raise ErrorLLM(
                f"Error inesperado del modelo ({prov}/{mod}, agente {agente}): {type(exc).__name__}.",
                causa="error_proveedor",
                proveedor=prov,
                modelo=mod,
                agente=agente,
            ) from exc

    atendido_por: dict[str, str] | None = None
    try:
        resultado, entrada, salida = intentar(proveedor, modelo, cliente)
    except ErrorLLM as error:
        respaldo = _respaldo(proveedor) if error.causa == "refusal" else None
        if respaldo is None:
            raise
        prov_r, mod_r = respaldo
        resultado, entrada, salida = intentar(prov_r, mod_r, cliente_respaldo)
        atendido_por = {"proveedor": prov_r, "modelo": mod_r}

    # El uso ya lo registró cada proveedor al recibir la respuesta (también cuando falló); aquí no se vuelve a sumar.
    if modo == "grabar":
        # Se guarda bajo la clave de lo pedido (proveedor principal) para que reproducir lo encuentre.
        _guardar_grabacion(
            directorio,
            clave,
            proveedor=proveedor,
            modelo=modelo,
            esquema=esquema,
            agente=agente,
            salida=resultado,
            entrada_tokens=entrada,
            salida_tokens=salida,
            atendido_por=atendido_por,
        )
    return resultado


# ---------------------------------------------------------------- API pública


def generar_estructurado(
    prompt: str,
    esquema: type[T],
    archivos: Sequence[Archivo] | None = None,
    *,
    sistema: str | None = None,
    agente: str = "desconocido",
    effort: str = "low",
    max_tokens: int = 4096,
    cliente: Any | None = None,
    cliente_respaldo: Any | None = None,
    cache_sistema: bool = False,
) -> T:
    """Llama al modelo y devuelve una instancia validada de `esquema`.

    `archivos`: rutas o (mime, bytes); imágenes (jpeg, png, gif, webp) y PDF. `cliente` y `cliente_respaldo`
    se inyectan en pruebas (doble que imita `messages.create`, o `with_structured_output` con gemini).
    `cache_sistema=True` marca el `sistema` con cache_control cuando es largo y fijo.
    """
    return _despachar(
        prompt,
        esquema,
        archivos,
        sistema=sistema,
        agente=agente,
        effort=effort,
        max_tokens=max_tokens,
        cliente=cliente,
        cliente_respaldo=cliente_respaldo,
        cache_sistema=cache_sistema,
    )


def generar_texto(
    prompt: str,
    *,
    sistema: str | None = None,
    agente: str = "desconocido",
    effort: str = "low",
    max_tokens: int = 2048,
    cliente: Any | None = None,
    cliente_respaldo: Any | None = None,
    cache_sistema: bool = False,
) -> str:
    """Llama al modelo y devuelve el texto libre (para explicaciones y mensajes)."""
    return _despachar(
        prompt,
        None,
        None,
        sistema=sistema,
        agente=agente,
        effort=effort,
        max_tokens=max_tokens,
        cliente=cliente,
        cliente_respaldo=cliente_respaldo,
        cache_sistema=cache_sistema,
    )
