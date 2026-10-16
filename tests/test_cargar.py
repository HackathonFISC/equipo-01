"""Carga inicial en puente.db (#46): ofertas por Mercado con LLM falso, perfiles, idempotencia y lecturas de db."""

import json
from pathlib import Path

import pytest

from puente import cargar
from puente.rules.taxonomia import cargar_taxonomia
from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil
from puente.tools import db

FIXTURES = Path("tests/fixtures")
CASOS = json.loads((FIXTURES / "ofertas_mini.json").read_text(encoding="utf-8"))
TAXONOMIA = cargar_taxonomia(FIXTURES / "taxonomia_mini.csv")


class ExtractorFalso:
    """Devuelve la extracción del fixture cuyo texto abre la entrada; cuenta las llamadas.

    `falla_con` simula un timeout del LLM en ese archivo; `cambios` pisa campos de la extracción.
    """

    def __init__(self, falla_con: str | None = None, cambios: dict[str, dict] | None = None):
        self.llamadas = 0
        self.falla_con = falla_con
        self.cambios = cambios or {}

    def __call__(self, entrada):
        self.llamadas += 1
        if entrada.fuente == self.falla_con:
            raise TimeoutError("el LLM no respondió")
        for caso in CASOS:
            if entrada.texto.startswith(caso["texto"]):
                return {**caso["extraida"], **self.cambios.get(entrada.fuente, {})}
        raise ValueError("texto no reconocido por el extractor falso")


def _perfil(id_: str, edad: int = 20) -> dict:
    return {
        "id": id_, "nombre": f"Joven Ficticia {id_}", "edad": edad, "corregimiento": "La Chorrera",
        "horario_libre": [{"dias": ["lunes", "martes"], "hora_inicio": "08:00", "hora_fin": "17:00"}],
        "salario_minimo": 300, "presupuesto_pasaje_diario": 2.5,
        "habilidades": [{"skill_id": "atencion_cliente", "evidencia": "atiendo la fonda de mi mamá"}],
    }


@pytest.fixture(autouse=True)
def _sin_modo_grabar(monkeypatch):
    """Con LLM_MODO=grabar la carga relee todo; las pruebas no deben depender del entorno."""
    monkeypatch.delenv("LLM_MODO", raising=False)


@pytest.fixture
def carpetas(tmp_path):
    ofertas = tmp_path / "ofertas"
    perfiles = tmp_path / "perfiles"
    ofertas.mkdir()
    perfiles.mkdir()
    for caso in CASOS:
        (ofertas / f"{caso['nombre']}.txt").write_text(caso["texto"], encoding="utf-8")
    (ofertas / ".gitkeep").write_text("", encoding="utf-8")
    (perfiles / "yaritza.json").write_text(json.dumps(_perfil("yaritza")), encoding="utf-8")
    (perfiles / "otros.json").write_text(json.dumps([_perfil("p2"), _perfil("p3")]), encoding="utf-8")
    return ofertas, perfiles


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "puente.db")
    yield c
    c.close()


def _cargar(conn, carpetas, extractor, taxonomia=TAXONOMIA):
    ofertas, perfiles = carpetas
    return (
        cargar.cargar_ofertas(conn, ofertas, extraer=extractor, taxonomia=taxonomia),
        cargar.cargar_perfiles(conn, perfiles),
    )


def _conteos(conn) -> tuple[int, int, int, int]:
    contar = lambda tabla: conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: E731
    return contar("ofertas"), contar("perfiles"), contar("decisiones"), contar("cargas")


def _puestos(conn) -> list[str]:
    return sorted(o.puesto for o in db.listar_ofertas(conn, no_bloqueadas=False))


def test_carga_ofertas_con_bloqueadas_marcadas_y_duplicado_fusionado(conn, carpetas):
    resumen, perfiles = _cargar(conn, carpetas, ExtractorFalso())

    todas = db.listar_ofertas(conn, no_bloqueadas=False)
    assert resumen.llamadas_llm == 4 and resumen.desde_cargas == 0 and not resumen.errores
    assert _puestos(conn) == ["Ayudante de cocina", "Cajera", "Vendedor"]  # la cocina duplicada se fusiona
    bloqueadas = [o for o in todas if o.bloqueada]
    assert [o.puesto for o in bloqueadas] == ["Cajera"] and bloqueadas[0].alertas
    assert all(o.fuente.endswith(".txt") for o in todas)
    assert {o.id for o in db.listar_ofertas(conn)} == {o.id for o in todas if not o.bloqueada}
    assert perfiles.perfiles_nuevos == 3 and not perfiles.errores
    assert {f[0] for f in conn.execute("SELECT agente FROM decisiones")} == {"mercado", "carga"}


