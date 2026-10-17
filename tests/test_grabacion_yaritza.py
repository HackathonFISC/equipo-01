"""La demo sin conexión debe tener la grabación del prompt vigente de Perfil (#132)."""

import json
from pathlib import Path

from puente.agents.perfil import construir_perfil
from puente.graph import cargar_taxonomia_grafo
from puente.llm import clave_grabacion, generar_estructurado
from puente.rules.taxonomia import normalizar_habilidad
from puente.schemas import PerfilExtraido


RAIZ = Path(__file__).resolve().parents[1]


def test_yaritza_tiene_grabacion_para_el_prompt_vigente(monkeypatch):
    caso = json.loads((RAIZ / "tests/fixtures/caso_yaritza.json").read_text(encoding="utf-8"))
    entrada = next(p for p in caso["entradas_perfil"] if p["id"] == "yaritza")
    taxonomia = cargar_taxonomia_grafo()
    prompts = []

    def capturar_prompt(prompt):
        prompts.append(prompt)
        return PerfilExtraido()

    construir_perfil(
        entrada,
        taxonomia=taxonomia,
        normalizar=lambda habilidad: normalizar_habilidad(habilidad, taxonomia),
        llm=capturar_prompt,
    )
    assert len(prompts) == 1
    clave = clave_grabacion("anthropic", "claude-opus-5-5", None, prompts[0], PerfilExtraido, [])
    directorio = RAIZ / "data/grabaciones"
    ruta = directorio / f"{clave}.json"
    assert ruta.is_file(), f"falta la grabación {ruta.name} para el prompt vigente de Yaritza"

    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setenv("LLM_PROVEEDOR", "anthropic")
    monkeypatch.setenv("PUENTE_MODELO_LLM", "claude-opus-5-5")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(directorio))
    assert isinstance(generar_estructurado(prompts[0], PerfilExtraido, agente="perfil"), PerfilExtraido)
