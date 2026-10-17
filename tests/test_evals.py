"""Pruebas del runner de evals (issue #13): sin red, sin claves y sin .env.

Los ejecutores son dobles que devuelven lo esperado o algo alterado. Los resultados se escriben en tmp_path.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evals import run
from evals.run import DependenciaPendiente, ResultadoCaso

RAIZ = Path(__file__).resolve().parents[1]
CONTEO_ESPERADO = {
    "extraccion_ofertas": 5,
    "requisitos_discriminatorios": 11,  # Incluye DIS-11: límite «menor de 28 años».
    "perfil_sin_cv": 4,
    "matching_factible": 3,
    "sesgo_contrafactual": 3,
    "guardrails": 7,
}


@pytest.fixture(autouse=True)
def _entorno_sin_claves(monkeypatch):
    for nombre in ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_MODO", "LLM_DIR_GRABACIONES", "LLM_PROVEEDOR"):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    # Ni el runner ni puente.llm leen el .env de quien corre las pruebas: con una clave real y LLM_MODO=vivo,
    # main() llamaría al modelo de verdad.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


@pytest.fixture
def casos():
    return run.cargar_casos(run.RUTA_CASOS)


def _caso(casos, caso_id):
    return next(c for c in casos["casos"] if c["id"] == caso_id)


def _solo(casos, *ids):
    """Copia de casos con solo los ids pedidos."""
    copia = copy.deepcopy(casos)
    copia["casos"] = [c for c in copia["casos"] if c["id"] in ids]
    return copia


def _devuelve_esperado(caso):
    return copy.deepcopy(caso["esperado"])


def _escribir_yaml(ruta, datos):
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ruta


def _yaml_minimo():
    return {
        "version": 1,
        "categorias": {"guardrails": {"issue": 9, "usa_llm": False, "metrica": "veredicto", "meta": 1.0}},
        "casos": [
            {
                "id": "GRD-01",
                "categoria": "guardrails",
                "entrada": {"tipo": "archivo"},
                "esperado": {"veredicto": "rechazar"},
                "escrito_el": "2026-10-16",
            }
        ],
    }


# ---------------------------------------------------------------------------- casos.yaml
def test_casos_yaml_completo(casos):
    ids = [c["id"] for c in casos["casos"]]
    assert len(ids) == sum(CONTEO_ESPERADO.values()) == len(set(ids))
    conteo = {nombre: sum(1 for c in casos["casos"] if c["categoria"] == nombre) for nombre in casos["categorias"]}
    assert conteo == CONTEO_ESPERADO
    for caso in casos["casos"]:
        assert caso["esperado"], caso["id"]
        assert caso["escrito_el"], caso["id"]
        assert caso["datos_sinteticos"] is True, caso["id"]
        assert isinstance(caso["fallos"], list), caso["id"]
        for fallo in caso["fallos"]:  # anotar fallos es lo esperado: solo se valida la estructura del registro
            assert {"fecha", "observado", "cambio_hecho", "issue", "nueva_ejecucion"} <= set(fallo), caso["id"]
    for nombre, info in casos["categorias"].items():
        assert info["metrica"].strip(), nombre
        assert isinstance(info["meta"], (int, float)) and 0 < info["meta"] <= 1, nombre


def test_ids_siguen_el_formato_pedido(casos):
    prefijos = {
        "extraccion_ofertas": "EXT",
        "requisitos_discriminatorios": "DIS",
        "perfil_sin_cv": "PER",
        "matching_factible": "MAT",
        "sesgo_contrafactual": "SES",
        "guardrails": "GRD",
    }
    for caso in casos["casos"]:
        assert caso["id"].startswith(prefijos[caso["categoria"]] + "-")


def test_entradas_de_matching_y_sesgo_cumplen_los_esquemas(casos):
    from puente.schemas import Oferta, Perfil

    for caso in casos["casos"]:
        if caso["categoria"] not in ("matching_factible", "sesgo_contrafactual"):
            continue
        for perfil in caso["entrada"]["perfiles"]:
            Perfil(**perfil)
        for oferta in caso["entrada"]["ofertas"]:
            Oferta(**oferta)


def test_casos_de_matching_y_sesgo_no_dependen_de_datos_de_demo(casos):
    texto = run.RUTA_CASOS.read_text(encoding="utf-8")
    for linea in texto.splitlines():
        if linea.lstrip().startswith("#"):
            continue
        assert "data/" not in linea, linea


def test_esperados_de_matching_respetan_las_restricciones_duras(casos):
    """El esperado se calculó a mano: debe cumplir las restricciones duras sin pasar por ningún solver."""
    for caso in casos["casos"]:
        if caso["categoria"] != "matching_factible":
            continue
        asignacion = caso["esperado"]["asignacion"]
        assert run._violaciones_duras(caso["entrada"], asignacion) == [], caso["id"]


def test_matching_de_yaritza_descarta_tocumen(casos):
    entrada = _caso(casos, "MAT-01")["entrada"]
    violaciones = run._violaciones_duras(entrada, {"yaritza": "OF-TOCUMEN"})
    texto = " ".join(violaciones)
    assert "traslado de 95 min" in texto
    assert "presupuesto" in texto


def test_violaciones_por_horario_salario_y_cupo(casos):
    mat03 = _caso(casos, "MAT-03")["entrada"]
    assert any("cruza con las clases" in v for v in run._violaciones_duras(mat03, {"carlos": "OF-MANANA"}))
    assert any("salario menor" in v for v in run._violaciones_duras(mat03, {"carlos": "OF-POCO"}))
    mat02 = _caso(casos, "MAT-02")["entrada"]
    todos = {p: "VAC-UNICA" for p in ("p1", "p2", "p3", "p4")}
    assert any("k = 3" in v for v in run._violaciones_duras(mat02, todos))


def test_cargar_casos_rechaza_archivos_invalidos(tmp_path):
    def con(cambio):
        datos = _yaml_minimo()
        cambio(datos)
        return _escribir_yaml(tmp_path / "c.yaml", datos)

    assert run.cargar_casos(con(lambda d: None))["casos"][0]["fallos"] == []
    with pytest.raises(ValueError, match="repetido"):
        run.cargar_casos(con(lambda d: d["casos"].append(copy.deepcopy(d["casos"][0]))))
    with pytest.raises(ValueError, match="categoría desconocida"):
        run.cargar_casos(con(lambda d: d["casos"][0].update(categoria="otra")))
    with pytest.raises(ValueError, match="esperado"):
        run.cargar_casos(con(lambda d: d["casos"][0].update(esperado={})))
    with pytest.raises(ValueError, match="escrito_el"):
        run.cargar_casos(con(lambda d: d["casos"][0].pop("escrito_el")))
    with pytest.raises(ValueError, match="métrica"):
        run.cargar_casos(con(lambda d: d["categorias"]["guardrails"].pop("metrica")))
    with pytest.raises(ValueError, match="meta"):
        run.cargar_casos(con(lambda d: d["categorias"]["guardrails"].update(meta="alta")))
    with pytest.raises(ValueError, match="No se pudo leer"):
        run.cargar_casos(tmp_path / "no_existe.yaml")


# ---------------------------------------------------------------------------- comparadores
@pytest.mark.parametrize(
    ("categoria", "esperado", "observado", "cumple"),
    [
        (
            "extraccion_ofertas",
            {"puesto": "Cajero", "empresa": "Super Rio", "corregimiento": "La Chorrera", "salario_mensual": 520, "habilidades": ["a", "b"]},
            {"puesto": "Cajero/a", "empresa": "Super Rio Ficticio S.A.", "corregimiento": "la chorrera", "salario_mensual": 520.0, "habilidades": ["b", "a"]},
            True,
        ),
        ("extraccion_ofertas", {"puesto": "Auxiliar administrativo"}, {"puesto": "Auxiliar"}, False),
        ("extraccion_ofertas", {"empresa": "Distribuidora Ficticia Istmo"}, {"empresa": "Istmo"}, False),
        ("extraccion_ofertas", {"puesto": "Auxiliar administrativo"}, {"puesto": "Auxiliar administrativo/a"}, True),
        ("extraccion_ofertas", {"salario_mensual": 520}, {"salario_mensual": 5000}, False),
        ("extraccion_ofertas", {"habilidades": ["a"]}, {"habilidades": ["a", "b"]}, False),
        ("extraccion_ofertas", {"corregimiento": "Arraiján"}, {"corregimiento": "Arraijan"}, True),
        (
            "requisitos_discriminatorios",
            {"bloqueada": True, "reglas": ["art. 2"], "alertas": ["presencia"]},
            {"bloqueada": True, "reglas": ["Ley 11 de 2005, artículo 2"], "alertas": ["buena presencia"]},
            True,
        ),
        ("requisitos_discriminatorios", {"bloqueada": True, "reglas": ["art. 1"], "alertas": []}, {"bloqueada": True, "reglas": ["art. 2"], "alertas": []}, False),
        (
            "requisitos_discriminatorios",
            {"bloqueada": True, "reglas": ["art. 1", "art. 2"], "alertas": []},
            {"bloqueada": True, "reglas": ["Ley 11 de 2005 (arts. 1 y 2)"], "alertas": []},
            True,
        ),
        (
            "requisitos_discriminatorios",
            {"bloqueada": True, "reglas": ["art. 1", "art. 2"], "alertas": []},
            {"bloqueada": True, "reglas": ["Ley 11 de 2005, artículos 1 y 2"], "alertas": []},
            True,
        ),
        (
            "requisitos_discriminatorios",
            {"bloqueada": True, "reglas": ["art. 2"], "alertas": []},
            {"bloqueada": True, "reglas": ["Ley 11 de 2005 (arts. 1 y 2)"], "alertas": []},
            False,
        ),
        ("requisitos_discriminatorios", {"bloqueada": False, "reglas": [], "alertas": ["foto"]}, {"bloqueada": True, "reglas": [], "alertas": ["foto"]}, False),
        ("requisitos_discriminatorios", {"bloqueada": False, "reglas": [], "alertas": []}, {"bloqueada": False, "reglas": [], "alertas": ["foto"]}, False),
        (
            "requisitos_discriminatorios",
            {"bloqueada": False, "reglas": [], "alertas": []},
            {"bloqueada": False, "reglas": [], "alertas": ["posible requisito de edad", "exige sexo"]},
            False,
        ),
        ("matching_factible", {"asignacion": {"a": "X", "b": None}, "violaciones_duras": 0}, {"asignacion": {"a": "X", "b": "SIN_OFERTA"}, "violaciones_duras": 0}, True),
        ("matching_factible", {"asignacion": {"a": "X"}, "violaciones_duras": 0}, {"asignacion": {"a": "Y"}, "violaciones_duras": 0}, False),
        ("matching_factible", {"asignacion": {"a": "X"}, "violaciones_duras": 0}, {"asignacion": {"a": "X"}, "violaciones_duras": 1}, False),
        ("sesgo_contrafactual", {"sesgo_detectado": True}, {"sesgo_detectado": True}, True),
        ("sesgo_contrafactual", {"sesgo_detectado": True}, {"sesgo_detectado": False}, False),
        ("guardrails", {"veredicto": "rechazar", "llamo_llm": False}, {"veredicto": "rechazar", "llamo_llm": False}, True),
        ("guardrails", {"veredicto": "rechazar", "llamo_llm": False}, {"veredicto": "rechazar", "llamo_llm": True}, False),
        ("guardrails", {"veredicto": "marcar", "sigue_como_dato": True}, {"veredicto": "marcar", "sigue_como_dato": False}, False),
        ("guardrails", {"veredicto": "fuera_de_alcance"}, {"veredicto": "aceptar"}, False),
    ],
)
def test_comparar_por_categoria(categoria, esperado, observado, cumple):
    assert run.comparar(categoria, esperado, observado) is cumple


def test_regla_normalizada_captura_enumeraciones():
    assert run._regla_normalizada("Ley 11 de 2005 (arts. 1 y 2)") == ["art. 1", "art. 2"]
    assert run._regla_normalizada("artículos 1, 2 y 3") == ["art. 1", "art. 2", "art. 3"]
    assert run._regla_normalizada("Ley 11 de 2005, artículo 2") == ["art. 2"]
    assert run._regla_normalizada("sin cita") == []


def test_comparar_categoria_desconocida():
    with pytest.raises(ValueError, match="categoría"):
        run.comparar("otra", {"a": 1}, {"a": 1})


def test_comparar_perfil_exige_evidencia_literal_y_sin_datos_personales():
    esperado = {"habilidades": ["limpieza"], "no_incluir": ["manejo_caja"], "sin_pii": ["6000-0000"]}
    texto = "Limpio casas los sábados. Mi teléfono es 6000-0000."
    bueno = {"habilidades": [{"skill_id": "limpieza", "evidencia": "Limpio casas los sábados"}], "texto_fuente": texto}
    assert run.comparar("perfil_sin_cv", esperado, bueno)
    inventada = {"habilidades": [{"skill_id": "limpieza", "evidencia": "limpio oficinas"}], "texto_fuente": texto}
    assert not run.comparar("perfil_sin_cv", esperado, inventada)
    sin_evidencia = {"habilidades": [{"skill_id": "limpieza", "evidencia": ""}], "texto_fuente": texto}
    assert not run.comparar("perfil_sin_cv", esperado, sin_evidencia)
    con_pii = {"habilidades": [{"skill_id": "limpieza", "evidencia": "Mi teléfono es 6000-0000"}], "texto_fuente": texto}
    assert not run.comparar("perfil_sin_cv", esperado, con_pii)
    sobra = {
        "habilidades": [
            {"skill_id": "limpieza", "evidencia": "Limpio casas"},
            {"skill_id": "manejo_caja", "evidencia": "Limpio casas"},
        ],
        "texto_fuente": texto,
    }
    assert not run.comparar("perfil_sin_cv", esperado, sobra)
    falta = {"habilidades": [], "texto_fuente": texto}
    assert not run.comparar("perfil_sin_cv", esperado, falta)


# ---------------------------------------------------------------------------- correr, tabla y criterios del issue
def test_llm_tres_corridas_y_tasa(casos):
    """Doble equivalente al LLM real: acierta, falla, acierta. La medición con Gemini real no entra en pytest."""
    ext01 = _caso(casos, "EXT-01")
    llamadas = []

    def extractor(caso):
        llamadas.append(caso["id"])
        observado = _devuelve_esperado(caso)
        if len(llamadas) == 2:
            observado["salario_mensual"] = 5000
        return observado

    chico = _solo(casos, "EXT-01")
    resultados = run.correr(chico, ejecutores={"extraccion_ofertas": extractor}, usar_llm=True)
    assert llamadas == ["EXT-01"] * 3
    (resultado,) = resultados
    assert (resultado.corridas, resultado.aciertos, resultado.estado) == (3, 2, "fallo")
    fila = next(f for f in run.tabla(resultados, chico).splitlines() if f.startswith("| extraccion_ofertas"))
    assert "| 2/3 | 67 % |" in fila
    assert "no |" in fila  # 67 % < meta de 80 %

    sin_llm = run.correr(chico, ejecutores={"extraccion_ofertas": extractor}, usar_llm=False)
    assert [r.estado for r in sin_llm] == ["omitido"]
    assert "--sin-llm" in sin_llm[0].detalle
    assert len(llamadas) == 3  # omitido no llama al ejecutor
    assert ext01["esperado"]["salario_mensual"] == 520


def test_por_defecto_los_casos_con_llm_se_ejecutan_sin_mirar_claves(casos):
    chico = _solo(casos, "EXT-01")
    resultado = run.correr(chico, ejecutores={"extraccion_ofertas": _devuelve_esperado})[0]
    assert (resultado.estado, resultado.corridas) == ("ok", 3)


@pytest.mark.parametrize(("causa", "motivo"), [("grabacion_faltante", "sin grabación"), ("sin_clave", "sin clave del LLM")])
def test_sin_respuesta_del_llm_el_caso_queda_omitido_y_no_fallido(casos, causa, motivo):
    from puente.llm import ErrorLLM

    llamadas = []

    def sin_respuesta(caso):
        llamadas.append(caso["id"])
        raise ErrorLLM("no hubo respuesta del modelo", causa=causa)

    resultado = run.correr(_solo(casos, "PER-01"), ejecutores={"perfil_sin_cv": sin_respuesta})[0]
    assert (resultado.estado, resultado.corridas, resultado.aciertos) == ("omitido", 0, 0)
    assert resultado.detalle == f"omitido ({motivo})"
    assert llamadas == ["PER-01"]  # no insiste: las otras corridas tampoco tendrían respuesta


def test_otro_error_del_llm_sigue_siendo_fallo(casos):
    from puente.llm import ErrorLLM

    def rechazo(caso):
        raise ErrorLLM("el modelo se negó", causa="refusal")

    resultado = run.correr(_solo(casos, "PER-01"), ejecutores={"perfil_sin_cv": rechazo}, repeticiones=1)[0]
    assert resultado.estado == "fallo" and "ErrorLLM" in resultado.detalle


def test_sin_respuesta_en_categoria_sin_llm_es_fallo(casos):
    from puente.llm import ErrorLLM

    def raro(caso):
        raise ErrorLLM("no debería llamar al LLM", causa="grabacion_faltante")

    resultado = run.correr(_solo(casos, "DIS-01"), ejecutores={"requisitos_discriminatorios": raro})[0]
    assert resultado.estado == "fallo"  # discriminación no usa LLM: si lo pide, es un error del agente


def test_extraccion_sin_grabacion_queda_omitida(casos):
    from puente.llm import ErrorLLM

    def sin_grabacion(entrada):
        raise ErrorLLM("falta la grabación", causa="grabacion_faltante")

    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], extraer=sin_grabacion)["extraccion_ofertas"]
    resultado = run.correr(_solo(casos, "EXT-01"), ejecutores={"extraccion_ofertas": ejecutor})[0]
    assert (resultado.estado, resultado.detalle) == ("omitido", "omitido (sin grabación)")


def test_main_reproduce_grabaciones_y_omite_las_que_faltan(casos, tmp_path, monkeypatch, capsys):
    vacio = tmp_path / "grabaciones"
    vacio.mkdir()
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(vacio))
    salida = tmp_path / "r.md"
    assert run.main(["--salida", str(salida), "--repeticiones", "1"]) == 0
    texto = salida.read_text(encoding="utf-8")
    con_llm = [c["id"] for c in casos["casos"] if casos["categorias"][c["categoria"]]["usa_llm"]]
    for caso_id in con_llm:
        assert re.search(rf"\| {caso_id} \| \w+ \| omitido \| 0/0 \| omitido \(sin grabación\) \|", texto), caso_id
    assert "modo reproducir" in texto
    assert f"{len(con_llm)} casos con LLM omitidos (sin grabación)" in capsys.readouterr().out
    assert "LLM_MODO" not in os.environ  # el valor por defecto solo vale durante la corrida
    assert os.environ["LLM_DIR_GRABACIONES"] == str(vacio)


def test_entorno_llm_exige_modo_explicito_para_llamadas_reales(monkeypatch):
    monkeypatch.setenv("LLM_MODO", "grabar")
    with run._entorno_llm():
        assert os.environ["LLM_MODO"] == "reproducir"
        assert os.environ["LLM_DIR_GRABACIONES"] == str(run.DIR_GRABACIONES)
    with run._entorno_llm("grabar"):
        assert os.environ["LLM_MODO"] == "grabar"
    assert os.environ["LLM_MODO"] == "grabar" and "LLM_DIR_GRABACIONES" not in os.environ


def test_main_no_usa_modo_vivo_del_entorno_sin_bandera(casos, tmp_path, monkeypatch):
    import puente.llm as llm

    monkeypatch.setenv("LLM_MODO", "vivo")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(tmp_path / "grabaciones"))
    monkeypatch.setattr(llm, "_cliente_por_defecto", lambda *args: pytest.fail("llamada real sin --vivo"))
    assert run.main(["--salida", str(tmp_path / "resultado.md")]) == 0
    assert os.environ["LLM_MODO"] == "vivo"
    assert "modo reproducir" in (tmp_path / "resultado.md").read_text(encoding="utf-8")


def test_entorno_llm_trata_el_valor_vacio_como_no_definido(monkeypatch):
    """.env.example trae «LLM_DIR_GRABACIONES=» vacío: las grabaciones de los evals no deben irse a data/."""
    monkeypatch.setenv("LLM_DIR_GRABACIONES", "")
    monkeypatch.setenv("LLM_MODO", "  ")
    with run._entorno_llm():
        assert os.environ["LLM_DIR_GRABACIONES"] == str(run.DIR_GRABACIONES)
        assert os.environ["LLM_MODO"] == run.MODO_LLM_POR_DEFECTO
    assert os.environ["LLM_DIR_GRABACIONES"] == "" and os.environ["LLM_MODO"] == "  "


def test_categorias_sin_llm_corren_una_sola_vez(casos):
    llamadas = []

    def ejecutor(caso):
        llamadas.append(caso["id"])
        return _devuelve_esperado(caso)

    resultados = run.correr(_solo(casos, "DIS-01"), ejecutores={"requisitos_discriminatorios": ejecutor}, usar_llm=True)
    assert llamadas == ["DIS-01"]
    assert (resultados[0].corridas, resultados[0].aciertos, resultados[0].estado) == (1, 1, "ok")


def test_dobles_perfectos_cumplen_todas_las_metas_que_no_dependen_de_perfil(casos):
    ejecutores = {
        categoria: _devuelve_esperado
        for categoria in (
            "extraccion_ofertas",
            "requisitos_discriminatorios",
            "matching_factible",
            "sesgo_contrafactual",
            "guardrails",
        )
    }
    resultados = run.correr(casos, ejecutores=ejecutores, usar_llm=True)
    por_categoria = {}
    for r in resultados:
        por_categoria.setdefault(r.categoria, set()).add(r.estado)
    assert por_categoria["perfil_sin_cv"] == {"pendiente"}  # sin ejecutor
    assert all(por_categoria[c] == {"ok"} for c in ejecutores)
    assert "| 15/15 | 100 % |" in run.tabla(resultados, casos)


def test_dependencia_pendiente(casos):
    def pendiente(caso):
        raise DependenciaPendiente(5)

    def roto(caso):
        raise ValueError("algo salió mal")

    ejecutores = {"requisitos_discriminatorios": pendiente, "guardrails": roto}
    resultados = run.correr(_solo(casos, "DIS-01", "GRD-01"), ejecutores=ejecutores, usar_llm=False)
    dis, grd = resultados
    assert dis.estado == "pendiente" and "#5" in dis.detalle
    assert grd.estado == "fallo" and "ValueError" in grd.detalle
    tabla = run.tabla(resultados, casos)
    assert "sin medir" in tabla
    assert tabla.count("\n") >= 7  # encabezado, separador y 6 categorías


def _fila(tabla, categoria):
    return next(f for f in tabla.splitlines() if f.startswith(f"| {categoria} |"))


def test_categoria_medida_a_medias_dice_parcial_y_no_si(casos):
    """Una categoría con casos ok y otros omitidos o pendientes no está aprobada: dice «parcial», no «sí»."""
    dis = [c["id"] for c in casos["casos"] if c["categoria"] == "requisitos_discriminatorios"]
    resultados = [ResultadoCaso(dis[0], "requisitos_discriminatorios", "ok", 1, 1, {}, "cumple lo esperado")]
    resultados += [ResultadoCaso(i, "requisitos_discriminatorios", "pendiente", 0, 0, None, "pendiente (#5)") for i in dis[1:]]
    fila = _fila(run.tabla(resultados, casos), "requisitos_discriminatorios")
    assert fila.endswith("| parcial |") and "| 1/1 | 100 % |" in fila

    con_fallo = [ResultadoCaso(dis[0], "requisitos_discriminatorios", "fallo", 0, 1, {}, "difiere")] + resultados[1:]
    assert _fila(run.tabla(con_fallo, casos), "requisitos_discriminatorios").endswith("| no (parcial) |")

    completos = [ResultadoCaso(i, "requisitos_discriminatorios", "ok", 1, 1, {}, "cumple lo esperado") for i in dis]
    assert _fila(run.tabla(completos, casos), "requisitos_discriminatorios").endswith("| sí |")


def test_categoria_con_llm_omitido_en_parte_dice_parcial(casos):
    ext = [c["id"] for c in casos["casos"] if c["categoria"] == "extraccion_ofertas"]
    resultados = [ResultadoCaso(ext[0], "extraccion_ofertas", "ok", 3, 3, {}, "cumple lo esperado")]
    resultados += [ResultadoCaso(i, "extraccion_ofertas", "omitido", 0, 0, None, "omitido (sin grabación)") for i in ext[1:]]
    assert _fila(run.tabla(resultados, casos), "extraccion_ofertas").endswith("| parcial |")


def test_resolver_deja_pendiente_si_falta_el_modulo_o_la_funcion():
    with pytest.raises(DependenciaPendiente) as sin_modulo:
        run._resolver(("puente.modulo_que_no_existe", "f", 7))
    assert sin_modulo.value.issue == 7
    with pytest.raises(DependenciaPendiente) as sin_funcion:
        run._resolver(("puente.schemas", "funcion_que_no_existe", 2))
    assert sin_funcion.value.issue == 2
    assert run._resolver(("puente.schemas", "Oferta", 2)) is not None


def test_resolver_no_oculta_un_import_roto_dentro_de_un_modulo_existente(tmp_path, monkeypatch):
    (tmp_path / "modulo_roto_eval.py").write_text("import paquete_que_no_existe_eval", encoding="utf-8")
    (tmp_path / "modulo_sano_eval.py").write_text("def otra(): return 1", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    # un import transitivo roto en un módulo presente no es 'pendiente': se propaga
    with pytest.raises(ModuleNotFoundError):
        run._resolver(("modulo_roto_eval", "f", 5))
    # función con otro nombre en un módulo presente: pendiente con mensaje que pide ajustar la constante
    with pytest.raises(DependenciaPendiente) as sin_funcion:
        run._resolver(("modulo_sano_eval", "extraer_oferta", 5))
    assert "extraer_oferta" in str(sin_funcion.value) and "modulo_sano_eval" in str(sin_funcion.value)

    casos = {
        "categorias": {"requisitos_discriminatorios": {"usa_llm": False}},
        "casos": [
            {"id": "DIS-X", "categoria": "requisitos_discriminatorios", "entrada": {"texto": "x"}, "esperado": {"bloqueada": False}}
        ],
    }

    def ejecutor(caso):
        return {"bloqueada": run._resolver(("modulo_roto_eval", "f", 5))}

    resultado = run.correr(casos, ejecutores={"requisitos_discriminatorios": ejecutor}, usar_llm=False)[0]
    assert resultado.estado == "fallo"
    assert "ModuleNotFoundError" in resultado.detalle


def test_detalle_pendiente_conserva_modulo_y_funcion():
    casos = {
        "categorias": {"requisitos_discriminatorios": {"usa_llm": False}},
        "casos": [
            {"id": "DIS-X", "categoria": "requisitos_discriminatorios", "entrada": {"texto": "x"}, "esperado": {"bloqueada": False}}
        ],
    }

    def ejecutor(caso):
        run._resolver(("puente.schemas", "funcion_que_no_existe", 5))

    resultado = run.correr(casos, ejecutores={"requisitos_discriminatorios": ejecutor}, usar_llm=False)[0]
    assert resultado.estado == "pendiente"
    assert "#5" in resultado.detalle and "puente.schemas" in resultado.detalle and "funcion_que_no_existe" in resultado.detalle


def test_fallo_abre_issue_bug(casos):
    def doble(caso):
        observado = _devuelve_esperado(caso)
        if caso["id"] == "DIS-03":
            observado["bloqueada"] = True
        return observado

    chico = _solo(casos, "DIS-01", "DIS-02", "DIS-03")
    resultados = run.correr(chico, ejecutores={"requisitos_discriminatorios": doble})
    assert [r.estado for r in resultados] == ["ok", "ok", "fallo"]

    llamadas = []

    def crear(titulo, cuerpo, etiquetas):
        llamadas.append((titulo, cuerpo, etiquetas))
        return "https://github.com/HackathonFISC/equipo-01/issues/999"

    urls = run.abrir_issues_de_fallos(resultados, chico, crear=crear)
    assert urls == ["https://github.com/HackathonFISC/equipo-01/issues/999"]
    assert len(llamadas) == 1
    titulo, cuerpo, etiquetas = llamadas[0]
    assert etiquetas == ["bug"]
    assert "DIS-03" in titulo
    assert "DIS-03" in cuerpo and "uv run python -m evals.run" in cuerpo

    # Un caso cuyo historial ya tiene issue no se vuelve a abrir.
    con_issue = copy.deepcopy(chico)
    _caso(con_issue, "DIS-03")["fallos"] = [{"fecha": "2026-10-16", "issue": 999}]
    assert run.abrir_issues_de_fallos(resultados, con_issue, crear=crear) == []
    assert len(llamadas) == 1


def test_abrir_issues_ignora_pendientes_y_omitidos(casos):
    resultados = run.correr(casos, ejecutores={}, usar_llm=False)
    llamadas = []
    assert run.abrir_issues_de_fallos(resultados, casos, crear=lambda *a: llamadas.append(a) or "x") == []
    assert llamadas == []


# ---------------------------------------------------------------------------- main, archivos y registro de fallos
def test_main_imprime_y_guarda_tabla(tmp_path, capsys):
    salida = tmp_path / "r.md"
    assert run.main(["--salida", str(salida), "--sin-llm"]) == 0
    impreso = capsys.readouterr().out
    assert "Categoría | Casos" in impreso
    for categoria in CONTEO_ESPERADO:
        assert categoria in impreso
    guardado = salida.read_text(encoding="utf-8")
    assert impreso.split("\n\n")[0].strip() in guardado  # la misma tabla impresa
    for seccion in ("## Tabla por categoría", "## Detalle por caso", "## Registro de fallos"):
        assert seccion in guardado
    assert "hora de Panamá" in guardado
    assert "Taxonomía usada en esta corrida" in guardado and "el mismo catálogo que usa la demo" in guardado


def test_modulo_se_ejecuta_como_script(tmp_path):
    salida = tmp_path / "script.md"
    entorno = {k: v for k, v in os.environ.items() if k != "GOOGLE_API_KEY"}
    entorno.update({"LANGSMITH_TRACING": "false", "PYTHONIOENCODING": "utf-8"})
    proceso = subprocess.run(
        [sys.executable, "-m", "evals.run", "--salida", str(salida), "--sin-llm"],
        cwd=RAIZ, env=entorno, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proceso.returncode == 0, proceso.stderr
    assert "Categoría | Casos" in proceso.stdout
    assert salida.exists() and "Categoría | Casos" in salida.read_text(encoding="utf-8")


def test_main_con_casos_invalidos_sale_con_2(tmp_path, capsys):
    malo = tmp_path / "malo.yaml"
    malo.write_text("casos: []\n", encoding="utf-8")
    assert run.main(["--casos", str(malo), "--salida", str(tmp_path / "r.md")]) == 2
    assert "Error en los casos" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()


def test_registro_de_fallos_y_casos_intactos(tmp_path, casos, monkeypatch, capsys):
    datos = yaml.safe_load(run.RUTA_CASOS.read_text(encoding="utf-8"))
    dis01 = next(c for c in datos["casos"] if c["id"] == "DIS-01")
    dis01["fallos"] = [
        {
            "fecha": "2026-10-16",
            "observado": "no bloqueó la oferta de 18 a 25 años",
            "cambio_hecho": "se amplió el patrón de edad en discriminacion.py",
            "issue": 77,
            "nueva_ejecucion": "DIS-01 ok el 2026-10-16 17:00",
        }
    ]
    copia = _escribir_yaml(tmp_path / "casos.yaml", datos)
    antes = copia.read_bytes()

    def doble(caso):
        observado = _devuelve_esperado(caso)
        if caso["id"] == "DIS-02":
            observado["bloqueada"] = False
        return observado

    monkeypatch.setattr(run, "ejecutores_por_defecto", lambda *a, **k: {"requisitos_discriminatorios": doble})
    salida = tmp_path / "resultados.md"
    assert run.main(["--casos", str(copia), "--salida", str(salida), "--sin-llm"]) == 0
    assert copia.read_bytes() == antes  # el runner nunca reescribe casos.yaml
    assert len(run.cargar_casos(copia)["casos"]) == sum(CONTEO_ESPERADO.values())  # y ningún caso se borra

    texto = salida.read_text(encoding="utf-8")
    registro = texto[texto.index("## Registro de fallos"):]
    assert "DIS-02" in registro.split("### Historial")[0]  # fallo nuevo de esta corrida
    historial = registro.split("### Historial")[1]
    for fragmento in ("DIS-01", "se amplió el patrón de edad", "DIS-01 ok el 2026-10-16 17:00", "77"):
        assert fragmento in historial
    capsys.readouterr()


def test_escribir_resultados_con_fecha_de_panama(tmp_path, casos):
    resultados = [ResultadoCaso("GRD-01", "guardrails", "ok", 1, 1, {"veredicto": "rechazar"}, "cumple lo esperado")]
    sin_historial = copy.deepcopy(casos)
    for caso in sin_historial["casos"]:
        caso["fallos"] = []
    ruta = run.escribir_resultados(
        resultados, sin_historial, tmp_path / "sub" / "r.md", ahora=datetime.fromisoformat("2026-10-16T22:30:00+00:00")
    )
    texto = ruta.read_text(encoding="utf-8")
    assert "2026-10-16 17:30 (hora de Panamá, UTC-5)" in texto
    assert "Sin fallos anotados todavía" in texto


def test_el_historial_de_casos_yaml_sale_en_resultados(tmp_path, casos):
    anotados = [c["id"] for c in casos["casos"] if c.get("fallos")]
    assert "DIS-07" in anotados  # #117: «soltera» sin alerta de sexo implícito
    texto = run.escribir_resultados([], casos, tmp_path / "r.md").read_text(encoding="utf-8")
    historial = texto.split("### Historial anotado en casos.yaml", 1)[1]
    assert "| DIS-07 |" in historial and "#117" in historial


def test_aviso_dice_que_no_se_midio_todo(casos):
    resultados = run.correr(casos, ejecutores={}, usar_llm=False)
    aviso = run._aviso_de_cobertura(resultados)
    assert "no mide todo el sistema" in aviso and "LLM_MODO=grabar" in aviso and "--sin-llm" in aviso


# ---------------------------------------------------------------------------- ejecutores por defecto (con dependencias dobles)
def test_ejecutores_por_defecto_cubren_las_seis_categorias():
    assert set(run.ejecutores_por_defecto()) == set(CONTEO_ESPERADO)


def test_ejecutores_por_defecto_quedan_pendientes_si_falta_la_dependencia(casos, monkeypatch):
    def faltante(destino):
        raise DependenciaPendiente(destino[2])

    monkeypatch.setattr(run, "_resolver", faltante)
    resultados = run.correr(casos, usar_llm=True)
    assert {r.estado for r in resultados} == {"pendiente"}
    assert {"#4", "#5", "#6", "#8", "#9"} <= {m for r in resultados for m in re.findall(r"#\d+", r.detalle)}


def test_ejecutor_de_matching_ignora_filas_no_viables_y_cuenta_violaciones(casos, monkeypatch):
    caso = _caso(casos, "MAT-02")

    def asignar(perfiles, ofertas, traslados, config):
        assert len(perfiles) == 4 and traslados == caso["entrada"]["traslados"] and config == {"max_vacantes_por_perfil": 1}
        filas = [
            {"perfil_id": p, "oferta_id": "VAC-UNICA", "puntaje": 100 - n, "desglose": {"viable": True}}
            for n, p in enumerate(("p1", "p2", "p3"))
        ]
        filas.append({"perfil_id": "p4", "oferta_id": "VAC-UNICA", "puntaje": 0, "desglose": {"viable": False}})
        return filas

    monkeypatch.setattr(run, "_resolver", lambda destino: asignar)
    observado = run._ejecutar_matching(caso)
    assert observado["asignacion"] == caso["esperado"]["asignacion"]
    assert observado["violaciones_duras"] == 0
    assert run.comparar("matching_factible", caso["esperado"], observado)

    def asignar_mal(perfiles, ofertas, traslados, config):
        return [{"perfil_id": "p1", "oferta_id": "VAC-UNICA", "puntaje": 1, "desglose": {}}] + [
            {"perfil_id": p, "oferta_id": "VAC-UNICA", "puntaje": 1, "desglose": {}} for p in ("p2", "p3", "p4")
        ]

    monkeypatch.setattr(run, "_resolver", lambda destino: asignar_mal)
    mal = run._ejecutar_matching(caso)
    assert mal["violaciones_duras"] == 1
    assert not run.comparar("matching_factible", caso["esperado"], mal)


def test_matching_mide_violaciones_sobre_todas_las_filas_asignadas(casos, monkeypatch):
    caso = _caso(casos, "MAT-01")
    recibido = {}

    def asignar_doble(perfiles, ofertas, traslados, config):
        recibido["config"] = config
        return [
            {"perfil_id": "yaritza", "oferta_id": "OF-CHORRERA", "puntaje": 85, "desglose": {"viable": True}},
            {"perfil_id": "yaritza", "oferta_id": "OF-TOCUMEN", "puntaje": 5, "desglose": {"viable": True}},
        ]

    monkeypatch.setattr(run, "_resolver", lambda destino: asignar_doble)
    observado = run._ejecutar_matching(caso)
    assert recibido["config"] == {"max_vacantes_por_perfil": 1}
    assert observado["asignacion"] == {"yaritza": "OF-CHORRERA"}  # la mejor coincide con lo esperado...
    assert observado["violaciones_duras"] >= 2  # ...pero Tocumen (traslado, pasaje) y la 2.ª vacante cuentan
    texto = " ".join(observado["violaciones"])
    assert "OF-TOCUMEN" in texto and "vacantes asignadas" in texto
    assert not run.comparar("matching_factible", caso["esperado"], observado)


def test_ejecutor_de_sesgo_inyecta_el_matching_sesgado_solo_cuando_el_caso_lo_pide(casos, monkeypatch):
    recibidos = []

    def auditar(perfiles, ofertas, **kwargs):
        recibidos.append(kwargs)
        return {"sesgo_detectado": "fn_matching" in kwargs}

    monkeypatch.setattr(run, "_resolver", lambda destino: auditar)
    assert run._ejecutar_sesgo(_caso(casos, "SES-01")) == {"sesgo_detectado": False}
    assert run._ejecutar_sesgo(_caso(casos, "SES-02")) == {"sesgo_detectado": True}
    assert "fn_matching" not in recibidos[0] and recibidos[0]["traslados"]

    caso = _caso(casos, "SES-02")
    matching = recibidos[1]["fn_matching"]
    mujer = caso["entrada"]["perfiles"][0]
    hombre = {**mujer, "genero": "masculino"}
    puntajes = lambda perfil: [m["puntaje"] for m in matching([perfil], caso["entrada"]["ofertas"])]  # noqa: E731
    assert puntajes(hombre)[0] - puntajes(mujer)[0] == 40  # el sesgo inyectado es exactamente de 40 puntos
    assert puntajes(hombre) == [85]  # 100 % de cobertura menos 15 min de traslado


def test_espia_cuenta_las_llamadas_al_llm_y_se_restaura():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    original = BaseChatModel.invoke
    modelo = FakeListChatModel(responses=["hola"])
    with run._espiar_llm() as espia:
        assert espia["llamadas"] == 0
        modelo.invoke("buenas")
        assert espia["llamadas"] == 1
    assert BaseChatModel.invoke is original
    modelo.invoke("sin espía")  # fuera del contexto ya no se cuenta
    assert espia["llamadas"] == 1


# ---------------------------------------------------------------------------- adaptadores contra las APIs reales de #5, #6 y #9
# Las dependencias de #5, #6 y #9 ya están fusionadas: estos adaptadores se prueban contra sus funciones reales,
# con dobles solo donde habría LLM (extraer y llm), que tienen la forma de las APIs reales.
def _oferta_extraida(**cambios):
    from puente.agents.mercado import HorarioExtraido, OfertaExtraida

    datos = {
        "puesto": "Cajero/a",
        "empresa": "Super Rio Ficticio S.A.",
        "corregimiento": "La Chorrera",
        "salario_mensual": 520.0,
        "horario": HorarioExtraido(dias=["lunes", "martes"], hora_inicio="08:00", hora_fin="16:00"),
        "habilidades": ["cobro en caja", "atender clientes"],  # nombres de la taxonomía real de #3, no frases de los casos
        "requisitos_texto": "cobrar en caja",
    }
    return OfertaExtraida(**{**datos, **cambios})


def test_taxonomia_de_demo_cubre_los_skill_id_esperados(casos):
    from puente.rules.taxonomia import cargar_taxonomia

    assert casos["taxonomia"] == cargar_taxonomia(RAIZ / "data" / "taxonomia.csv")
    esperados = set()
    for caso in casos["casos"]:
        if caso["categoria"] in ("extraccion_ofertas", "perfil_sin_cv"):
            esperados |= set(caso["esperado"]["habilidades"]) | set(caso["esperado"].get("no_incluir", []))
    assert esperados <= set(casos["taxonomia"])


def test_extraccion_usa_procesar_ofertas_y_mide_la_primera_oferta(casos):
    recibidos = []

    def extraer(entrada):
        recibidos.append(entrada.texto)
        return _oferta_extraida()

    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], extraer=extraer)["extraccion_ofertas"]
    caso = _caso(casos, "EXT-01")
    observado = ejecutor(caso)
    assert recibidos == [caso["entrada"]["texto"]]
    assert observado["salario_mensual"] == 520 and observado["habilidades"] == ["manejo_caja", "atencion_cliente"]
    assert run.comparar("extraccion_ofertas", caso["esperado"], observado)


def test_extraccion_acepta_la_salida_en_dict_y_detecta_un_campo_mal_extraido(casos):
    def extraer(entrada):
        return {**_oferta_extraida().model_dump(), "salario_mensual": 5000}  # la inyección del texto funcionó

    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], extraer=extraer)["extraccion_ofertas"]
    resultados = run.correr(_solo(casos, "EXT-01"), ejecutores={"extraccion_ofertas": ejecutor}, usar_llm=True)
    assert [(r.estado, r.aciertos, r.corridas) for r in resultados] == [("fallo", 0, 3)]
    assert "salario_mensual" in resultados[0].detalle


def test_extraccion_sin_oferta_valida_es_fallo_con_el_motivo(casos):
    # Con el contrato v2 (#42) casi todo es opcional (salario_mensual=None ya es válido); una lista de
    # habilidades que no es lista sigue siendo una salida inválida del extractor.
    extraer = lambda entrada: {**_oferta_extraida().model_dump(), "habilidades": "no es una lista"}  # noqa: E731
    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], extraer=extraer)["extraccion_ofertas"]
    resultados = run.correr(_solo(casos, "EXT-01"), ejecutores={"extraccion_ofertas": ejecutor}, usar_llm=True, repeticiones=1)
    assert resultados[0].estado == "fallo"
    assert "ValueError" in resultados[0].detalle and "ninguna oferta" in resultados[0].detalle


def _con_fallo_pendiente(caso):
    """Caso con un fallo anotado en casos.yaml cuya nueva ejecución sigue pendiente (bug abierto)."""
    return any(str(f.get("nueva_ejecucion", "")).startswith("pendiente") for f in caso.get("fallos") or [])


def test_discriminacion_contra_revisar_texto_real(casos):
    ejecutor = run.ejecutores_por_defecto()["requisitos_discriminatorios"]
    for caso in casos["casos"]:
        # Un fallo conocido y anotado (p. ej. DIS-07, #117) no rompe la suite: el eval lo reporta como fallo.
        if caso["categoria"] == "requisitos_discriminatorios" and not _con_fallo_pendiente(caso):
            observado = ejecutor(caso)
            assert run.comparar(caso["categoria"], caso["esperado"], observado), (caso["id"], observado)
    assert ejecutor(_caso(casos, "DIS-01")) == {"bloqueada": True, "reglas": ["art. 2"], "alertas": ["presencia"]}
    assert ejecutor(_caso(casos, "DIS-06"))["reglas"] == ["art. 1", "art. 2"]


def test_discriminacion_traduce_hallazgos_con_la_forma_de_la_api(casos, monkeypatch):
    hallazgos = [
        SimpleNamespace(regla="edad", frase="18 a 25 años", bloquea=True),
        SimpleNamespace(regla="buena_presencia", frase="buena presencia", bloquea=False),
        SimpleNamespace(regla="regla_nueva", frase="x", bloquea=False),
    ]
    destinos = {
        run.DISCRIMINACION_REVISAR[1]: lambda texto: hallazgos,
        run.DISCRIMINACION_BLOQUEADA[1]: lambda lista: any(h.bloquea for h in lista),
    }
    monkeypatch.setattr(run, "_resolver", lambda destino: destinos[destino[1]])
    observado = run._ejecutar_discriminacion(_caso(casos, "DIS-01"))
    assert observado == {"bloqueada": True, "reglas": ["art. 2"], "alertas": ["otra:regla_nueva", "presencia"]}
    assert not run.comparar("requisitos_discriminatorios", _caso(casos, "DIS-01")["esperado"], observado)  # alerta espuria


def test_a_dict_rechaza_las_formas_no_soportadas():
    from puente.rules.discriminacion import Hallazgo

    hallazgo = Hallazgo("edad", "18 a 25 años", True)
    assert run._a_dict(hallazgo)["regla"] == "edad"
    assert run._a_dict({"a": 1}) == {"a": 1}
    for forma in ([hallazgo], (hallazgo,), None, "texto", 3):
        with pytest.raises(TypeError, match="salida con forma inesperada"):
            run._a_dict(forma)


def test_salida_con_forma_inesperada_es_fallo_de_contrato_y_no_un_fallo_falso(casos, monkeypatch):
    from puente.guardrails import validar_texto

    # Una dependencia que devuelve un solo objeto (y no la lista) o un dict (y no la lista de Hallazgo).
    monkeypatch.setattr(run, "_resolver", lambda destino: lambda entrada: validar_texto("hola"))
    grd = run.correr(_solo(casos, "GRD-01"), ejecutores={"guardrails": run._ejecutar_guardrails}, usar_llm=False)[0]
    assert grd.estado == "fallo" and "TypeError" in grd.detalle and "forma inesperada" in grd.detalle

    monkeypatch.setattr(run, "_resolver", lambda destino: lambda *a, **k: {"bloqueada": True})
    ejecutores = {"requisitos_discriminatorios": run._ejecutar_discriminacion}
    dis = run.correr(_solo(casos, "DIS-01"), ejecutores=ejecutores, usar_llm=False)[0]
    assert dis.estado == "fallo" and "TypeError" in dis.detalle and "forma inesperada" in dis.detalle

    def lista(caso):
        return [{"bloqueada": True}]

    otro = run.correr(_solo(casos, "DIS-01"), ejecutores={"requisitos_discriminatorios": lista}, usar_llm=False)[0]
    assert otro.estado == "fallo" and "forma inesperada: list" in otro.detalle  # el propio ejecutor devolvió una lista


def test_lista_vacia_de_hallazgos_no_es_error_de_contrato(casos, monkeypatch):
    destinos = {run.DISCRIMINACION_REVISAR[1]: lambda texto: [], run.DISCRIMINACION_BLOQUEADA[1]: lambda lista: False}
    monkeypatch.setattr(run, "_resolver", lambda destino: destinos[destino[1]])
    ejecutores = {"requisitos_discriminatorios": run._ejecutar_discriminacion}
    resultados = run.correr(_solo(casos, "DIS-01"), ejecutores=ejecutores, usar_llm=False)
    # Lista vacía es una salida válida: el caso falla por lo que mide (no bloqueó), no por el contrato.
    assert resultados[0].estado == "fallo" and "TypeError" not in resultados[0].detalle
    assert "bloqueada" in resultados[0].detalle


def _llm_de_perfil(*habilidades):
    import json

    return lambda prompt: json.dumps({"habilidades": [{"skill_id": s, "evidencia": e} for s, e in habilidades]})


def test_perfil_contra_construir_perfil_real_con_llm_doble(casos):
    llm = _llm_de_perfil(
        ("atencion_cliente", "Atiendo a los clientes"),
        ("manejo_caja", "cobro en caja"),
        ("inventario", "el control del inventario"),
    )
    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], llm=llm)["perfil_sin_cv"]
    caso = _caso(casos, "PER-01")
    observado = ejecutor(caso)
    assert [h["skill_id"] for h in observado["habilidades"]] == ["atencion_cliente", "manejo_caja", "inventario"]
    resultados = run.correr(_solo(casos, "PER-01"), ejecutores={"perfil_sin_cv": ejecutor}, usar_llm=True)
    assert [(r.estado, r.corridas, r.aciertos) for r in resultados] == [("ok", 3, 3)]


def test_perfil_descarta_evidencia_inventada_y_quita_pii_antes_del_llm(casos):
    prompts = []
    respuesta = _llm_de_perfil(
        ("limpieza", "Limpio casas y oficinas los sábados"),
        ("trabajo_equipo", "trabajo en equipo con mi prima"),
        ("manejo_caja", "sabe cobrar en la caja registradora"),  # inventada: no es literal
    )

    def llm(prompt):
        prompts.append(prompt)
        return respuesta(prompt)

    caso = _caso(casos, "PER-04")
    observado = run.ejecutores_por_defecto(casos["taxonomia"], llm=llm)["perfil_sin_cv"](caso)
    assert {h["skill_id"] for h in observado["habilidades"]} == {"limpieza", "trabajo_equipo"}
    fuente = "\n".join(caso["entrada"]["respuestas"])
    assert observado["prompt_llm"] == prompts[0]  # el observado lleva lo que recibió el LLM
    for dato in ("8-000-0000", "6000-0000"):  # la cédula y el teléfono no llegan al prompt
        assert dato not in prompts[0]
    # Esas dos y la evidencia limpia cumplen lo esperado; la dirección parcial es el fallo de #6 de la prueba siguiente.
    esperado = {**caso["esperado"], "sin_pii": ["8-000-0000", "6000-0000"]}
    assert run.comparar("perfil_sin_cv", esperado, {"texto_fuente": fuente, **observado})


def test_perfil_04_no_filtra_la_direccion_al_llm(casos):
    # Regresión de #63: quitar_pii dejaba pasar 'Calle 5ta' y 'Barrio Ficticio' al prompt (corregido en db930ae).
    caso = _caso(casos, "PER-04")
    llm = _llm_de_perfil(("limpieza", "Limpio casas y oficinas los sábados"))
    observado = run.ejecutores_por_defecto(casos["taxonomia"], llm=llm)["perfil_sin_cv"](caso)
    fuente = "\n".join(caso["entrada"]["respuestas"])
    assert run.comparar("perfil_sin_cv", {**caso["esperado"], "habilidades": ["limpieza"]}, {"texto_fuente": fuente, **observado})


def test_el_eval_detecta_pii_en_el_prompt_aunque_la_evidencia_este_limpia(casos, monkeypatch):
    evidencia =[{"skill_id": "limpieza", "evidencia": "Limpio casas y oficinas los sábados"}]
    esperado = {"habilidades": ["limpieza"], "sin_pii": ["Calle 5ta", "8-000-0000"]}
    limpio = {"habilidades": evidencia, "prompt_llm": "P: ...\nR: vivo en [DIRECCION]"}
    fuga = {"habilidades": evidencia, "prompt_llm": "P: ...\nR: vivo en Calle 5ta, [DIRECCION]"}
    assert run.comparar("perfil_sin_cv", esperado, limpio)
    assert not run.comparar("perfil_sin_cv", esperado, fuga)
    assert run._diferencias("perfil_sin_cv", esperado, fuga) == ["PII en el prompt (Calle 5ta)"]

    # De punta a punta: un agente que deja pasar la dirección al LLM hace fallar el caso con ese motivo.
    from puente.schemas import Perfil

    def construir_con_fuga(entrada, *, llm, **kwargs):
        llm("R: vivo en Calle 5ta, [DIRECCION]")
        perfil = Perfil(id="x", nombre="n", edad=22, corregimiento="Arraiján", salario_minimo=0, presupuesto_pasaje_diario=0)
        return perfil, None

    resolver_real = run._resolver
    monkeypatch.setattr(
        run, "_resolver", lambda destino: construir_con_fuga if destino[1] == run.PERFIL_CONSTRUIR[1] else resolver_real(destino)
    )
    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], llm=lambda prompt: "{}")["perfil_sin_cv"]
    resultado = run.correr(_solo(casos, "PER-04"), ejecutores={"perfil_sin_cv": ejecutor}, usar_llm=True, repeticiones=1)[0]
    assert resultado.estado == "fallo" and "PII en el prompt (Calle 5ta)" in resultado.detalle


def test_perfil_mide_una_habilidad_que_solo_se_desea(casos):
    llm = _llm_de_perfil(("manejo_caja", "Me gustaría aprender a manejar la caja"), ("limpieza", "Limpio casas y oficinas"))
    caso = _caso(casos, "PER-04")
    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], llm=llm)["perfil_sin_cv"]
    resultados = run.correr(_solo(casos, "PER-04"), ejecutores={"perfil_sin_cv": ejecutor}, usar_llm=True, repeticiones=1)
    assert resultados[0].estado == "fallo" and "sobra manejo_caja" in resultados[0].detalle
    assert caso["esperado"]["no_incluir"] == ["manejo_caja"]


def test_perfil_exige_la_tupla_de_construir_perfil(casos, monkeypatch):
    destinos = {
        run.PERFIL_CONSTRUIR[1]: lambda entrada, **kw: {"habilidades": []},
        run.TAXONOMIA_NORMALIZAR[1]: lambda texto, taxonomia: None,
        run.PERFIL_LLM[1]: lambda prompt: "{}",
    }
    monkeypatch.setattr(run, "_resolver", lambda destino: destinos[destino[1]])
    with pytest.raises(TypeError, match="forma inesperada: dict"):
        run._ejecutar_perfil(_caso(casos, "PER-01"), taxonomia=casos["taxonomia"])


def test_perfil_manda_el_formulario_y_las_respuestas_de_la_entrevista(casos, monkeypatch):
    from puente.schemas import Perfil

    recibido = {}

    def construir(entrada, **kwargs):
        recibido.update(entrada=entrada, kwargs=kwargs)
        perfil = Perfil(id="x", nombre="n", edad=20, corregimiento="La Chorrera", salario_minimo=0, presupuesto_pasaje_diario=0)
        return perfil, None

    destinos = {run.PERFIL_CONSTRUIR[1]: construir, run.TAXONOMIA_NORMALIZAR[1]: lambda texto, taxonomia: None}
    monkeypatch.setattr(run, "_resolver", lambda destino: destinos[destino[1]])
    caso = _caso(casos, "PER-02")
    observado = run._ejecutar_perfil(caso, taxonomia=casos["taxonomia"], llm=lambda prompt: "{}")
    assert observado == {"habilidades": [], "prompt_llm": ""}
    assert recibido["entrada"] == {**caso["entrada"]["formulario"], "id": "PER-02", "respuestas": caso["entrada"]["respuestas"]}
    assert "cv_texto" not in recibido["entrada"]
    assert set(recibido["kwargs"]) == {"taxonomia", "normalizar", "llm"}


def test_los_casos_de_perfil_sin_cv_corren_el_agente_en_modo_entrevista(casos, monkeypatch):
    from puente.agents import perfil as agente

    decisiones = []
    construir_real = agente.construir_perfil
    resolver_real = run._resolver

    def construir(entrada, **kwargs):
        perfil, decision = construir_real(entrada, **kwargs)
        decisiones.append(decision)
        return perfil, decision

    monkeypatch.setattr(run, "_resolver", lambda destino: construir if destino[1] == run.PERFIL_CONSTRUIR[1] else resolver_real(destino))
    ejecutor = run.ejecutores_por_defecto(casos["taxonomia"], llm=lambda prompt: '{"habilidades": []}')["perfil_sin_cv"]
    por_caso = [c for c in casos["casos"] if c["categoria"] == "perfil_sin_cv"]
    assert len(por_caso) == 4
    for caso in por_caso:
        respuestas = caso["entrada"]["respuestas"]
        assert 1 <= len(respuestas) <= agente.MAX_PREGUNTAS and "texto" not in caso["entrada"]
        ejecutor(caso)
    assert len(decisiones) == 4
    assert all("· entrevista ·" in d.entrada_resumen and "· cv ·" not in d.entrada_resumen for d in decisiones)


def test_guardrails_contra_evaluar_entrada_real(casos):
    ejecutor = run.ejecutores_por_defecto()["guardrails"]
    for caso in casos["casos"]:
        if caso["categoria"] == "guardrails":
            observado = ejecutor(caso)
            assert run.comparar("guardrails", caso["esperado"], observado), (caso["id"], observado)
    assert ejecutor(_caso(casos, "GRD-01")) == {"veredicto": "rechazar", "sigue_como_dato": False, "llamo_llm": False}
    assert ejecutor(_caso(casos, "GRD-02"))["sigue_como_dato"] is True
    aceptado = {"entrada": {"tipo": "texto", "texto": "Busco trabajo de cajero en La Chorrera"}}
    assert ejecutor(aceptado)["veredicto"] == "aceptar"


def test_guardrails_envuelve_el_archivo_y_traduce_el_veredicto(casos, monkeypatch):
    recibidas = []
    respuestas = {}

    def evaluar(entrada):
        recibidas.append(entrada)
        return respuestas["valor"]

    monkeypatch.setattr(run, "_resolver", lambda destino: evaluar)
    ok = SimpleNamespace(ok=True, marcas=[], motivo="bien", respuesta=None)
    respuestas["valor"] = [SimpleNamespace(ok=False, marcas=[], motivo="grande", respuesta=None)]
    assert run._ejecutar_guardrails(_caso(casos, "GRD-01"))["veredicto"] == "rechazar"
    assert recibidas[0] == {"archivo": {"nombre": "cv_ficticio.pdf", "tamano_bytes": 52428800}}

    respuestas["valor"] = [ok, SimpleNamespace(ok=True, marcas=["inyeccion:ignora"], motivo="m", respuesta=None)]
    marcado = {"veredicto": "marcar", "sigue_como_dato": True, "llamo_llm": False}
    assert run._ejecutar_guardrails(_caso(casos, "GRD-02")) == marcado
    assert recibidas[1] == {"texto": _caso(casos, "GRD-02")["entrada"]["texto"]}

    respuestas["valor"] = [SimpleNamespace(ok=False, marcas=[], motivo="fuera", respuesta="Solo atiendo empleo y formación.")]
    assert run._ejecutar_guardrails(_caso(casos, "GRD-03"))["veredicto"] == "fuera_de_alcance"

    respuestas["valor"] = [ok]
    assert run._ejecutar_guardrails(_caso(casos, "GRD-03"))["veredicto"] == "aceptar"

    respuestas["valor"] = [{"motivo": "sin ok"}]
    with pytest.raises(TypeError, match="sin 'ok'"):
        run._ejecutar_guardrails(_caso(casos, "GRD-03"))


def test_guardrails_detecta_si_se_llamo_al_llm(casos, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    def sin_llm(entrada):
        return [SimpleNamespace(ok=False, marcas=[], motivo="grande", respuesta=None)]

    monkeypatch.setattr(run, "_resolver", lambda destino: sin_llm)
    grd01 = _caso(casos, "GRD-01")
    observado = run._ejecutar_guardrails(grd01)
    assert observado["llamo_llm"] is False
    assert run.comparar("guardrails", grd01["esperado"], observado)

    def llamando_al_llm(entrada):
        FakeListChatModel(responses=["x"]).invoke("hola")
        return sin_llm(entrada)

    monkeypatch.setattr(run, "_resolver", lambda destino: llamando_al_llm)
    observado = run._ejecutar_guardrails(grd01)
    assert observado["llamo_llm"] is True
    assert not run.comparar("guardrails", grd01["esperado"], observado)


def test_correr_pasa_la_taxonomia_del_yaml_a_los_ejecutores_por_defecto(casos, monkeypatch):
    recibido = {}

    def falso(taxonomia=None, **kwargs):
        recibido["taxonomia"] = taxonomia
        return {}

    monkeypatch.setattr(run, "ejecutores_por_defecto", falso)
    run.correr(_solo(casos, "EXT-01"), usar_llm=True)
    assert recibido["taxonomia"] == casos["taxonomia"]


def test_cargar_casos_valida_la_taxonomia_en_linea(tmp_path):
    def con(taxonomia):
        datos = _yaml_minimo()
        datos["taxonomia"] = taxonomia
        return _escribir_yaml(tmp_path / "t.yaml", datos)

    assert "a" in run.cargar_casos(con({"a": {"nombre_es": "A", "sinonimos": ["aa"]}}))["taxonomia"]
    for malo in ([], {}, {"a": "texto"}, {"a": {"sinonimos": []}}, {"a": {"nombre_es": "A", "sinonimos": "aa"}}):
        with pytest.raises(ValueError, match="taxonom"):
            run.cargar_casos(con(malo))
