"""Capa común de LLM (#45): sin red ni claves, con clientes dobles, tmp_path y variables de entorno falsas."""

import base64
import json
import socket
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from puente import llm
from puente.agents.mercado import OfertaExtraida
from puente.llm import ErrorLLM, clave_grabacion, configuracion, generar_estructurado, generar_texto

VARIABLES = (
    "LLM_PROVEEDOR",
    "PUENTE_MODELO_LLM",
    "LLM_MODO",
    "LLM_DIR_GRABACIONES",
    "LLM_PROVEEDOR_RESPALDO",
    "LLM_MODELO_RESPALDO",
    "PUENTE_LLM_FALLBACKS",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)
PNG = b"\x89PNG\r\n\x1a\n" + b"sintetico"
PDF = b"%PDF-1.4 sintetico"


class Salida(BaseModel):
    respuesta: str
    puntos: int = 0


@pytest.fixture(autouse=True)
def entorno(monkeypatch, tmp_path):
    for nombre in VARIABLES:
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(tmp_path / "grabaciones"))
    llm.reiniciar_uso()
    llm._clientes.clear()
    yield
    llm.reiniciar_uso()
    llm._clientes.clear()


def respuesta(parsed=None, texto="", stop="end_turn", entrada=100, salida=50, detalle=None):
    return SimpleNamespace(
        stop_reason=stop,
        stop_details=detalle,
        parsed_output=parsed,
        content=[SimpleNamespace(type="text", text=texto)] if texto else [],
        usage=SimpleNamespace(input_tokens=entrada, output_tokens=salida),
    )


class ClienteAnthropic:
    """Imita messages.parse / messages.create (y la versión beta) y guarda los argumentos recibidos."""

    def __init__(self, *respuestas, error=None):
        self.respuestas = list(respuestas)
        self.error = error
        self.llamadas: list[tuple[str, dict]] = []
        self.messages = SimpleNamespace(parse=self._llamar("parse"), create=self._llamar("create"))
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(parse=self._llamar("beta.parse"), create=self._llamar("beta.create"))
        )

    def _llamar(self, nombre):
        def f(**kwargs):
            self.llamadas.append((nombre, kwargs))
            if self.error:
                raise self.error
            return self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]

        return f

    @property
    def kwargs(self):
        return self.llamadas[-1][1]


class ClienteGemini:
    """Imita ChatGoogleGenerativeAI: with_structured_output(esquema).invoke(...) e invoke(...).content."""

    def __init__(self, salida=None, texto=""):
        self.salida = salida
        self.texto = texto
        self.mensajes = []
        self.esquema = None

    def with_structured_output(self, esquema):
        self.esquema = esquema
        return SimpleNamespace(invoke=self._invocar_estructurado)

    def _invocar_estructurado(self, mensajes):
        self.mensajes.append(mensajes)
        return self.salida

    def invoke(self, mensajes):
        self.mensajes.append(mensajes)
        return SimpleNamespace(content=self.texto)


class ClienteGeminiRoto:
    def with_structured_output(self, esquema):
        def invocar(mensajes):
            raise RuntimeError("falla del proveedor")

        return SimpleNamespace(invoke=invocar)


class ClienteProhibido:
    def __getattr__(self, nombre):
        raise AssertionError(f"no debía usarse el cliente ({nombre})")


# ---------------------------------------------------------------- API pública


def test_api_publica_estructurado_y_texto():
    assert callable(llm.generar_estructurado) and callable(llm.generar_texto)
    cliente = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok", puntos=3)))
    resultado = generar_estructurado("Extrae", Salida, sistema="Eres un extractor", agente="mercado", cliente=cliente)
    assert resultado == Salida(respuesta="ok", puntos=3)
    nombre, kw = cliente.llamadas[0]
    assert nombre == "parse"
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_format"] is Salida
    assert kw["output_config"] == {"effort": "low"}
    assert kw["system"] == "Eres un extractor"
    assert kw["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Extrae"}]}]
    for prohibido in ("tool_choice", "temperature", "top_p", "top_k", "thinking", "tools"):
        assert prohibido not in kw

    texto = ClienteAnthropic(
        SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(type="thinking", thinking="pensando"),
                SimpleNamespace(type="text", text="Hola "),
                SimpleNamespace(type="text", text="Yaritza"),
            ],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
    )
    assert generar_texto("Saluda", cliente=texto, effort="medium", max_tokens=100) == "Hola Yaritza"
    nombre, kw = texto.llamadas[0]
    assert nombre == "create"
    assert kw["output_config"] == {"effort": "medium"} and kw["max_tokens"] == 100
    assert "output_format" not in kw and "system" not in kw


