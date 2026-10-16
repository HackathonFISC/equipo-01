"""Requisitos discriminatorios: bloqueo (edad y sexo, Ley 11 de 2005) y alertas (buena presencia, foto, estado civil). Issue #5.

Solo regex sobre el texto original de la oferta, sin LLM: el modelo nunca decide si una
oferta es legal. Bloquean el límite de edad (art. 2) y la exigencia de sexo (art. 1).
"Buena presencia", foto y estado civil solo alertan, porque la ley no menciona la apariencia.
Cada hallazgo guarda la regla, la frase literal y si bloquea.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

EDAD = "edad"
SEXO = "sexo"
BUENA_PRESENCIA = "buena_presencia"
FOTO = "foto"
ESTADO_CIVIL = "estado_civil"

FUNDAMENTO = {
    EDAD: "límite de edad, Ley 11 de 2005 art. 2",
    SEXO: "exigencia de sexo, Ley 11 de 2005 art. 1",
    BUENA_PRESENCIA: "solo alerta: la ley no menciona la apariencia",
    FOTO: "solo alerta: la ley no menciona la apariencia",
    ESTADO_CIVIL: "solo alerta",
}
REGLAS_BLOQUEO = frozenset({EDAD, SEXO})

_ANOS = r"a(?:n|ñ)os?"
_PERSONA = r"(?:damas?|caballeros?|senoritas?|se(?:n|ñ)oritas?|varon(?:es)?|mujer(?:es)?|hombres?|chicas?|chicos?|muchachas?|muchachos?)"

# Rango numérico de edad: "18 a 25 años", "entre 18 y 25", "edad: 18-30". Se filtra después
# para no confundirlo con experiencia ("de 2 a 3 años de experiencia").
_RANGO = re.compile(
    rf"(?:\bentre\s+|\bde\s+)?\b(?P<a>\d{{2}})\s*(?:a|-|–|y|hasta)\s*(?P<b>\d{{2}})\b\s*(?:{_ANOS}\b(?:\s+de\s+edad\b)?)?"
)
_TOPE_EDAD = re.compile(
    rf"\b(?:menores?\s+de|no\s+mayor(?:es)?\s+de|mayores?\s+de|maximo|minimo|hasta|no\s+mas\s+de)\s+\d{{2}}\s*{_ANOS}\b"
    rf"|\bedad\s+(?:maxima|minima|limite|tope|requerida)\s*:?\s*(?:de\s+)?\d{{2}}\b(?:\s*{_ANOS}\b)?"
    rf"|\bedad\s*:?\s*(?:de\s+|entre\s+)?\d{{2}}\b(?:\s*(?:a|-|–|y|hasta)\s*\d{{2}}\b)?(?:\s*{_ANOS}\b)?"
    rf"|\b(?:jovenes|personas?)\s+(?:menores|mayores)\s+de\s+\d{{2}}\b"
)

_PATRONES: list[tuple[str, re.Pattern[str]]] = [
    (SEXO, re.compile(r"\b(?:sexo|genero)\s*:?\s*(?:femenino|masculino|mujer|hombre|f|m)\b")),
    (SEXO, re.compile(rf"\b(?:solo|solamente|unicamente|exclusivamente)\s+(?:para\s+)?(?:{_PERSONA}|personal\s+(?:femenino|masculino))\b")),
    (SEXO, re.compile(rf"\bse\s+(?:busca|buscan|solicita|solicitan|necesita|necesitan|requiere|requieren|contrata|contratan)\s+(?:una?\s+)?{_PERSONA}\b")),
    (SEXO, re.compile(r"\bpersonal\s+(?:femenino|masculino)\b")),
    (BUENA_PRESENCIA, re.compile(r"\b(?:buena|excelente|muy\s+buena)\s+presencia\b|\bpresencia\s+agradable\b|\bbuen\s+aspecto(?:\s+fisico)?\b")),
    (FOTO, re.compile(
        r"\b(?:hoja\s+de\s+vida|cv|curriculum(?:\s+vitae)?)\s+con\s+foto(?:grafia)?\b"
        r"|\b(?:adjunt\w*|envi\w*|incluir|anexar|con)\s+(?:una\s+|su\s+)?foto(?:grafia)?s?\b"
        r"|\bfoto(?:grafia)?\s+(?:reciente|tamano\s+carnet|tipo\s+carnet|de\s+cuerpo\s+entero)\b"
    )),
    (ESTADO_CIVIL, re.compile(r"\bestado\s+civil\b(?:\s*:?\s*(?:solter|casad|divorciad|viud)[oa]s?\b)?|\b(?:solter[oa]s?|casad[oa]s?|sin\s+hijos)\b")),
]


@dataclass(frozen=True)
class Hallazgo:
    regla: str
    frase: str
    bloquea: bool

    @property
    def fundamento(self) -> str:
        return FUNDAMENTO[self.regla]

    def como_texto(self) -> str:
        """Formato con que se guarda en `Oferta.alertas`: tipo, regla, fundamento y frase literal."""
        tipo = "bloqueo" if self.bloquea else "alerta"
        return f"{tipo} · {self.regla} · {self.fundamento} · «{self.frase}»"


def _plegar(texto: str) -> str:
    """Minúsculas y sin acentos carácter a carácter, conservando la longitud para recortar la frase original."""
    plegado = []
    for c in texto:
        base = "".join(b for b in unicodedata.normalize("NFKD", c.lower()) if not unicodedata.combining(b))
        plegado.append(base if len(base) == 1 else c.lower()[:1] or c)
    return "".join(plegado)


def _es_rango_de_edad(plegado: str, m: re.Match[str]) -> bool:
    a, b = int(m.group("a")), int(m.group("b"))
    if not (14 <= a < b <= 99):
        return False
    contexto_antes = plegado[max(0, m.start() - 30) : m.start()]
    contexto_despues = plegado[m.end() : m.end() + 25]
    if "experiencia" in contexto_antes or re.match(r"\s*(?:de\s+)?experiencia", contexto_despues):
        return False
    if re.search(r"\d{1,2}\s*[:h]\s*$", contexto_antes) or re.match(r"\s*(?:horas|hrs|h\b|:|lps|balboas|\$)", contexto_despues):
        return False
    tiene_anos = re.search(_ANOS, m.group(0)) is not None
    return tiene_anos or "edad" in contexto_antes


def _es_minimo_legal(frase: str) -> bool:
    """"Mayor de 18 años" o "edad mínima 18" es la edad legal para trabajar, no un límite discriminatorio."""
    numero = re.search(r"\d{2}", frase)
    return bool(numero) and int(numero.group()) <= 18 and re.search(r"mayor|minim", frase) is not None and "no mayor" not in frase


def _frase(original: str, inicio: int, fin: int) -> str:
    return " ".join(original[inicio:fin].split())


def revisar_texto(texto: str) -> list[Hallazgo]:
    """Aplica todas las reglas al texto original. Un hallazgo por frase distinta, en orden de aparición."""
    plegado = _plegar(texto)
    encontrados: list[tuple[int, Hallazgo]] = []
    vistos: set[tuple[str, int, int]] = set()

    def anotar(regla: str, inicio: int, fin: int) -> None:
        if any(r == regla and i < fin and inicio < f for r, i, f in vistos):
            return
        vistos.add((regla, inicio, fin))
        encontrados.append((inicio, Hallazgo(regla, _frase(texto, inicio, fin), regla in REGLAS_BLOQUEO)))

    for m in _TOPE_EDAD.finditer(plegado):
        if _es_minimo_legal(m.group(0)):
            continue
        anotar(EDAD, m.start(), m.end())
    for m in _RANGO.finditer(plegado):
        if _es_rango_de_edad(plegado, m):
            anotar(EDAD, m.start(), m.end())
    for regla, patron in _PATRONES:
        for m in patron.finditer(plegado):
            anotar(regla, m.start(), m.end())

    return [h for _, h in sorted(encontrados, key=lambda par: par[0])]


def esta_bloqueada(hallazgos: list[Hallazgo]) -> bool:
    return any(h.bloquea for h in hallazgos)
