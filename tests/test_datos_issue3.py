"""Los datos de demostración del issue #3 respetan el contrato compartido."""

import csv
import json
from pathlib import Path

import pytest

from puente.agents.mercado import UMBRAL_SIMILITUD, similitud
from puente.rules.taxonomia import cargar_taxonomia, normalizar_habilidad
from puente.schemas import Perfil

DATA = Path(__file__).resolve().parents[1] / "data"

# Frases que las ofertas de data/ofertas publican tal cual como requisito, con la habilidad a la que corresponden.
# Sin ellas como sinónimos, Mercado las normaliza a None y las descarta (revisión del PR #27).
FRASES_LITERALES_DE_OFERTAS = {
    "Trabajo en equipo": "trabajo_equipo",
    "Trabajo en alturas": "seguridad_altura",
    "Aseo general": "limpieza",
    "Limpieza general": "limpieza",
    "Prospección de clientes": "captacion_clientes",
    "Cierre de ventas": "negociacion_ventas",
    "Capacidad de negociación": "negociacion_ventas",
    "Elaboración de bebidas": "preparar_bebidas_calientes",
    "Pedidos": "tomar_pedidos",
    "Comunicación": "comunicacion_efectiva",
    "Comunicación con el cliente": "atencion_cliente",
    "Soporte al cliente": "atencion_cliente",
    "Asesoría en ventas": "ventas",
}


def test_perfiles_sinteticos_validan_y_justifican_habilidades():
    with (DATA / "taxonomia.csv").open(encoding="utf-8", newline="") as archivo:
        skill_ids = {fila["skill_id"] for fila in csv.DictReader(archivo)}
    perfiles = [json.loads(ruta.read_text(encoding="utf-8")) for ruta in (DATA / "perfiles").glob("*.json")]
    assert len(perfiles) >= 10
    assert len({p["id"] for p in perfiles}) == len(perfiles)
    for datos in perfiles:
        Perfil.model_validate(datos)
        assert datos["datos_sinteticos"] is True
        for habilidad in datos["habilidades"]:
            assert habilidad["skill_id"] in skill_ids
            assert habilidad["evidencia"] in datos["experiencia_texto"]


def test_fichas_de_ofertas_tienen_fuente_y_empresa_de_prueba():
    fichas = list((DATA / "ofertas").glob("*.txt"))
    assert len(fichas) >= 20
    fuentes = (DATA / "FUENTES.md").read_text(encoding="utf-8")
    for ruta in fichas:
        texto = ruta.read_text(encoding="utf-8")
        assert f"ID: {ruta.stem}" in texto
        assert "Empresa de prueba: " in texto
        assert "URL: " not in texto
        assert f"| {ruta.stem} |" in fuentes


def test_casos_sinteticos_de_duplicados_incluyen_sucursales_distintas():
    datos = json.loads((DATA / "ofertas_prueba" / "duplicados.json").read_text(encoding="utf-8"))
    assert datos["tipo_dato"] == "sintetico_de_prueba"
    assert len(datos["pares_duplicados"]) == 2
    for par in datos["pares_duplicados"]:
        assert par["a"]["texto"] != par["b"]["texto"]
        assert par["a"]["corregimiento"] == par["b"]["corregimiento"]
        assert similitud(par["a"]["texto"], par["b"]["texto"]) >= UMBRAL_SIMILITUD
    distinto = datos["par_distinto"]
    assert distinto["a"]["empresa"] == distinto["b"]["empresa"]
    assert distinto["a"]["puesto"] == distinto["b"]["puesto"]
    assert distinto["a"]["corregimiento"] != distinto["b"]["corregimiento"]


def test_datos_sin_marcadores_de_conflicto_y_taxonomia_sin_repetidos():
    """Un merge mal resuelto deja marcadores que csv.DictReader lee como filas; las pruebas de arriba pasarían igual."""
    for ruta in DATA.rglob("*"):
        if ruta.is_file() and ruta.suffix in {".csv", ".md", ".json", ".txt"}:
            contenido = ruta.read_text(encoding="utf-8")
            # En markdown, «=======» también subraya un título: solo cuenta si el archivo trae un inicio de conflicto.
            separador_cuenta = ruta.suffix != ".md" or "<<<<<<<" in contenido
            for linea in contenido.splitlines():
                marcador = linea.startswith(("<<<<<<<", ">>>>>>>")) or (separador_cuenta and linea.rstrip() == "=======")
                assert not marcador, f"{ruta.relative_to(DATA)}: marcador de conflicto «{linea[:20]}»"
    with (DATA / "taxonomia.csv").open(encoding="utf-8", newline="") as archivo:
        skill_ids = [fila["skill_id"].strip() for fila in csv.DictReader(archivo)]
    assert len(skill_ids) == len(set(skill_ids)), "skill_id repetido en taxonomia.csv"


@pytest.mark.parametrize(("frase", "skill_id"), FRASES_LITERALES_DE_OFERTAS.items())
def test_frases_literales_de_las_ofertas_se_normalizan(frase, skill_id):
    fichas = " ".join(ruta.read_text(encoding="utf-8") for ruta in (DATA / "ofertas").glob("*.txt"))
    assert frase in fichas, f"«{frase}» ya no aparece en ninguna ficha de data/ofertas"
    assert normalizar_habilidad(frase, cargar_taxonomia(DATA / "taxonomia.csv")) == skill_id
