"""Agente Perfil (#6): evidencia literal, taxonomía, PII fuera del LLM y nodo del grafo.

Sin red ni claves: el LLM es un falso que devuelve respuestas guardadas y anota cada prompt,
y la taxonomía es un falso con la interfaz acordada con #5 sobre tests/fixtures/taxonomia_mini.csv.
"""
import csv
import json
import unicodedata
from pathlib import Path

import pytest

from puente.agents import perfil as agente
from puente.agents.perfil import (
    MAX_PREGUNTAS,
    PREGUNTAS_ENTREVISTA,
    construir_perfil,
    detectar_corregimiento,
    entrevistar,
    filtrar_habilidades,
    nodo_perfil,
    quitar_pii,
    tiene_evidencia,
)
from puente.schemas import Decision, Perfil

FIXTURES = Path(__file__).parent / "fixtures"
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
    def __init__(self, respuesta: dict | str):
        self.respuesta = respuesta if isinstance(respuesta, str) else json.dumps(respuesta, ensure_ascii=False)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"```json\n{self.respuesta}\n```"


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
    assert perfil.edad == 20 and perfil.corregimiento == "La Chorrera"
    assert perfil.horario_clases[0].hora_inicio == "18:00"
    assert perfil.salario_minimo == 400
    assert "6123-4567" not in llm.prompts[0]
    assert "liderazgo" in decision.razon and "ventas" in decision.razon


@pytest.mark.parametrize("caso", CASOS, ids=[c["nombre"] for c in CASOS])
def test_eval_cero_habilidades_sin_evidencia(caso, taxonomia):
    perfil, _, _ = _construir(caso, taxonomia)
    respuestas = caso["entrada"].get("respuestas") or [caso["entrada"].get("cv_texto", "")]
    for h in perfil.habilidades:
        assert h.skill_id in taxonomia
        assert any(" ".join(h.evidencia.lower().split()) in " ".join(r.lower().split()) for r in respuestas)
    assert sorted(h.skill_id for h in perfil.habilidades) == sorted(caso["habilidades_esperadas"])


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
    llm = LLMFalso({"habilidades": [{"skill_id": "ventas", "evidencia": "vendía minutos de celular"}], "edad": 21})
    entrada = {"id": "foto", "cv_ruta": ruta}
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
    llm = LLMFalso({"habilidades": [{"skill_id": "atencion_cliente", "evidencia": "Atendia clientes"}], "edad": 22})
    perfil, _ = construir_perfil({"id": "pdf", "cv_ruta": ruta}, taxonomia=taxonomia, normalizar=normalizar_falso, llm=llm)
    assert [h.skill_id for h in perfil.habilidades] == ["atencion_cliente"]
    assert "8-222-3333" not in llm.prompts[0]


def test_llm_sin_json_es_error(taxonomia):
    entrada = {"id": "x", "edad": 20, "corregimiento": "Tocumen", "respuestas": ["hola"]}
    with pytest.raises(ValueError, match="JSON"):
        construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar_falso, llm=lambda _: "no sé")


# ---------------------------------------------------------------- nodo del grafo


class LLMPorId:
    """Devuelve la respuesta guardada del caso cuyo texto aparece en el prompt."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for caso in CASOS:
            muestra = (caso["entrada"].get("respuestas") or [caso["entrada"].get("cv_texto", "")])[-1][-30:]
            if muestra in prompt:
                return json.dumps(caso["respuesta_llm"], ensure_ascii=False)
        return "{}"


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


def test_modelo_se_lee_de_variable_de_entorno():
    assert agente.MODELO_POR_DEFECTO
    assert "PUENTE_MODELO_LLM" in Path(agente.__file__).read_text(encoding="utf-8")


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
