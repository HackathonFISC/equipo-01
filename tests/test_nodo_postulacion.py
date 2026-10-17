"""Nodo `postulacion` dentro del grafo real de main (issue #12).

El nodo se inyecta con `construir_grafo(nodos={"postulacion": ...})` y checkpointer SQLite en
`tmp_path`. Sin red ni claves: Telegram es un doble y todo vive en directorios temporales.
Todos los datos son SINTÉTICOS (caso_yaritza.json y perfiles ficticios).
"""
import urllib.request
from datetime import datetime, timedelta, timezone
from itertools import pairwise

import ics
import pytest
from langgraph.types import Command

from puente import graph as g
from puente.agents.postulacion import (
    crear_nodo_postulacion,
    nodo_postulacion,
    postular,
    texto_para_joven,
)
from puente.graph import (
    aprobacion_pendiente,
    cargar_caso,
    checkpointer_sqlite,
    config_caso,
    construir_grafo,
)
from puente.schemas import EstadoPostulacion, HabilidadPerfil, Horario, Match, Oferta, Perfil
from puente.tools import db

PANAMA = timezone(timedelta(hours=-5))
AHORA = datetime(2026, 10, 19, 7, 0, tzinfo=PANAMA)  # SINTÉTICO: un lunes a las 07:00
CAJERO = "of-cajero-vista-alegre"
COCINA = "of-ayudante-cocina-chorrera"
TOCUMEN = "of-tocumen-sintetica"
CHAT_EMPRESA = "chat-empresa-ficticio"
CHAT_CARLOS = "chat-carlos-ficticio"
CHAT_YARITZA = "chat-yaritza-ficticio"
MOTIVO_TOCUMEN = "traslado de 95 min a Tocumen"


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    """Tokens y chats ficticios; cualquier salida real a la red hace fallar la prueba."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", CHAT_EMPRESA)
    monkeypatch.setenv("TELEGRAM_CHATS_JOVENES",
                       f"carlos={CHAT_CARLOS},yaritza={CHAT_YARITZA},ana=chat-ana,bea=chat-bea,cira=chat-cira")
    monkeypatch.delenv("PUENTE_ENTREVISTA_INICIO", raising=False)

    def prohibido(*a, **k):
        raise AssertionError("no debe haber red en las pruebas")

    monkeypatch.setattr(urllib.request, "urlopen", prohibido)


class Doble:
    """Transporte de Telegram falso: guarda (token, chat_id, texto, adjuntos)."""

    def __init__(self):
        self.mensajes = []

    def __call__(self, token, chat_id, texto, adjuntos):
        self.mensajes.append((token, chat_id, texto, list(adjuntos)))
        return {"ok": True}


class Caso:
    """Grafo real con el nodo inyectado, detenido en el interrupt del orientador."""

    def __init__(self, tmp_path, *, estado_extra=None, con_tocumen=False, matching=None, **nodo):
        self.tmp_path = tmp_path
        self.empresa, self.joven = Doble(), Doble()
        self.ruta_db = tmp_path / "puente.db"
        self.outbox = tmp_path / "outbox"
        nodos = {
            "postulacion": crear_nodo_postulacion(
                ruta_db=self.ruta_db, outbox=self.outbox,
                enviar=self.empresa, enviar_joven=self.joven, ahora=lambda: AHORA, **nodo,
            )
        }
        thread, estado = cargar_caso()
        estado.update(estado_extra or {})
        if con_tocumen:
            tocumen = estado["ofertas"][0].model_copy(
                update={"id": TOCUMEN, "puesto": "Operario de bodega", "empresa": "Bodega Ficticia",
                        "corregimiento": "Tocumen", "habilidades": ["soldadura"]}
            )
            estado["ofertas"] = [*estado["ofertas"], tocumen]
            nodos["matching"] = matching or _matching_con_descarte_por_traslado
        # Stubs de la Fase 1 para lo demás: desde #10 fase 2, construir_grafo usa los agentes reales.
        self.grafo = construir_grafo(
            nodos={**g.NODOS_FASE_1, **nodos}, checkpointer=checkpointer_sqlite(tmp_path / "checkpoints.db")
        )
        self.config = config_caso(thread)
        self.grafo.invoke(estado, self.config)

    @property
    def solicitud(self):
        return aprobacion_pendiente(self.grafo, self.config)

    @property
    def valores(self):
        return self.grafo.get_state(self.config).values

    def responder(self, **respuesta):
        return self.grafo.invoke(Command(resume=respuesta), self.config)

    def aprobar(self, consentidos=None, **extra):
        solicitud = self.solicitud
        todos = [c["perfil_id"] for c in solicitud["candidatos"]]
        return self.responder(
            accion="aprobar", orientador="Orientadora Ficticia", oferta_id=solicitud["oferta_id"],
            consentidos=todos if consentidos is None else consentidos, **extra,
        )

    def conexion(self):
        return db.conectar(self.ruta_db)


def _matching_con_descarte_por_traslado(estado):
    """Matching de la Fase 1 más un descarte por traslado de Yaritza a la oferta de Tocumen."""
    salida = g.stub_matching(estado)
    matches = []
    for m in salida["matches"]:
        if (m.perfil_id, m.oferta_id) == ("yaritza", TOCUMEN):
            m = m.model_copy(update={"desglose": {
                **m.desglose, "viable": False, "motivo_descarte": MOTIVO_TOCUMEN, "minutos_traslado": 95}})
        matches.append(m)
    return {**salida, "matches": matches}


def test_aprobar_en_el_tablero_envia_a_la_empresa_y_actualiza_el_embudo(tmp_path):
    caso = Caso(tmp_path)
    solicitud = caso.solicitud
    aprobados = [c["perfil_id"] for c in solicitud["candidatos"]]
    assert aprobados == ["carlos", "yaritza"]

    caso.aprobar()  # como lo haría el tablero: Command(resume={accion, orientador, oferta_id, consentidos})

    assert len(caso.empresa.mensajes) == 1
    token, chat_id, texto, adjuntos = caso.empresa.mensajes[0]
    assert chat_id == CHAT_EMPRESA
    assert "Yaritza Ficticia" in texto and "Carlos Ficticio" in texto
    assert {a.suffix for a in adjuntos} == {".pdf", ".ics"}

    valores = caso.valores
    assert valores["postulaciones"] == {
        f"carlos:{CAJERO}": EstadoPostulacion.ENVIADO,
        f"yaritza:{CAJERO}": EstadoPostulacion.ENVIADO,
    }
    assert [c["perfil_id"] for c in valores["consentimientos"]] == aprobados
    assert all(c["otorgado"] and c["medio"] == "orientador" for c in valores["consentimientos"])

    conn = caso.conexion()
    try:
        assert db.embudo(conn)["enviado"] == len(aprobados)
        assert sum(db.embudo(conn).values()) == len(aprobados)
        agentes = {t["agente"] for t in db.trazas(conn)}
        assert {"postulacion", "aprobacion", "matching"} <= agentes  # el volcado trae lo de los otros nodos
        # Cronológico: la aprobación del orientador va antes de lo que Postulación hizo con ella.
        trazas = db.trazas(conn)
        ids_aprobacion = [t["id"] for t in trazas if t["agente"] == "aprobacion"]
        ids_postulacion = [t["id"] for t in trazas if t["agente"] == "postulacion"]
        assert ids_aprobacion and ids_postulacion and max(ids_aprobacion) < min(ids_postulacion)
        consentimientos = [t for t in db.trazas(conn, agente="postulacion") if "consentimiento de" in t["razon"]]
        assert len(consentimientos) == len(aprobados)
        assert "por orientador el" in consentimientos[0]["razon"]
    finally:
        conn.close()


def test_el_nodo_elige_la_franja_y_el_mensaje_a_la_empresa_es_ciego(tmp_path):
    caso = Caso(tmp_path)
    caso.aprobar(mensaje="Buen día: les presentamos a dos personas para la caja.")

    texto = caso.empresa.mensajes[0][2]
    assert texto.startswith("Buen día: les presentamos a dos personas para la caja.")  # va primero
    for dato in ("Barrio Colón", "20 años", "edad", "género", "Femenino"):
        assert dato not in texto

    # Yaritza estudia de 18:00 a 22:00 y no declaró horario libre; Carlos no tiene clases. Con
    # traslado 0 del Matching de la Fase 1, lunes 08:00 es el primer hueco posterior a AHORA
    # (07:00) y es de Carlos, que va primero en la aprobación. Yaritza recibe la siguiente hora
    # libre de la misma empresa: 08:45, no la misma 08:00.
    eventos = {}
    for ruta in caso.outbox.glob("entrevista_*.ics"):
        (evento,) = ics.Calendar(ruta.read_text(encoding="utf-8")).events
        eventos[ruta.stem] = evento.begin.datetime
    assert eventos["entrevista_carlos_" + CAJERO] == datetime(2026, 10, 19, 8, 0, tzinfo=PANAMA)
    assert eventos["entrevista_yaritza_" + CAJERO] == datetime(2026, 10, 19, 8, 45, tzinfo=PANAMA)


def test_sin_consentido_postulacion_no_envia_nada(tmp_path):
    # (a) postular() sin consentimientos, o con uno otorgado=False, lanza ValueError sin efectos.
    perfil = Perfil(id="p1", nombre="Persona Ficticia", edad=21, corregimiento="La Chorrera",
                    salario_minimo=300.0, presupuesto_pasaje_diario=2.0,
                    habilidades=[HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo clientes")])
    oferta = Oferta(id="o1", puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
                    salario_mensual=420.0, horario=Horario(dias=["lunes"], hora_inicio="08:00", hora_fin="16:00"),
                    habilidades=["atencion_cliente"])
    match = Match(perfil_id="p1", oferta_id="o1", puntaje=90,
                  desglose={"cobertura": 1.0, "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0})
    aprobacion = {"orientador": "Orientadora Ficticia", "timestamp": "2026-10-20T09:30:00-05:00",
                  "aprobados": [{"perfil_id": "p1", "oferta_id": "o1"}]}
    denegado = [{"perfil_id": "p1", "oferta_id": "o1", "otorgado": False, "medio": "joven"}]
    doble = Doble()
    conn = db.conectar(tmp_path / "directa.db")
    try:
        for consentimientos in (None, [], denegado):
            with pytest.raises(ValueError, match="Sin consentimiento"):
                postular([match], {"p1": perfil}, {"o1": oferta}, aprobacion=aprobacion, conn=conn,
                         outbox=tmp_path / "directo", consentimientos=consentimientos, enviar=doble)
        assert doble.mensajes == [] and not (tmp_path / "directo").exists()
        assert conn.execute("SELECT COUNT(*) FROM postulaciones").fetchone()[0] == 0
        assert db.embudo(conn)["consentido"] == db.embudo(conn)["enviado"] == 0
    finally:
        conn.close()

    # (b) el nodo con un otorgado=False explícito para la única pareja aprobada.
    caso = Caso(tmp_path, estado_extra={"consentimientos": [
        {"perfil_id": "yaritza", "oferta_id": CAJERO, "otorgado": False, "medio": "joven"}]})
    caso.aprobar(consentidos=["yaritza"])

    assert caso.empresa.mensajes == [] and caso.joven.mensajes == []
    assert not caso.outbox.exists()
    valores = caso.valores
    assert any("consentimiento" in e and "yaritza" in e for e in valores["errores"])
    assert valores["postulaciones"] == {}
    conn = caso.conexion()
    try:
        assert db.embudo(conn)["enviado"] == 0 and db.embudo(conn)["consentido"] == 0
        (explica,) = [t for t in db.trazas(conn, agente="postulacion") if t["salida_resumen"] == "no se envió nada"]
        assert "consentimiento" in explica["razon"].lower()
    finally:
        conn.close()


def test_un_otorgado_false_solo_frena_a_esa_pareja(tmp_path):
    caso = Caso(tmp_path, estado_extra={"consentimientos": [
        {"perfil_id": "yaritza", "oferta_id": CAJERO, "otorgado": False, "medio": "joven"}]})
    # El orientador aprueba a Carlos y a Yaritza sin escribir `mensaje`: el grafo deja el sugerido,
    # que nombra a toda la lista corta. A la empresa no puede llegar el nombre de quien no consintió.
    caso.aprobar()

    (token, chat_id, texto, adjuntos) = caso.empresa.mensajes[0]
    assert "Carlos Ficticio" in texto and "Yaritza" not in texto
    assert caso.valores["postulaciones"] == {f"carlos:{CAJERO}": EstadoPostulacion.ENVIADO}
    conn = caso.conexion()
    try:
        assert db.embudo(conn)["enviado"] == 1
    finally:
        conn.close()


def test_el_consentimiento_del_joven_queda_con_su_medio_y_fecha(tmp_path):
    marca = "2026-10-19T06:45:00-05:00"
    caso = Caso(tmp_path, estado_extra={"consentimientos": [
        {"perfil_id": "yaritza", "oferta_id": CAJERO, "otorgado": True, "medio": "joven", "timestamp": marca}]})
    caso.aprobar()

    conn = caso.conexion()
    try:
        razones = [t["razon"] for t in db.trazas(conn, agente="postulacion") if "consentimiento de" in t["razon"]]
        assert any(f"consentimiento de yaritza para {CAJERO} por joven el {marca}" in r for r in razones)
        assert any(f"consentimiento de carlos para {CAJERO} por orientador el" in r for r in razones)
        historial = db.historial(conn, conn.execute(
            "SELECT id FROM postulaciones WHERE perfil_id = 'yaritza'").fetchone()["id"])
        assert [h["estado_nuevo"] for h in historial] == ["preseleccionado", "aprobado", "consentido", "enviado"]
    finally:
        conn.close()


def test_rechazar_no_envia_y_queda_como_decision(tmp_path):
    caso = Caso(tmp_path)
    pendientes = caso.solicitud["vacantes_pendientes"]
    assert pendientes == [CAJERO, COCINA]
    for oferta_id in pendientes:
        caso.responder(accion="rechazar", orientador="Orientadora Ficticia", oferta_id=oferta_id)

    assert caso.empresa.mensajes == [] and caso.joven.mensajes == []
    assert not caso.outbox.exists() or not list(caso.outbox.iterdir())
    final = caso.valores
    assert final.get("postulaciones") in (None, {})

    conn = caso.conexion()  # rechazar no pasa por el nodo: el tablero vuelca el estado
    try:
        resultado = db.volcar_estado(conn, final)
        assert resultado["decisiones"] > 0
        assert db.embudo(conn) == {estado: 0 for estado in db.ESTADOS}
        rechazos = db.trazas(conn, agente="aprobacion")
        assert [r["salida_resumen"] for r in rechazos] == ["rechazar", "rechazar"]
        assert "Orientadora Ficticia" in rechazos[0]["razon"]
        assert db.volcar_estado(conn, final) == {"decisiones": 0, "descartes": 0}  # idempotente
        assert any(d["perfil_id"] == "maria" and d["oferta_id"] == CAJERO for d in db.descartes(conn))
    finally:
        conn.close()


def test_mensaje_al_joven_trae_postulacion_descarte_e_ics(tmp_path):
    caso = Caso(tmp_path, con_tocumen=True)
    caso.aprobar()

    por_chat = {m[1]: m[2] for m in caso.joven.mensajes}
    assert set(por_chat) == {CHAT_CARLOS, CHAT_YARITZA}  # uno por joven, nunca el de la empresa
    assert len(caso.joven.mensajes) == 2  # Carlos y Yaritza
    # Cada joven recibe solo lo suyo: ni el nombre ni los descartes del otro.
    assert por_chat[CHAT_YARITZA].startswith("Hola, Yaritza") and "Carlos" not in por_chat[CHAT_YARITZA]
    assert por_chat[CHAT_CARLOS].startswith("Hola, Carlos") and "Yaritza" not in por_chat[CHAT_CARLOS]
    assert MOTIVO_TOCUMEN not in por_chat[CHAT_CARLOS]
    textos = {m[2].split(".")[0].removeprefix("Hola, "): m for m in caso.joven.mensajes}
    _, _, texto, adjuntos = textos["Yaritza Ficticia"]
    assert "Cajero(a) de minisúper" in texto and "Minisúper Ficticio El Ahorro" in texto
    assert MOTIVO_TOCUMEN in texto and "Bodega Ficticia" in texto
    assert [a.suffix for a in adjuntos] == [".ics"] and "yaritza" in adjuntos[0].name
    assert ".ics" in texto
    # El mensaje a la empresa no cuenta lo que se descartó ni lleva datos del joven.
    assert MOTIVO_TOCUMEN not in caso.empresa.mensajes[0][2]

    conn = caso.conexion()
    try:
        assert {"perfil_id": "yaritza", "oferta_id": TOCUMEN, "motivo": MOTIVO_TOCUMEN, "origen": "matching"} \
            in db.descartes(conn, perfil_id="yaritza")
    finally:
        conn.close()


def test_sin_chat_del_joven_el_mensaje_queda_en_outbox(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHATS_JOVENES")
    caso = Caso(tmp_path, con_tocumen=True)
    caso.aprobar()

    assert caso.joven.mensajes == []
    archivos = sorted(caso.outbox.glob("joven_*.txt"))
    assert len(archivos) == 2
    assert [a.name.split("_")[1] for a in archivos] == ["carlos", "yaritza"]  # un archivo por joven
    yaritza = next(a for a in archivos if "Yaritza Ficticia" in a.read_text(encoding="utf-8"))
    contenido = yaritza.read_text(encoding="utf-8")
    assert MOTIVO_TOCUMEN in contenido and "entrevista_yaritza_" in contenido
    assert len(caso.empresa.mensajes) == 1  # el de la empresa sí salió por su chat


def test_volver_a_correr_el_nodo_no_repite_nada(tmp_path):
    caso = Caso(tmp_path)
    caso.aprobar()
    estado = dict(caso.valores)
    enviados_empresa, enviados_joven = len(caso.empresa.mensajes), len(caso.joven.mensajes)
    archivos = sorted(p.name for p in caso.outbox.iterdir())

    cambios = crear_nodo_postulacion(
        ruta_db=caso.ruta_db, outbox=caso.outbox, enviar=caso.empresa, enviar_joven=caso.joven,
        ahora=lambda: AHORA,
    )(estado)

    assert len(caso.empresa.mensajes) == enviados_empresa and len(caso.joven.mensajes) == enviados_joven
    assert sorted(p.name for p in caso.outbox.iterdir()) == archivos
    assert set(cambios["postulaciones"].values()) == {EstadoPostulacion.ENVIADO}
    assert any("ya postulada" in d.razon.lower() for d in cambios["decisiones"])
    conn = caso.conexion()
    try:
        assert db.embudo(conn)["enviado"] == 2
    finally:
        conn.close()


def test_el_nodo_sin_aprobacion_no_hace_nada(tmp_path):
    empresa, joven = Doble(), Doble()
    nodo = crear_nodo_postulacion(ruta_db=tmp_path / "puente.db", outbox=tmp_path / "outbox",
                                  enviar=empresa, enviar_joven=joven, ahora=lambda: AHORA)
    cambios = nodo({"aprobacion": None, "decisiones": [], "errores": []})
    assert empresa.mensajes == [] and joven.mensajes == [] and not (tmp_path / "outbox").exists()
    assert cambios["errores"] and cambios["decisiones"][-1].salida_resumen == "no se envió nada"


def test_nodo_postulacion_por_defecto_es_un_nodo_listo_para_el_grafo():
    assert callable(nodo_postulacion)
    assert nodo_postulacion.__name__ == "nodo_postulacion"


def test_texto_para_joven_cuenta_a_donde_se_postulo_y_que_se_descarto():
    perfil = Perfil(id="p1", nombre="Persona Ficticia", edad=21, corregimiento="La Chorrera",
                    salario_minimo=300.0, presupuesto_pasaje_diario=2.0)
    oferta = Oferta(id="o1", puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
                    salario_mensual=420.0, horario=Horario(dias=["lunes"], hora_inicio="08:00", hora_fin="16:00"),
                    habilidades=["atencion_cliente"])
    texto = texto_para_joven(perfil, [(oferta, AHORA + timedelta(days=1, hours=3))],
                             [("Operario en Tocumen", MOTIVO_TOCUMEN)])
    assert "Persona Ficticia" in texto
    assert "Cajera en Super Demo" in texto and "2026-10-20 10:00" in texto
    assert f"Operario en Tocumen: {MOTIVO_TOCUMEN}" in texto
    assert ".ics" in texto
    sin_envio = texto_para_joven(perfil, [], [("Operario en Tocumen", MOTIVO_TOCUMEN)])
    assert "no te postulamos" in sin_envio and ".ics" not in sin_envio


def test_sin_mensaje_del_orientador_la_empresa_no_ve_a_quien_no_consintio(tmp_path):
    """El mensaje sugerido por el grafo nombra a toda la lista corta; se rearma con los consentidos."""
    caso = Caso(tmp_path)
    assert "Yaritza Ficticia" in caso.solicitud["mensaje_sugerido"]  # el sugerido sí la nombra
    caso.aprobar(consentidos=["carlos"])

    (mensaje,) = caso.empresa.mensajes
    texto = mensaje[2]
    assert "Yaritza" not in texto and "Ficticia" not in texto.replace("Carlos Ficticio", "")
    assert texto.startswith("Buen día, Minisúper Ficticio El Ahorro: desde Puente les presentamos a Carlos Ficticio")
    assert caso.valores["aprobacion"]["sin_consentimiento"] == ["yaritza"]
    assert caso.valores["postulaciones"] == {f"carlos:{CAJERO}": EstadoPostulacion.ENVIADO}
    assert {a.name for a in mensaje[3]} == {
        f"cv_carlos_{CAJERO}.pdf", f"carta_carlos_{CAJERO}.pdf", f"entrevista_carlos_{CAJERO}.ics"
    }
    conn = caso.conexion()
    try:
        assert any("rearmado" in t["salida_resumen"] for t in db.trazas(conn, agente="postulacion"))
    finally:
        conn.close()


def test_un_mensaje_propio_que_nombra_a_quien_no_sale_tambien_se_rearma(tmp_path):
    caso = Caso(tmp_path)
    caso.aprobar(consentidos=["carlos"], mensaje="Buen día: les presentamos a Carlos y a yaritza para la caja.")
    texto = caso.empresa.mensajes[0][2]
    assert "yaritza" not in texto.lower()
    assert "Carlos Ficticio" in texto


def test_un_mensaje_propio_que_solo_nombra_a_quienes_salen_se_respeta(tmp_path):
    caso = Caso(tmp_path)
    caso.aprobar(consentidos=["carlos"], mensaje="Buen día: les presento a Carlos para la caja.")
    assert caso.empresa.mensajes[0][2].startswith("Buen día: les presento a Carlos para la caja.")


def _matching_con_forma_del_solver(estado):
    """Como el solver real: el descarte viaja en desglose["ofertas_descartadas"], sin motivo por pareja."""
    salida = g.stub_matching(estado)
    matches = []
    for m in salida["matches"]:
        if (m.perfil_id, m.oferta_id) == ("yaritza", CAJERO):
            m = m.model_copy(update={
                "explicacion": f"Asignado a {CAJERO}: cobertura 100%, traslado 40 min, pasaje $1.00/día | "
                               f"Descartes: {TOCUMEN} (salario $200.00 < salario mínimo del perfil ($300.00))",
                "desglose": {**m.desglose, "viable": True, "motivo_descarte": None,
                             "ofertas_descartadas": {TOCUMEN: MOTIVO_TOCUMEN}}})
        matches.append(m)
    return {**salida, "matches": matches}


def test_con_el_matching_real_el_descarte_llega_a_la_tabla_y_al_joven_sin_filtrarse_a_la_empresa(tmp_path):
    caso = Caso(tmp_path, con_tocumen=True, matching=_matching_con_forma_del_solver)
    caso.aprobar()

    textos = {m[2].split(".")[0].removeprefix("Hola, "): m[2] for m in caso.joven.mensajes}
    assert MOTIVO_TOCUMEN in textos["Yaritza Ficticia"] and "Bodega Ficticia" in textos["Yaritza Ficticia"]
    empresa = caso.empresa.mensajes[0][2]
    for privado in ("Descartes", "salario mínimo", "$300", "pasaje", MOTIVO_TOCUMEN, "Por qué"):
        assert privado not in empresa
    conn = caso.conexion()
    try:
        assert {"perfil_id": "yaritza", "oferta_id": TOCUMEN, "motivo": MOTIVO_TOCUMEN, "origen": "matching"} \
            in db.descartes(conn, perfil_id="yaritza")
    finally:
        conn.close()


def test_el_mensaje_libre_del_orientador_sale_ciego_hacia_la_empresa(tmp_path):
    caso = Caso(tmp_path)
    caso.aprobar(
        consentidos=["yaritza"],
        mensaje="Les presento a Yaritza Ficticia, mujer de 20 años que vive en Barrio Colón.",
    )
    texto = caso.empresa.mensajes[0][2]
    assert texto.startswith("Les presento a Yaritza Ficticia, ")
    for dato in ("Barrio Colón", "20 años", "mujer"):
        assert dato not in texto


def _con_horarios(libre, clases):
    """Estado extra con el horario libre y de clases dados para todos los perfiles (SINTÉTICOS).

    Yaritza llega por ``entradas_perfil`` (el stub de Perfil la arma ahí) y Carlos ya viene en
    ``perfiles``; se cambian los dos.
    """
    _, estado = cargar_caso()
    cambio = {"horario_libre": libre, "horario_clases": clases}
    return {
        "perfiles": [p.model_copy(update=cambio) for p in estado["perfiles"]],
        "entradas_perfil": [
            {**e, **{k: [h.model_dump() for h in v] for k, v in cambio.items()}}
            for e in estado["entradas_perfil"]
        ],
    }


def test_sin_franja_normal_la_fecha_queda_por_confirmar_y_no_cae_sobre_las_clases(tmp_path):
    """Libre de 18:00 a 21:00 y clases de 08:00 a 12:00: no hay hora laboral compatible.

    Antes caía en 'mañana 10:00', en plena clase. Ahora se propone dentro del horario libre y
    queda una Decision que dice que la fecha está por confirmar.
    """
    semana = ["lunes", "martes", "miercoles", "jueves", "viernes"]
    perfiles = _con_horarios(
        [Horario(dias=semana, hora_inicio="18:00", hora_fin="21:00")],
        [Horario(dias=semana, hora_inicio="08:00", hora_fin="12:00")],
    )
    caso = Caso(tmp_path, estado_extra=perfiles)
    caso.aprobar()

    inicios = {}
    for ruta in caso.outbox.glob("entrevista_*.ics"):
        (evento,) = ics.Calendar(ruta.read_text(encoding="utf-8")).events
        inicios[ruta.stem] = evento.begin.datetime.astimezone(PANAMA)
    assert len(inicios) == 2
    for inicio in inicios.values():
        assert inicio > AHORA
        assert inicio.weekday() < 5
        # Dentro de su horario libre y lejos de las clases de la mañana.
        assert inicio.hour >= 18 and (inicio.hour, inicio.minute) < (21, 0)
    conn = caso.conexion()
    try:
        avisos = [t for t in db.trazas(conn, agente="postulacion")
                  if t["salida_resumen"] == "sin franja compatible: fecha por confirmar"]
        assert len(avisos) == 2
        assert all("horario libre" in t["razon"] for t in avisos)
    finally:
        conn.close()
    assert "por confirmar" in caso.empresa.mensajes[0][2]


def test_si_ninguna_hora_cabe_fuera_de_las_clases_no_se_envia_y_queda_la_decision(tmp_path):
    todo = Horario(dias=["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"],
                   hora_inicio="00:00", hora_fin="23:59")
    caso = Caso(tmp_path, estado_extra=_con_horarios([], [todo]))
    caso.aprobar()
    assert caso.empresa.mensajes == []
    assert any("sin franja compatible" in e for e in caso.valores["errores"])
    conn = caso.conexion()
    try:
        assert db.embudo(conn)["enviado"] == 0
        assert any("sin franja compatible: fecha por confirmar" in t["razon"] + t["salida_resumen"]
                   for t in db.trazas(conn, agente="postulacion"))
    finally:
        conn.close()


SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes"]


def _tres_de_una_oferta():
    """Tres candidatos SINTÉTICOS de la misma oferta, que no publica franjas (horario laboral).

    Ana no tiene clases ni traslado; Bea tiene 30 min de traslado; Cira estudia de 07:00 a 08:30
    y tiene 15 min de traslado, así que no puede antes de las 08:45.
    """
    oferta = Oferta(id="o-tres", puesto="Cajera", empresa="Super Ficticio", corregimiento="Arraiján",
                    salario_mensual=500.0, habilidades=["atencion_cliente"])
    datos = [
        ("ana", "Ana Ficticia", [], 0),
        ("bea", "Bea Ficticia", [], 30),
        ("cira", "Cira Ficticia", [Horario(dias=SEMANA, hora_inicio="07:00", hora_fin="08:30")], 15),
    ]
    perfiles, matches = [], []
    for pid, nombre, clases, traslado in datos:
        perfiles.append(Perfil(
            id=pid, nombre=nombre, edad=21, corregimiento="La Chorrera", salario_minimo=300.0,
            presupuesto_pasaje_diario=2.0, horario_clases=clases,
            habilidades=[HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo clientes")]))
        matches.append(Match(perfil_id=pid, oferta_id=oferta.id, puntaje=90, desglose={
            "cobertura": 1.0, "minutos_traslado": traslado, "pasaje_diario": 1.0, "penalizacion": 0}))
    return oferta, perfiles, matches


def _inicios_ics(outbox):
    """{perfil_id: inicio} de los .ics de entrevista que quedaron en ``outbox``."""
    inicios = {}
    for ruta in outbox.glob("entrevista_*.ics"):
        (evento,) = ics.Calendar(ruta.read_text(encoding="utf-8")).events
        inicios[ruta.stem.removeprefix("entrevista_").split("_")[0]] = evento.begin.datetime.astimezone(PANAMA)
    return inicios


def test_cada_candidato_de_una_oferta_recibe_una_hora_distinta_sin_cruzar_sus_clases(tmp_path):
    oferta, perfiles, matches = _tres_de_una_oferta()
    empresa, joven = Doble(), Doble()
    nodo = crear_nodo_postulacion(ruta_db=tmp_path / "puente.db", outbox=tmp_path / "outbox",
                                  enviar=empresa, enviar_joven=joven, ahora=lambda: AHORA)
    cambios = nodo({
        "aprobacion": {"orientador": "Orientadora Ficticia", "timestamp": "2026-10-19T06:30:00-05:00",
                       "oferta_id": oferta.id,
                       "aprobados": [{"perfil_id": m.perfil_id, "oferta_id": oferta.id} for m in matches]},
        "ofertas": [oferta], "perfiles": perfiles, "matches": matches, "lista_corta": matches,
        "decisiones": [], "errores": [],
    })

    assert cambios["errores"] == []
    inicios = _inicios_ics(tmp_path / "outbox")
    # Ana toma las 08:00 y Bea la siguiente libre para la empresa, 08:45. Cira no puede antes
    # de las 08:45 (clases hasta las 08:30 + 15 de traslado) y esa hora ya es de Bea: 09:30.
    assert inicios == {
        "ana": datetime(2026, 10, 19, 8, 0, tzinfo=PANAMA),
        "bea": datetime(2026, 10, 19, 8, 45, tzinfo=PANAMA),
        "cira": datetime(2026, 10, 19, 9, 30, tzinfo=PANAMA),
    }
    entrevistas = sorted((i, i + timedelta(minutes=45)) for i in inicios.values())
    assert all(fin <= siguiente for (_, fin), (siguiente, _) in pairwise(entrevistas))
    # Con su margen de traslado, la de Cira no toca sus clases.
    assert inicios["cira"] - timedelta(minutes=15) >= datetime(2026, 10, 19, 8, 30, tzinfo=PANAMA)

    (mensaje,) = empresa.mensajes
    for hora in ("08:00", "08:45", "09:30"):
        assert f"Entrevista propuesta: 2026-10-19 {hora}" in mensaje[2]
    assert set(cambios["postulaciones"].values()) == {EstadoPostulacion.ENVIADO}
    assert len(joven.mensajes) == 3


def test_una_persona_sin_ninguna_hora_posible_no_frena_al_resto_de_la_oferta(tmp_path):
    """Yaritza tiene clases a toda hora: queda sin .ics, con su Decision, y Carlos sale igual."""
    todo = Horario(dias=[*SEMANA, "sabado", "domingo"], hora_inicio="00:00", hora_fin="23:59")
    _, estado = cargar_caso()
    entradas = [
        {**e, "horario_clases": [todo.model_dump()]} if e["id"] == "yaritza" else e
        for e in estado["entradas_perfil"]
    ]
    caso = Caso(tmp_path, estado_extra={"entradas_perfil": entradas})
    assert [c["perfil_id"] for c in caso.solicitud["candidatos"]] == ["carlos", "yaritza"]
    caso.aprobar()

    (mensaje,) = caso.empresa.mensajes
    _, _, texto, adjuntos = mensaje
    assert "Carlos Ficticio" in texto and "Yaritza" not in texto
    assert {a.name for a in adjuntos} == {
        f"cv_carlos_{CAJERO}.pdf", f"carta_carlos_{CAJERO}.pdf", f"entrevista_carlos_{CAJERO}.ics"
    }
    assert not list(caso.outbox.glob("*yaritza*"))  # ni .ics, ni CV, ni carta para ella
    valores = caso.valores
    # Yaritza consintió (nodo_aprobacion la deja en consentido, #54) pero no sale: no tiene hora.
    assert valores["postulaciones"] == {
        f"carlos:{CAJERO}": EstadoPostulacion.ENVIADO,
        f"yaritza:{CAJERO}": EstadoPostulacion.CONSENTIDO,
    }
    assert any("yaritza" in e and "sin franja compatible" in e for e in valores["errores"])
    assert not any("carlos" in e for e in valores["errores"])
    (aviso_joven,) = caso.joven.mensajes
    assert "Carlos Ficticio" in aviso_joven[2]

    conn = caso.conexion()
    try:
        assert db.embudo(conn)["enviado"] == 1
        (decision,) = [t for t in db.trazas(conn, agente="postulacion")
                       if t["entrada_resumen"] == f"yaritza -> {CAJERO}"]
        assert decision["salida_resumen"] == "no se envió nada"
        assert "sin franja compatible" in decision["razon"]
    finally:
        conn.close()


def test_perfiles_y_ofertas_que_no_vienen_en_el_estado_salen_de_la_base(tmp_path):
    """El Matching puede traer parejas de puente.db (#46, #48): el nodo completa lo que falte."""
    caso = Caso(tmp_path)
    valores, solicitud = caso.valores, caso.solicitud
    conn = caso.conexion()
    try:
        for p in valores["perfiles"]:
            db.guardar_perfil(conn, p)
        for o in valores["ofertas"]:
            db.guardar_oferta(conn, o)
    finally:
        conn.close()
    nodo = crear_nodo_postulacion(
        ruta_db=caso.ruta_db, outbox=caso.outbox, enviar=caso.empresa, enviar_joven=caso.joven,
        ahora=lambda: AHORA,
    )
    aprobados = [{"perfil_id": c["perfil_id"], "oferta_id": solicitud["oferta_id"]} for c in solicitud["candidatos"]]
    salida = nodo({
        **valores, "perfiles": [], "ofertas": [],
        "aprobacion": {"orientador": "Orientadora Ficticia", "timestamp": AHORA.isoformat(),
                       "aprobados": aprobados, "oferta_id": solicitud["oferta_id"], "mensaje": ""},
    })
    assert len(caso.empresa.mensajes) == 1 and not salida["errores"]
    assert set(salida["postulaciones"].values()) == {EstadoPostulacion("enviado")}


def test_una_pareja_sin_perfil_en_ningun_lado_sale_sola_y_las_demas_siguen(tmp_path):
    caso = Caso(tmp_path)
    valores, solicitud = caso.valores, caso.solicitud
    nodo = crear_nodo_postulacion(
        ruta_db=caso.ruta_db, outbox=caso.outbox, enviar=caso.empresa, enviar_joven=caso.joven,
        ahora=lambda: AHORA,
    )
    aprobados = [{"perfil_id": c["perfil_id"], "oferta_id": solicitud["oferta_id"]} for c in solicitud["candidatos"]]
    sin_carlos = [p for p in valores["perfiles"] if p.id != "carlos"]
    salida = nodo({
        **valores, "perfiles": sin_carlos,
        "aprobacion": {"orientador": "Orientadora Ficticia", "timestamp": AHORA.isoformat(),
                       "aprobados": aprobados, "oferta_id": solicitud["oferta_id"], "mensaje": ""},
    })
    assert len(caso.empresa.mensajes) == 1 and "Carlos" not in caso.empresa.mensajes[0][2]
    assert any("falta el perfil carlos" in e for e in salida["errores"])


def test_ni_el_pdf_ni_el_mensaje_a_la_empresa_filtran_ubicacion_descartes_ni_otras_ofertas(tmp_path):
    """#123: hacia la empresa no salen el corregimiento del joven, «Descartes», el presupuesto ni otras ofertas."""
    from tests.test_cv_ciego import _texto_pdf

    caso = Caso(tmp_path, con_tocumen=True)
    caso.aprobar()
    perfiles = {p.id: p for p in caso.valores["perfiles"]}
    otras = [o for o in caso.valores["ofertas"] if o.id != caso.valores["aprobacion"]["oferta_id"]]
    _, _, texto, adjuntos = caso.empresa.mensajes[0]
    pdfs = [a for a in adjuntos if a.suffix == ".pdf"]
    assert pdfs
    for salida in [texto, *(_texto_pdf(a) for a in pdfs)]:
        for prohibido in ("Descartes", "descart", "presupuesto", "pasaje", MOTIVO_TOCUMEN):
            assert prohibido not in salida
        for p in perfiles.values():
            assert p.corregimiento not in salida
        for o in otras:
            assert o.empresa not in salida and o.puesto not in salida


def _aprobar_directo(nodo, oferta, perfiles, matches, ofertas=None):
    """Llama al nodo con una aprobación del orientador para esos matches (SINTÉTICO)."""
    return nodo({
        "aprobacion": {"orientador": "Orientadora Ficticia", "timestamp": "2026-10-19T06:30:00-05:00",
                       "oferta_id": oferta.id,
                       "aprobados": [{"perfil_id": m.perfil_id, "oferta_id": m.oferta_id} for m in matches]},
        "ofertas": ofertas or [oferta], "perfiles": perfiles, "matches": matches, "lista_corta": matches,
        "decisiones": [], "errores": [],
    })


def _nodo_para(tmp_path):
    return crear_nodo_postulacion(ruta_db=tmp_path / "puente.db", outbox=tmp_path / "outbox",
                                  enviar=Doble(), enviar_joven=Doble(), ahora=lambda: AHORA)


def _inicio_ics(outbox, perfil_id, oferta_id):
    (evento,) = ics.Calendar((outbox / f"entrevista_{perfil_id}_{oferta_id}.ics").read_text(encoding="utf-8")).events
    return evento.begin.datetime.astimezone(PANAMA)


def test_la_misma_oferta_en_dos_aprobaciones_no_repite_la_hora(tmp_path):
    oferta, perfiles, matches = _tres_de_una_oferta()
    nodo = _nodo_para(tmp_path)
    _aprobar_directo(nodo, oferta, perfiles, [matches[0]])  # Ana primero
    _aprobar_directo(nodo, oferta, perfiles, [matches[1]])  # Bea en otra aprobación
    ana = _inicio_ics(tmp_path / "outbox", "ana", oferta.id)
    bea = _inicio_ics(tmp_path / "outbox", "bea", oferta.id)
    assert ana == datetime(2026, 10, 19, 8, 0, tzinfo=PANAMA)
    assert bea >= ana + timedelta(minutes=45)  # la empresa no entrevista a dos a la vez


def test_dos_ofertas_de_la_misma_empresa_no_cruzan_sus_entrevistas(tmp_path):
    oferta, perfiles, matches = _tres_de_una_oferta()
    otra = oferta.model_copy(update={"id": "o-tres-b", "puesto": "Repositor"})  # misma empresa
    m_otra = matches[1].model_copy(update={"oferta_id": otra.id})
    nodo = _nodo_para(tmp_path)
    _aprobar_directo(nodo, oferta, perfiles, [matches[0]])
    _aprobar_directo(nodo, otra, perfiles, [m_otra], ofertas=[oferta, otra])
    ana = _inicio_ics(tmp_path / "outbox", "ana", oferta.id)
    bea = _inicio_ics(tmp_path / "outbox", "bea", otra.id)
    assert bea >= ana + timedelta(minutes=45) or bea + timedelta(minutes=45) <= ana


def test_el_mismo_joven_en_dos_ofertas_no_queda_en_dos_entrevistas_que_se_cruzan(tmp_path):
    oferta, perfiles, matches = _tres_de_una_oferta()
    otra = oferta.model_copy(update={"id": "o-otra-empresa", "empresa": "Otra Empresa Ficticia"})
    bea, bea_otra = matches[1], matches[1].model_copy(update={"oferta_id": otra.id})  # 30 min de traslado
    nodo = _nodo_para(tmp_path)
    _aprobar_directo(nodo, oferta, perfiles, [bea])
    _aprobar_directo(nodo, otra, perfiles, [bea_otra], ofertas=[oferta, otra])
    primera = _inicio_ics(tmp_path / "outbox", "bea", oferta.id)
    segunda = _inicio_ics(tmp_path / "outbox", "bea", otra.id)
    margen, duracion = timedelta(minutes=30), timedelta(minutes=45)
    # Con su traslado de ida y vuelta, las dos ventanas no se tocan.
    assert segunda - margen >= primera + duracion + margen or segunda + duracion + margen <= primera - margen
