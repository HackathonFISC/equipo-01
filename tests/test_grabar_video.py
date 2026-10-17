"""Grabaciones del video (#194): el guion de Yaritza y `scripts/grabar_video.py`, sin red ni claves.

Lo crítico es que el script pida al LLM exactamente lo mismo que el video ejecuta en `LLM_MODO=reproducir`: si el prompt
cambia en una letra, el hash cambia y la grabación no sirve. Por eso un LLM espía en la capa común (`puente.llm`) anota
sistema, prompt, esquema y hash de cada llamada y se comparan los dos caminos: el del script (las funciones del repo) y
el real (la vista por HTTP y el `main` de cada CLI). Esas pruebas no dependen de las grabaciones y siempre corren.

Las grabaciones las hace una persona con clave (`scripts/grabar_video.sh`). Mientras no existan, su prueba de
reproducción se omite, como los evals con «omitido (sin grabación)». Cuando el script ya las grabó, anota sus hashes en
`data/demo/grabaciones_video.json`, y desde ahí un cambio de prompt, esquema o taxonomía que las deje sin uso falla.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import app.server as srv
import puente.llm as llm
from puente.agents import micromision, redactor
from puente.agents.perfil import (
    MAX_PREGUNTAS,
    PREGUNTAS_ENTREVISTA,
    quitar_cedula_y_telefono,
    quitar_pii,
)
from puente.guardrails import limpiar_entrada
from puente.schemas import Oferta

RAIZ = Path(__file__).resolve().parents[1]
DIR_GRABACIONES = RAIZ / "data" / "grabaciones"

_spec = importlib.util.spec_from_file_location("grabar_video", RAIZ / "scripts" / "grabar_video.py")
gv = importlib.util.module_from_spec(_spec)
sys.modules["grabar_video"] = gv  # antes de cargarlo: sus dataclasses buscan su módulo
_spec.loader.exec_module(gv)

GUION = gv.cargar_guion()
RESPUESTAS = [paso["respuesta"] for paso in GUION["entrevista"]]
TEXTO_RETO = GUION["micromision"]["texto"]

# Lo que devuelve el espía por esquema: basta con que valide; estas pruebas miran lo que se pide, no lo que vuelve.
SALIDAS_ESPIA = {
    "PerfilExtraido": {"habilidades": []},
    "TextoRedactado": {"texto_nuevo": "texto del espía"},
    "RespuestaExtraida": {"respuesta": "5.45", "cita": None},
}


@pytest.fixture(autouse=True)
def _sin_env_ni_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ni el `.env` de quien corre las pruebas ni llamadas reales: con una clave y LLM_MODO=vivo gastaría de verdad."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(llm, "_cliente_por_defecto", lambda *args, **kwargs: pytest.fail("llamada real al modelo"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LLM_PROVEEDOR", "anthropic")
    monkeypatch.setenv("PUENTE_MODELO_LLM", "claude-opus-5-5")
    monkeypatch.delenv("LLM_PROVEEDOR_RESPALDO", raising=False)
    monkeypatch.delenv("PUENTE_TAXONOMIA", raising=False)
    monkeypatch.setenv("PUENTE_DB", str(tmp_path / "sin_base.db"))  # la CLI del redactor no abre ninguna base
    monkeypatch.setattr(srv, "llm_perfil_joven", srv.llm_puente)  # Perfil vía puente.llm, como en la demo
    monkeypatch.setattr(srv, "_envios_perfil", {})  # el límite por minuto empieza en cero


@pytest.fixture
def espia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """LLM espía en la capa común: la llamada pasa por `_despachar` de verdad (configuración, hash) y, en lugar de
    leer una grabación, devuelve una salida fija. Anota sistema, prompt, esquema, modelo y hash."""
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(tmp_path / "sin_grabaciones"))
    llamadas: list[dict] = []
    original = llm.clave_grabacion

    def anotar(proveedor, modelo, sistema, prompt, esquema, archivos_bytes):
        clave = original(proveedor, modelo, sistema, prompt, esquema, archivos_bytes)
        llamadas.append(
            {"modelo": modelo, "sistema": sistema, "prompt": prompt, "esquema": esquema.__name__, "clave": clave}
        )
        return clave

    def responder(directorio, clave, esquema, **contexto):
        return esquema.model_validate(SALIDAS_ESPIA[esquema.__name__])

    monkeypatch.setattr(llm, "clave_grabacion", anotar)
    monkeypatch.setattr(llm, "_leer_grabacion", responder)
    return llamadas


def _tomar(llamadas: list[dict]) -> list[dict]:
    copia = list(llamadas)
    llamadas.clear()
    return copia


@pytest.fixture
def vista(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """El tablero con su propio estado, base y outbox, y un TestClient que habla con él como el navegador."""
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "vista.db")
    monkeypatch.setattr(srv, "ESTADO_GLOBAL", estado)
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")
    with TestClient(srv.app) as cliente:
        yield cliente


# ---------------------------------------------------------------- el guion


def test_guion_tiene_cinco_respuestas_en_el_orden_de_las_preguntas() -> None:
    entrevista = GUION["entrevista"]
    assert len(entrevista) == MAX_PREGUNTAS == len(PREGUNTAS_ENTREVISTA) == 5
    assert [paso["pregunta"] for paso in entrevista] == list(PREGUNTAS_ENTREVISTA)
    # Cada respuesta contesta su pregunta, no la de al lado (en caso_yaritza.json la primera dice la edad).
    contesta = [("fonda", "clientes", "vuelto"), ("otros trabajos",), ("voluntariado",), ("celular", "cocinar"), ("me pide",)]
    for paso, palabras in zip(entrevista, contesta):
        for palabra in palabras:
            assert palabra in paso["respuesta"].lower(), (paso["pregunta"], palabra)
    for respuesta in RESPUESTAS:
        assert respuesta == respuesta.strip() and 3 <= len(respuesta) <= srv.MAX_CARACTERES_RESPUESTA


def test_guion_sin_cedula_ni_telefono_ni_direccion() -> None:
    """Si la limpieza de PII cambiara una respuesta, la vista avisaría al joven y el prompt ya no sería el del guion."""
    for texto in [*RESPUESTAS, TEXTO_RETO]:
        assert quitar_cedula_y_telefono(texto) == texto
        assert quitar_pii(texto) == texto
        assert limpiar_entrada(texto) == texto
    completo = json.dumps(GUION, ensure_ascii=False)
    assert quitar_cedula_y_telefono(completo) == completo
    assert not {"cedula", "cédula", "telefono", "teléfono", "direccion", "dirección"} & set(GUION["formulario"])


def test_formulario_es_el_del_fixture_con_un_corregimiento_que_acepta_la_vista() -> None:
    caso = json.loads((RAIZ / "tests/fixtures/caso_yaritza.json").read_text(encoding="utf-8"))
    yaritza = next(e for e in caso["entradas_perfil"] if e["id"] == "yaritza")
    formulario = GUION["formulario"]
    for campo in ("nombre", "edad", "salario_minimo", "presupuesto_pasaje_diario", "horario_clases"):
        assert formulario[campo] == yaritza[campo], campo
    # El fixture dice Barrio Colón, que no es origen de data/traslados.csv: la vista lo rechazaría.
    assert formulario["corregimiento"] == "La Chorrera" in srv.corregimientos_con_traslado()
    srv.PerfilJovenRequest.model_validate({**formulario, "respuestas": RESPUESTAS})


def test_micromision_trae_un_solo_numero_nuevo_y_el_respaldo_tambien_lo_saca() -> None:
    reto = micromision.cargar_retos()[GUION["micromision"]["reto_id"]]
    dados = set(micromision._numeros(reto.enunciado))
    nuevos = [n for n in dict.fromkeys(micromision._numeros(TEXTO_RETO)) if n not in dados]
    assert nuevos == [Decimal(reto.respuesta_esperada)] == [Decimal("5.45")]
    sin_llm = micromision.extraer_respuesta(TEXTO_RETO, reto, llm=None)
    assert (sin_llm.valor, sin_llm.metodo) == ("5.45", "respaldo")
    assert micromision.calificar(reto, sin_llm)["resultado"] == micromision.CORRECTO


# ---------------------------------------------------------------- el script pide lo mismo que el camino real


def test_chat_el_script_pide_lo_mismo_que_la_vista(espia, vista, tmp_path: Path) -> None:
    """El chat de /joven (fase 2) por HTTP, el formulario de la fase 1 con las mismas respuestas (#201: una grabación
    sirve para las dos) y el camino de `verificar` piden el mismo prompt y el mismo hash que el script al grabar."""
    _tomar(espia)
    gv.chat_por_funciones(GUION, tmp_path)
    del_script = _tomar(espia)

    r = vista.post("/api/joven/entrevista", json=GUION["formulario"])
    assert r.status_code == 201, r.text
    pid, cabeceras = r.json()["perfil_id"], {"X-Token-Joven": r.json()["token"]}
    for indice, respuesta in enumerate(RESPUESTAS):
        r = vista.post(f"/api/joven/{pid}/entrevista", json={"indice": indice, "respuesta": respuesta}, headers=cabeceras)
        assert r.status_code == 200 and r.json()["respuesta_guardada"] == respuesta, r.text
    r = vista.post(f"/api/joven/{pid}/entrevista/terminar", json={}, headers=cabeceras)
    assert r.status_code == 201, r.text
    del_chat = _tomar(espia)

    r = vista.post("/api/joven/perfil", json={**GUION["formulario"], "respuestas": RESPUESTAS})
    assert r.status_code == 201, r.text
    de_la_fase_1 = _tomar(espia)

    assert gv.chat_por_la_vista(GUION, tmp_path)["listo"] is True
    de_verificar = _tomar(espia)

    assert len(del_script) == 1
    assert del_script == del_chat == de_la_fase_1 == de_verificar
    llamada = del_script[0]
    assert (llamada["esquema"], llamada["sistema"], llamada["modelo"]) == ("PerfilExtraido", None, "claude-opus-5-5")
    for pregunta, respuesta in zip(PREGUNTAS_ENTREVISTA, RESPUESTAS):
        assert f"P: {pregunta}\nR: {respuesta}" in llamada["prompt"]  # cada respuesta junto a su pregunta


def test_redactor_el_script_pide_lo_mismo_que_la_cli(espia, tmp_path: Path) -> None:
    _tomar(espia)
    gv.redactor_por_funciones(GUION, tmp_path)
    del_script = _tomar(espia)
    assert redactor.main(["--oferta", redactor.ID_OFERTA_DEMO], escribir=lambda _texto: None) == 0
    de_la_cli = _tomar(espia)
    gv.redactor_por_la_cli(GUION, tmp_path)
    de_verificar = _tomar(espia)

    assert len(del_script) == 1
    assert del_script == de_la_cli == de_verificar
    llamada = del_script[0]
    assert (llamada["esquema"], llamada["sistema"]) == ("TextoRedactado", redactor.INSTRUCCIONES)
    assert "edad entre 18 a 25 años" in llamada["prompt"] and "Cambios:" in llamada["prompt"]


def test_redactor_la_oferta_del_tablero_es_la_del_fixture(tmp_path: Path) -> None:
    """Si la base de quien corre la CLI ya trae OF-BLOQ-EDAD-04 (la del tablero de la demo), la toma de ahí. El prompt
    sale del puesto, los requisitos y el corregimiento, que tienen que ser los mismos que en el fixture."""
    estado = srv.obtener_datos_iniciales_demo(db_path=tmp_path / "tablero.db")
    del_tablero = next(o for o in estado["ofertas"] if o["id"] == redactor.ID_OFERTA_DEMO)
    fixture = json.loads(redactor.OFERTA_DEMO.read_text(encoding="utf-8"))
    for campo in ("puesto", "requisitos_texto", "corregimiento"):
        assert del_tablero[campo] == fixture[campo], campo


def test_micromision_el_script_pide_lo_mismo_que_la_cli(espia, tmp_path: Path, capsys) -> None:
    _tomar(espia)
    gv.micromision_por_funciones(GUION, tmp_path)
    del_script = _tomar(espia)
    assert micromision.main(["--reto", "CAJA-01", "--texto", TEXTO_RETO]) == 0
    de_la_cli = _tomar(espia)
    assert micromision.main(["--skill", "manejo_caja", "--texto", TEXTO_RETO]) == 0  # su primer reto es CAJA-01
    por_habilidad = _tomar(espia)
    gv.micromision_por_la_cli(GUION, tmp_path)
    de_verificar = _tomar(espia)
    capsys.readouterr()

    assert len(del_script) == 1
    assert del_script == de_la_cli == por_habilidad == de_verificar
    llamada = del_script[0]
    assert (llamada["esquema"], llamada["sistema"]) == ("RespuestaExtraida", micromision.INSTRUCCIONES)
    assert TEXTO_RETO in llamada["prompt"] and "5.45" not in llamada["prompt"].split("Texto del joven")[0]


# ---------------------------------------------------------------- grabar y verificar, de punta a punta y sin red


class ClienteFalso:
    """Imita `anthropic.Anthropic().messages.create`: responde según el esquema pedido y cuenta los pedidos."""

    def __init__(self, texto_redactado: str) -> None:
        self.messages = self
        self.pedidos: list[dict] = []
        self.texto_redactado = texto_redactado

    def create(self, **argumentos):
        self.pedidos.append(argumentos)
        propiedades = argumentos["output_config"]["format"]["schema"]["properties"]
        if "habilidades" in propiedades:
            salida = {
                "habilidades": [
                    {"skill_id": "atencion_cliente", "evidencia": "atiendo a los clientes"},
                    {"skill_id": "tomar_pedidos", "evidencia": "les tomo el pedido"},
                    {"skill_id": "manejo_caja", "evidencia": "cobro y doy el vuelto"},
                    {"skill_id": "cocina", "evidencia": "ayudo a cocinar"},
                ]
            }
        elif "texto_nuevo" in propiedades:
            salida = {"texto_nuevo": self.texto_redactado}
        else:
            salida = {"respuesta": "5.45", "cita": "le doy 5.45 de vuelto"}
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(salida, ensure_ascii=False))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )


def test_grabar_y_verificar_de_punta_a_punta_con_un_cliente_falso(tmp_path: Path, monkeypatch, capsys) -> None:
    """Lo que graba el script lo encuentran la vista y las CLI en reproducir; una segunda corrida no gasta."""
    oferta = Oferta.model_validate(json.loads(redactor.OFERTA_DEMO.read_text(encoding="utf-8")))
    legal = redactor.proponer_version(oferta, [], redactar=redactor.redactor_plantilla).texto_nuevo
    cliente = ClienteFalso(legal)
    carpeta = tmp_path / "grabaciones"
    monkeypatch.setattr(gv, "MANIFIESTO", tmp_path / "grabaciones_video.json")
    monkeypatch.setattr(llm, "_cliente_por_defecto", lambda *args, **kwargs: cliente)
    monkeypatch.setenv("LLM_MODO", "grabar")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(carpeta))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-falsa-de-prueba")
    monkeypatch.setenv("PUENTE_LLM_TOPE_USD", "1")
    llm.reiniciar_uso()

    lista = tmp_path / "lista.txt"
    assert gv.grabar(GUION, tmp_path, lista=lista) == 0, capsys.readouterr().out
    salida = capsys.readouterr().out
    assert len(cliente.pedidos) == 3 and salida.count("✔ grabada") == 3
    assert "Costo estimado de esta corrida: US$0.0180" in salida  # 3 × (1000 × 4 + 100 × 20) / 1e6
    grabadas = gv.leer_manifiesto()["llamadas"]
    assert set(grabadas) == {"chat", "redactor", "micromision"}
    archivos = sorted(carpeta.glob("*.json"))
    assert [a.stem for a in archivos] == sorted(grabadas.values())
    assert lista.read_text(encoding="utf-8").splitlines() == [
        *(str(carpeta / f"{grabadas[n]}.json") for n in ("chat", "redactor", "micromision")),
        str(tmp_path / "grabaciones_video.json"),
    ]
    for archivo in archivos:
        texto = archivo.read_text(encoding="utf-8")
        assert "sk-ant" not in texto and json.loads(texto)["modelo"] == "claude-opus-5-5"  # la clave nunca se guarda

    assert gv.grabar(GUION, tmp_path) == 0  # ya están y dan lo esperado: no vuelve a llamar
    assert len(cliente.pedidos) == 3
    assert capsys.readouterr().out.count("ya estaba grabada") == 3

    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setattr(llm, "_cliente_por_defecto", lambda *args, **kwargs: pytest.fail("reproducir no llama"))
    assert gv.verificar(GUION, tmp_path) == 0, capsys.readouterr().out
    salida = capsys.readouterr().out
    assert "Propuesta (llm)" in salida and "correcto · respuesta 5.45 · leída por llm" in salida
    assert "perfil listo · atencion_cliente, tomar_pedidos, manejo_caja, cocina" in salida


def test_grabar_sin_clave_no_llama_al_modelo_ni_escribe_nada(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("LLM_MODO", "grabar")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(tmp_path / "grabaciones"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(gv, "MANIFIESTO", tmp_path / "grabaciones_video.json")
    assert gv.grabar(GUION, tmp_path) == 2
    assert "Falta ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "grabaciones_video.json").exists() and not (tmp_path / "grabaciones").exists()


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="sin bash o git")
def test_script_de_bash_sin_clave_falla_claro_y_sin_traceback(tmp_path: Path) -> None:
    """En un repo vacío (sin .env), para no leer la clave de quien corre las pruebas."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "grabar_video.sh"
    shutil.copy(RAIZ / "scripts" / "grabar_video.sh", script)
    r = subprocess.run(["bash", str(script)], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 1
    assert "Falta ANTHROPIC_API_KEY en .env: no se llamó al modelo ni se gastó nada." in r.stderr
    assert "Traceback" not in r.stdout + r.stderr


# ---------------------------------------------------------------- las grabaciones del video, cuando existen


@pytest.mark.parametrize("nombre", [llamada.nombre for llamada in gv.LLAMADAS])
def test_grabacion_del_video_se_reproduce_si_existe(nombre: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(DIR_GRABACIONES))
    llamada = gv.LLAMADA[nombre]
    clave = gv.correr(llamada, GUION, tmp_path).clave  # en reproducir: si falta la grabación, el hash igual se anota
    assert clave is not None
    ruta = DIR_GRABACIONES / f"{clave}.json"
    grabadas = gv.leer_manifiesto().get("llamadas", {})
    if nombre in grabadas:
        assert grabadas[nombre] == clave, (
            f"{nombre}: el prompt ya no da el hash grabado para el video ({grabadas[nombre]}); "
            "vuelve a correr scripts/grabar_video.sh y sube lo que imprime"
        )
        assert ruta.is_file(), f"{nombre}: falta {ruta.name}; vuelve a correr scripts/grabar_video.sh"
    elif not ruta.is_file():
        pytest.skip(f"{nombre}: todavía sin grabación ({clave[:12]}…); la graba scripts/grabar_video.sh (#194)")

    corrida = gv.correr(llamada, GUION, tmp_path, camino_real=True)
    assert corrida.error == "" and corrida.clave == clave
    resultado = corrida.resultado
    if nombre == "chat":
        assert resultado["listo"] and {"manejo_caja", "atencion_cliente"} <= set(resultado["habilidades"])
    elif nombre == "redactor":
        assert resultado["redactado_por"] == "llm", resultado["nota"]  # la CLI imprime «Propuesta (llm): …»
    else:
        assert (resultado["resultado"], resultado["metodo"]) == ("correcto", "llm")
    assert llamada.revisar(resultado) == []
