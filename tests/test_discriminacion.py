"""Reglas de discriminación (#5, #43): regex sobre el texto original, sin LLM."""

import pytest

from puente.rules.discriminacion import (
    BUENA_PRESENCIA,
    CLASE_SOCIAL,
    DISCAPACIDAD,
    EDAD,
    ESTADO_CIVIL,
    FOTO,
    IDEAS_POLITICAS,
    NACIMIENTO,
    RAZA,
    RELIGION,
    SEXO,
    SEXO_IMPLICITO,
    esta_bloqueada,
    revisar_texto,
)
from puente.rules.taxonomia import cargar_taxonomia, normalizar_habilidad, normalizar_texto

TAXONOMIA_MINI = "tests/fixtures/taxonomia_mini.csv"


def _reglas(texto):
    return [(h.regla, h.frase) for h in revisar_texto(texto)]


def test_criterio_del_issue_edad_bloquea_y_presencia_alerta():
    hallazgos = revisar_texto("Se busca cajera: buena presencia, 18 a 25 años.")
    assert esta_bloqueada(hallazgos)
    assert [(h.regla, h.bloquea) for h in hallazgos] == [(SEXO_IMPLICITO, False), (BUENA_PRESENCIA, False), (EDAD, True)]
    assert hallazgos[2].frase == "18 a 25 años"
    assert "art. 2" in hallazgos[2].como_texto()


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Jóvenes entre 18 y 29 años de edad", "entre 18 y 29 años de edad"),
        ("Edad: 20-30. Turno nocturno", "Edad: 20-30"),
        ("Edad máxima 35 años", "Edad máxima 35 años"),
        ("Candidatos no mayores de 30 años", "no mayores de 30 años"),
        ("Menores de 28 AÑOS", "Menores de 28 AÑOS"),
    ],
)
def test_bloqueo_por_limite_de_edad(texto, frase):
    hallazgos = revisar_texto(texto)
    assert esta_bloqueada(hallazgos)
    assert (EDAD, frase) in _reglas(texto)


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Sexo: femenino", "Sexo: femenino"),
        ("Género masculino", "Género masculino"),
        ("Solo mujeres", "Solo mujeres"),
        ("Se solicitan caballeros para bodega", "Se solicitan caballeros"),
        ("Se busca señorita para recepción", "Se busca señorita"),
        ("Personal masculino con licencia", "Personal masculino"),
    ],
)
def test_bloqueo_por_exigencia_de_sexo(texto, frase):
    hallazgos = revisar_texto(texto)
    assert esta_bloqueada(hallazgos)
    assert (SEXO, frase) in _reglas(texto)
    assert "art. 1" in hallazgos[0].como_texto()


@pytest.mark.parametrize(
    "texto, regla, frase",
    [
        ("Excelente presencia y puntualidad", BUENA_PRESENCIA, "Excelente presencia"),
        ("Enviar hoja de vida con foto reciente", FOTO, "hoja de vida con foto"),
        ("Adjuntar fotografía tamaño carnet", FOTO, "Adjuntar fotografía"),
        # «Estado civil: soltera» da además sexo_implicito desde #117: ver la sección de #117 abajo.
        ("Estado civil: soltero/a", ESTADO_CIVIL, "Estado civil: soltero"),
        ("Preferiblemente casado", ESTADO_CIVIL, "casado"),
    ],
)
def test_alertas_no_bloquean(texto, regla, frase):
    hallazgos = revisar_texto(texto)
    assert not esta_bloqueada(hallazgos)
    assert _reglas(texto) == [(regla, frase)]
    assert hallazgos[0].como_texto().startswith("alerta")


@pytest.mark.parametrize(
    "texto",
    [
        "Mayor de 18 años, 2 a 3 años de experiencia",
        "Experiencia de 15 a 20 años en el sector",
        "Edad mínima 18",
        "Turno de 14 a 22 horas",
        "Salario de 600 a 800 balboas",
        "Edad: indiferente. Ambos sexos.",
        "Se busca cajero(a) con manejo de caja",
        "Atención de clientes con buena actitud",
    ],
)
def test_sin_falsos_positivos(texto):
    assert revisar_texto(texto) == []