def test_sistema_con_cache_control_si_se_pide():
    cliente = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok")))
    generar_estructurado("p", Salida, sistema="Instrucciones largas", cache_sistema=True, cliente=cliente)
    assert cliente.kwargs["system"] == [
        {"type": "text", "text": "Instrucciones largas", "cache_control": {"type": "ephemeral"}}
    ]


def test_archivos_como_bloques(tmp_path):
    png = tmp_path / "oferta.png"
    pdf = tmp_path / "oferta.pdf"
    png.write_bytes(PNG)
    pdf.write_bytes(PDF)
    cliente = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok")))
    generar_estructurado("Lee", Salida, [png, str(pdf), ("image/webp", b"RIFFxxxx")], cliente=cliente)
    bloques = cliente.kwargs["messages"][0]["content"]
    assert [b["type"] for b in bloques] == ["image", "document", "image", "text"]
    assert bloques[0]["source"] == {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode()}
    assert bloques[1]["source"] == {
        "type": "base64",
        "media_type": "application/pdf",
        "data": base64.b64encode(PDF).decode(),
    }
    assert bloques[2]["source"]["media_type"] == "image/webp"
    assert "\n" not in bloques[0]["source"]["data"]
    assert bloques[3] == {"type": "text", "text": "Lee"}


def test_audio_y_otros_tipos_dan_error(tmp_path):
    ogg = tmp_path / "nota.ogg"
    ogg.write_bytes(b"OggS")
    with pytest.raises(ErrorLLM, match="#55") as info:
        generar_estructurado("Transcribe", Salida, [ogg], cliente=ClienteProhibido())
    assert info.value.causa == "mime"
    with pytest.raises(ErrorLLM, match="imágenes y PDF"):
        generar_estructurado("x", Salida, [("text/plain", b"hola")], cliente=ClienteProhibido())
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("x", Salida, [tmp_path / "no_existe.png"], cliente=ClienteProhibido())
    assert info.value.causa == "archivo"


def test_extraccion_oferta_con_doble_y_costo():
    oferta = OfertaExtraida(puesto="Cajera", empresa="Comercial Ejemplo (sintético)", salario_mensual=650.0)
    cliente = ClienteAnthropic(respuesta(parsed=oferta, entrada=2000, salida=500))
    resultado = generar_estructurado("Oferta sintética", OfertaExtraida, agente="mercado", cliente=cliente)
    assert isinstance(resultado, OfertaExtraida) and resultado.puesto == "Cajera"
    fila = llm.uso_por_agente()["mercado"]
    assert fila["llamadas"] == 1 and fila["reproducidas"] == 0
    assert fila["tokens_entrada"] == 2000 and fila["tokens_salida"] == 500
    assert fila["costo_usd"] == pytest.approx(2000 * 4 / 1e6 + 500 * 20 / 1e6)


def test_contador_acumula_por_agente_y_se_reinicia():
    cliente = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok"), entrada=1_000_000, salida=0))
    generar_estructurado("a", Salida, agente="perfil", cliente=cliente)
    generar_estructurado("b", Salida, agente="perfil", cliente=cliente)
    generar_estructurado("c", Salida, agente="mercado", cliente=cliente)
    uso = llm.uso_por_agente()
    assert uso["perfil"]["llamadas"] == 2 and uso["perfil"]["costo_usd"] == pytest.approx(8.0)
    assert uso["mercado"]["llamadas"] == 1
    uso["perfil"]["llamadas"] = 99  # es una copia
    assert llm.uso_por_agente()["perfil"]["llamadas"] == 2
    llm.reiniciar_uso()
    assert llm.uso_por_agente() == {}


