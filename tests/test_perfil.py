"""Agente Perfil (#6, #44): evidencia literal, taxonomía, PII fuera del LLM, formulario fijo,
modo terminal y nodo del grafo.

Sin red, claves ni `puente.llm`: el LLM es un falso que devuelve `PerfilExtraido` guardados y
anota cada prompt, y la taxonomía es un falso con la interfaz acordada con #5 sobre tests/fixtures/taxonomia_mini.csv.
"""
import csv
import json
import subprocess
import sys
import types
import unicodedata
from pathlib import Path

import pytest

from puente.agents import perfil as agente
from puente.agents.perfil import (
    CAMPOS_FORMULARIO,
    MAX_PREGUNTAS,
    PREGUNTAS_ENTREVISTA,
    construir_perfil,
    construir_prompt,
    detectar_corregimiento,
    entrada_entrevista,
    entrevistar,
    filtrar_habilidades,
    leer_horario,
    main,
    medir_habilidades,
    nodo_perfil,
    pedir_formulario,
    quitar_pii,
    tiene_evidencia,
)
from puente.schemas import Decision, Perfil, PerfilExtraido

FIXTURES = Path(__file__).parent / "fixtures"
RAIZ = Path(__file__).resolve().parents[1]
CASOS = json.loads((FIXTURES / "perfiles_eval.json").read_text(encoding="utf-8"))["casos"]


# ---------------------------------------------------------------- falsos


def cargar_taxonomia_falsa(ruta=FIXTURES / "taxonomia_mini.csv") -> dict[str, dict]:
    with open(ruta, encoding="utf-8") as f:
        return {
            fila["skill_id"]: {
                "nombre_es": fila["nombre_es"],
                "sinonimos": [s for s in fila["sinonimos"].split("|") if s],
                "uri_esco": fila["uri_esco"],
            }
            for fila in csv.DictReader(f)
        }


def _plano(texto: str) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin.lower().split())


def normalizar_falso(texto: str, taxonomia: dict[str, dict]) -> str | None:
    buscado = _plano(texto)
    for sid, datos in taxonomia.items():
        if buscado in {_plano(sid), _plano(datos["nombre_es"]), *(_plano(s) for s in datos["sinonimos"])}:
            return sid
    return None


class LLMFalso:
    """Imita `llm_puente`: recibe el prompt y devuelve un PerfilExtraido (o el texto dado)."""

    def __init__(self, respuesta: dict | str):
        self.respuesta = respuesta
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> PerfilExtraido | str:
        self.prompts.append(prompt)
        if isinstance(self.respuesta, str):
            return self.respuesta
        return PerfilExtraido.model_validate(self.respuesta)


@pytest.fixture
def taxonomia():
    return cargar_taxonomia_falsa()


def _caso(nombre: str) -> dict:
    return next(c for c in CASOS if c["nombre"] == nombre)


def _construir(caso: dict, taxonomia, llm=None):
    llm = llm or LLMFalso(caso["respuesta_llm"])
    perfil, decision = construir_perfil(caso["entrada"], taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)
    return perfil, decision, llm


# ---------------------------------------------------------------- caso guía


def test_yaritza_sin_cv_fonda(taxonomia):
    perfil, decision, llm = _construir(_caso("yaritza_fonda_sin_cv"), taxonomia)

    ids = {h.skill_id for h in perfil.habilidades}
    assert {"atencion_cliente", "manejo_caja", "inventario"} <= ids
    assert all(h.evidencia for h in perfil.habilidades)
    assert "ventas" not in ids, "evidencia inventada: no está en sus respuestas"
    assert "liderazgo" not in ids, "fuera de la taxonomía"
    # El LLM guardado dice edad 25 y salario 900: mandan los datos del formulario.
    assert perfil.edad == 20 and perfil.corregimiento == "La Chorrera"
    assert perfil.horario_clases[0].hora_inicio == "18:00"
    assert perfil.salario_minimo == 400 and perfil.presupuesto_pasaje_diario == 3.0
    assert "6123-4567" not in llm.prompts[0]
    assert "liderazgo" in decision.razon and "ventas" in decision.razon
    assert "ignorado del LLM" in decision.razon and "edad=25" in decision.razon


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
def test_eval_cero_habilidades_sin_evidencia(caso, taxonomia):
    perfil, _, _ = _construir(caso, taxonomia)
    respuestas = caso["entrada"].get("respuestas") or [caso["entrada"].get("cv_texto", "")]
    for h in perfil.habilidades:
        assert h.skill_id in taxonomia
        assert any(" ".join(h.evidencia.lower().split()) in " ".join(r.lower().split()) for r in respuestas)
    assert sorted(h.skill_id for h in perfil.habilidades) == sorted(caso["habilidades_esperadas"])