def test_reglas_son_deterministas():
    texto = "Solo hombres, 20 a 30 años, con foto, buena presencia, estado civil"
    assert revisar_texto(texto) == revisar_texto(texto)
    assert [h.regla for h in revisar_texto(texto)] == [SEXO, EDAD, FOTO, BUENA_PRESENCIA, ESTADO_CIVIL]


# ---------------------------------------------------------------- taxonomía


def test_normalizar_texto():
    assert normalizar_texto("  Atención   al CLIENTE ñ ") == "atencion al cliente n"


def test_cargar_taxonomia():
    taxonomia = cargar_taxonomia(TAXONOMIA_MINI)
    assert taxonomia["manejo_caja"] == {
        "nombre_es": "Manejo de caja",
        "sinonimos": ["cajero", "cobro en caja", "cuadre de caja"],
        "uri_esco": "http://data.europa.eu/esco/skill/ficticia-manejo-caja",
    }


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Atención al cliente", "atencion_cliente"),
        ("SERVICIO  al cliente", "atencion_cliente"),
        ("hojas de calculo", "excel"),
        ("manejo_caja", "manejo_caja"),
        ("liderazgo transformacional", None),
        ("", None),
    ],
)
def test_normalizar_habilidad(texto, esperado):
    assert normalizar_habilidad(texto, cargar_taxonomia(TAXONOMIA_MINI)) == esperado


# ---------------------------------------------------------------- #43: sexo implícito y demás causales del art. 1


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Solo damas", "Solo damas"),
        ("Señorita para recepción, turno de mañana", "Señorita"),
        ("Requisitos: masculino, licencia de conducir", "masculino"),
        ("Bachiller, femenino, disponibilidad inmediata", "femenino"),
    ],
)
def test_sexo_implicito_bloquea(texto, frase):
    hallazgos = revisar_texto(texto)
    assert esta_bloqueada(hallazgos)
    assert (SEXO, frase) in _reglas(texto)
    assert "art. 1" in hallazgos[0].como_texto()


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Se busca cajera con manejo de caja", "cajera"),
        ("Vendedora para tienda de ropa en Albrook", "Vendedora"),
        ("Necesitamos meseras para fin de semana", "meseras"),
        ("Niñera con experiencia", "Niñera"),
    ],
)
def test_puesto_solo_en_femenino_alerta_sin_bloquear(texto, frase):
    hallazgos = revisar_texto(texto)
    assert not esta_bloqueada(hallazgos)
    assert _reglas(texto) == [(SEXO_IMPLICITO, frase)]
    texto_alerta = hallazgos[0].como_texto()
    assert texto_alerta.startswith("alerta · sexo_implicito") and "art. 1" in texto_alerta


@pytest.mark.parametrize(
    "texto",
    [
        "Se busca cajero(a) para minisúper",
        "Se busca cajera(o) para minisúper",
        "Vendedor/a con experiencia",
        "Cajera/o para turno nocturno",
        "Solicitamos mesero o mesera",
        "Meseras y meseros para eventos",
        "Sexo: masculino o femenino",
        "Género: femenino/masculino",
        "Se aceptan personas con discapacidad",
        "Empresa inclusiva: igualdad de oportunidades",
    ],
)
def test_sexo_implicito_sin_falsos_positivos(texto):
    assert revisar_texto(texto) == []