# ---------------------------------------------------------------- stop_reason y errores


class RespuestaExplosiva(SimpleNamespace):
    @property
    def parsed_output(self):
        raise AssertionError("no se debe leer parsed_output")


def test_refusal_y_max_tokens_levantan_error_sin_leer_la_salida():
    uso = SimpleNamespace(input_tokens=1, output_tokens=1)
    rechazo = RespuestaExplosiva(
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="no puedo ayudar"),
        content=[],
        usage=uso,
    )
    with pytest.raises(ErrorLLM, match="rechazó") as info:
        generar_estructurado("x", Salida, agente="perfil", cliente=ClienteAnthropic(rechazo))
    assert info.value.causa == "refusal" and info.value.agente == "perfil"
    assert "cyber" in str(info.value) and "claude-opus-5-5" in str(info.value)

    cortado = RespuestaExplosiva(stop_reason="max_tokens", stop_details=None, content=[], usage=uso)
    with pytest.raises(ErrorLLM, match="límite de tokens") as info:
        generar_estructurado("x", Salida, cliente=ClienteAnthropic(cortado))
    assert info.value.causa == "max_tokens"
    with pytest.raises(ErrorLLM) as info:
        generar_texto("x", cliente=ClienteAnthropic(cortado))
    assert info.value.causa == "max_tokens"


def test_refusal_reintenta_una_sola_vez_con_el_respaldo(monkeypatch):
    monkeypatch.setenv("LLM_PROVEEDOR_RESPALDO", "gemini")
    principal = ClienteAnthropic(respuesta(stop="refusal"))
    respaldo = ClienteGemini(salida=Salida(respuesta="desde gemini"))
    resultado = generar_estructurado("x", Salida, agente="mercado", cliente=principal, cliente_respaldo=respaldo)
    assert resultado.respuesta == "desde gemini"
    assert len(principal.llamadas) == 1 and len(respaldo.mensajes) == 1
    assert llm.uso_por_agente()["mercado"]["llamadas"] == 1


def test_respaldo_que_tambien_falla_no_genera_un_tercer_intento(monkeypatch):
    monkeypatch.setenv("LLM_PROVEEDOR_RESPALDO", "gemini")
    monkeypatch.setenv("LLM_MODELO_RESPALDO", "gemini-otro")
    principal = ClienteAnthropic(respuesta(stop="refusal"))
    with pytest.raises(ErrorLLM, match="gemini-otro") as info:
        generar_estructurado("x", Salida, cliente=principal, cliente_respaldo=ClienteGeminiRoto())
    assert info.value.proveedor == "gemini"
    assert len(principal.llamadas) == 1


def test_refusal_sin_respaldo_configurado_es_error():
    cliente = ClienteAnthropic(respuesta(stop="refusal"))
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("x", Salida, cliente=cliente, cliente_respaldo=ClienteProhibido())
    assert info.value.causa == "refusal"


def test_max_tokens_no_dispara_el_respaldo(monkeypatch):
    monkeypatch.setenv("LLM_PROVEEDOR_RESPALDO", "gemini")
    cliente = ClienteAnthropic(respuesta(stop="max_tokens"))
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("x", Salida, cliente=cliente, cliente_respaldo=ClienteProhibido())
    assert info.value.causa == "max_tokens"


def _peticion():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _http(codigo):
    return httpx2.Response(codigo, request=_peticion())


