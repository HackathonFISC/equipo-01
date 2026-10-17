"""CVs sintéticos de data/cvs y su perfil esperado al lado (#223).

Cada `data/cvs/perfil_NN.json` es lo que el agente Perfil debería sacar de `cv_NN_*.txt|pdf`:
toda habilidad existe en la taxonomía y su evidencia aparece literal en el CV ya limpio de PII, que es
lo que Perfil manda al LLM. Así los CVs sirven de casos de oro sin gastar una llamada. No están en
`data/perfiles/` para no cambiar el grupo de candidatos de la demo (Matching, redactor, tablero).
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from puente.agents.perfil import extraer_texto_cv, quitar_pii, tiene_evidencia
from puente.schemas import Perfil

DATA = Path(__file__).resolve().parents[1] / "data"
CVS = sorted(p for p in (DATA / "cvs").glob("cv_*") if p.suffix in {".txt", ".pdf"})
# Cédula y teléfono inventados de los CVs: 8-000-0011, 6000-0011...
PII_FICTICIA = re.compile(r"\b\d-000-00\d\d\b|\b6000-00\d\d\b")


def _numero(cv: Path) -> str:
    return cv.name.split("_")[1]


@pytest.fixture(scope="module")
def taxonomia() -> set[str]:
    with (DATA / "taxonomia.csv").open(encoding="utf-8") as f:
        return {fila["skill_id"] for fila in csv.DictReader(f)}


@pytest.fixture(scope="module")
def nodos() -> set[str]:
    with (DATA / "traslados.csv").open(encoding="utf-8") as f:
        return {fila["origen"] for fila in csv.DictReader(f)}


def test_hay_diez_cvs_en_varios_formatos():
    assert len(CVS) >= 10
    assert {cv.suffix for cv in CVS} == {".txt", ".pdf"}
    assert len({_numero(cv) for cv in CVS}) == len(CVS), "dos CVs con el mismo número"


@pytest.mark.parametrize("cv", CVS, ids=lambda p: p.name)
def test_cada_cv_tiene_su_perfil_con_evidencia_literal(cv, taxonomia, nodos):
    ruta_perfil = DATA / "cvs" / f"perfil_{_numero(cv)}.json"
    assert ruta_perfil.exists(), f"{cv.name} no tiene {ruta_perfil.name}"
    datos = json.loads(ruta_perfil.read_text(encoding="utf-8"))
    perfil = Perfil.model_validate(datos)
    assert datos.get("datos_sinteticos") is True
    assert perfil.corregimiento in nodos, f"{perfil.corregimiento} no está en traslados.csv"

    texto = extraer_texto_cv(cv)
    assert "CV sintético para Puente" in texto
    limpio = quitar_pii(texto)
    assert perfil.habilidades
    for habilidad in perfil.habilidades:
        assert habilidad.skill_id in taxonomia, habilidad.skill_id
        assert tiene_evidencia(habilidad.evidencia, [limpio]), f"{cv.name}: «{habilidad.evidencia}» no está literal"


@pytest.mark.parametrize("cv", CVS, ids=lambda p: p.name)
def test_la_pii_ficticia_del_cv_no_llega_al_llm(cv):
    texto = extraer_texto_cv(cv)
    limpio = quitar_pii(texto)
    assert not PII_FICTICIA.findall(limpio), PII_FICTICIA.findall(limpio)
    assert "Calle Ficticia" not in limpio and "Barriada Ficticia" not in limpio
