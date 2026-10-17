"""Carga de data/taxonomia.csv y mapeo de nombres o sinónimos a skill_id. Issue #5.

Interfaz acordada con #6 (Perfil): `cargar_taxonomia` y `normalizar_habilidad`.
`normalizar_texto` se expone también porque la deduplicación de ofertas usa la misma
normalización. Sin LLM: misma entrada, misma salida.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

RUTA_TAXONOMIA = Path("data/taxonomia.csv")
_ESPACIOS = re.compile(r"\s+")


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sin acentos ni diéresis (ñ -> n) y con un solo espacio entre palabras."""
    descompuesto = unicodedata.normalize("NFKD", texto.casefold())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", sin_acentos).strip()


def cargar_taxonomia(ruta: str | Path = RUTA_TAXONOMIA) -> dict[str, dict]:
    """Lee el CSV (`skill_id, nombre_es, sinonimos, uri_esco`; sinónimos separados por `|`).

    Devuelve `{skill_id: {"nombre_es": str, "sinonimos": list[str], "uri_esco": str}}`.
    Las filas sin `skill_id` se ignoran.
    """
    taxonomia: dict[str, dict] = {}
    with open(ruta, encoding="utf-8", newline="") as archivo:
        for fila in csv.DictReader(archivo):
            skill_id = (fila.get("skill_id") or "").strip()
            if not skill_id:
                continue
            sinonimos = [s.strip() for s in (fila.get("sinonimos") or "").split("|") if s.strip()]
            taxonomia[skill_id] = {
                "nombre_es": (fila.get("nombre_es") or "").strip(),
                "sinonimos": sinonimos,
                "uri_esco": (fila.get("uri_esco") or "").strip(),
            }
    return taxonomia


def _indice(taxonomia: dict[str, dict]) -> dict[str, str]:
    """Texto normalizado -> skill_id. El propio skill_id también cuenta como nombre."""
    indice: dict[str, str] = {}
    for skill_id, datos in taxonomia.items():
        for nombre in (skill_id, skill_id.replace("_", " "), datos.get("nombre_es", ""), *datos.get("sinonimos", [])):
            clave = normalizar_texto(nombre)
            if clave:
                indice.setdefault(clave, skill_id)
    return indice


def normalizar_habilidad(texto: str, taxonomia: dict[str, dict]) -> str | None:
    """Devuelve el skill_id cuyo nombre, sinónimo o id coincide tras normalizar; `None` si no existe."""
    clave = normalizar_texto(texto)
    if not clave:
        return None
    return _indice(taxonomia).get(clave)


# --- Coincidencia aproximada para las habilidades que Mercado lee de una oferta (#153, #154, #155) ---

# Palabras que no distinguen una habilidad de otra: «registro de datos» y «registro datos» son lo mismo.
_PALABRAS_VACIAS = frozenset("a al con de del e el en la las lo los o para por su sus u un una unos unas y".split())
# Terminaciones que se quitan para comparar raíces, la más larga primero: plural («ventas» y «venta»),
# infinitivo y sustantivo («archivar» y «archivo») y sustantivo en -ción («verificación» y «verificar»).
_TERMINACIONES = ("aciones", "iciones", "ciones", "siones", "acion", "icion", "cion", "sion",
                  "es", "ar", "er", "ir", "os", "as", "o", "a", "e", "s")
_RAIZ_MINIMA = 3
_PALABRA = re.compile(r"[a-z0-9]+")


def _raiz(palabra: str) -> str:
    """Raíz de una palabra ya normalizada; si quitar la terminación la deja muy corta, queda igual."""
    for terminacion in _TERMINACIONES:
        if palabra.endswith(terminacion) and len(palabra) - len(terminacion) >= _RAIZ_MINIMA:
            return palabra[: -len(terminacion)]
    return palabra


def _raices(texto: str) -> tuple[str, ...]:
    """Raíces de las palabras con contenido, en orden: «Recibir mercancías» -> ('recib', 'mercanci')."""
    return tuple(_raiz(p) for p in _PALABRA.findall(normalizar_texto(texto)) if p not in _PALABRAS_VACIAS)


def _indice_raices(taxonomia: dict[str, dict]) -> dict[tuple[str, ...], str | None]:
    """Raíces de cada nombre, sinónimo o id -> skill_id; None si dos habilidades comparten las mismas raíces."""
    indice: dict[tuple[str, ...], str | None] = {}
    for skill_id, datos in taxonomia.items():
        for nombre in (skill_id, skill_id.replace("_", " "), datos.get("nombre_es", ""), *datos.get("sinonimos", [])):
            clave = _raices(nombre)
            if clave:
                indice[clave] = skill_id if indice.get(clave, skill_id) == skill_id else None
    return indice


def buscar_habilidades(texto: str, taxonomia: dict[str, dict]) -> list[str]:
    """skill_id que nombra una frase de habilidad de una oferta, comparando raíces. Sin LLM ni datos nuevos.

    Para Mercado, cuando `normalizar_habilidad` no da coincidencia exacta. Las frases del extractor rara vez
    copian la taxonomía letra por letra: «venta de mostrador» por «ventas de mostrador», «archivo de
    documentos» por «archivar documentos», «registro de datos de facturas» por «registro de datos».
    Se comparan raíces (sin palabras vacías, plural, infinitivo ni -ción) contra los nombres, sinónimos e
    ids de `taxonomia`:
    - la frase entera igual a un nombre, aunque sea de una sola palabra («Cobros» -> manejo_caja por «cobrar»);
    - o la frase contiene, seguidas, las raíces de un nombre de dos o más palabras; gana el más largo y no
      se solapan. Los de una sola palabra no cuentan dentro de una frase: «limpieza de cocina» no es cocina.
    Las raíces que comparten dos habilidades no se usan: ante la duda no se elige ninguna.
    Perfil no la usa: allí cada habilidad necesita evidencia literal y sigue con `normalizar_habilidad`.
    """
    raices = _raices(texto)
    if not raices:
        return []
    indice = _indice_raices(taxonomia)
    if len(raices) == 1 or raices in indice:
        skill_id = indice.get(raices)
        return [skill_id] if skill_id else []
    tramos = [
        (inicio, largo, indice[raices[inicio : inicio + largo]])
        for largo in range(len(raices) - 1, 1, -1)
        for inicio in range(len(raices) - largo + 1)
        if indice.get(raices[inicio : inicio + largo])
    ]
    usadas: set[int] = set()
    elegidos: list[tuple[int, str]] = []
    for inicio, largo, skill_id in tramos:  # ya van del más largo al más corto y de izquierda a derecha
        posiciones = set(range(inicio, inicio + largo))
        if not posiciones & usadas:
            usadas |= posiciones
            elegidos.append((inicio, skill_id))
    return list(dict.fromkeys(skill_id for _, skill_id in sorted(elegidos)))