@pytest.mark.parametrize(
    ("error", "causa", "texto"),
    [
        (anthropic.APITimeoutError(request=_peticion()), "timeout", "a tiempo"),
        (anthropic.APIConnectionError(request=_peticion()), "conexion", "conexión"),
        (anthropic.RateLimitError("limite", response=_http(429), body=None), "limite", "límite de uso"),
        (anthropic.AuthenticationError("no", response=_http(401), body=None), "estado_http", "ANTHROPIC_API_KEY"),
        (anthropic.InternalServerError("boom", response=_http(500), body=None), "estado_http", "HTTP 500"),
    ],
)
def test_errores_del_sdk_se_traducen(error, causa, texto):
    with pytest.raises(ErrorLLM, match=texto) as info:
        generar_estructurado("x", Salida, agente="matching", cliente=ClienteAnthropic(error=error))
    assert info.value.causa == causa and info.value.agente == "matching" and info.value.proveedor == "anthropic"
    assert llm.uso_por_agente() == {}


def test_salida_invalida_y_sin_salida():
    with pytest.raises(Exception) as validacion:
        Salida.model_validate({"respuesta": 1})
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("x", Salida, cliente=ClienteAnthropic(error=validacion.value))
    assert info.value.causa == "salida_invalida"
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("x", Salida, cliente=ClienteAnthropic(respuesta(parsed=None)))
    assert info.value.causa == "sin_salida"


def test_cliente_real_se_crea_perezoso_con_reintentos_y_timeout(monkeypatch):
    creados = []
    doble = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok")))

    def falso(**kwargs):
        creados.append(kwargs)
        return doble

    monkeypatch.setattr(anthropic, "Anthropic", falso)
    assert creados == []  # importar y configurar no crea clientes
    generar_estructurado("a", Salida)
    generar_estructurado("b", Salida)
    assert creados == [{"max_retries": 2, "timeout": 60.0}]


def test_fallbacks_del_servidor_apagado_por_defecto_y_activable(monkeypatch):
    assert llm.FALLBACKS_SERVIDOR_ACTIVO is False
    cliente = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok")))
    generar_estructurado("x", Salida, cliente=cliente)
    assert cliente.llamadas[0][0] == "parse" and "fallbacks" not in cliente.kwargs
    monkeypatch.setenv("PUENTE_LLM_FALLBACKS", "true")
    generar_estructurado("y", Salida, cliente=cliente)
    nombre, kw = cliente.llamadas[1]
    assert nombre == "beta.parse" and kw["fallbacks"] == "default" and kw["betas"] == [llm.BETA_FALLBACKS]


# ---------------------------------------------------------------- modelo y proveedor por entorno


def test_configuracion_por_defecto():
    cfg = configuracion()
    assert (cfg["proveedor"], cfg["modelo"], cfg["modo"]) == ("anthropic", "claude-opus-5-5", "vivo")
    assert cfg["directorio"].endswith("grabaciones")


def test_directorio_de_grabaciones_por_defecto_en_el_repo(monkeypatch):
    monkeypatch.delenv("LLM_DIR_GRABACIONES")
    assert configuracion()["directorio"].replace("\\", "/").endswith("data/grabaciones")


def test_modelo_y_proveedor_por_entorno(monkeypatch):
    monkeypatch.setenv("PUENTE_MODELO_LLM", "claude-otro")
    cliente = ClienteAnthropic(respuesta(parsed=Salida(respuesta="ok")))
    generar_estructurado("x", Salida, cliente=cliente)
    assert cliente.kwargs["model"] == "claude-otro"

    monkeypatch.setenv("LLM_PROVEEDOR", "gemini")
    gemini = ClienteGemini(salida=Salida(respuesta="gemini"))
    resultado = generar_estructurado("x", Salida, sistema="sis", cliente=gemini)
    assert resultado.respuesta == "gemini" and gemini.esquema is Salida
    assert gemini.mensajes[0] == [("system", "sis"), ("human", "x")]
    assert len(cliente.llamadas) == 1  # anthropic no se tocó

    monkeypatch.delenv("PUENTE_MODELO_LLM")
    assert configuracion()["modelo"] == "gemini-2.5-flash"
    assert generar_texto("hola", cliente=ClienteGemini(texto="respuesta")) == "respuesta"