def test_segunda_corrida_deja_los_mismos_conteos_y_no_llama_al_llm(conn, carpetas):
    _cargar(conn, carpetas, ExtractorFalso())
    antes = _conteos(conn)

    extractor = ExtractorFalso()
    resumen, perfiles = _cargar(conn, carpetas, extractor)

    assert extractor.llamadas == 0 and resumen.llamadas_llm == 0 and resumen.desde_cargas == 4
    assert perfiles.perfiles_nuevos == 0
    assert _conteos(conn) == antes


def test_extraccion_fallida_se_reintenta_en_la_siguiente_corrida(conn, carpetas):
    resumen, _ = _cargar(conn, carpetas, ExtractorFalso(falla_con="vendedor_sin_salario.txt"))
    assert any("vendedor_sin_salario.txt" in e for e in resumen.errores)
    assert "Vendedor" not in _puestos(conn)

    extractor = ExtractorFalso()
    resumen, _ = _cargar(conn, carpetas, extractor)
    assert extractor.llamadas == 1 and resumen.desde_cargas == 3 and not resumen.errores
    assert "Vendedor" in _puestos(conn)


def test_reintento_de_un_duplicado_se_compara_con_todo_el_conjunto(conn, carpetas):
    ofertas, _ = carpetas
    cocina = next(c["texto"] for c in CASOS if c["nombre"] == "ayudante_cocina")
    (ofertas / "zz_cocina_otro_portal.txt").write_text(cocina + " Se requiere puntualidad.", encoding="utf-8")

    _cargar(conn, carpetas, ExtractorFalso(falla_con="zz_cocina_otro_portal.txt"))
    _cargar(conn, carpetas, ExtractorFalso())

    assert _puestos(conn).count("Ayudante de cocina") == 1


def test_archivo_cambiado_reemplaza_su_oferta_sin_duplicarla(conn, carpetas):
    ofertas, _ = carpetas
    _cargar(conn, carpetas, ExtractorFalso())
    vieja = next(o.id for o in db.listar_ofertas(conn) if o.puesto == "Vendedor")
    archivo = ofertas / "vendedor_sin_salario.txt"
    archivo.write_text(archivo.read_text(encoding="utf-8") + " Turno rotativo.", encoding="utf-8")

    extractor = ExtractorFalso()
    resumen, _ = _cargar(conn, carpetas, extractor)

    assert extractor.llamadas == 1 and resumen.desde_cargas == 3
    assert _puestos(conn) == ["Ayudante de cocina", "Cajera", "Vendedor"]
    assert vieja not in {o.id for o in db.listar_ofertas(conn)}


def test_archivo_borrado_saca_su_oferta(conn, carpetas):
    ofertas, _ = carpetas
    _cargar(conn, carpetas, ExtractorFalso())
    (ofertas / "vendedor_sin_salario.txt").unlink()

    _cargar(conn, carpetas, ExtractorFalso())
    assert _puestos(conn) == ["Ayudante de cocina", "Cajera"]

    caso = next(c for c in CASOS if c["nombre"] == "vendedor_sin_salario")
    (ofertas / "vendedor_sin_salario.txt").write_text(caso["texto"], encoding="utf-8")
    extractor = ExtractorFalso()
    _cargar(conn, carpetas, extractor)
    assert extractor.llamadas == 0 and "Vendedor" in _puestos(conn)  # su extracción seguía en cargas