def test_eval_precision_y_exhaustividad(taxonomia):
    pares = []
    for caso in CASOS:
        perfil, _, _ = _construir(caso, taxonomia)
        pares.append(([h.skill_id for h in perfil.habilidades], caso["habilidades_esperadas"]))
    metricas = medir_habilidades(pares)
    assert metricas["precision"] == 1.0, metricas["falsos_positivos"]
    assert metricas["exhaustividad"] == 1.0, metricas["faltantes"]
    assert metricas["aciertos"] == sum(len(c["habilidades_esperadas"]) for c in CASOS)


def test_medir_habilidades_cuenta_falsos_positivos_y_faltantes():
    metricas = medir_habilidades([(["a", "b", "x"], ["a", "b", "c", "d"]), ([], [])])
    assert metricas["precision"] == pytest.approx(2 / 3)
    assert metricas["exhaustividad"] == pytest.approx(2 / 4)
    assert metricas["falsos_positivos"] == ["x"] and metricas["faltantes"] == ["c", "d"]
    assert medir_habilidades([([], [])]) == {
        "precision": 1.0, "exhaustividad": 1.0, "aciertos": 0, "falsos_positivos": [], "faltantes": [],
    }


# ---------------------------------------------------------------- PII


def test_cedula_del_cv_no_llega_al_llm(taxonomia):
    caso = _caso("carlos_cv_supermercado")
    perfil, _, llm = _construir(caso, taxonomia)
    enviado = "\n".join(llm.prompts)
    assert "8-000-1234" not in enviado
    assert "6987-6543" not in enviado
    assert "Calle 5" not in enviado and "casa 23" not in enviado
    assert "[CEDULA]" in enviado
    assert perfil.corregimiento == "Barrio Colón"


@pytest.mark.parametrize(
    "texto, prohibido",
    [
        ("Cédula 8-123-4567", "8-123-4567"),
        ("ced. PE-12-345 ok", "PE-12-345"),
        ("extranjero E-8-123456", "E-8-123456"),
        ("cédula 3AV-12-345", "3AV-12-345"),
        ("Cédula: 81234567", "81234567"),
        ("cel 6123-4567", "6123-4567"),
        ("whatsapp +507 61234567", "61234567"),
        ("casa 254-1234", "254-1234"),
        ("vivo en la calle 50, casa 12", "casa 12"),
    ],
)
def test_quitar_pii(texto, prohibido):
    assert prohibido not in quitar_pii(texto)


def test_quitar_pii_conserva_el_resto():
    texto = "Tengo mi cédula vigente. Trabajé 2 años de 8:00 a 16:00 y cobraba B/. 600."
    assert quitar_pii(texto) == texto


def test_direccion_deja_solo_corregimiento():
    limpio = quitar_pii("Dirección: Barriada El Progreso, calle 3, casa 45, Guadalupe, La Chorrera\nOtra línea")
    assert limpio.splitlines() == ["Corregimiento: Guadalupe", "Otra línea"]
    assert quitar_pii("Domicilio: casa azul frente al chino") == "[DIRECCION]"


def test_detectar_corregimiento_prefiere_el_especifico():
    assert detectar_corregimiento("vivo en barrio colon, la chorrera") == "Barrio Colón"
    assert detectar_corregimiento("vivo en La Chorrera") == "La Chorrera"
    assert detectar_corregimiento("vivo lejos") is None