def test_gemini_envia_archivos_y_texto_en_bloques(monkeypatch):
    monkeypatch.setenv("LLM_PROVEEDOR", "gemini")
    gemini = ClienteGemini(salida=Salida(respuesta="ok"))
    generar_estructurado("lee", Salida, [("image/png", PNG), ("application/pdf", PDF)], cliente=gemini)
    contenido = gemini.mensajes[0][-1][1]
    assert [b["type"] for b in contenido] == ["image_url", "file", "text"]
    assert contenido[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_proveedor_o_modo_desconocido(monkeypatch):
    monkeypatch.setenv("LLM_PROVEEDOR", "openai")
    with pytest.raises(ErrorLLM, match="openai") as info:
        generar_texto("x", cliente=ClienteProhibido())
    assert info.value.causa == "proveedor_desconocido"
    monkeypatch.setenv("LLM_PROVEEDOR", "anthropic")
    monkeypatch.setenv("LLM_MODO", "inventado")
    with pytest.raises(ErrorLLM) as info:
        generar_texto("x", cliente=ClienteProhibido())
    assert info.value.causa == "modo_desconocido"
    monkeypatch.setenv("LLM_MODO", "vivo")
    monkeypatch.setenv("LLM_PROVEEDOR_RESPALDO", "otro")
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("x", Salida, cliente=ClienteAnthropic(respuesta(stop="refusal")))
    assert info.value.causa == "proveedor_desconocido"


# ---------------------------------------------------------------- grabar y reproducir


def test_hash_cambia_con_entradas():
    base = {
        "proveedor": "anthropic",
        "modelo": "claude-opus-5-5",
        "sistema": "s",
        "prompt": "p",
        "esquema": Salida,
        "archivos_bytes": [b"abc"],
    }

    def clave(**cambios):
        return clave_grabacion(**{**base, **cambios})

    assert clave() == clave() and len(clave()) == 64
    cambiadas = {
        clave(proveedor="gemini"),
        clave(modelo="claude-otro"),
        clave(sistema="s2"),
        clave(sistema=None),
        clave(prompt="p2"),
        clave(esquema=OfertaExtraida),
        clave(esquema=None),
        clave(archivos_bytes=[b"abd"]),
        clave(archivos_bytes=[]),
    }
    assert len(cambiadas) == 9 and clave() not in cambiadas
    assert clave(prompt="a\r\nb") == clave(prompt="a\nb")  # igual en Windows y Linux


def test_grabar_y_reproducir_ida_y_vuelta(monkeypatch, tmp_path):
    carpeta = tmp_path / "grabaciones"
    monkeypatch.setenv("LLM_MODO", "grabar")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-clave-de-prueba-no-real")
    entrevista = "Entrevista de Yaritza (sintético): atiendo la fonda de mi mamá y estudio de noche."
    cliente = ClienteAnthropic(
        respuesta(parsed=Salida(respuesta="atención al cliente", puntos=2), entrada=300, salida=40),
        respuesta(texto="Resumen para el orientador", entrada=50, salida=10),
    )
    original = generar_estructurado(
        entrevista, Salida, [("image/png", PNG)], sistema="Perfil", agente="perfil", cliente=cliente
    )
    texto = generar_texto("Explica", agente="matching", cliente=cliente)
    archivos = sorted(carpeta.glob("*.json"))
    assert len(archivos) == 2
    cargados = [json.loads(a.read_text(encoding="utf-8")) for a in archivos]
    registros = {r["tipo"]: r for r in cargados}
    est = registros["estructurado"]
    assert est["proveedor"] == "anthropic" and est["modelo"] == "claude-opus-5-5" and est["agente"] == "perfil"
    assert est["esquema"] == "Salida" and est["uso"] == {"entrada": 300, "salida": 40}
    assert est["salida"] == {"respuesta": "atención al cliente", "puntos": 2} and est["creado"]
    assert registros["texto"]["salida"] == "Resumen para el orientador" and registros["texto"]["esquema"] is None
    assert "sk-ant" not in "".join(a.read_text(encoding="utf-8") for a in archivos)
    llm.reiniciar_uso()

    monkeypatch.setenv("LLM_MODO", "reproducir")
    prohibido = ClienteProhibido()
    repetido = generar_estructurado(
        entrevista, Salida, [("image/png", PNG)], sistema="Perfil", agente="perfil", cliente=prohibido
    )
    assert repetido == original
    assert generar_texto("Explica", agente="matching", cliente=prohibido) == texto
    uso = llm.uso_por_agente()
    assert uso["perfil"]["llamadas"] == 1 and uso["perfil"]["reproducidas"] == 1 and uso["perfil"]["costo_usd"] == 0
    assert uso["perfil"]["tokens_entrada"] == 0


def test_grabar_con_respaldo_guarda_bajo_la_clave_pedida(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODO", "grabar")
    monkeypatch.setenv("LLM_PROVEEDOR_RESPALDO", "gemini")
    generar_estructurado(
        "x",
        Salida,
        cliente=ClienteAnthropic(respuesta(stop="refusal")),
        cliente_respaldo=ClienteGemini(salida=Salida(respuesta="g")),
    )
    (archivo,) = (tmp_path / "grabaciones").glob("*.json")
    registro = json.loads(archivo.read_text(encoding="utf-8"))
    assert registro["proveedor"] == "anthropic" and registro["atendido_por"]["proveedor"] == "gemini"
    monkeypatch.setenv("LLM_MODO", "reproducir")
    assert generar_estructurado("x", Salida, cliente=ClienteProhibido()).respuesta == "g"


def test_reproducir_sin_red_ni_claves(monkeypatch, tmp_path):
    carpeta = tmp_path / "grabaciones"
    carpeta.mkdir()
    monkeypatch.setenv("LLM_MODO", "reproducir")

    def sin_red(*args, **kwargs):
        raise AssertionError("no debe haber red en reproducir")

    def sin_cliente(*args, **kwargs):
        raise AssertionError("no debe construirse anthropic.Anthropic en reproducir")

    monkeypatch.setattr(socket.socket, "connect", sin_red)
    monkeypatch.setattr(anthropic, "Anthropic", sin_cliente)
    modelo = configuracion()["modelo"]
    clave_e = clave_grabacion("anthropic", modelo, None, "extrae", Salida, [])
    clave_t = clave_grabacion("anthropic", modelo, "sis", "saluda", None, [])
    estructurada = {"hash": clave_e, "salida": {"respuesta": "grabada", "puntos": 7}}
    (carpeta / f"{clave_e}.json").write_text(json.dumps(estructurada), encoding="utf-8")
    (carpeta / f"{clave_t}.json").write_text(json.dumps({"hash": clave_t, "salida": "hola grabado"}), encoding="utf-8")
    assert generar_estructurado("extrae", Salida) == Salida(respuesta="grabada", puntos=7)
    assert generar_texto("saluda", sistema="sis") == "hola grabado"


def test_reproducir_grabacion_faltante(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODO", "reproducir")
    clave = clave_grabacion("anthropic", "claude-opus-5-5", None, "no grabado", Salida, [])
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("no grabado", Salida, agente="perfil", cliente=ClienteProhibido())
    assert info.value.causa == "grabacion_faltante"
    mensaje = str(info.value)
    assert clave in mensaje and str(tmp_path / "grabaciones" / f"{clave}.json") in mensaje and "perfil" in mensaje


def test_reproducir_grabacion_danada(monkeypatch, tmp_path):
    carpeta = tmp_path / "grabaciones"
    carpeta.mkdir()
    monkeypatch.setenv("LLM_MODO", "reproducir")
    clave = clave_grabacion("anthropic", "claude-opus-5-5", None, "p", Salida, [])
    (carpeta / f"{clave}.json").write_text(json.dumps({"salida": {"respuesta": 123}}), encoding="utf-8")
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("p", Salida)
    assert info.value.causa == "grabacion_invalida"
    (carpeta / f"{clave}.json").write_text("no es json", encoding="utf-8")
    with pytest.raises(ErrorLLM) as info:
        generar_estructurado("p", Salida)
    assert info.value.causa == "grabacion_invalida"