@pytest.mark.parametrize(
    "texto, regla, frase",
    [
        ("Preferiblemente de tez clara", RAZA, "tez clara"),
        ("Indicar raza en la hoja de vida", RAZA, "raza"),
        ("Panameño de nacimiento", NACIMIENTO, "Panameño de nacimiento"),
        ("Sin discapacidad ni impedimentos", DISCAPACIDAD, "Sin discapacidad"),
        ("No se aceptan personas con discapacidad", DISCAPACIDAD, "No se aceptan personas con discapacidad"),
        ("Persona de buena familia", CLASE_SOCIAL, "de buena familia"),
        ("Nivel socioeconómico medio alto", CLASE_SOCIAL, "Nivel socioeconómico"),
        ("Católico practicante", RELIGION, "Católico"),
        ("Indicar religión", RELIGION, "religión"),
        ("Afiliado al PRD", IDEAS_POLITICAS, "Afiliado al PRD"),
        ("Afinidad con nuestras ideas políticas", IDEAS_POLITICAS, "ideas políticas"),
    ],
)
def test_demas_causales_del_art_1_alertan_citando_el_articulo(texto, regla, frase):
    hallazgos = revisar_texto(texto)
    assert not esta_bloqueada(hallazgos)
    assert _reglas(texto) == [(regla, frase)]
    assert hallazgos[0].como_texto().startswith(f"alerta · {regla}")
    assert "Ley 11 de 2005 art. 1" in hallazgos[0].como_texto()


# ---------------------------------------------------------------- #73: edad mínima mayor de 18 y más puestos en femenino


def test_ofertas_reales_ct40_bloquea_por_edad_minima_y_ct03_alerta_por_operaria():
    ct40 = revisar_texto("Edad: A partir de 26 años")
    assert esta_bloqueada(ct40)
    assert [(h.regla, h.frase) for h in ct40] == [(EDAD, "Edad: A partir de 26 años")]
    assert ct40[0].como_texto().startswith("bloqueo · edad") and "Ley 11 de 2005 art. 2" in ct40[0].como_texto()

    ct03 = revisar_texto("Puesto: Operaria de Limpieza")
    assert not esta_bloqueada(ct03)
    assert [(h.regla, h.frase) for h in ct03] == [(SEXO_IMPLICITO, "Operaria")]
    assert ct03[0].como_texto().startswith("alerta · sexo_implicito")


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Requisitos: desde 25 años, bachiller", "desde 25 años"),
        ("Mínimo 26 años", "Mínimo 26 años"),
        ("Edad mínima: 30", "Edad mínima: 30"),
        ("A partir de los 26 años", "A partir de los 26 años"),
        ("Personas de 26 años en adelante", "26 años en adelante"),
    ],
)
def test_edad_minima_mayor_de_18_bloquea(texto, frase):
    hallazgos = revisar_texto(texto)
    assert esta_bloqueada(hallazgos)
    assert _reglas(texto) == [(EDAD, frase)]
    assert "art. 2" in hallazgos[0].como_texto()


@pytest.mark.parametrize(
    "texto",
    [
        "Mayor de 18",
        "A partir de 18 años",
        "18 años en adelante",
        "Edad: 18 años en adelante",
        "Edad: desde 18 años",
    ],
)
def test_edad_minima_legal_de_18_no_bloquea(texto):
    assert revisar_texto(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Inicio a partir de 2026",
        "Entrada a partir de las 8",
        "Bono desde 25 dólares",
        "Mínimo 26 horas semanales",
        "2 años de experiencia",
        "Mayor de 18, turno nocturno",
        "Mínimo 20 años de experiencia",
        "Experiencia: desde 20 años",
        "Experiencia de 20 años en adelante",
        "Experiencia mínima de 20 años en adelante",
        "Experiencia laboral: 20 años en adelante",
        "Experiencia comprobable desde 20 años",
        "Experiencia deseable de 30 años en adelante",
        "Experiencia demostrada de 20 años en adelante",
        "Experiencia relacionada desde 20 años",
    ],
)
def test_numeros_que_no_son_edad_no_bloquean(texto):
    assert revisar_texto(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Buscamos experiencia y desde 25 años",
        "Experiencia como cajera desde 25 años",
        "Sin experiencia, desde 25 años",
        "Experiencia en ventas. Edad: 26 años en adelante",
        "experiencia indispensable mayores de 25 años",
        "Experiencia indispensable desde 25 años",
        "experiencia indispensable mínimo 25 años",
        "Experiencia deseable desde 25 años de edad",
        "Experiencia deseable desde los 25 años",
        "Experiencia activa desde 25 años",
        "Experiencia: desde 25 años de edad",
        "Experiencia: desde los 25 años",
        "experiencia mayores de 25 años",
    ],
)
def test_experiencia_cerca_de_un_limite_de_edad_no_lo_tapa(texto):
    assert esta_bloqueada(revisar_texto(texto))


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Operaria de producción", "Operaria"),
        ("Empacadora para supermercado", "Empacadora"),
        ("Despachadora de combustible", "Despachadora"),
        ("Mesera para restaurante", "Mesera"),
    ],
)
def test_mas_puestos_en_femenino_alertan(texto, frase):
    hallazgos = revisar_texto(texto)
    assert not esta_bloqueada(hallazgos)
    assert _reglas(texto) == [(SEXO_IMPLICITO, frase)]