def test_oferta_obsoleta_con_postulaciones_no_se_borra_y_se_reporta(conn, carpetas):
    ofertas, _ = carpetas
    _cargar(conn, carpetas, ExtractorFalso())
    vieja = next(o.id for o in db.listar_ofertas(conn) if o.puesto == "Vendedor")
    db.crear_postulacion(conn, "yaritza", vieja, estado=db.ENVIADO)
    (ofertas / "vendedor_sin_salario.txt").unlink()

    for _ in range(2):  # se reporta en cada corrida, no solo la primera
        resumen, _ = _cargar(conn, carpetas, ExtractorFalso())
        assert any(vieja in e and "postulaciones" in e for e in resumen.errores)
    assert vieja in {o.id for o in db.listar_ofertas(conn)}


def test_taxonomia_que_llega_despues_se_aplica_sin_llamar_al_llm(conn, carpetas):
    _cargar(conn, carpetas, ExtractorFalso(), taxonomia={})
    assert all(not o.habilidades for o in db.listar_ofertas(conn, no_bloqueadas=False))

    extractor = ExtractorFalso()
    _cargar(conn, carpetas, extractor)

    assert extractor.llamadas == 0
    assert any(o.habilidades for o in db.listar_ofertas(conn, no_bloqueadas=False))


def test_oferta_invalida_tras_extraer_se_reporta_en_cada_corrida(conn, carpetas):
    hora_mal = {"cajera_discriminatoria.txt": {"horario": {"dias": ["lunes"], "hora_inicio": "8:00", "hora_fin": "16:00"}}}
    primera, _ = _cargar(conn, carpetas, ExtractorFalso(cambios=hora_mal))
    segunda, _ = _cargar(conn, carpetas, ExtractorFalso())

    for resumen in (primera, segunda):
        assert any("cajera_discriminatoria.txt" in e for e in resumen.errores)
    assert "Cajera" not in _puestos(conn)


def test_cambiar_un_archivo_no_repite_las_decisiones_de_los_demas(conn, carpetas):
    ofertas, _ = carpetas
    _cargar(conn, carpetas, ExtractorFalso())
    antes = conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0]
    archivo = ofertas / "vendedor_sin_salario.txt"
    archivo.write_text(archivo.read_text(encoding="utf-8") + " Turno rotativo.", encoding="utf-8")

    _cargar(conn, carpetas, ExtractorFalso())

    assert conn.execute("SELECT COUNT(*) FROM decisiones").fetchone()[0] == antes + 1
    repetidas = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM decisiones GROUP BY agente, entrada_resumen, salida_resumen, razon "
        "HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert repetidas == 0


def test_carpeta_vacia_no_borra_lo_ya_cargado(conn, carpetas, tmp_path):
    _cargar(conn, carpetas, ExtractorFalso())
    antes = _conteos(conn)
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    (vacia / ".gitkeep").write_text("", encoding="utf-8")

    resumen = cargar.cargar_ofertas(conn, vacia, extraer=ExtractorFalso(), taxonomia=TAXONOMIA)

    assert resumen.errores and "no se borra nada" in resumen.errores[0]
    assert _conteos(conn) == antes


def test_releer_vuelve_a_pedir_todo_al_llm_y_lo_activa_el_modo_grabar(conn, carpetas, monkeypatch):
    ofertas, _ = carpetas
    _cargar(conn, carpetas, ExtractorFalso())

    extractor = ExtractorFalso()
    cargar.cargar_ofertas(conn, ofertas, extraer=extractor, taxonomia=TAXONOMIA, releer=True)
    assert extractor.llamadas == 4

    monkeypatch.setenv("LLM_MODO", "grabar")
    extractor = ExtractorFalso()
    cargar.cargar_ofertas(conn, ofertas, extraer=extractor, taxonomia=TAXONOMIA)
    assert extractor.llamadas == 4 and _puestos(conn) == ["Ayudante de cocina", "Cajera", "Vendedor"]


def test_perfil_invalido_se_reporta_y_no_detiene_la_carga(conn, carpetas):
    _, perfiles = carpetas
    (perfiles / "menor.json").write_text(json.dumps(_perfil("menor", edad=17)), encoding="utf-8")
    (perfiles / "roto.json").write_text("{no es json", encoding="utf-8")

    resumen = cargar.cargar_perfiles(conn, perfiles)

    assert resumen.perfiles_nuevos == 3
    assert len(resumen.errores) == 2
    assert "menor.json" in resumen.errores[0] and "roto.json" in resumen.errores[1]
    assert [p.id for p in db.listar_perfiles(conn)] == ["p2", "p3", "yaritza"]