# ---------------------------------------------------------------- evidencia y taxonomía


def test_evidencia_tolera_espacios_y_mayusculas_pero_no_cambios():
    fuente = ["Cobro y   doy el\nvuelto todos los días"]
    assert tiene_evidencia("cobro y doy el vuelto", fuente)
    assert tiene_evidencia("«COBRO Y DOY EL VUELTO.»", fuente)
    assert not tiene_evidencia("cobro y entrego el vuelto", fuente)
    assert not tiene_evidencia("cobro", fuente), "demasiado corta para respaldar algo"


def test_evidencia_no_vale_si_esta_en_la_pregunta(taxonomia):
    # "cobrar" sale en la pregunta 5, no en la respuesta del joven.
    entrada = {"id": "x", "edad": 19, "corregimiento": "Tocumen", "respuestas": ["no sé"] * 5}
    llm = LLMFalso({"habilidades": [{"skill_id": "manejo_caja", "evidencia": "cocina, cobrar, idiomas"}]})
    perfil, _ = construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)
    assert perfil.habilidades == []


def test_filtrar_normaliza_sinonimos_y_quita_duplicados(taxonomia):
    fuentes = ["Fui cajero y atendía clientes; también cobraba a los clientes"]
    propuestas = [
        {"skill_id": "Cajero", "evidencia": "Fui cajero y atendía clientes"},
        {"skill_id": "manejo_caja", "evidencia": "también cobraba a los clientes"},
        {"skill_id": "astronauta", "evidencia": "Fui cajero y atendía clientes"},
        "basura",
    ]
    aceptadas, descartes = filtrar_habilidades(propuestas, fuentes, taxonomia, normalizar_falso)
    assert [h.skill_id for h in aceptadas] == ["manejo_caja"]
    assert len(descartes) == 2


# ---------------------------------------------------------------- entrevista y entradas


def test_entrevista_maximo_cinco_preguntas():
    hechas = []
    respuestas = entrevistar(lambda p: hechas.append(p) or "ok", list(PREGUNTAS_ENTREVISTA) * 2)
    assert len(hechas) == len(respuestas) == MAX_PREGUNTAS == len(PREGUNTAS_ENTREVISTA)


def test_mas_de_cinco_respuestas_es_error(taxonomia):
    entrada = {"id": "x", "edad": 20, "corregimiento": "Tocumen", "respuestas": ["a"] * 6}
    with pytest.raises(ValueError, match="5 respuestas"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=LLMFalso({}))


def test_cv_desde_archivo_de_texto(tmp_path, taxonomia):
    caso = _caso("carlos_cv_supermercado")
    ruta = tmp_path / "cv.txt"
    ruta.write_text(caso["entrada"]["cv_texto"], encoding="utf-8")
    entrada = {k: v for k, v in caso["entrada"].items() if k != "cv_texto"} | {"cv_ruta": ruta}
    llm = LLMFalso(caso["respuesta_llm"])
    perfil, _ = construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)
    assert len(perfil.habilidades) == 3
    assert "8-000-1234" not in llm.prompts[0]


def test_cv_en_foto_usa_ocr_local_inyectado(tmp_path, taxonomia):
    ruta = tmp_path / "cv.jpg"
    ruta.write_bytes(b"no es una imagen real")
    llm = LLMFalso({"habilidades": [{"skill_id": "ventas", "evidencia": "vendía minutos de celular"}]})
    entrada = {"id": "foto", "edad": 21, "cv_ruta": ruta}
    perfil, _ = construir_perfil(
        entrada,
        taxonomia=taxonomia,
        normalizar=normalizar_falso,
        llm=llm,
        ocr=lambda _: "Cédula 4-111-2222. Vivo en Pedregal. Vendía minutos de celular.",
    )
    assert [h.skill_id for h in perfil.habilidades] == ["ventas"]
    assert perfil.corregimiento == "Pedregal"
    assert "4-111-2222" not in llm.prompts[0]


