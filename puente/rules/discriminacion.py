"""Requisitos discriminatorios: bloqueo (edad y sexo, Ley 11 de 2005) y alertas. Issues #5, #43 y #73.

Solo regex sobre el texto original de la oferta, sin LLM: el modelo nunca decide si una
oferta es legal. Bloquean el límite de edad (art. 2) y la exigencia de sexo (art. 1).
Las demás causales del art. 1 (raza, nacimiento, discapacidad, clase social, religión e
ideas políticas) y el puesto escrito solo en femenino ("cajera") alertan citando el artículo,
para que el orientador decida. "Buena presencia", foto y estado civil solo alertan, porque
la ley no menciona la apariencia. Cada hallazgo guarda la regla, la frase literal y si bloquea.
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
SEXO_IMPLICITO = "sexo_implicito"
RAZA = "raza"
NACIMIENTO = "nacimiento"
DISCAPACIDAD = "discapacidad"
CLASE_SOCIAL = "clase_social"
RELIGION = "religion"
IDEAS_POLITICAS = "ideas_politicas"

_ART_1 = "Ley 11 de 2005 art. 1"
FUNDAMENTO = {
    EDAD: "límite de edad, Ley 11 de 2005 art. 2",
    SEXO: f"exigencia de sexo, {_ART_1}",
    BUENA_PRESENCIA: "solo alerta: la ley no menciona la apariencia",
    FOTO: "solo alerta: la ley no menciona la apariencia",
    ESTADO_CIVIL: "solo alerta",
    SEXO_IMPLICITO: f"solo alerta: puesto escrito solo en femenino, posible preferencia de sexo, {_ART_1}",
    RAZA: f"solo alerta: raza, {_ART_1}",
    NACIMIENTO: f"solo alerta: nacimiento, {_ART_1}",
    DISCAPACIDAD: f"solo alerta: discapacidad, {_ART_1}",
    CLASE_SOCIAL: f"solo alerta: clase social, {_ART_1}",
    RELIGION: f"solo alerta: religión, {_ART_1}",
    IDEAS_POLITICAS: f"solo alerta: ideas políticas, {_ART_1}",
}
REGLAS_BLOQUEO = frozenset({EDAD, SEXO})

_ANOS = r"a(?:n|ñ)os?"
_PERSONA = r"(?:damas?|caballeros?|senoritas?|se(?:n|ñ)oritas?|varon(?:es)?|mujer(?:es)?|hombres?|chicas?|chicos?|muchachas?|muchachos?)"

# Rango numérico de edad: "18 a 25 años", "entre 18 y 25", "edad: 18-30". Se filtra después
# para no confundirlo con experiencia ("de 2 a 3 años de experiencia").
_RANGO = re.compile(
    rf"(?:\bentre\s+|\bde\s+)?\b(?P<a>\d{{2}})\s*(?:a|-|–|y|hasta)\s*(?P<b>\d{{2}})\b\s*(?:{_ANOS}\b(?:\s+de\s+edad\b)?)?"
)
# Tope de edad, máximo o mínimo. La edad mínima ("a partir de 26 años", "desde 25 años",
# "26 años en adelante") solo cuenta con "años" detrás o con "edad" delante, para no leer
# como edad "a partir de 2026", "desde 25 dólares" o "mínimo 26 horas".
_DESDE = r"(?:a\s+partir\s+de|desde)(?:\s+los)?"
_EN_ADELANTE = r"(?:\s+en\s+adelante\b)?"
_TOPE_EDAD = re.compile(
    rf"\b(?:menores?\s+de|no\s+mayor(?:es)?\s+de|mayores?\s+de|maximo|minimo|hasta|no\s+mas\s+de|{_DESDE})\s+\d{{2}}\s*{_ANOS}\b{_EN_ADELANTE}"
    rf"|\b\d{{2}}\s*{_ANOS}\s+en\s+adelante\b"
    rf"|\bedad\s+(?:maxima|minima|limite|tope|requerida)\s*:?\s*(?:de\s+)?\d{{2}}\b(?:\s*{_ANOS}\b)?"
    rf"|\bedad\s*:?\s*(?:de\s+|entre\s+|{_DESDE}\s+)?\d{{2}}\b(?:\s*(?:a|-|–|y|hasta)\s*\d{{2}}\b)?(?:\s*{_ANOS}\b)?{_EN_ADELANTE}"
    rf"|\b(?:jovenes|personas?)\s+(?:menores|mayores)\s+de\s+\d{{2}}\b"
)

_PATRONES: list[tuple[str, re.Pattern[str]]] = [
    (SEXO, re.compile(r"\b(?:sexo|genero)\s*:?\s*(?:femenino|masculino|mujer|hombre|f|m)\b")),
    (SEXO, re.compile(rf"\b(?:solo|solamente|unicamente|exclusivamente)\s+(?:para\s+)?(?:{_PERSONA}|personal\s+(?:femenino|masculino))\b")),
    (SEXO, re.compile(rf"\bse\s+(?:busca|buscan|solicita|solicitan|necesita|necesitan|requiere|requieren|contrata|contratan)\s+(?:una?\s+)?{_PERSONA}\b")),
    (SEXO, re.compile(r"\bpersonal\s+(?:femenino|masculino)\b")),
    # Sexo implícito: "señorita", "masculino" o "femenino" sueltos como requisito.
    (SEXO, re.compile(r"\bsenoritas?\b")),
    (SEXO, re.compile(r"\b(?:masculino|femenino)s?\b")),
    (BUENA_PRESENCIA, re.compile(r"\b(?:buena|excelente|muy\s+buena)\s+presencia\b|\bpresencia\s+agradable\b|\bbuen\s+aspecto(?:\s+fisico)?\b")),
    (FOTO, re.compile(
        r"\b(?:hoja\s+de\s+vida|cv|curriculum(?:\s+vitae)?)\s+con\s+foto(?:grafia)?\b"
        r"|\b(?:adjunt\w*|envi\w*|incluir|anexar|con)\s+(?:una\s+|su\s+)?foto(?:grafia)?s?\b"
        r"|\bfoto(?:grafia)?\s+(?:reciente|tamano\s+carnet|tipo\s+carnet|de\s+cuerpo\s+entero)\b"
    )),
    (ESTADO_CIVIL, re.compile(r"\bestado\s+civil\b(?:\s*:?\s*(?:solter|casad|divorciad|viud)[oa]s?\b)?|\b(?:solter[oa]s?|casad[oa]s?|sin\s+hijos)\b")),
    # Demás causales del art. 1: solo alertan.
    (RAZA, re.compile(
        r"\b(?:raza|etnia|origen\s+etnico)\b|\bcolor\s+de\s+(?:piel|tez)\b"
        r"|\b(?:tez|piel)\s+(?:clara|blanca|morena|oscura|negra)\b"
    )),
    (NACIMIENTO, re.compile(
        r"\b(?:panamen[oa]s?|nacional(?:es)?)\s+de\s+nacimiento\b|\blugar\s+de\s+nacimiento\b"
        r"|\bnacid[oa]s?\s+en\b|\boriginari[oa]s?\s+de\b"
    )),
    (DISCAPACIDAD, re.compile(
        r"\bsin\s+(?:ningun[oa]?\s+)?(?:discapacidad(?:es)?|impedimentos?\s+fisicos?|limitaciones?\s+fisicas?|defectos?\s+fisicos?)\b"
        r"|\bno\s+(?:se\s+aceptan\s+|aceptamos\s+)?(?:personas?\s+con\s+discapacidad|discapacitad[oa]s?)\b"
        r"|\bperfecto\s+estado\s+de\s+salud\b|\b(?:100\s*%|completamente)\s+san[oa]s?\b"
    )),
    (CLASE_SOCIAL, re.compile(
        r"\bclase\s+social\b|\bnivel\s+socio\s*-?\s*economico\b|\bestrato\s+(?:social|alto|medio|bajo)\b"
        r"|\bde\s+buena\s+familia\b|\bbuen\s+nivel\s+social\b|\bbuena\s+posicion\s+social\b"
    )),
    (RELIGION, re.compile(
        r"\breligion\b|\bcredo\b|\b(?:catolic|cristian|evangelic|adventist|protestant|musulman|judi)[oa]s?\b"
        r"|\bmusulmanas?\b|\btestigos?\s+de\s+jehova\b|\bmormon(?:es|as?)?\b"
    )),
    (IDEAS_POLITICAS, re.compile(
        r"\bideas?\s+politicas?\b|\b(?:afiliacion|filiacion|preferencia|militancia|tendencia)\s+politica\b|\bpartido\s+politico\b"
        r"|\b(?:afiliad|militante|simpatizante|copartidari)\w*\s+(?:al|del|de|a)\s+(?:partido|prd|molirena|panamenista|realizando\s+metas|cambio\s+democratico)\b"
    )),
]

# "masculino o femenino", "femenino y masculino", "masculino/femenino": no exige un sexo.
_AMBOS_SEXOS = re.compile(r"\b(?:masculino|femenino)s?\s*(?:y\s*/\s*o|o|y|u|/|,)\s*(?:masculino|femenino)s?\b")

# Puestos en femenino; el masculino se deriva (cajera -> cajero, vendedora -> vendedor).
_PUESTOS_FEMENINOS = (
    "cajera", "mesera", "salonera", "cocinera", "camarera", "secretaria", "ninera", "empleada", "dependienta",
    "vendedora", "promotora", "operadora", "supervisora", "asesora", "conductora", "repartidora", "aseadora",
    "limpiadora", "planchadora", "lavandera", "costurera", "enfermera", "azafata", "anfitriona",
    "operaria", "empacadora", "despachadora",
)
_PUESTO_FEMENINO = re.compile(rf"\b(?P<fem>{'|'.join(_PUESTOS_FEMENINOS)})s?\b")
_CONECTOR = r"\s*(?:y\s*/\s*o|o|y|u|e|/|,)\s*"


def _masculino(fem: str) -> str:
    return fem[:-1] if fem.endswith(("ora", "ona")) else fem[:-1] + "o"


def _puesto_solo_en_femenino(plegado: str, m: re.Match[str]) -> bool:
    """"cajera" alerta; "cajera(o)", "cajera/o", "cajero o cajera" y "cajeras y cajeros" no."""
    masc = _masculino(m.group("fem"))
    antes, despues = plegado[max(0, m.start() - 40) : m.start()], plegado[m.end() : m.end() + 40]
    if re.match(r"\s*(?:\(\s*os?\s*\)|/\s*os?\b)", despues):
        return False
    if re.match(rf"{_CONECTOR}{masc}(?:e?s)?\b", despues) or re.search(rf"\b{masc}(?:e?s)?{_CONECTOR}$", antes):
        return False
    return True


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
    """"Mayor de 18 años", "a partir de 18 años", "18 años en adelante" o "edad mínima 18" es la
    edad legal para trabajar, no un límite discriminatorio. Un mínimo mayor de 18 sí bloquea."""
    numero = re.search(r"\d{2}", frase)
    minimo = re.search(r"mayor|minim|partir|desde|adelante", frase) is not None
    return bool(numero) and int(numero.group()) <= 18 and minimo and "no mayor" not in frase


def _es_experiencia(plegado: str, m: re.Match[str]) -> bool:
    """"Mínimo 20 años de experiencia", "experiencia: desde 20 años" o "experiencia comprobable de 20
    años en adelante" cuentan experiencia, no edad.

    Entre "experiencia" y el número se admite un solo calificativo (laboral, mínima, previa o un
    adjetivo en -ble, -ado/-ada o -ivo/-iva). Una lista abierta dejaría pasar un límite de edad real
    como "experiencia y desde 25 años" o "experiencia como cajera desde 25 años".
    """
    antes, despues = plegado[max(0, m.start() - 40) : m.start()], plegado[m.end() : m.end() + 20]
    calificativo = r"(?:laboral|minima|previa|\w+(?:ble|ad[oa]|iv[oa]))"
    previa = rf"experiencia(?:\s+{calificativo})?\s*:?\s*(?:de\s*)?$"
    return re.match(r"\s*(?:de\s+)?experiencia", despues) is not None or re.search(previa, antes) is not None


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
        if _es_minimo_legal(m.group(0)) or _es_experiencia(plegado, m):
            continue
        anotar(EDAD, m.start(), m.end())
    for m in _RANGO.finditer(plegado):
        if _es_rango_de_edad(plegado, m):
            anotar(EDAD, m.start(), m.end())
    ambos = [(m.start(), m.end()) for m in _AMBOS_SEXOS.finditer(plegado)]
    for regla, patron in _PATRONES:
        for m in patron.finditer(plegado):
            if regla == SEXO and any(i < m.end() and m.start() < f for i, f in ambos):
                continue
            anotar(regla, m.start(), m.end())
    for m in _PUESTO_FEMENINO.finditer(plegado):
        if _puesto_solo_en_femenino(plegado, m):
            anotar(SEXO_IMPLICITO, m.start(), m.end())

    return [h for _, h in sorted(encontrados, key=lambda par: par[0])]


def esta_bloqueada(hallazgos: list[Hallazgo]) -> bool:
    return any(h.bloquea for h in hallazgos)
