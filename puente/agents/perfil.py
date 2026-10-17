"""Agente Perfil: CV o entrevista corta -> Perfil con habilidades y su evidencia. Issues #6 y #44.

Modelo de lenguaje en el borde, código en el centro:
- El código quita cédula, teléfono y dirección exacta por regex ANTES de armar el prompt;
  del domicilio solo se guarda el corregimiento.
- El LLM propone habilidades (skill_id + frase literal que la respalda) con el esquema
  `PerfilExtraido` del contrato, a través de `puente.llm` (#45), la única puerta al modelo.
- El código descarta toda habilidad cuya evidencia no aparezca literalmente en el texto del
  joven (tras normalizar espacios y mayúsculas) o cuyo skill_id no exista en la taxonomía.
- Edad, corregimiento, horarios, salario mínimo y pasaje vienen de un formulario fijo; si el LLM
  los devuelve en `PerfilExtraido`, se ignoran y quedan anotados en la Decision. El código arma
  el `Perfil` con las habilidades verificadas más los datos del formulario.
- Las fotos de CV se leen con OCR local: la imagen (y la cédula) nunca llega al LLM.
- La entrevista corre fuera del grafo (`entrevistar()`, o el modo terminal
  `uv run python -m puente.agents.perfil --entrevista`) y entra al grafo ya completa como un
  elemento de `entradas_perfil` (`entrada_entrevista()`), sin un `interrupt()` por pregunta.

La taxonomía (#5) y el LLM entran por inyección de dependencias: los tests pasan falsos y
no necesitan red, claves, `puente.llm` ni `puente.rules.taxonomia`.

Grabar y reproducir la entrevista de Yaritza (requiere `puente/llm.py` y una clave para grabar):
    LLM_MODO=grabar uv run python -m puente.agents.perfil --desde tests/fixtures/perfiles_eval.json --id yaritza
    LLM_MODO=reproducir uv run python -m puente.agents.perfil --desde tests/fixtures/perfiles_eval.json --id yaritza
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Any

from pydantic import TypeAdapter, ValidationError

from puente.schemas import (
    DIAS_VALIDOS,
    Decision,
    EstadoPuente,
    HabilidadPerfil,
    Horario,
    Perfil,
    PerfilExtraido,
)

AGENTE = "perfil"
MAX_PREGUNTAS = 5
# Una evidencia de 1 o 2 palabras ("caja", "sí") aparece en casi cualquier texto y no respalda nada.
MIN_CARACTERES_EVIDENCIA = 8

# llm(prompt) -> PerfilExtraido. También se acepta un dict o un texto JSON con esa forma
# (respuestas grabadas, falsos de los tests); el código lo valida contra PerfilExtraido.
LLM = Callable[..., PerfilExtraido | dict[str, Any] | str]
# Campos de PerfilExtraido que manda el formulario fijo: si el LLM los trae, se ignoran.
CAMPOS_DEL_FORMULARIO_EN_EXTRAIDO = (
    "edad", "corregimiento", "ubicacion_texto", "salario_minimo", "horario_libre", "horario_clases",
)
Normalizador = Callable[[str, dict[str, dict]], str | None]

# Solo habilidades: edad, corregimiento, horarios, salario y pasaje van en el formulario fijo.
PREGUNTAS_ENTREVISTA: tuple[str, ...] = (
    (
        "¿Ayudas o has ayudado en algún negocio de tu familia, de un vecino o por tu cuenta? "
        "Cuéntame qué hacías en un día normal."
    ),
    "¿Has tenido otros trabajos, aunque sean por días o sin contrato? ¿Qué tareas hacías?",
    "¿Has hecho voluntariado o actividades en la iglesia, el deporte o tu comunidad? ¿Qué tareas te tocaban?",
    "¿Qué sabes usar o hacer (computadora, celular, cocina, cobrar, idiomas)?",
    "¿En qué te piden ayuda en tu casa, tus amigos o tus vecinos? Cuéntame un ejemplo.",
)

# Campos del formulario fijo, en el orden en que los pide el modo terminal.
CAMPOS_FORMULARIO: tuple[str, ...] = (
    "nombre", "genero", "edad", "corregimiento", "horario_clases", "horario_libre",
    "salario_minimo", "presupuesto_pasaje_diario",
)

# Corregimientos de juguete hasta que #3 publique data/traslados.csv; se pueden inyectar.
CORREGIMIENTOS: tuple[str, ...] = (
    "Barrio Balboa", "Barrio Colón", "Guadalupe", "El Coco", "Herrera", "Hurtado", "Iturralde",
    "Los Díaz", "Playa Leona", "Puerto Caimito", "Santa Rita", "Juan Demóstenes Arosemena",
    "Vista Alegre", "Burunga", "Veracruz", "Nuevo Emperador", "Cerro Silvestre",
    "Bethania", "San Francisco", "Bella Vista", "Calidonia", "Curundú", "Juan Díaz", "Tocumen",
    "Pedregal", "Pueblo Nuevo", "Río Abajo", "Parque Lefevre", "Ancón", "Las Cumbres",
    "Belisario Porras", "Amelia Denis de Icaza", "José Domingo Espinar", "Rufina Alfaro",
    "La Chorrera", "Arraiján", "San Miguelito",
)

# ---------------------------------------------------------------- PII

# Cédula panameña: 8-123-4567, PE-12-345, E-8-123456, N-19-1234, 3AV-12-345, 8-NT-12-345.
_CEDULA = re.compile(
    r"(?<![\w-])(?:\d{1,2}|PE|E|N)(?:-?(?:AV|PI|NT))?\s*-\s*\d{1,4}\s*-\s*\d{1,6}(?![\w-])",
    re.IGNORECASE,
)
# Cédula escrita sin guiones tras su etiqueta: "Cédula: 81234567", "C.I.P. 8 123 4567".
_CEDULA_ETIQUETA = re.compile(
    r"\b(c[ée]dula|c\.\s?i\.(?:\s?p\.)?)(\s*(?:n[°º.o]*|#)?\s*:?\s*)(?=[\w-]*\d)[\w-]+(?:\s\d+)*",
    re.IGNORECASE,
)
# Celular 6xxx-xxxx, fijo 2xx-xxxx, con o sin +507.
_TELEFONO = re.compile(r"(?<![\d-])(?:\+?\s?507[\s.-]?)?(?:6\d{3}|[2-9]\d{2})[\s.-]?\d{4}(?![\d-])")
_DIRECCION_ETIQUETA = re.compile(r"\b(direcci[óo]n|domicilio)\b[^\n]*", re.IGNORECASE)
_DIRECCION_EXACTA = re.compile(
    r"\b(calle|avenida|ave?\.|casa|edificio|edif\.|apartamento|apto\.?|manzana|mz\.|lote)"
    r"\s*(?:n[°º.o]*\s*|#\s*)?\d+[a-z]?\b",
    re.IGNORECASE,
)


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def detectar_corregimiento(texto: str, corregimientos: Iterable[str] = CORREGIMIENTOS) -> str | None:
    """Primer corregimiento de la lista que aparece en el texto (sin acentos ni mayúsculas).

    La lista va de lo más específico a lo más general: "Barrio Colón" gana a "La Chorrera".
    """
    plano = _sin_acentos(texto).casefold()
    for nombre in corregimientos:
        if re.search(rf"\b{re.escape(_sin_acentos(nombre).casefold())}\b", plano):
            return nombre
    return None


def quitar_pii(texto: str, corregimientos: Iterable[str] = CORREGIMIENTOS) -> str:
    """Quita cédula, teléfono y dirección exacta. De una línea de dirección deja solo el corregimiento."""
    corregimientos = tuple(corregimientos)

    def _linea_direccion(m: re.Match) -> str:
        corregimiento = detectar_corregimiento(m.group(0), corregimientos)
        return f"Corregimiento: {corregimiento}" if corregimiento else "[DIRECCION]"

    texto = _DIRECCION_ETIQUETA.sub(_linea_direccion, texto)
    texto = _CEDULA.sub("[CEDULA]", texto)
    texto = _CEDULA_ETIQUETA.sub(lambda m: f"{m.group(1)}{m.group(2)}[CEDULA]", texto)
    texto = _TELEFONO.sub("[TELEFONO]", texto)
    return _DIRECCION_EXACTA.sub("[DIRECCION]", texto)


# ---------------------------------------------------------------- evidencia y taxonomía


def normalizar_texto(texto: str) -> str:
    """Minúsculas y espacios colapsados; es lo único que se perdona al comparar evidencia."""
    return " ".join(texto.casefold().split())


def _limpiar_evidencia(evidencia: str) -> str:
    return evidencia.strip().strip("\"'“”‘’«».,;:… ").strip()


def tiene_evidencia(evidencia: str, fuentes: Iterable[str]) -> bool:
    """True si la frase aparece literalmente en alguna fuente (una respuesta o el CV completo)."""
    frase = normalizar_texto(_limpiar_evidencia(evidencia))
    if len(frase) < MIN_CARACTERES_EVIDENCIA:
        return False
    return any(frase in normalizar_texto(f) for f in fuentes)


def filtrar_habilidades(
    propuestas: Iterable[HabilidadPerfil | dict[str, Any]],
    fuentes: list[str],
    taxonomia: dict[str, dict],
    normalizar: Normalizador,
) -> tuple[list[HabilidadPerfil], list[str]]:
    """Devuelve (habilidades aceptadas, motivos de descarte). Un skill_id solo una vez."""
    aceptadas: list[HabilidadPerfil] = []
    descartes: list[str] = []
    vistos: set[str] = set()
    for propuesta in propuestas:
        if isinstance(propuesta, HabilidadPerfil):
            propuesta = propuesta.model_dump()
        if not isinstance(propuesta, dict):
            descartes.append(f"propuesta no es un objeto: {propuesta!r}")
            continue
        crudo = str(propuesta.get("skill_id") or propuesta.get("nombre") or "").strip()
        evidencia = _limpiar_evidencia(str(propuesta.get("evidencia") or ""))
        skill_id = crudo if crudo in taxonomia else normalizar(crudo, taxonomia)
        if skill_id is None:
            descartes.append(f"{crudo!r}: fuera de la taxonomía")
        elif not tiene_evidencia(evidencia, fuentes):
            descartes.append(f"{skill_id}: sin evidencia literal ({evidencia!r})")
        elif skill_id not in vistos:
            vistos.add(skill_id)
            aceptadas.append(HabilidadPerfil(skill_id=skill_id, evidencia=evidencia))
    return aceptadas, descartes


# ---------------------------------------------------------------- entradas: CV y entrevista


def extraer_texto_cv(ruta: str | Path, ocr: Callable[[Path], str] | None = None) -> str:
    """Texto de un CV en PDF, imagen (OCR local) o texto plano. Nunca pasa el archivo al LLM."""
    ruta = Path(ruta)
    sufijo = ruta.suffix.lower()
    if sufijo == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise RuntimeError("leer CV en PDF requiere `pypdf` (uv add pypdf)") from exc
        return "\n".join(pagina.extract_text() or "" for pagina in PdfReader(ruta).pages)
    if sufijo in {".png", ".jpg", ".jpeg", ".webp", ".heic"}:
        return (ocr or _ocr_local)(ruta)
    return ruta.read_text(encoding="utf-8")


def _ocr_local(ruta: Path) -> str:  # pragma: no cover - depende del entorno
    # OCR local a propósito: mandar la foto al LLM expondría la cédula antes de poder borrarla.
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("leer CV en foto requiere OCR local: `pytesseract`, `pillow` y tesseract") from exc
    return pytesseract.image_to_string(Image.open(ruta), lang="spa")


def entrevistar(responder: Callable[[str], str], preguntas: Iterable[str] = PREGUNTAS_ENTREVISTA) -> list[str]:
    """Entrevista por chat: hace como máximo 5 preguntas y devuelve las respuestas en orden."""
    return [responder(p) for p in list(preguntas)[:MAX_PREGUNTAS]]


# ---------------------------------------------------------------- formulario fijo

_FRANJA = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(?:-|a|hasta)\s*(\d{1,2})(?::(\d{2}))?\s*$")
_RANGO_DIAS = re.compile(r"\s*([a-z]+)\s*(?:-|a|al|hasta)\s*([a-z]+)\s*")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


def _dia(palabra: str) -> str | None:
    return next((d for d in DIAS_VALIDOS if d[:3] == palabra[:3]), None) if len(palabra) >= 3 else None


def _leer_dias(texto: str) -> list[str]:
    plano = _sin_acentos(texto).casefold()
    rango = _RANGO_DIAS.fullmatch(plano)
    if rango and _dia(rango[1]) and _dia(rango[2]):
        i, j = DIAS_VALIDOS.index(_dia(rango[1])), DIAS_VALIDOS.index(_dia(rango[2]))
        if i <= j:
            return list(DIAS_VALIDOS[i : j + 1])
    palabras = [p for p in re.findall(r"[a-z]+", plano) if p not in {"y", "e", "los", "las"}]
    dias = [_dia(p) for p in palabras]
    if not dias or None in dias:
        raise ValueError(f"días no reconocidos: {texto.strip()!r}")
    return list(dict.fromkeys(dias))


def leer_horario(texto: str) -> list[Horario]:
    """'lunes a viernes 18:00-22:00; sábado 8-12' -> lista de Horario. Vacío o 'no' -> []."""
    horarios = []
    for bloque in re.split(r"[;\n]", texto):
        bloque = bloque.strip()
        if not bloque or _sin_acentos(bloque).casefold() in {"no", "ninguno", "ninguna", "-"}:
            continue
        franja = _FRANJA.search(bloque)
        if not franja:
            raise ValueError(f"falta la franja HH:MM-HH:MM en {bloque!r}")
        h1, m1, h2, m2 = franja.groups()
        try:
            horarios.append(
                Horario(
                    dias=_leer_dias(bloque[: franja.start()]),
                    hora_inicio=f"{int(h1):02d}:{m1 or '00'}",
                    hora_fin=f"{int(h2):02d}:{m2 or '00'}",
                )
            )
        except ValidationError as exc:
            raise ValueError(f"horario inválido {bloque!r}: {exc.errors()[0]['msg']}") from exc
    return horarios


def _leer_monto(texto: str) -> float:
    numero = _NUMERO.search(texto)
    if not numero:
        raise ValueError(f"no es un monto: {texto!r}")
    return float(numero[0].replace(",", "."))


def pedir_formulario(
    preguntar: Callable[[str], str],
    avisar: Callable[[str], Any] = print,
    corregimientos: Iterable[str] = CORREGIMIENTOS,
) -> dict[str, Any]:
    """Formulario fijo por terminal: valida cada campo en código y repite la pregunta si no sirve."""
    corregimientos = tuple(corregimientos)
    # Los límites de edad salen del contrato: si `Perfil.edad` cambia en schemas.py (#42), esto lo sigue.
    campo_edad = Perfil.model_fields["edad"]
    edad_valida = TypeAdapter(Annotated[campo_edad.annotation, *campo_edad.metadata])

    def _edad(texto: str) -> int:
        return edad_valida.validate_python(int(texto.strip()))

    def _corregimiento(texto: str) -> str:
        encontrado = detectar_corregimiento(texto, corregimientos)
        if not encontrado:
            raise ValueError(f"corregimiento no reconocido: {texto!r}")
        return encontrado

    campos: list[tuple[str, str, Callable[[str], Any]]] = [
        ("nombre", "Nombre ficticio (Enter para omitir): ", lambda t: t.strip() or None),
        ("genero", "Género (F, M o Enter para no decir): ", lambda t: t.strip().upper()[:1] or None),
        ("edad", "Edad: ", _edad),
        ("corregimiento", "Corregimiento donde vives: ", _corregimiento),
        ("horario_clases", "Horario de clases (ej. lunes a viernes 18:00-22:00; Enter si no estudias): ", leer_horario),
        ("horario_libre", "Horario disponible para trabajar (ej. lunes a sábado 07:00-16:00; Enter si no sabes): ", leer_horario),
        ("salario_minimo", "Salario mínimo que necesitas al mes (B/.): ", _leer_monto),
        ("presupuesto_pasaje_diario", "Cuánto puedes gastar en pasaje al día (B/.): ", _leer_monto),
    ]
    formulario: dict[str, Any] = {}
    for campo, pregunta, leer in campos:
        while True:
            try:
                valor = leer(preguntar(pregunta))
                break
            except ValidationError as exc:
                avisar(f"  No entendí: {exc.errors()[0]['msg']}. Intenta de nuevo.")
            except ValueError as exc:
                avisar(f"  No entendí: {exc}. Intenta de nuevo.")
        if valor is not None:
            formulario[campo] = [h.model_dump() for h in valor] if campo.startswith("horario") else valor
    return formulario


def entrada_entrevista(pid: str, formulario: dict[str, Any], respuestas: list[str]) -> dict[str, Any]:
    """Un elemento de `entradas_perfil` (forma de #39): id, datos del formulario y las respuestas."""
    if len(respuestas) > MAX_PREGUNTAS:
        raise ValueError(f"{pid}: la entrevista admite {MAX_PREGUNTAS} respuestas, llegaron {len(respuestas)}")
    desconocidos = set(formulario) - set(CAMPOS_FORMULARIO)
    if desconocidos:
        raise ValueError(f"{pid}: campos fuera del formulario: {sorted(desconocidos)}")
    return {"id": pid, **formulario, "respuestas": [str(r) for r in respuestas]}


# ---------------------------------------------------------------- métricas para evals


def medir_habilidades(pares: Iterable[tuple[Iterable[str], Iterable[str]]]) -> dict[str, Any]:
    """Precisión y exhaustividad (micro) de pares (obtenidas, esperadas) de skill_id.

    Sin habilidades obtenidas la precisión es 1.0; sin esperadas, la exhaustividad es 1.0.
    """
    aciertos = obtenidas_total = esperadas_total = 0
    falsos_positivos: list[str] = []
    faltantes: list[str] = []
    for obtenidas, esperadas in pares:
        o, e = set(obtenidas), set(esperadas)
        aciertos += len(o & e)
        obtenidas_total += len(o)
        esperadas_total += len(e)
        falsos_positivos += sorted(o - e)
        faltantes += sorted(e - o)
    return {
        "precision": aciertos / obtenidas_total if obtenidas_total else 1.0,
        "exhaustividad": aciertos / esperadas_total if esperadas_total else 1.0,
        "aciertos": aciertos,
        "falsos_positivos": falsos_positivos,
        "faltantes": faltantes,
    }


# ---------------------------------------------------------------- LLM


def llm_puente(prompt: str) -> PerfilExtraido:
    """LLM por defecto: la capa común de #45. Import diferido para que los tests no la necesiten.

    Proveedor, modelo y modo (vivo, grabar, reproducir) se eligen por entorno en `puente.llm`.
    Sus errores llegan como `ErrorLLM` (un RuntimeError) y el nodo los pasa a `errores`.
    """
    try:
        from puente.llm import generar_estructurado
    except ImportError as exc:
        raise RuntimeError("falta puente/llm.py (#45): no hay LLM; usa --reproducir o un llm inyectado") from exc
    return generar_estructurado(prompt, PerfilExtraido, agente=AGENTE)


def construir_prompt(texto: str, modo: str, taxonomia: dict[str, dict]) -> str:
    catalogo = "\n".join(
        f"- {sid}: {d.get('nombre_es', sid)}" + (f" (también: {', '.join(d['sinonimos'])})" if d.get("sinonimos") else "")
        for sid, d in sorted(taxonomia.items())
    )
    fuente = "el CV" if modo == "cv" else "las respuestas de la entrevista"
    return f"""Eres el agente Perfil de Puente, un servicio de empleo juvenil en Panamá.
Lee {fuente} y lista las habilidades laborales de la persona, incluidas las informales
(negocio familiar, voluntariado, oficios). No inventes nada.

Reglas:
- Usa solo skill_id de este catálogo:
{catalogo}
- Para cada habilidad copia en "evidencia" una frase LITERAL, sin cambiar palabras, de {fuente}.
- Si no hay frase que la respalde, no la incluyas.
- Llena solo "habilidades". Deja vacíos o nulos los demás campos (edad, lugar, horarios, dinero):
  esos datos llegan por un formulario aparte.

Texto:
<<<
{texto}
>>>"""


def _como_extraido(salida: PerfilExtraido | dict[str, Any] | str) -> PerfilExtraido:
    """Valida la salida del LLM contra PerfilExtraido. ValueError si no tiene esa forma."""
    if isinstance(salida, PerfilExtraido):
        return salida
    if isinstance(salida, str):
        inicio, fin = salida.find("{"), salida.rfind("}")
        if inicio == -1 or fin <= inicio:
            raise ValueError(f"el LLM no devolvió JSON: {salida[:120]!r}")
        try:
            salida = json.loads(salida[inicio : fin + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"el LLM devolvió JSON inválido: {exc}") from exc
    try:
        return PerfilExtraido.model_validate(salida)
    except ValidationError as exc:
        raise ValueError(f"la salida del LLM no cumple PerfilExtraido: {exc.errors()[0]['msg']}") from exc


def _ignorados_del_llm(extraido: PerfilExtraido) -> list[str]:
    """Campos del formulario que el LLM llenó igual; no se usan, solo se anotan."""
    return [
        f"{campo}={valor!r}"
        for campo in CAMPOS_DEL_FORMULARIO_EN_EXTRAIDO
        if (valor := getattr(extraido, campo)) not in (None, [], "")
    ]


def _horarios(valor: Any, avisos: list[str]) -> list[Horario]:
    if isinstance(valor, dict):
        valor = [valor]
    horarios = []
    for h in valor or []:
        try:
            horarios.append(Horario.model_validate(h))
        except ValidationError:
            avisos.append(f"horario descartado: {h!r}")
    return horarios


# ---------------------------------------------------------------- agente


def construir_perfil(
    entrada: dict[str, Any],
    *,
    taxonomia: dict[str, dict],
    normalizar: Normalizador,
    llm: LLM = llm_puente,
    corregimientos: Iterable[str] = CORREGIMIENTOS,
    ocr: Callable[[Path], str] | None = None,
) -> tuple[Perfil, Decision]:
    """Una entrada -> Perfil verificado y su Decision. Lanza ValueError si no se puede armar.

    `entrada` trae `id` y uno de: `cv_texto`, `cv_ruta` (PDF o foto) o `respuestas`
    (máximo 5, en el orden de PREGUNTAS_ENTREVISTA), más los campos del formulario
    (CAMPOS_FORMULARIO). Del `PerfilExtraido` del LLM solo se toman habilidades; lo demás se ignora.
    Sin corregimiento en el formulario, el código lo busca en el texto del joven, nunca el LLM.
    """
    corregimientos = tuple(corregimientos)
    pid = str(entrada.get("id") or "").strip()
    if not pid:
        raise ValueError("entrada de perfil sin id")

    if entrada.get("cv_texto") or entrada.get("cv_ruta"):
        modo = "cv"
        original = entrada.get("cv_texto") or extraer_texto_cv(entrada["cv_ruta"], ocr)
        limpio = quitar_pii(original, corregimientos)
        fuentes = [limpio]
        texto_llm = limpio
    elif entrada.get("respuestas"):
        modo = "entrevista"
        respuestas = [str(r) for r in entrada["respuestas"]]
        if len(respuestas) > MAX_PREGUNTAS:
            raise ValueError(f"{pid}: la entrevista admite {MAX_PREGUNTAS} respuestas, llegaron {len(respuestas)}")
        original = "\n".join(respuestas)
        fuentes = [quitar_pii(r, corregimientos) for r in respuestas]
        texto_llm = "\n".join(f"P: {p}\nR: {r}" for p, r in zip(PREGUNTAS_ENTREVISTA, fuentes))
    else:
        raise ValueError(f"{pid}: sin CV ni respuestas de entrevista")

    extraido = _como_extraido(llm(construir_prompt(texto_llm, modo, taxonomia)))
    habilidades, descartes = filtrar_habilidades(extraido.habilidades, fuentes, taxonomia, normalizar)

    corregimiento = entrada.get("corregimiento") or detectar_corregimiento(original, corregimientos)
    edad = entrada.get("edad")
    if not corregimiento:
        raise ValueError(f"{pid}: falta el corregimiento en el formulario")
    if edad is None:
        raise ValueError(f"{pid}: falta la edad en el formulario")

    avisos: list[str] = []
    try:
        perfil = Perfil(
            id=pid,
            nombre=entrada.get("nombre") or f"Joven {pid}",
            genero=entrada.get("genero"),
            edad=edad,
            corregimiento=corregimiento,
            horario_libre=_horarios(entrada.get("horario_libre"), avisos),
            horario_clases=_horarios(entrada.get("horario_clases"), avisos),
            salario_minimo=entrada.get("salario_minimo") or 0,
            presupuesto_pasaje_diario=entrada.get("presupuesto_pasaje_diario") or 0,
            habilidades=habilidades,
        )
    except ValidationError as exc:
        raise ValueError(f"{pid}: perfil inválido: {exc.errors()[0]['msg']}") from exc

    razon = f"{len(habilidades)} habilidades con evidencia literal"
    if descartes or avisos:
        razon += "; descartadas: " + "; ".join(descartes + avisos)
    if ignorados := _ignorados_del_llm(extraido):
        razon += "; ignorado del LLM (manda el formulario): " + ", ".join(ignorados)
    decision = Decision(
        agente=AGENTE,
        entrada_resumen=f"{pid} · {modo} · {len(original)} caracteres (PII quitada antes del LLM)",
        salida_resumen=f"{pid} · {corregimiento} · " + (", ".join(h.skill_id for h in habilidades) or "sin habilidades"),
        razon=razon,
    )
    return perfil, decision


def nodo_perfil(
    estado: EstadoPuente,
    *,
    llm: LLM = llm_puente,
    taxonomia: dict[str, dict] | None = None,
    normalizar: Normalizador | None = None,
    corregimientos: Iterable[str] = CORREGIMIENTOS,
    ocr: Callable[[Path], str] | None = None,
) -> dict:
    """Nodo del grafo. Lee `estado["entradas_perfil"]` y devuelve perfiles, decisiones y errores completos.

    Un perfil con el mismo id se reemplaza. Un error en una entrada no detiene las demás.
    """
    entradas = estado.get("entradas_perfil") or []  # type: ignore[typeddict-item]
    perfiles = list(estado.get("perfiles") or [])
    decisiones = list(estado.get("decisiones") or [])
    errores = list(estado.get("errores") or [])
    if not entradas:
        return {}

    try:
        if taxonomia is None:
            from puente.rules.taxonomia import cargar_taxonomia

            taxonomia = cargar_taxonomia()
        if normalizar is None:
            from puente.rules.taxonomia import normalizar_habilidad

            normalizar = normalizar_habilidad
    except (ImportError, OSError) as exc:
        return {"errores": errores + [f"perfil: taxonomía no disponible ({exc})"]}

    for entrada in entradas:
        try:
            perfil, decision = construir_perfil(
                entrada, taxonomia=taxonomia, normalizar=normalizar, llm=llm, corregimientos=corregimientos, ocr=ocr
            )
        except (ValueError, RuntimeError, OSError) as exc:
            errores.append(f"perfil: {exc}")
            continue
        perfiles = [p for p in perfiles if p.id != perfil.id] + [perfil]
        decisiones.append(decision)

    return {"perfiles": perfiles, "decisiones": decisiones, "errores": errores}


# ---------------------------------------------------------------- modo terminal (demo H12)


def _caso_de_archivo(ruta: str | Path, pid: str) -> dict[str, Any]:
    """El JSON tal cual o, si es un archivo de casos (`casos`), el caso con `entrada.id == pid`."""
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    if "casos" not in datos:
        return datos
    caso = next((c for c in datos["casos"] if c.get("entrada", {}).get("id") == pid), None)
    if caso is None:
        raise ValueError(f"{ruta}: no hay un caso con id {pid!r}")
    return caso


def _respuesta_guardada(ruta: str | Path, pid: str) -> PerfilExtraido:
    """Respuesta del LLM guardada en JSON: el objeto de respuesta, un caso con `respuesta_llm`
    o un archivo de casos del que se toma el de `pid`."""
    datos = _caso_de_archivo(ruta, pid)
    return _como_extraido(datos.get("respuesta_llm", datos))


def _cargar_taxonomia_cli(ruta: str | None, avisar: Callable[[str], Any]) -> tuple[dict[str, dict], Normalizador]:
    from puente.rules.taxonomia import (
        RUTA_TAXONOMIA,
        cargar_taxonomia,
        normalizar_habilidad,
    )

    elegida = Path(ruta) if ruta else RUTA_TAXONOMIA
    if not ruta and not elegida.exists():
        # data/taxonomia.csv llega con #3; mientras tanto la demo usa la mini del repo.
        elegida = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "taxonomia_mini.csv"
        avisar(f"(sin {RUTA_TAXONOMIA}: uso {elegida.name})")
    return cargar_taxonomia(elegida), normalizar_habilidad


def main(
    argv: list[str] | None = None,
    *,
    preguntar: Callable[[str], str] = input,
    mostrar: Callable[[str], Any] = print,
    llm: LLM | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m puente.agents.perfil",
        description=__doc__.splitlines()[0],
        epilog="Sin --reproducir el LLM va por puente.llm: LLM_MODO=vivo|grabar|reproducir elige el modo.",
    )
    parser.add_argument("--entrevista", action="store_true", help="formulario fijo + 5 preguntas por terminal")
    parser.add_argument("--id", default="joven", help="id del perfil (por defecto: joven)")
    parser.add_argument(
        "--desde", help="JSON de una entrada guardada con --guardar, o archivo de casos (toma el de --id): no pregunta nada"
    )
    parser.add_argument("--guardar", help="escribe la entrada (elemento de entradas_perfil) en este JSON")
    parser.add_argument("--reproducir", help="JSON con la respuesta guardada del LLM; sin red, claves ni puente.llm")
    parser.add_argument("--taxonomia", help="CSV de la taxonomía (por defecto data/taxonomia.csv)")
    args = parser.parse_args(argv)
    if not (args.entrevista or args.desde):
        parser.error("usa --entrevista o --desde RUTA")

    if args.desde:
        datos = _caso_de_archivo(args.desde, args.id)
        entrada = datos.get("entrada", datos)
    else:
        mostrar("Formulario")
        formulario = pedir_formulario(preguntar, mostrar)
        mostrar("\nEntrevista (responde con tus palabras)")
        respuestas = entrevistar(lambda p: preguntar(f"\n{p}\n> "))
        entrada = entrada_entrevista(args.id, formulario, respuestas)
    if args.guardar:
        Path(args.guardar).write_text(json.dumps(entrada, ensure_ascii=False, indent=2), encoding="utf-8")
        mostrar(f"Entrada guardada en {args.guardar}")

    if llm is None and args.reproducir:
        grabada = _respuesta_guardada(args.reproducir, entrada["id"])

        def llm(_prompt: str) -> PerfilExtraido:
            return grabada
    elif llm is None:
        llm = llm_puente
    taxonomia, normalizar = _cargar_taxonomia_cli(args.taxonomia, mostrar)
    try:
        perfil, decision = construir_perfil(entrada, taxonomia=taxonomia, normalizar=normalizar, llm=llm)
    except (ValueError, RuntimeError, OSError) as exc:  # ErrorLLM es un RuntimeError
        mostrar(f"Error: {exc}")
        return 1
    mostrar("\nHabilidades con evidencia:")
    for h in perfil.habilidades:
        mostrar(f"  - {h.skill_id}: «{h.evidencia}»")
    mostrar(f"\nDecisión: {decision.razon}")
    mostrar("\nPerfil:\n" + perfil.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
