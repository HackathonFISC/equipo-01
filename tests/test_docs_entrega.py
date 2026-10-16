"""Pruebas estáticas de la documentación de entrega (issue #15), sin red ni claves.

Criterios del issue:
- Alguien de afuera instala y corre la demo solo con el README: el README tiene las
  secciones, cada comando y ruta que cita existe, cada variable de .env.example está
  documentada, no hay secretos y el diagrama es un PNG válido.
- La bitácora cubre cada parte hecha con IA.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
README = RAIZ / "README.md"
METODOLOGIA = RAIZ / "docs" / "metodologia.md"
BITACORA = RAIZ / "docs" / "bitacora_ia.md"
DIAGRAMA = RAIZ / "docs" / "arquitectura.png"
ENV_EJEMPLO = RAIZ / ".env.example"

ENCABEZADOS_README = (
    "## Problema",
    "## Cobertura del reto",
    "## Arquitectura de agentes",
    "## Tecnologías",
    "## Fuentes de datos",
    "## Instalación y ejecución",
    "## Credenciales de demo",
    "## Evals",
    "## Bitácora de IA",
)

# Variables que el código lee y .env.example no trae (modelo del LLM y checkpoints del grafo),
# más la que activa la comprobación estricta de la bitácora.
VARIABLES_EXTRA = ("PUENTE_MODELO_LLM", "PUENTE_CHECKPOINTS", "PUENTE_CHEQUEO_ENTREGA")

# Agentes mínimos que sugiere el reto (sección 5.4), en su orden.
AGENTES_RETO = (
    "Inteligencia de mercado",
    "Perfil",
    "Matching",
    "Ruta de formación",
    "Postulación",
    "Entrevista simulada",
    "Auditor",
)
ESTADOS_COBERTURA = ("Completo", "Parcial", "Fuera de alcance")

COMPONENTES_BITACORA = (
    "Esquemas y contratos",
    "Grafo",
    "Mercado",
    "Perfil",
    "Matching/solver",
    "Auditor",
    "Formación",
    "Postulación/herramientas",
    "Guardrails",
    "Tablero",
    "Evals",
    "Datos",
)

PATRONES_SECRETOS = (
    r"AIza[0-9A-Za-z_-]{20,}",
    r"lsv2_",
    r"ghp_",
    r"\d{8,10}:[A-Za-z0-9_-]{30,}",
)

EXTENSIONES_RUTA = {
    ".md", ".py", ".png", ".csv", ".yaml", ".yml", ".json", ".toml", ".txt",
    ".example", ".sh", ".lock", ".pdf", ".html", ".js", ".css",
}

PENDIENTE = re.compile(r"pendiente de #\d+", re.IGNORECASE)


def _leer(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


def _sin_bloques_de_codigo(texto: str) -> str:
    return re.sub(r"```.*?```", "", texto, flags=re.DOTALL)


def _seccion(texto: str, encabezado: str) -> str:
    """Texto de una sección `## ...` hasta el siguiente `## ` (sin contar bloques de código)."""
    lineas = texto.splitlines()
    dentro = False
    en_bloque = False
    salida: list[str] = []
    for linea in lineas:
        if linea.startswith("```"):
            en_bloque = not en_bloque
        if not en_bloque and linea.startswith("## "):
            if dentro:
                break
            dentro = linea.startswith(encabezado)
        if dentro:
            salida.append(linea)
    return "\n".join(salida)


def _filas_tabla(texto: str) -> list[list[str]]:
    filas = []
    for linea in texto.splitlines():
        if linea.startswith("|"):
            # Un `\|` escapado es texto de la celda, como en GitHub; un `|` suelto parte la fila.
            celdas = [c.strip() for c in re.split(r"(?<!\\)\|", linea.strip().strip("|"))]
            filas.append(celdas)
    return filas


def _es_separador(celdas: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", c) for c in celdas)


def _rutas_citadas(texto: str) -> list[tuple[str, str]]:
    """Pares (ruta, línea) de enlaces markdown relativos y rutas en backticks."""
    citadas: list[tuple[str, str]] = []
    for linea in _sin_bloques_de_codigo(texto).splitlines():
        for destino in re.findall(r"\]\(([^)\s]+)\)", linea):
            if re.match(r"[a-z]+://", destino) or destino.startswith(("#", "mailto:")):
                continue
            citadas.append((destino.split("#")[0], linea))
        for token in re.findall(r"`([^`]+)`", linea):
            if not re.fullmatch(r"[\w./-]+", token) or token.endswith("/") or "..." in token:
                continue  # comandos, variables, carpetas (algunas se crean al correr) y nombres con marcadores
            if "/" in token or Path(token).suffix in EXTENSIONES_RUTA:
                citadas.append((token, linea))
    return citadas


def _comandos_python_m(texto: str) -> list[str]:
    modulos = []
    for bloque in re.findall(r"```bash\n(.*?)```", texto, flags=re.DOTALL):
        for linea in bloque.splitlines():
            m = re.match(r"\s*uv run python -m ([\w.]+)", linea)
            if m:
                modulos.append(m.group(1))
    return modulos


def test_readme_es_seguible():
    texto = _leer(README)

    # 1. Secciones del contrato y la línea del reto.
    for encabezado in ENCABEZADOS_README:
        assert re.search(rf"^{re.escape(encabezado)}", texto, flags=re.MULTILINE), (
            f"Falta la sección '{encabezado}' en README.md"
        )
    assert re.search(r"^Reto: 3$", texto, flags=re.MULTILINE), "README.md debe conservar la línea 'Reto: 3'"
    for obligatoria in ("docs/arquitectura.png", "docs/metodologia.md", "docs/bitacora_ia.md", ".env.example"):
        assert f"({obligatoria})" in texto, f"README.md debe enlazar {obligatoria}"

    # 2. Cada `uv run python -m X` apunta a un módulo que existe.
    modulos = _comandos_python_m(texto)
    assert modulos, "README.md debe traer comandos 'uv run python -m ...' en bloques bash"
    for modulo in modulos:
        assert importlib.util.find_spec(modulo) is not None, f"El README manda a correr un módulo que no existe: {modulo}"
    assert {"app.server", "puente.graph", "evals.run"} <= set(modulos)

    # 3. Toda ruta citada existe, salvo las rotuladas 'pendiente de #N' en su línea.
    inexistentes = sorted(
        {
            ruta
            for ruta, linea in _rutas_citadas(texto)
            if not (RAIZ / ruta).exists() and not PENDIENTE.search(linea)
        }
    )
    assert not inexistentes, f"README.md cita rutas que no existen y no están rotuladas 'pendiente de #N': {inexistentes}"

    # 4. Cada variable de .env.example (y las extra) está documentada.
    variables = re.findall(r"^([A-Z][A-Z0-9_]*)=", _leer(ENV_EJEMPLO), flags=re.MULTILINE)
    assert variables, ".env.example no trae variables"
    faltan = [v for v in (*variables, *VARIABLES_EXTRA) if v not in texto]
    assert not faltan, f"README.md no documenta estas variables: {faltan}"

    # 5. Sin secretos.
    for archivo in (README, METODOLOGIA):
        contenido = _leer(archivo)
        for patron in PATRONES_SECRETOS:
            assert not re.search(patron, contenido), f"{archivo.name} parece contener un secreto ({patron})"

    # 6. El diagrama es un PNG válido y legible (ancho suficiente).
    from PIL import Image

    with Image.open(DIAGRAMA) as imagen:
        assert imagen.format == "PNG"
        assert imagen.width >= 1200, f"docs/arquitectura.png mide {imagen.width} px de ancho; se piden al menos 1200"

    # 7. La metodología tiene sus secciones, con las decisiones de diseño de la revisión del plan.
    metodologia = _leer(METODOLOGIA)
    for encabezado in ("## Despliegue", "## Funcionamiento", "## Arquitectura", "## Decisiones de diseño"):
        assert re.search(rf"^{re.escape(encabezado)}\s*$", metodologia, flags=re.MULTILINE), (
            f"Falta '{encabezado}' en docs/metodologia.md"
        )


def test_readme_cita_cifras_con_fuente():
    texto = _leer(README)
    problema = _seccion(texto, "## Problema")
    for fragmento in (
        "inec.gob.pa",
        "panama.justia.com",
        "tvn-2.com",
        "prensa.com",
    ):
        assert fragmento in problema, f"La sección Problema debe enlazar la fuente {fragmento}"
    # Cifras verificadas en la fuente, tal cual (las del INEC, en su comentario de septiembre de 2025).
    for cifra in ("10.4 %", "9.7 %", "24.3 %", "20,378", "47.1 %", "1,663", "Gaceta Oficial 25287"):
        assert cifra in problema, f"Falta la cifra verificada {cifra}"


def test_readme_cubre_los_agentes_del_reto():
    tabla = [f for f in _filas_tabla(_seccion(_leer(README), "## Cobertura del reto")) if not _es_separador(f)]
    assert tabla and tabla[0][:4] == ["Agente sugerido (5.4)", "En Puente", "Issue", "Estado"], (
        "La tabla de cobertura debe tener las columnas Agente sugerido (5.4) | En Puente | Issue | Estado"
    )
    # La sección trae también la tabla de requisitos técnicos, de 3 columnas.
    agentes = [f for f in tabla[1:] if len(f) == len(tabla[0]) and re.match(r"\d\. ", f[0])]
    assert len(agentes) == len(AGENTES_RETO), f"La tabla de cobertura debe listar los {len(AGENTES_RETO)} agentes del reto"
    for n, (fila, nombre) in enumerate(zip(agentes, AGENTES_RETO), start=1):
        assert fila[0].startswith(f"{n}. {nombre}"), f"Fila {n} de la cobertura: se esperaba '{nombre}', dice '{fila[0]}'"
        assert fila[3] in ESTADOS_COBERTURA, f"Estado de '{nombre}' fuera de {ESTADOS_COBERTURA}: '{fila[3]}'"
        if fila[3] != "Fuera de alcance":
            assert fila[2].startswith("#"), f"'{nombre}' debe indicar su issue"


def _filas_bitacora() -> list[list[str]]:
    filas = []
    for celdas in _filas_tabla(_leer(BITACORA)):
        if _es_separador(celdas) or celdas[0].startswith("Hora"):
            continue
        filas.append(celdas)
    return filas


def _modulos_con_codigo() -> list[tuple[Path, set[int]]]:
    """Módulos de puente/, app/ y evals/ con código además del docstring y los issues que citan."""
    resultado = []
    for carpeta in ("puente", "app", "evals"):
        for ruta in sorted((RAIZ / carpeta).rglob("*.py")):
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            doc = ast.get_docstring(arbol) or ""
            cuerpo = arbol.body
            if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(getattr(cuerpo[0], "value", None), ast.Constant):
                cuerpo = cuerpo[1:]
            if not cuerpo:
                continue  # solo docstring: stub
            issues = {int(n) for n in re.findall(r"#(\d+)", doc)}
            if issues:
                resultado.append((ruta, issues))
    return resultado


def test_bitacora_cubre_partes_con_ia():
    # 1. Formato de la bitácora: 5 columnas, herramienta y qué se pidió no vacíos.
    filas = _filas_bitacora()
    assert filas, "docs/bitacora_ia.md no tiene filas"
    for celdas in filas:
        assert len(celdas) == 5, f"Fila de bitácora con {len(celdas)} columnas en vez de 5: {celdas}"
        _hora, _quien, herramienta, pedido, _mano = celdas
        assert herramienta, f"Fila de bitácora sin herramienta: {celdas}"
        assert pedido, f"Fila de bitácora sin 'qué se pidió': {celdas}"

    # 2. Cada módulo con código cita un issue que tiene fila (solo en el ensayo de entrega,
    #    para no romper main en PRs ajenos).
    if os.environ.get("PUENTE_CHEQUEO_ENTREGA") == "1":
        con_fila = {int(m.group(1)) for celdas in filas if (m := re.match(r"#(\d+) ·", celdas[3]))}
        sin_cobertura = [
            f"{ruta.relative_to(RAIZ).as_posix()} (cita {sorted(issues)})"
            for ruta, issues in _modulos_con_codigo()
            if not (issues & con_fila)
        ]
        assert not sin_cobertura, f"Módulos con código sin ninguna fila de su issue en la bitácora: {sin_cobertura}"

    # 3. El README resume la bitácora por componente y enlaza el registro completo.
    seccion = _seccion(_leer(README), "## Bitácora de IA")
    assert "(docs/bitacora_ia.md)" in seccion, "La sección 'Bitácora de IA' debe enlazar docs/bitacora_ia.md"
    tabla = [f for f in _filas_tabla(seccion) if not _es_separador(f)]
    assert tabla and tabla[0][:4] == ["Componente", "Issue", "Herramienta", "Qué se cambió a mano"], (
        "La tabla de la bitácora del README debe tener las columnas Componente | Issue | Herramienta | Qué se cambió a mano"
    )
    por_componente = {f[0]: f for f in tabla[1:] if len(f) == 4}
    for componente in COMPONENTES_BITACORA:
        assert componente in por_componente, f"Falta la fila del componente '{componente}' en el resumen de la bitácora"
        fila = por_componente[componente]
        assert fila[1].startswith("#"), f"El componente '{componente}' debe indicar su issue"
        assert fila[2], f"El componente '{componente}' no indica la herramienta"
        assert fila[3], f"El componente '{componente}' no indica qué se cambió a mano"
