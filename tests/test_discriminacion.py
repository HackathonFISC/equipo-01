"""Reglas de discriminación (#5): regex sobre el texto original, sin LLM."""

import pytest

from puente.rules.discriminacion import (
    BUENA_PRESENCIA,
    EDAD,
    ESTADO_CIVIL,
    FOTO,
    SEXO,
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
    assert [(h.regla, h.bloquea) for h in hallazgos] == [(BUENA_PRESENCIA, False), (EDAD, True)]
    assert hallazgos[1].frase == "18 a 25 años"
    assert "art. 2" in hallazgos[1].como_texto()


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
        "Se busca cajera con manejo de caja",
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
