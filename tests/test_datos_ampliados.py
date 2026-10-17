"""Ofertas ampliadas y volantes de #223: formato de ficha, fuente citada y sin datos de contacto.

Quedan fuera de data/ofertas a propósito (sin grabación no corren en modo reproducir); estas pruebas
cuidan que sigan siendo entradas válidas para Mercado cuando se graben.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image

from puente.agents import mercado
from puente.agents.perfil import quitar_cedula_y_telefono

DATA = Path(__file__).resolve().parents[1] / "data"
FICHAS = sorted((DATA / "ofertas_ampliadas").glob("*.txt"))
VOLANTES = sorted(p for p in (DATA / "volantes").iterdir() if p.suffix.lower() in mercado.EXTENSIONES_IMAGEN)
FUENTES = (DATA / "FUENTES.md").read_text(encoding="utf-8")
CAMPOS = ("ID", "Fuente", "Puesto", "Empresa de prueba", "Ubicación publicada", "Salario publicado", "Requisitos publicados")


def test_hay_ofertas_y_volantes_nuevos_fuera_de_la_demo():
    assert len(FICHAS) >= 40
    assert len(VOLANTES) >= 5
    en_demo = {p.stem for p in (DATA / "ofertas").iterdir()}
    assert not en_demo & {p.stem for p in FICHAS}, "una ficha sin grabación entró a data/ofertas"


@pytest.mark.parametrize("ficha", FICHAS, ids=lambda p: p.stem)
def test_ficha_con_formato_fuente_y_sin_contacto(ficha):
    texto = ficha.read_text(encoding="utf-8")
    campos = dict(linea.split(": ", 1) for linea in texto.splitlines() if ": " in linea)
    for campo in CAMPOS:
        assert campos.get(campo), f"{ficha.name}: falta «{campo}»"
    assert campos["ID"] == ficha.stem
    assert re.fullmatch(r"Empresa [A-Z]{1,2} \(.+\)", campos["Empresa de prueba"])
    assert re.search(rf"\| {re.escape(ficha.stem)} \|.*\[Enlace\]\(https://", FUENTES), "sin URL en FUENTES.md"
    assert "@" not in texto and "wa.me" not in texto
    assert quitar_cedula_y_telefono(texto) == texto, "la ficha trae un teléfono o una cédula"


@pytest.mark.parametrize("volante", VOLANTES, ids=lambda p: p.name)
def test_volante_es_una_imagen_que_mercado_acepta(volante):
    with Image.open(volante) as img:
        img.verify()
    ident = volante.name.split("_")[0]
    assert re.search(rf"\| {ident} \|.*\(https://", FUENTES), "sin fuente en FUENTES.md"
    assert mercado.a_entrada(str(volante)).es_archivo