def test_id_de_perfil_repetido_se_reporta_y_no_alterna(conn, carpetas):
    _, perfiles = carpetas
    (perfiles / "z_repetido.json").write_text(json.dumps(_perfil("yaritza", edad=25)), encoding="utf-8")

    primera = cargar.cargar_perfiles(conn, perfiles)
    segunda = cargar.cargar_perfiles(conn, perfiles)

    assert any("yaritza" in e and "z_repetido.json" in e for e in primera.errores)
    assert segunda.perfiles_nuevos == 0
    assert next(p.edad for p in db.listar_perfiles(conn) if p.id == "yaritza") == 20


def test_carpeta_inexistente_es_error_y_no_excepcion(conn, tmp_path):
    resumen = cargar.cargar_ofertas(conn, tmp_path / "no_existe", extraer=ExtractorFalso())
    assert resumen.errores and resumen.llamadas_llm == 0
    assert cargar.cargar_perfiles(conn, tmp_path / "no_existe").errores


def test_main_imprime_conteos_y_devuelve_1_si_hay_errores(carpetas, tmp_path, monkeypatch, capsys):
    ofertas, perfiles = carpetas
    monkeypatch.setattr(cargar, "_extractor_por_defecto", lambda: ExtractorFalso())
    monkeypatch.setenv("PUENTE_TAXONOMIA", str(FIXTURES / "taxonomia_mini.csv"))
    argumentos = ["--ofertas", str(ofertas), "--perfiles", str(perfiles), "--db", str(tmp_path / "p.db")]

    assert cargar.main(argumentos) == 0
    salida = capsys.readouterr().out.splitlines()
    assert "en la base: 3 (1 bloqueadas)" in salida[0] and "en la base: 3" in salida[1]

    (perfiles / "menor.json").write_text(json.dumps(_perfil("menor", edad=17)), encoding="utf-8")
    assert cargar.main(argumentos) == 1
    assert "menor.json" in capsys.readouterr().out


# ---------------------------------------------------------------- lecturas de db (#46)


def _oferta(id_: str, bloqueada: bool = False) -> Oferta:
    return Oferta(
        id=id_, puesto="Cajera", empresa="Super Ficticio", corregimiento="Arraiján", salario_mensual=420.0,
        horario=Horario(dias=["lunes"], hora_inicio="08:00", hora_fin="16:00"), bloqueada=bloqueada,
    )


def test_listar_perfiles_y_ofertas_ordenados_y_sin_bloqueadas(conn):
    for id_ in ("p2", "p1"):
        db.guardar_perfil(conn, Perfil(
            id=id_, nombre="Ficticia", edad=22, corregimiento="Tocumen", salario_minimo=300,
            presupuesto_pasaje_diario=2, habilidades=[HabilidadPerfil(skill_id="ventas", evidencia="vendo")],
        ))
    db.guardar_oferta(conn, _oferta("o2"))
    db.guardar_oferta(conn, _oferta("o1", bloqueada=True))

    assert [p.id for p in db.listar_perfiles(conn)] == ["p1", "p2"]
    assert [o.id for o in db.listar_ofertas(conn)] == ["o2"]
    assert [o.id for o in db.listar_ofertas(conn, no_bloqueadas=False)] == ["o1", "o2"]
    assert db.listar_perfiles(conn)[0].habilidades[0].evidencia == "vendo"


def test_parejas_fijas_son_las_enviadas_o_posteriores(conn):
    estados = {
        "o1": db.PRESELECCIONADO, "o2": db.APROBADO, "o3": db.ENVIADO,
        "o4": db.ENTREVISTA, "o5": db.CONTRATADO, "o6": db.NO_SELECCIONADO,
    }
    db.guardar_perfil(conn, Perfil(id="p1", nombre="Ficticia", edad=20, corregimiento="La Chorrera",
                                   salario_minimo=300, presupuesto_pasaje_diario=2))
    for oferta_id, estado in estados.items():
        db.guardar_oferta(conn, _oferta(oferta_id))
        db.crear_postulacion(conn, "p1", oferta_id, estado=estado)

    assert db.parejas_fijas(conn) == [("p1", "o3"), ("p1", "o4"), ("p1", "o5")]
