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
        ("Estado civil: soltera", ESTADO_CIVIL, "Estado civil: soltera"),
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
