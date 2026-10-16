"""Comprueba que el esqueleto importa y que las dependencias clave están instaladas."""
import importlib

import pytest

MODULOS = [
    "puente.schemas",
    "puente.graph",
    "puente.guardrails",
    "puente.agents.mercado",
    "puente.agents.perfil",
    "puente.agents.matching",
    "puente.agents.formacion",
    "puente.agents.auditor",
    "puente.agents.postulacion",
    "puente.solver.asignacion",
    "puente.rules.discriminacion",
    "puente.rules.taxonomia",
    "puente.rules.traslados",
    "puente.tools.db",
    "puente.tools.pdf",
    "puente.tools.telegram",
    "puente.tools.calendario",
]


@pytest.mark.parametrize("modulo", MODULOS)
def test_modulo_importa(modulo):
    importlib.import_module(modulo)


@pytest.mark.parametrize(
    "dependencia",
    ["langgraph", "langchain_google_genai", "langsmith", "ortools", "streamlit", "fpdf", "ics", "pydantic", "yaml"],
)
def test_dependencia_instalada(dependencia):
    importlib.import_module(dependencia)
