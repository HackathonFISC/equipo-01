"""Configuración común de las pruebas: ninguna llama al modelo, carga un .env real ni toca la base de la demo."""

import os
import shutil
import tempfile
from pathlib import Path

# Antes de importar nada de puente/ o app/: algunos módulos leen PUENTE_DB, PUENTE_CHECKPOINTS o
# PUENTE_OUTBOX al importarse, y con el entorno de quien corre pytest (o el de la máquina de la
# demo) escribirían en la base y el outbox reales. Se fijan aquí, al cargar este archivo, y otra vez
# en pytest_configure. Los valores que traía el entorno quedan en RUTAS_ORIGINALES (#17, ING-3/DEM-6).
VARIABLES_RUTAS = {"PUENTE_DB": "puente.db", "PUENTE_CHECKPOINTS": "checkpoints.db", "PUENTE_OUTBOX": "outbox"}
RUTAS_ORIGINALES = {variable: os.environ.get(variable) for variable in VARIABLES_RUTAS}
CARPETA_PRUEBAS = Path(tempfile.mkdtemp(prefix="puente-pytest-"))


def _aislar_rutas_de_la_demo() -> None:
    for variable, nombre in VARIABLES_RUTAS.items():
        os.environ[variable] = str(CARPETA_PRUEBAS / nombre)


_aislar_rutas_de_la_demo()

import dotenv  # noqa: E402
import pytest  # noqa: E402

import puente.llm  # noqa: E402

VARIABLES_TELEGRAM = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID_EMPRESA", "TELEGRAM_CHATS_JOVENES")


def pytest_configure(config):
    """Repite el aislamiento por si un plugin cambió el entorno después de cargar este archivo."""
    _aislar_rutas_de_la_demo()


def pytest_unconfigure(config):
    shutil.rmtree(CARPETA_PRUEBAS, ignore_errors=True)


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
    llama a `load_dotenv()`; las pruebas que lo necesitan ponen su propio doble), y con `PUENTE_DB`,
    `PUENTE_CHECKPOINTS` y `PUENTE_OUTBOX` en un directorio temporal propio de cada prueba (aparte de `tmp_path`,
    para no chocar con los archivos que la prueba arme ahí). Una prueba que necesite Telegram fija sus variables y
    un transporte falso.
    """
    for variable in VARIABLES_TELEGRAM:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    carpeta = tmp_path_factory.mktemp("puente_entorno")
    for variable, nombre in VARIABLES_RUTAS.items():
        monkeypatch.setenv(variable, str(carpeta / nombre))
