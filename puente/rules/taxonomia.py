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