def test_cv_pdf(tmp_path, taxonomia):
    pytest.importorskip("pypdf")
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, "Cedula 8-222-3333\nVivo en Tocumen\nAtendia clientes en una tienda de barrio")
    ruta = tmp_path / "cv.pdf"
    pdf.output(str(ruta))
    llm = LLMFalso({"habilidades": [{"skill_id": "atencion_cliente", "evidencia": "Atendia clientes"}]})
    entrada = {"id": "pdf", "edad": 22, "cv_ruta": ruta}
    perfil, _ = construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)
    assert [h.skill_id for h in perfil.habilidades] == ["atencion_cliente"]
    assert "8-222-3333" not in llm.prompts[0]


def test_llm_sin_json_es_error(taxonomia):
    entrada = {"id": "x", "edad": 20, "corregimiento": "Tocumen", "respuestas": ["hola"]}
    with pytest.raises(ValueError, match="JSON"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=LLMFalso("no sé"))


def test_salida_que_no_cumple_perfil_extraido_es_error(taxonomia):
    entrada = {"id": "x", "edad": 20, "corregimiento": "Tocumen", "respuestas": ["hola"]}
    llm = LLMFalso('{"habilidades": "muchas"}')
    with pytest.raises(ValueError, match="PerfilExtraido"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)


# ---------------------------------------------------------------- formulario fijo (#44)


def test_preguntas_solo_piden_habilidades():
    prohibidas = ("años", "edad", "corregimiento", "vives", "horario", "ganar", "salario", "pasaje")
    for pregunta in PREGUNTAS_ENTREVISTA:
        assert not any(p in pregunta.lower() for p in prohibidas), pregunta
    assert len(PREGUNTAS_ENTREVISTA) == MAX_PREGUNTAS


def test_prompt_no_pide_datos_del_formulario(taxonomia):
    prompt = construir_prompt("texto del joven", "entrevista", taxonomia)
    assert "Llena solo \"habilidades\"" in prompt and "manejo_caja" in prompt and "texto del joven" in prompt
    assert '"edad"' not in prompt and '"salario_minimo"' not in prompt and '"horario_clases"' not in prompt


def test_acepta_llm_que_devuelve_texto_json_como_el_grabado_del_grafo(taxonomia):
    """`_llm_grabado` de puente/graph.py devuelve el JSON como texto con la firma llm(prompt)."""
    caso = _caso("yaritza_fonda_sin_cv")
    texto = json.dumps(caso["respuesta_llm"], ensure_ascii=False)
    perfil, _ = construir_perfil(caso["entrada"], taxonomia=taxonomia, normalizar=normalizar_falso, llm=lambda _p: texto)
    assert {"atencion_cliente", "manejo_caja", "inventario"} <= {h.skill_id for h in perfil.habilidades}


def test_sin_edad_en_el_formulario_es_error_aunque_el_llm_la_diga(taxonomia):
    entrada = {"id": "x", "corregimiento": "Tocumen", "respuestas": ["hola"]}
    llm = LLMFalso({"habilidades": [], "edad": 20})
    with pytest.raises(ValueError, match="edad"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)


def test_corregimiento_del_llm_se_ignora(taxonomia):
    entrada = {"id": "x", "edad": 20, "respuestas": ["vivo cerca"]}
    llm = LLMFalso({"habilidades": [], "corregimiento": "Tocumen"})
    with pytest.raises(ValueError, match="corregimiento"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("lunes a viernes 18:00-22:00", [(["lunes", "martes", "miercoles", "jueves", "viernes"], "18:00", "22:00")]),
        ("Lun, Mié y Vie 7 a 12:30", [(["lunes", "miercoles", "viernes"], "07:00", "12:30")]),
        ("sábado 8-12; domingo 9:00 hasta 13:00", [(["sabado"], "08:00", "12:00"), (["domingo"], "09:00", "13:00")]),
        ("", []),
        ("no", []),
    ],
)
def test_leer_horario(texto, esperado):
    assert [(h.dias, h.hora_inicio, h.hora_fin) for h in leer_horario(texto)] == esperado