@pytest.mark.parametrize(
    "texto",
    [
        "Recepcionista bilingüe",
        "Se busca cajero(a)",
        "Operario(a) de producción",
        "Operaria/o de limpieza",
        "Solicitamos mesero o mesera",
    ],
)
def test_puestos_neutros_o_en_ambos_generos_no_alertan(texto):
    assert revisar_texto(texto) == []


# ---------------------------------------------------------------- #121: singular, «máx.», rangos sin «años» y preferencia de sexo


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Edad: menor de 28 años", "Edad: menor de 28 años"),
        ("mayor de 25 años", "mayor de 25 años"),
        ("Candidato mayor de 25 años, bachiller", "mayor de 25 años"),
        ("Menor de 28 años", "Menor de 28 años"),
        ("Edad: menor de 28", "Edad: menor de 28"),
        ("Edad: no mayor de 30", "Edad: no mayor de 30"),
        ("Persona mayor de 30", "Persona mayor de 30"),
        ("máx. 25 años", "máx. 25 años"),
        ("Máx 25 años", "Máx 25 años"),
        ("Edad máx. 25", "Edad máx. 25"),
        ("mín. 26 años", "mín. 26 años"),
        ("Jóvenes entre 18 y 24", "entre 18 y 24"),
        ("Personas de 18 a 24, turno de mañana", "de 18 a 24"),
        ("Edad: 25+", "Edad: 25+"),
        ("25+ años", "25+ años"),
        ("experiencia indispensable mayor de 25 años", "mayor de 25 años"),
        ("Experiencia mínima 2 años. Mayor de 25 años", "Mayor de 25 años"),
        ("No se aceptan menores de 25 años", "menores de 25 años"),
    ],
)
def test_limite_de_edad_en_singular_y_abreviado_bloquea(texto, frase):
    hallazgos = revisar_texto(texto)
    assert esta_bloqueada(hallazgos)
    assert (EDAD, frase) in _reglas(texto)
    assert "Ley 11 de 2005 art. 2" in next(h for h in hallazgos if h.regla == EDAD).como_texto()


@pytest.mark.parametrize(
    "texto",
    [
        "Ser mayor de edad",
        "Mayor de 18 años",
        "Edad: mayor de 18 años",
        "Edad: no menor de 18 años",
        "Edad: 18+",
        "Edad 18+",
        "18+ años",
        "Mín. 18 años",
        "Edad mín. 18",
        "No se aceptan menores de 18 años",
        "No se aceptan menores de edad",
        "Abstenerse menores de 18 años",
        "Cuidado de niños menores de 10 años",
        "Niñero(a) para hijos menores de 12 años",
        "Cuidador(a) de adultos mayores de 65 años",
        "Experiencia mayor de 10 años",
        "mayor de 10 años de experiencia",
        "mín. 10 años de experiencia",
        "Experiencia mín. 10 años",
        "Jóvenes, turno de 14 a 22",
    ],
)
def test_edad_legal_experiencia_o_edad_de_otros_no_bloquea(texto):
    assert revisar_texto(texto) == []


