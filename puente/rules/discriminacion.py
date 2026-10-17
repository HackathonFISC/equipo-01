"""Requisitos discriminatorios: bloqueo (edad y sexo, Ley 11 de 2005) y alertas. Issues #5, #43, #73, #117, #121, #190 y #191.

Solo regex sobre el texto original de la oferta, sin LLM: el modelo nunca decide si una
oferta es legal. Bloquean el límite de edad (art. 2) y la exigencia de sexo (art. 1), que
incluye excluir por embarazo ("no embarazadas" o pedir una prueba de embarazo, #191). Las
demás causales del art. 1 (raza, nacimiento, discapacidad, clase social, religión e ideas
políticas) y el puesto o el estado civil escritos solo en femenino ("cajera", "soltera")
alertan citando el artículo, para que el orientador decida. "Buena presencia" o "buena apariencia", foto y estado civil solo alertan,
porque la ley no menciona la apariencia. Cada hallazgo guarda la regla, la frase literal y si
bloquea. `revisar_oferta` aplica las mismas reglas al puesto y a los requisitos (#190).
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
# Singular y plural (#121): "menor de 28 años" y "mayor de 25 años" son el mismo límite que "menores de".
_MENOR_MAYOR = r"(?:no\s+)?(?:menor|mayor)(?:es)?\s+de"
# "máximo", "máxima", "máx." y "máx" (#121), y lo mismo para el mínimo.
_MAX_MIN = r"(?:maxim[oa]|minim[oa]|max|min)\.?"
_TOPE_EDAD = re.compile(
    rf"\b(?:{_MENOR_MAYOR}|{_MAX_MIN}|hasta|no\s+mas\s+de|{_DESDE})\s+\d{{2}}\s*{_ANOS}\b{_EN_ADELANTE}"
    rf"|\b\d{{2}}\s*{_ANOS}\s+en\s+adelante\b"
    rf"|\b\d{{2}}\s*\+\s*{_ANOS}\b"
    rf"|\bedad\s+(?:maxima|minima|max\.?|min\.?|limite|tope|requerida)\s*:?\s*(?:de\s+)?\d{{2}}\b(?:\s*\+)?(?:\s*{_ANOS}\b)?"
    rf"|\bedad\s*:?\s*(?:de\s+|entre\s+|{_DESDE}\s+|{_MENOR_MAYOR}\s+|{_MAX_MIN}\s+)?\d{{2}}\b(?:\s*\+)?"
    rf"(?:\s*(?:a|-|–|y|hasta)\s*\d{{2}}\b)?(?:\s*{_ANOS}\b)?{_EN_ADELANTE}"
    rf"|\b(?:joven(?:es)?|personas?)\s+(?:menor|mayor)(?:es)?\s+de\s+\d{{2}}\b"
)
# Rango con «mayor de» y «menor de» (#191): «Mayores de 18 años y menores de 35» o «Mayor de 18 y menor de 30».
# El mínimo de 18 es la edad legal, pero el tope («menores de 35», con o sin «años») es un límite de edad y
# bloquea por el art. 2. Va aparte de _TOPE_EDAD para que la frase sea el rango entero y para que el mínimo legal
# del principio no lo deje pasar.
_MAYOR_Y_MENOR = re.compile(
    rf"\bmayor(?:es)?\s+de\s+\d{{2}}(?:\s*{_ANOS}\b)?\s*(?:,\s*)?(?:y|e|,)\s*menor(?:es)?\s+de\s+\d{{2}}\b(?:\s*{_ANOS}\b)?"
)
# Quién se contrata y quién se atiende (#191): «buscamos joven de 18 a 25» es la edad del candidato;
# «atención a jóvenes de 15 a 24 años» o «cuidado de personas mayores de 65» es la de quien recibe el servicio.
_CANDIDATO = rf"(?:joven(?:es)?|personas?|candidat[oa]s?|aspirantes?|postulantes?|{_PERSONA})"
_ATENDIDOS_ANTES = re.compile(
    r"\b(?:atencion|atender|apoyo|apoyar|orientacion|orientar|acompanamiento|acompanar|cuidado|cuidar"
    r"|ensenar|ensenanza|clases|tutorias?|trabajo|trabajar)\s+(?:a|al|con|de)\s+(?:(?:los|las|nuestr[oa]s)\s+)?"
    r"(?:(?:joven(?:es)?|nin[oa]s|adolescentes|estudiantes|alumn[oa]s|pacientes|personas|adult[oa]s(?:\s+mayores)?)"
    r"(?:\s+\w+)?\s+)?$"
)
# Quién tiene la edad: "cuidar niños menores de 10 años" o "adultos mayores de 65 años" describen a
# otros, no al candidato. "Persona mayor de 25 años" sigue siendo edad: ante la duda, bloquea.
_OTROS_ANTES = re.compile(r"\b(?:hij[oa]s?|nin[oa]s?|bebes?|infantes|estudiantes|alumn[oa]s?|pacientes|adult[oa]s)\s+$")
# "No se aceptan menores de 18 años" anuncia la edad legal para trabajar, no un límite.
_NEGACION_ANTES = re.compile(
    r"\b(?:no\s+(?:se\s+)?(?:aceptan?|aceptamos|admiten?|admitimos|contratan?|contratamos|reciben?|recibimos)"
    r"|abstenerse|prohibido(?:\s+contratar)?|excepto|salvo)\s+(?:a\s+)?(?:personas\s+|jovenes\s+)?$"
)

_VERBO_CONTRATAR = (
    r"(?:se\s+(?:busca|buscan|solicita|solicitan|necesita|necesitan|requiere|requieren|contrata|contratan)"
    r"|buscamos|solicitamos|necesitamos|requerimos|contratamos)"
)
# Sexo implícito: "señorita", "chicas", "masculino" o "femenino" sueltos como requisito. No cuentan
# cuando describen otra cosa: "uniforme masculino", "fútbol femenino", "ropa para chicas", "caja chica".
_SEXO_SUELTO = re.compile(r"\b(?:masculino|femenino)s?\b")
_OBJETO_ANTES = re.compile(
    r"\b(?:uniformes?|ropa|vestimenta|calzado|zapatos?|vestuarios?|vestidor(?:es)?|camerinos?|banos?|sanitarios?|duchas?"
    r"|perfumes?|fragancias?|tallas?|departamento|seccion|linea|moda|articulos?|productos?|publico|clientela|categoria"
    r"|equipo|liga|torneo|seleccion|futbol|baloncesto|voleibol|softbol|beisbol)\s+$"
)
_FEMENINO_SUELTO = re.compile(r"\b(?:senoritas?|chicas|muchachas?)\b")
_DESTINATARIO_ANTES = re.compile(r"\b(?:para|de|a|con|entre|cajas?)\s+(?:las?\s+|los\s+|el\s+|nuestr[oa]s\s+|sus\s+)?$")
# «Damas» y «caballeros» sueltos (#190): «Damas para recepción» o un puesto «Bodeguero caballeros» piden un sexo;
# «ropa de damas», «barbería para caballeros» o «sección caballeros» describen otra cosa. Solo en plural: en
# singular casi siempre es un apellido («Sr. José Caballero», «Supermercados Caballero, S.A.»), y «se busca
# caballero» o «solo dama» ya bloquean por las reglas de _PATRONES.
_DAMAS_CABALLEROS = re.compile(r"\b(?:damas|caballeros)\b")
# El nombre de un negocio tampoco pide un sexo: «Boutique Damas», «Barbería Los Caballeros».
_NEGOCIO_ANTES = re.compile(
    r"\b(?:boutiques?|tiendas?|almacen(?:es)?|salon(?:es)?|peluquerias?|barberias?|spa|gimnasios?|club(?:es)?"
    r"|supermercados?|minisuper|farmacias?|sastrerias?|zapaterias?|restaurantes?|bar|cafeterias?|fondas?|hotel(?:es)?)"
    r"\s+(?:las\s+|los\s+)?$"
)
_DESTINATARIO_U_OBJETO_ANTES = re.compile(
    rf"(?:{_DESTINATARIO_ANTES.pattern})|(?:{_OBJETO_ANTES.pattern})|(?:{_NEGOCIO_ANTES.pattern})"
)
# Ninguna exclusión de _EXCLUSION_ANTES vale si el «para» o el «a» va detrás de la vacante o del verbo de
# contratar (#190): «Vacante para damas», «Empleo para caballeros» o «Contratamos a damas» piden un sexo;
# «ropa para damas» o «zapatos para caballeros» no. Vale también para «Vacante para señoritas».
_VACANTE_ANTES = re.compile(
    rf"\b(?:{_VERBO_CONTRATAR}|vacantes?|empleos?|plazas?|puestos?|trabajos?|oportunidad(?:es)?(?:\s+laboral(?:es)?)?)"
    r"\s+(?:para|a)\s+(?:las\s+|los\s+)?$"
)
# Marca de sexo entre paréntesis (#190), típica del puesto: «Recepcionista (mujer)» o «Bodeguero (hombres)»
# exigen un sexo igual que «Mesero (masculino)». «Cajero(a)» y «Vendedor(es)» no.
_SEXO_ENTRE_PARENTESIS = re.compile(rf"\(\s*(?:solo\s+|solamente\s+|unicamente\s+)?{_PERSONA}\s*\)")
# Excluir por embarazo (#191) solo puede dejar fuera a mujeres: es exigencia de sexo y bloquea por el art. 1.
# «No embarazadas», «que no esté embarazada», «abstenerse embarazadas», «Embarazadas, abstenerse», «sin
# embarazo» o pedir una prueba de embarazo, negativa o no: «Prueba de embarazo obligatoria», «Exámenes: VIH y
# prueba de embarazo». «Atención a mujeres embarazadas», «licencia por embarazo», «fuero de maternidad» o «el
# seguro cubre embarazo y parto» no excluyen a nadie. La prueba cuenta en singular: «realizar pruebas de
# embarazo» es una tarea de laboratorio, y venderlas en una farmacia tampoco excluye a nadie (_VENTA_ANTES).
_EMBARAZO = re.compile(
    r"\b(?:no|ni)\s+(?:(?:estar|este|esten|encontrarse|se\s+encuentren?)\s+)?"
    r"(?:embarazadas?|en\s+(?:estado\s+de\s+)?(?:embarazo|gestacion|gravidez))\b"
    r"|\b(?:no\s+(?:se\s+)?(?:aceptan?|aceptamos|admiten?|admitimos|contratan?|contratamos|reciben?|recibimos)"
    r"|abstenerse|excepto|salvo)\s+(?:a\s+)?(?:(?:mujeres|personas|damas|senoras)\s+)?embarazadas\b"
    r"|\bembarazadas?\s*[,:\-–]?\s*(?:abstenerse|abstente|no\s+(?:aplican?|aplicar|postularse|se\s+aceptan|se\s+admiten))\b"
    r"|\bsin\s+(?:estar\s+)?(?:embarazo|embarazada)\b"
    r"|\b(?:presentar|entregar|aportar|adjuntar|realizarse|hacerse)\s+(?:una?\s+)?(?:prueba|test|examen)\s+de\s+embarazo\b"
    r"|\b(?:prueba|test|examen)\s+(?:medic[oa]\s+)?de\s+embarazo(?:\s+(?:negativ[oa]|obligatori[oa]))?\b"
    r"|\b(?:prueba|test|examen|certificado|constancia)\s+(?:medic[oa]\s+)?de\s+no\s+(?:estar\s+)?embaraz(?:o|ada)\b"
)
_VENTA_ANTES = re.compile(
    r"\b(?:venta|ventas|vender|vendemos|venden|despacho|despachar|inventario|reponer|reposicion|surtir)"
    r"\s+(?:de\s+)?(?:la\s+|las\s+|los\s+|el\s+|una?\s+)?$"
)
# Apariencia (#191): «buena apariencia» es «buena presencia» con otra palabra y solo alerta. También «imagen
# personal» y «presentación personal», con o sin adjetivo delante. La de un lugar o una cosa («mantener la
# buena apariencia del local») no habla del candidato, y «asesora de imagen personal» es un oficio.
_APARIENCIA = re.compile(
    r"\b(?:buena|excelente|muy\s+buena)\s+presencia\b|\bpresencia\s+agradable\b|\bbuen\s+aspecto(?:\s+fisico)?\b"
    r"|\b(?:buena|excelente|muy\s+buena|agradable|impecable)\s+apariencia\b"
    r"|\bapariencia\s+(?:fisica|personal|agradable|impecable|atractiva)\b"
    r"|\b(?:(?:buena|excelente|muy\s+buena|impecable)\s+)?imagen\s+(?:personal|fisica)\b"
    r"|\b(?:(?:buena|excelente|muy\s+buena|impecable)\s+)?presentacion\s+personal\b"
)
_OFICIO_DE_IMAGEN_ANTES = re.compile(
    r"\b(?:(?:asesor|consultor)(?:a|es|as|\(a\)|/a)?|asesoria|consultoria|cursos?|talleres?)\s+(?:de|en)\s+$"
)
_COSA_DESPUES = re.compile(
    r"\s+(?:del|de\s+(?:la|las|los|el|nuestr[oa]s?|sus?))\s+(?:locale?s?|tiendas?|negocios?|establecimientos?|oficinas?"
    r"|empresa|areas?|instalaciones|productos?|mercancias?|vitrinas?|sucursal(?:es)?|restaurantes?|marcas?|exhibici\w+"
    r"|exhibidores?|almacen(?:es)?|anaqueles?|estantes?|estanterias?|espacios?|lugar(?:es)?|puestos?|mostrador(?:es)?"
    r"|salon(?:es)?|comedor(?:es)?|mesas?|cocina|banos?|fachada|edificio|jardin(?:es)?|vehiculos?|flota|uniformes?"
    r"|menu|platos?|pagina|sitio|redes)\b"
)
_EXCLUSION_ANTES = {
    _SEXO_SUELTO: _OBJETO_ANTES,
    _FEMENINO_SUELTO: _DESTINATARIO_ANTES,
    _DAMAS_CABALLEROS: _DESTINATARIO_U_OBJETO_ANTES,
    _EMBARAZO: _VENTA_ANTES,
    _APARIENCIA: _OFICIO_DE_IMAGEN_ANTES,
}
_EXCLUSION_DESPUES = {_APARIENCIA: _COSA_DESPUES}

_PATRONES: list[tuple[str, re.Pattern[str]]] = [
    (SEXO, re.compile(r"\b(?:sexo|genero)\s*:?\s*(?:femenino|masculino|mujer|hombre|f|m)\b")),
    (SEXO, re.compile(rf"\b(?:solo|solamente|unicamente|exclusivamente)\s+(?:para\s+)?(?:{_PERSONA}|personal\s+(?:femenino|masculino))\b")),
    (SEXO, re.compile(rf"\b{_VERBO_CONTRATAR}\s+(?:a\s+)?(?:una?\s+|\d+\s+)?{_PERSONA}\b")),
    # Preferencia explícita de sexo (#121): "preferiblemente mujer" exige un sexo igual que
    # "preferiblemente masculino", que ya bloqueaba por la palabra suelta.
    (SEXO, re.compile(
        rf"\b(?:preferiblemente|preferentemente|de\s+preferencia|con\s+preferencia|se\s+prefieren?|preferimos)\s*:?\s*(?:una?\s+)?{_PERSONA}\b"
    )),
    (SEXO, re.compile(r"\bpersonal\s+(?:femenino|masculino)\b")),
    (SEXO, _FEMENINO_SUELTO),
    (SEXO, _DAMAS_CABALLEROS),
    (SEXO, _SEXO_ENTRE_PARENTESIS),
    (SEXO, _SEXO_SUELTO),
    (SEXO, _EMBARAZO),
    (BUENA_PRESENCIA, _APARIENCIA),
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

# "masculino o femenino", "femenino y masculino", "masculino/femenino", "damas y caballeros",
# "Damas & Caballeros" o "chicos y chicas": no exige un sexo.
_MASC = r"(?:masculinos?|hombres?|varon(?:es)?|caballeros?|chicos?|muchachos?)"
_FEM = r"(?:femeninos?|mujer(?:es)?|damas?|senoritas?|chicas?|muchachas?)"
_O_SEXO = r"\s*(?:y\s*/\s*o|o|y|u|e|/|,|&)\s*"
_AMBOS_SEXOS = re.compile(rf"\b(?:{_MASC}{_O_SEXO}{_FEM}|{_FEM}{_O_SEXO}{_MASC})\b")

# Estado civil en femenino (#117): "se prefiere soltera" pide una mujer aunque el puesto sea neutro.
# Alerta como "cajera"; "persona soltera", "soltero/a" y "soltero o soltera" no.
_ESTADO_CIVIL_FEMENINO = re.compile(r"\b(?P<fem>soltera|casada|divorciada|viuda)s?\b")

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
    if _atiende_a_otros(plegado, m.start()):
        return False
    tiene_anos = re.search(_ANOS, m.group(0)) is not None
    # "Jóvenes entre 18 y 24" sin "años" (#121): el rango va pegado a quién se busca. También en singular:
    # "Buscamos joven de 18 a 25" o "Recepcionista joven 18-25" (#190, #191).
    de_personas = re.search(rf"\b{_CANDIDATO}\s*$", contexto_antes)
    return tiene_anos or "edad" in contexto_antes or de_personas is not None


def _atiende_a_otros(plegado: str, inicio: int) -> bool:
    """"Atención a jóvenes de 15 a 24 años" o "cuidado de personas mayores de 65": la edad es de quien recibe
    el servicio, no del candidato (#191)."""
    return _ATENDIDOS_ANTES.search(plegado[max(0, inicio - 60) : inicio]) is not None


def _es_minimo_legal(frase: str) -> bool:
    """"Mayor de 18 años", "a partir de 18 años", "18 años en adelante", "edad mínima 18", "mín. 18",
    "no menor de 18" o "Edad: 18+" es la edad legal para trabajar, no un límite discriminatorio. Un
    mínimo mayor de 18 sí bloquea."""
    numero = re.search(r"\d{2}", frase)
    minimo = re.search(r"mayor|\bmin|partir|desde|adelante|no\s+menor|\+", frase) is not None
    return bool(numero) and int(numero.group()) <= 18 and minimo and "no mayor" not in frase


def _describe_a_otros(plegado: str, m: re.Match[str]) -> bool:
    """"Niños menores de 10 años" o "adultos mayores de 65 años": la edad es de otros, no del candidato."""
    return re.match(r"(?:menor|mayor)(?:es)?\s+de\b", m.group(0)) is not None and bool(
        _OTROS_ANTES.search(plegado[max(0, m.start() - 20) : m.start()])
    )


def _niega_menores_de_edad(plegado: str, m: re.Match[str]) -> bool:
    """"No se aceptan menores de 18 años" o "abstenerse menores de 18": repite la edad legal."""
    numero = re.match(r"menor(?:es)?\s+de\s+(\d{2})", m.group(0))
    return bool(numero) and int(numero.group(1)) <= 18 and bool(
        _NEGACION_ANTES.search(plegado[max(0, m.start() - 40) : m.start()])
    )


def _es_experiencia(plegado: str, m: re.Match[str]) -> bool:
    """"Mínimo 20 años de experiencia", "experiencia: desde 20 años" o "experiencia comprobable de 20
    años en adelante" cuentan experiencia, no edad.

    Ante la duda es edad: bajo la Ley 11 un límite que se escapa pesa más que una oferta bloqueada de
    más. Nunca es experiencia si la frase describe personas ("mayores de", "menores de", "no mayor
    de"), si empieza por "edad", si lleva "los" antes del número ("desde los 25 años") o "de edad"
    después. El singular "mayor de" concuerda también con "experiencia" ("experiencia mayor de 10
    años", #121), así que sigue la misma regla que "desde" o "mínimo". Entre "experiencia" y el número
    se admite un solo calificativo de una lista cerrada; "indispensable", "requerida" o "necesaria"
    quedan fuera porque tras ellas un "desde 25 años" suele ser edad.
    """
    frase = m.group(0)
    antes, despues = plegado[max(0, m.start() - 40) : m.start()], plegado[m.end() : m.end() + 20]
    if (
        re.match(r"no\s+(?:mayor|menor)(?:es)?\s+de\b|(?:mayor|menor)es\s+de\b", frase)
        or frase.startswith("edad")
        or re.search(r"\blos\s+\d", frase)
        or re.match(r"\s*de\s+edad\b", despues)
    ):
        return False
    calificativo = (
        r"(?:laboral|minima|previa|comprobable|comprobada|deseable|demostrada|relacionada|acumulada|certificada)"
    )
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

    # Primero el rango «mayores de 18 y menores de 35», para que la frase sea el rango entero. Si el rango es
    # experiencia o la edad de otros («niños mayores de 10 y menores de 12 años»), sus partes tampoco cuentan.
    descartados: list[tuple[int, int]] = []
    for m in _MAYOR_Y_MENOR.finditer(plegado):
        if _es_experiencia(plegado, m) or _describe_a_otros(plegado, m) or _atiende_a_otros(plegado, m.start()):
            descartados.append((m.start(), m.end()))
        else:
            anotar(EDAD, m.start(), m.end())
    for m in _TOPE_EDAD.finditer(plegado):
        if (
            any(i <= m.start() and m.end() <= f for i, f in descartados)
            or _es_minimo_legal(m.group(0))
            or _es_experiencia(plegado, m)
            or _describe_a_otros(plegado, m)
            or _atiende_a_otros(plegado, m.start())
            or _niega_menores_de_edad(plegado, m)
        ):
            continue
        anotar(EDAD, m.start(), m.end())
    for m in _RANGO.finditer(plegado):
        if _es_rango_de_edad(plegado, m):
            anotar(EDAD, m.start(), m.end())
    ambos = [(m.start(), m.end()) for m in _AMBOS_SEXOS.finditer(plegado)]
    for regla, patron in _PATRONES:
        excluir = _EXCLUSION_ANTES.get(patron)
        excluir_despues = _EXCLUSION_DESPUES.get(patron)
        for m in patron.finditer(plegado):
            if regla == SEXO and any(i < m.end() and m.start() < f for i, f in ambos):
                continue
            antes = plegado[max(0, m.start() - 30) : m.start()]
            if excluir is not None and excluir.search(antes) and not _VACANTE_ANTES.search(antes):
                continue
            if excluir_despues is not None and excluir_despues.match(plegado, m.end()):
                continue
            anotar(regla, m.start(), m.end())
    for m in _PUESTO_FEMENINO.finditer(plegado):
        if _puesto_solo_en_femenino(plegado, m):
            anotar(SEXO_IMPLICITO, m.start(), m.end())
    estado_civil = [(i, f) for r, i, f in vistos if r == ESTADO_CIVIL]
    for m in _ESTADO_CIVIL_FEMENINO.finditer(plegado):
        if (
            any(i <= m.start() and m.end() <= f for i, f in estado_civil)
            and not re.search(r"\bpersonas?\s+$", plegado[max(0, m.start() - 15) : m.start()])
            and _puesto_solo_en_femenino(plegado, m)
        ):
            anotar(SEXO_IMPLICITO, m.start(), m.end())

    return [h for _, h in sorted(encontrados, key=lambda par: par[0])]


def _plano(frase: str) -> str:
    return " ".join(_plegar(frase).split())


def revisar_oferta(puesto: str, requisitos: str) -> list[Hallazgo]:
    """El puesto y los requisitos de una oferta, con las mismas reglas (#190). "Mesero (masculino)", "Solo
    mujeres" o "Recepcionista joven 18-25" en el puesto bloquean igual que en los requisitos; "Vendedora" o
    "Cajera" alertan como sexo implícito (usar la forma inclusiva, "Vendedor(a)") y no bloquean, porque el
    puesto en femenino es habitual en Panamá.

    El puesto se revisa aparte, como el título que es: el contexto de los requisitos no le quita ni le pone
    hallazgos, y los requisitos se revisan igual que solos. Un hallazgo del puesto con la misma regla y frase
    que uno de los requisitos no se repite. Primero los del puesto, en el orden en que se lee la oferta.
    """
    de_requisitos = revisar_texto(requisitos or "")
    vistos = {(h.regla, _plano(h.frase)) for h in de_requisitos}
    del_puesto = [h for h in revisar_texto(puesto or "") if (h.regla, _plano(h.frase)) not in vistos]
    return del_puesto + de_requisitos


def esta_bloqueada(hallazgos: list[Hallazgo]) -> bool:
    return any(h.bloquea for h in hallazgos)