@pytest.mark.parametrize("texto", ["de noche", "lunes 25:00-26:00", "feriados 8-12"])
def test_leer_horario_rechaza_lo_que_no_entiende(texto):
    with pytest.raises(ValueError):
        leer_horario(texto)


def _respondedor(valores):
    pendientes = iter(valores)
    hechas: list[str] = []

    def preguntar(pregunta: str) -> str:
        hechas.append(pregunta)
        return next(pendientes)

    return preguntar, hechas


FORMULARIO_YARITZA = ["Yaritza Ficticia", "F", "20", "la chorrera", "lunes a viernes 18:00-22:00", "", "B/. 400", "3"]


def test_formulario_valida_y_repite_la_pregunta():
    preguntar, hechas = _respondedor(
        ["", "", "doce", "12", "20", "Marte", "Tocumen", "de noche", "", "", "400", "2.50"]
    )
    avisos: list[str] = []
    formulario = pedir_formulario(preguntar, avisos.append)
    assert formulario == {
        "edad": 20, "corregimiento": "Tocumen", "horario_clases": [], "horario_libre": [],
        "salario_minimo": 400.0, "presupuesto_pasaje_diario": 2.5,
    }
    assert len(avisos) == 4, "doce, 12, Marte y 'de noche' se rechazan y se vuelven a preguntar"
    assert len(hechas) == 12


def test_entrada_entrevista_tiene_la_forma_de_entradas_perfil(taxonomia):
    caso = _caso("yaritza_fonda_sin_cv")
    preguntar, _ = _respondedor(FORMULARIO_YARITZA)
    formulario = pedir_formulario(preguntar, lambda _: None)
    entrada = entrada_entrevista("yaritza", formulario, caso["entrada"]["respuestas"])

    assert entrada["id"] == "yaritza" and len(entrada["respuestas"]) == MAX_PREGUNTAS
    assert set(entrada) - {"id", "respuestas"} <= set(CAMPOS_FORMULARIO)
    json.dumps(entrada)  # serializable: se guarda para la demo sin conexión y viaja en el estado

    # La entrevista ya terminó fuera del grafo: el nodo la recibe completa en entradas_perfil.
    salida = nodo_perfil(
        {"entradas_perfil": [entrada]}, llm=LLMFalso(caso["respuesta_llm"]), taxonomia=taxonomia, normalizar=normalizar_falso
    )
    assert salida["errores"] == []
    yaritza = salida["perfiles"][0]
    assert {"atencion_cliente", "manejo_caja", "inventario"} <= {h.skill_id for h in yaritza.habilidades}
    assert yaritza.horario_clases[0].dias[-1] == "viernes" and yaritza.salario_minimo == 400


def test_entrevista_entra_al_grafo_como_un_elemento_de_entradas_perfil(taxonomia):
    from langgraph.checkpoint.memory import MemorySaver

    from puente.graph import cargar_caso, config_caso, construir_grafo

    caso = _caso("yaritza_fonda_sin_cv")
    preguntar, _ = _respondedor(FORMULARIO_YARITZA)
    entrada = entrada_entrevista("yaritza", pedir_formulario(preguntar, lambda _: None), caso["entrada"]["respuestas"])
    llm = LLMFalso(caso["respuesta_llm"])

    def perfil_real(estado):
        return nodo_perfil(estado, llm=llm, taxonomia=taxonomia, normalizar=normalizar_falso)

    _, estado = cargar_caso()
    grafo = construir_grafo(nodos={"perfil": perfil_real}, checkpointer=MemorySaver())
    config = config_caso("perfil-44")
    grafo.invoke({**estado, "tipo_entrada": "perfil", "perfiles": [], "entradas_perfil": [entrada]}, config)

    assert len(llm.prompts) == 1, "una sola llamada al LLM: la entrevista ya llegó completa"
    yaritza = next(p for p in grafo.get_state(config).values["perfiles"] if p.id == "yaritza")
    assert {"atencion_cliente", "manejo_caja", "inventario"} <= {h.skill_id for h in yaritza.habilidades}


