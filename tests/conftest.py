"""Configuración común de las pruebas: ninguna llama al modelo ni carga un .env real."""

import pytest

import puente.llm


@pytest.fixture(autouse=True)
def _llm_sin_red_ni_dotenv(monkeypatch):
    """Todas las pruebas parten en modo `reproducir` y sin cargar `.env`.

    Así una prueba que llegue a `puente.llm` sin querer no gasta crédito ni usa claves de la máquina: sin grabación
    falla con `grabacion_faltante`. Las pruebas de `tests/test_llm.py` fijan `LLM_MODO` por su cuenta.
    """
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setattr(puente.llm, "_dotenv_cargado", True)