@pytest.mark.parametrize(
    "texto, frase",
    [
        ("Preferiblemente mujer", "Preferiblemente mujer"),
        ("De preferencia hombres para carga", "De preferencia hombres"),
        ("Se prefiere mujer para caja", "Se prefiere mujer"),
        ("Buscamos chicas dinámicas para promoción", "Buscamos chicas"),
        ("Chicas dinámicas para impulsar productos", "Chicas"),
        ("Necesitamos 2 caballeros para bodega", "Necesitamos 2 caballeros"),
        ("Muchacha para atender fonda", "Muchacha"),
    ],
)
def test_preferencia_explicita_de_sexo_bloquea(texto, frase):
    hallazgos = revisar_texto(texto)
    assert esta_bloqueada(hallazgos)
    assert (SEXO, frase) in _reglas(texto)
    assert "Ley 11 de 2005 art. 1" in next(h for h in hallazgos if h.regla == SEXO).como_texto()


@pytest.mark.parametrize(
    "texto",
    [
        "Se entrega uniforme masculino",
        "Uniformes masculinos y camisa de la empresa",
        "Vendedor(a) para la sección de calzado masculino",
        "Baño masculino para el personal",
        "Entrenador(a) de fútbol femenino",
        "Tienda de ropa para chicas en Albrook",
        "Manejo de caja chica",
        "Se solicitan damas y caballeros",
        "Buscamos chicos y chicas para eventos",
        "Preferiblemente hombres o mujeres con licencia",
    ],
)
def test_sexo_que_describe_otra_cosa_o_ambos_no_bloquea(texto):
    assert revisar_texto(texto) == []


def test_masculino_suelto_como_requisito_sigue_bloqueando():
    for texto, frase in [("Requisitos: masculino, licencia", "masculino"), ("Preferiblemente masculino", "masculino")]:
        assert esta_bloqueada(revisar_texto(texto))
        assert (SEXO, frase) in _reglas(texto)


# ---------------------------------------------------------------- #117: «soltera» como requisito alerta sexo implícito


@pytest.mark.parametrize(
    "texto, esperado",
    [
        (
            "Asistente de tienda. Se prefiere soltera, sin hijos.",
            [(ESTADO_CIVIL, "soltera"), (SEXO_IMPLICITO, "soltera"), (ESTADO_CIVIL, "sin hijos")],
        ),
        ("Estado civil: soltera", [(ESTADO_CIVIL, "Estado civil: soltera"), (SEXO_IMPLICITO, "soltera")]),
        ("Solteras para turno de noche", [(ESTADO_CIVIL, "Solteras"), (SEXO_IMPLICITO, "Solteras")]),
        ("Preferiblemente casada", [(ESTADO_CIVIL, "casada"), (SEXO_IMPLICITO, "casada")]),
    ],
)
def test_estado_civil_en_femenino_alerta_sexo_implicito_sin_bloquear(texto, esperado):
    hallazgos = revisar_texto(texto)
    assert not esta_bloqueada(hallazgos)
    assert _reglas(texto) == esperado
    implicito = next(h for h in hallazgos if h.regla == SEXO_IMPLICITO)
    assert implicito.como_texto().startswith("alerta · sexo_implicito") and "art. 1" in implicito.como_texto()


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Se prefiere persona soltera", [(ESTADO_CIVIL, "soltera")]),
        ("Personas solteras", [(ESTADO_CIVIL, "solteras")]),
        ("Soltero/a", [(ESTADO_CIVIL, "Soltero")]),
        ("Soltera/o", [(ESTADO_CIVIL, "Soltera")]),
        ("Soltero o soltera", [(ESTADO_CIVIL, "Soltero"), (ESTADO_CIVIL, "soltera")]),
        ("Sin hijos", [(ESTADO_CIVIL, "Sin hijos")]),
        ("Se prefiere soltero", [(ESTADO_CIVIL, "soltero")]),
    ],
)
def test_estado_civil_neutro_solo_alerta_estado_civil(texto, esperado):
    assert _reglas(texto) == esperado
    assert not esta_bloqueada(revisar_texto(texto))