def test_entrada_entrevista_rechaza_campos_ajenos_y_mas_de_cinco():
    with pytest.raises(ValueError, match="fuera del formulario"):
        entrada_entrevista("x", {"cedula": "8-1-1"}, [])
    with pytest.raises(ValueError, match="5 respuestas"):
        entrada_entrevista("x", {}, ["a"] * 6)


# ---------------------------------------------------------------- modo terminal (#44)


def test_modo_terminal_yaritza(tmp_path):
    caso = _caso("yaritza_fonda_sin_cv")
    preguntar, hechas = _respondedor(FORMULARIO_YARITZA + caso["entrada"]["respuestas"])
    salida: list[str] = []
    guardada = tmp_path / "yaritza.json"
    codigo = main(
        [
            "--entrevista", "--id", "yaritza",
            "--reproducir", str(FIXTURES / "perfiles_eval.json"),
            "--taxonomia", str(FIXTURES / "taxonomia_mini.csv"),
            "--guardar", str(guardada),
        ],
        preguntar=preguntar,
        mostrar=salida.append,
    )
    texto = "\n".join(salida)
    assert codigo == 0, texto
    assert len(hechas) == len(CAMPOS_FORMULARIO) + MAX_PREGUNTAS
    assert all(p.strip() in "".join(hechas) for p in PREGUNTAS_ENTREVISTA)
    assert "atencion_cliente: «Atiendo a los clientes y les tomo el pedido»" in texto
    assert "manejo_caja: «cobro y doy el vuelto»" in texto
    assert "inventario: «anoto lo que se va acabando para comprar el surtido»" in texto
    assert "ventas" in texto.split("Decisión:")[1], "la descartada queda en la razón"

    # Con --desde se repite la demo sin volver a preguntar.
    otra: list[str] = []
    assert main(
        ["--desde", str(guardada), "--reproducir", str(FIXTURES / "perfiles_eval.json"),
         "--taxonomia", str(FIXTURES / "taxonomia_mini.csv")],
        preguntar=lambda _: pytest.fail("no debe preguntar"),
        mostrar=otra.append,
    ) == 0
    assert otra[-1] == salida[-1]


