"""Configuración común de las pruebas: ninguna llama al modelo ni carga un .env real."""

import dotenv
import pytest

import puente.llm

VARIABLES_TELEGRAM = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID_EMPRESA", "TELEGRAM_CHATS_JOVENES")


@pytest.fixture(autouse=True)
def _llm_sin_red_ni_dotenv(monkeypatch):
    """Todas las pruebas parten en modo `reproducir` y sin cargar `.env`.

    Así una prueba que llegue a `puente.llm` sin querer no gasta crédito ni usa claves de la máquina: sin grabación
    falla con `grabacion_faltante`. Las pruebas de `tests/test_llm.py` fijan `LLM_MODO` por su cuenta.
    """
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setattr(puente.llm, "_dotenv_cargado", True)


@pytest.fixture(autouse=True)
def _postulacion_sin_telegram_y_en_tmp(monkeypatch, tmp_path_factory):
    """Postulación (#12) nunca escribe en la raíz del repo ni manda mensajes reales.

    Sin `TELEGRAM_*` del entorno de quien corre pytest, sin el `.env` de su máquina (`python -m puente.graph`
    llama a `load_dotenv()`; las pruebas que lo necesitan ponen su propio doble), y con `PUENTE_DB` y
    `PUENTE_OUTBOX` en un directorio temporal propio de cada prueba (aparte de `tmp_path`, para no chocar con
    los archivos que la prueba arme ahí). Una prueba que necesite Telegram fija sus variables y un transporte falso.
    """
    for variable in VARIABLES_TELEGRAM:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    carpeta = tmp_path_factory.mktemp("puente_entorno")
    monkeypatch.setenv("PUENTE_DB", str(carpeta / "puente.db"))
    monkeypatch.setenv("PUENTE_OUTBOX", str(carpeta / "outbox"))