def test_modo_terminal_por_subproceso():
    caso = _caso("yaritza_fonda_sin_cv")
    teclado = "\n".join(FORMULARIO_YARITZA + caso["entrada"]["respuestas"]) + "\n"
    resultado = subprocess.run(
        [sys.executable, "-m", "puente.agents.perfil", "--entrevista", "--id", "yaritza",
         "--reproducir", str(FIXTURES / "perfiles_eval.json"), "--taxonomia", str(FIXTURES / "taxonomia_mini.csv")],
        input=teclado, capture_output=True, text=True, cwd=RAIZ, timeout=60, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    perfil = Perfil.model_validate_json(resultado.stdout.split("Perfil:\n", 1)[1])
    assert {"atencion_cliente", "manejo_caja", "inventario"} <= {h.skill_id for h in perfil.habilidades}
    assert perfil.edad == 20 and perfil.corregimiento == "La Chorrera"


def test_modo_terminal_sin_opciones_es_error():
    with pytest.raises(SystemExit):
        main([], mostrar=lambda _: None)


def test_menor_de_18_se_rechaza(taxonomia):
    """Los menores quedan fuera de la demo (#42, trabajo futuro en #15)."""
    entrada = {"id": "x", "edad": 17, "corregimiento": "Tocumen", "respuestas": ["hola"]}
    with pytest.raises(ValueError, match="perfil inválido"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=LLMFalso({"habilidades": []}))


# ---------------------------------------------------------------- nodo del grafo


class LLMPorId:
    """Devuelve la respuesta guardada del caso cuyo texto aparece en el prompt."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> PerfilExtraido:
        self.prompts.append(prompt)
        for caso in CASOS:
            muestra = (caso["entrada"].get("respuestas") or [caso["entrada"].get("cv_texto", "")])[-1][-30:]
            if muestra in prompt:
                return PerfilExtraido.model_validate(caso["respuesta_llm"])
        return PerfilExtraido()


def test_nodo_perfil_devuelve_listas_completas(taxonomia):
    previo = Perfil(id="yaritza", nombre="viejo", edad=20, corregimiento="X", salario_minimo=0, presupuesto_pasaje_diario=0)
    decision_previa = Decision(agente="mercado", entrada_resumen="-", salida_resumen="-", razon="-")
    estado = {
        "perfiles": [previo],
        "decisiones": [decision_previa],
        "errores": ["error previo"],
        "entradas_perfil": [c["entrada"] for c in CASOS] + [{"id": "vacio"}],
    }
    salida = nodo_perfil(estado, llm=LLMPorId(), taxonomia=taxonomia, normalizar=normalizar_falso)

    assert set(salida) == {"perfiles", "decisiones", "errores"}
    assert sorted(p.id for p in salida["perfiles"]) == ["carlos", "kevin", "maria", "yaritza"]
    yaritza = next(p for p in salida["perfiles"] if p.id == "yaritza")
    assert yaritza.nombre == "Yaritza Ficticia", "el perfil con el mismo id se reemplaza"
    assert salida["decisiones"][0] is decision_previa
    assert [d.agente for d in salida["decisiones"][1:]] == ["perfil"] * 4
    assert salida["errores"][0] == "error previo"
    assert any("vacio" in e for e in salida["errores"])


def test_nodo_sin_entradas_no_cambia_nada():
    assert nodo_perfil({"perfiles": []}, llm=LLMFalso({})) == {}


def test_nodo_sin_taxonomia_registra_error(monkeypatch):
    import puente.rules.taxonomia as tax

    monkeypatch.delattr(tax, "cargar_taxonomia", raising=False)
    salida = nodo_perfil({"entradas_perfil": [{"id": "x"}], "errores": []}, llm=LLMFalso({}))
    assert "taxonomía no disponible" in salida["errores"][0]


# ---------------------------------------------------------------- capa común de LLM (#45)


def _puente_llm_falso(monkeypatch, salida=None, error=None):
    """Instala un `puente.llm` falso con la interfaz de #45 y anota cada llamada."""
    llamadas: list[dict] = []

    class ErrorLLM(RuntimeError):
        def __init__(self, mensaje, *, causa="error_proveedor"):
            super().__init__(mensaje)
            self.causa = causa

    def generar_estructurado(prompt, esquema, archivos=None, **kwargs):
        llamadas.append({"prompt": prompt, "esquema": esquema, "archivos": archivos, **kwargs})
        if error:
            raise ErrorLLM(error, causa="grabacion_faltante")
        return esquema.model_validate(salida)

    modulo = types.ModuleType("puente.llm")
    modulo.generar_estructurado = generar_estructurado
    modulo.ErrorLLM = ErrorLLM
    monkeypatch.setitem(sys.modules, "puente.llm", modulo)
    return llamadas


def test_llm_por_defecto_usa_puente_llm_con_perfil_extraido(monkeypatch, taxonomia):
    caso = _caso("yaritza_fonda_sin_cv")
    llamadas = _puente_llm_falso(monkeypatch, salida=caso["respuesta_llm"])
    perfil, _ = construir_perfil(caso["entrada"], taxonomia=taxonomia, normalizar=normalizar_falso)

    assert len(llamadas) == 1
    llamada = llamadas[0]
    assert llamada["esquema"] is PerfilExtraido and llamada["agente"] == "perfil"
    assert not llamada["archivos"], "nunca se mandan archivos: el CV en foto se lee con OCR local"
    assert "6123-4567" not in llamada["prompt"] and "[TELEFONO]" in llamada["prompt"]
    assert {"atencion_cliente", "manejo_caja", "inventario"} <= {h.skill_id for h in perfil.habilidades}


def test_cv_en_foto_no_manda_la_imagen_al_llm(monkeypatch, tmp_path, taxonomia):
    llamadas = _puente_llm_falso(monkeypatch, salida={"habilidades": []})
    ruta = tmp_path / "cv.png"
    ruta.write_bytes(b"foto con cedula")
    construir_perfil(
        {"id": "f", "edad": 21, "cv_ruta": ruta}, taxonomia=taxonomia, normalizar=normalizar_falso,
        ocr=lambda _: "Cédula 4-111-2222. Vivo en Pedregal.",
    )
    assert not llamadas[0]["archivos"] and "4-111-2222" not in llamadas[0]["prompt"]


def test_error_del_llm_queda_en_errores_del_nodo(monkeypatch, taxonomia):
    _puente_llm_falso(monkeypatch, error="Falta la grabación abc para el agente perfil")
    caso = _caso("yaritza_fonda_sin_cv")
    salida = nodo_perfil({"entradas_perfil": [caso["entrada"]]}, taxonomia=taxonomia, normalizar=normalizar_falso)
    assert salida["perfiles"] == []
    assert salida["errores"] == ["perfil: Falta la grabación abc para el agente perfil"]


def test_sin_puente_llm_el_nodo_registra_error(monkeypatch, taxonomia):
    monkeypatch.setitem(sys.modules, "puente.llm", None)  # import falla como si #45 no estuviera en main
    caso = _caso("yaritza_fonda_sin_cv")
    salida = nodo_perfil({"entradas_perfil": [caso["entrada"]]}, taxonomia=taxonomia, normalizar=normalizar_falso)
    assert "falta puente/llm.py" in salida["errores"][0]


def test_modo_terminal_desde_archivo_de_casos_con_puente_llm(monkeypatch):
    """Lo que corre `LLM_MODO=grabar|reproducir ... --desde tests/fixtures/perfiles_eval.json --id yaritza`."""
    caso = _caso("yaritza_fonda_sin_cv")
    llamadas = _puente_llm_falso(monkeypatch, salida=caso["respuesta_llm"])
    salida: list[str] = []
    codigo = main(
        ["--desde", str(FIXTURES / "perfiles_eval.json"), "--id", "yaritza",
         "--taxonomia", str(FIXTURES / "taxonomia_mini.csv")],
        preguntar=lambda _: pytest.fail("no debe preguntar"),
        mostrar=salida.append,
    )
    texto = "\n".join(salida)
    assert codigo == 0, texto
    assert len(llamadas) == 1 and llamadas[0]["agente"] == "perfil"
    assert "inventario: «anoto lo que se va acabando para comprar el surtido»" in texto


def test_modo_terminal_reporta_error_del_llm(monkeypatch):
    _puente_llm_falso(monkeypatch, error="Falta la grabación abc")
    salida: list[str] = []
    codigo = main(
        ["--desde", str(FIXTURES / "perfiles_eval.json"), "--id", "yaritza",
         "--taxonomia", str(FIXTURES / "taxonomia_mini.csv")],
        mostrar=salida.append,
    )
    assert codigo == 1 and salida[-1] == "Error: Falta la grabación abc"


def test_perfil_no_llama_al_modelo_directamente():
    fuente = Path(agente.__file__).read_text(encoding="utf-8")
    assert "langchain_google_genai" not in fuente and "import anthropic" not in fuente
    assert "from puente.llm import generar_estructurado" in fuente


def test_nodo_con_taxonomia_real_de_mercado():
    # Implementación real de #5: cargar_taxonomia sobre el fixture y normalizar_habilidad por el import diferido.
    from puente.rules.taxonomia import cargar_taxonomia

    taxonomia = cargar_taxonomia(FIXTURES / "taxonomia_mini.csv")
    salida = nodo_perfil(
        {"entradas_perfil": [c["entrada"] for c in CASOS]}, llm=LLMPorId(), taxonomia=taxonomia
    )
    assert salida["errores"] == []
    obtenidas = {p.id: sorted(h.skill_id for h in p.habilidades) for p in salida["perfiles"]}
    esperadas = {c["entrada"]["id"]: sorted(c["habilidades_esperadas"]) for c in CASOS}
    assert obtenidas == esperadas
