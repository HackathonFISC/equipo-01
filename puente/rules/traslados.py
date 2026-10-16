"""Matriz de traslados (minutos y pasaje) entre corregimientos desde data/traslados.csv. Issue #4."""

from __future__ import annotations

import csv
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

RUTA_TRASLADOS_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "traslados.csv"

CORREGIMIENTOS_PANAMA: tuple[str, ...] = (
    "La Chorrera",
    "Arraiján",
    "Vista Alegre",
    "Juan Díaz",
    "Tocumen",
    "San Miguelito",
    "Bella Vista",
    "Calidonia",
    "Betania",
    "San Francisco",
    "Pedregal",
    "24 de Diciembre",
    "Ancón",
)

# Tabla de alias para resolver barrios, terminales o zonas especiales hacia su nodo canónico en traslados.csv (#51)
TABLA_ALIAS_LUGARES: dict[str, str] = {
    "barrio colon": "la chorrera",
    "barrio balboa": "la chorrera",
    "el coco": "la chorrera",
    "guadalupe": "la chorrera",
    "herrera": "la chorrera",
    "puerto caimito": "la chorrera",
    "juan demostenes arosemena": "vista alegre",
    "vacamonte": "vista alegre",
    "cerro silvestre": "vista alegre",
    "panama pacifico": "arraijan",
    "howard": "arraijan",
    "burunga": "arraijan",
    "veracruz": "arraijan",
    "nuevo emperador": "arraijan",
    "albrook": "ancon",
    "terminal de albrook": "ancon",
    "curundu": "calidonia",
    "santa ana": "calidonia",
    "el chorrillo": "calidonia",
    "pueblo nuevo": "betania",
    "bethania": "betania",
    "parque lefevre": "juan diaz",
    "rio abajo": "juan diaz",
    "costa del este": "juan diaz",
    "rufina alfaro": "san miguelito",
    "belisario porras": "san miguelito",
    "amelia denis de icaza": "san miguelito",
    "jose domingo espinar": "san miguelito",
    "las cumbres": "san miguelito",
    "mananitas": "tocumen",
}


def normalizar_corregimiento(nombre: str) -> str:
    """Normaliza el nombre de un corregimiento (minúsculas, sin tildes ni espacios dobles)."""
    if not nombre:
        return ""
    texto = nombre.strip().lower()
    texto = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return " ".join(texto.split())


def resolver_alias_corregimiento(nombre: str) -> str:
    """Normaliza y aplica la tabla de alias hacia el nodo canónico de traslados.csv."""
    norm = normalizar_corregimiento(nombre)
    return TABLA_ALIAS_LUGARES.get(norm, norm)


class InfoTraslado(BaseModel):
    """Datos determinísticos de traslado en transporte público entre dos corregimientos."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    origen: str = Field(..., description="Corregimiento de origen")
    destino: str = Field(..., description="Corregimiento de destino")
    minutos_hora_pico: int = Field(
        ..., ge=0, alias="minutos", description="Tiempo de traslado en minutos a hora pico"
    )
    pasaje_ida_usd: float = Field(
        ..., ge=0.0, alias="pasaje_ida", description="Costo del pasaje de ida en USD"
    )
    modo: str = Field(
        default="Transporte público", description="Modo de transporte público (Metro, Metrobús, etc.)"
    )
    fuente: str = Field(
        default="Google Maps Transit + Tarifas MiBus / Metro de Panamá / ATTT",
        description="Fuente de medición del tiempo y tarifa",
    )
    fecha_medicion: str = Field(
        default="2026-10-16",
        description="Fecha de consulta (YYYY-MM-DD)",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalizar_campos_entrada(cls, data: Any) -> Any:
        if isinstance(data, dict):
            copia = dict(data)
            if "minutos_hora_pico" not in copia and "minutos" in copia:
                copia["minutos_hora_pico"] = int(copia["minutos"])
            if "pasaje_ida_usd" not in copia and "pasaje_ida" in copia:
                copia["pasaje_ida_usd"] = float(copia["pasaje_ida"])
            if "pasaje_ida_usd" not in copia and "pasaje_diario" in copia:
                copia["pasaje_ida_usd"] = round(float(copia["pasaje_diario"]) / 2.0, 4)
            return copia
        return data

    @property
    def minutos(self) -> int:
        """Alias conveniente para minutos_hora_pico."""
        return self.minutos_hora_pico

    @property
    def pasaje_diario_usd(self) -> float:
        """Costo diario ida y vuelta (2 × pasaje_ida_usd)."""
        return round(2.0 * self.pasaje_ida_usd, 2)

    def pasaje_mensual_usd(self, dias_laborables: int = 22) -> float:
        """Costo mensual ida y vuelta por días laborables."""
        return round(2.0 * self.pasaje_ida_usd * dias_laborables, 2)


# Coordenadas relativas de referencia en el eje Oeste -> Centro -> Este para respaldo determinístico
# cuando data/traslados.csv aún no existe o falta un par específico.
_PERFIL_ZONA: dict[str, tuple[str, int, float]] = {
    normalizar_corregimiento("La Chorrera"): ("oeste_lejos", 0, 1.50),
    normalizar_corregimiento("Vista Alegre"): ("oeste_medio", 12, 1.25),
    normalizar_corregimiento("Arraiján"): ("oeste_cerca", 22, 1.00),
    normalizar_corregimiento("Ancón"): ("centro_oeste", 38, 0.25),
    normalizar_corregimiento("Calidonia"): ("centro", 42, 0.35),
    normalizar_corregimiento("Bella Vista"): ("centro", 45, 0.60),
    normalizar_corregimiento("Betania"): ("centro", 47, 0.60),
    normalizar_corregimiento("San Francisco"): ("centro", 50, 0.60),
    normalizar_corregimiento("San Miguelito"): ("norte_centro", 55, 0.60),
    normalizar_corregimiento("Juan Díaz"): ("este_medio", 62, 0.60),
    normalizar_corregimiento("Pedregal"): ("este_medio", 70, 0.85),
    normalizar_corregimiento("Tocumen"): ("este_lejos", 82, 0.85),
    normalizar_corregimiento("24 de Diciembre"): ("este_lejos", 88, 0.85),
}

# Pares explícitos clave calibrados a hora pico en Panamá (incluye caso Yaritza: La Chorrera <-> Tocumen = 95 min)
_PARES_EXPLICITOS: dict[tuple[str, str], tuple[int, float, str]] = {
    ("la chorrera", "la chorrera"): (15, 0.50, "Ruta interna La Chorrera"),
    ("la chorrera", "vista alegre"): (20, 0.75, "Chiva / bus interurbano Oeste"),
    ("la chorrera", "arraijan"): (30, 1.00, "Bus La Chorrera - Arraiján"),
    ("la chorrera", "ancon"): (50, 1.50, "Bus Expreso La Chorrera - Albrook"),
    ("la chorrera", "calidonia"): (55, 1.85, "Bus La Chorrera + Metro L1"),
    ("la chorrera", "bella vista"): (58, 1.85, "Bus La Chorrera + Metro L1"),
    ("la chorrera", "betania"): (60, 1.85, "Bus La Chorrera + Metro L1"),
    ("la chorrera", "san francisco"): (65, 2.10, "Bus La Chorrera + Metro L1 + Metrobús"),
    ("la chorrera", "san miguelito"): (70, 1.85, "Bus La Chorrera + Metro L1"),
    ("la chorrera", "juan diaz"): (80, 2.35, "Bus La Chorrera + Metro L1/L2"),
    ("la chorrera", "pedregal"): (88, 2.35, "Bus La Chorrera + Metro L1/L2"),
    ("la chorrera", "tocumen"): (95, 2.35, "Bus La Chorrera + Metro L1/L2"),
    ("la chorrera", "24 de diciembre"): (100, 2.35, "Bus La Chorrera + Metro L1/L2"),
    ("vista alegre", "vista alegre"): (12, 0.50, "Ruta interna Vista Alegre"),
    ("vista alegre", "arraijan"): (18, 0.75, "Chiva / bus Oeste"),
    ("vista alegre", "ancon"): (42, 1.25, "Bus Vista Alegre - Albrook"),
    ("vista alegre", "calidonia"): (48, 1.60, "Bus Vista Alegre + Metro L1"),
    ("vista alegre", "bella vista"): (50, 1.60, "Bus Vista Alegre + Metro L1"),
    ("vista alegre", "betania"): (52, 1.60, "Bus Vista Alegre + Metro L1"),
    ("vista alegre", "san francisco"): (56, 1.85, "Bus Vista Alegre + Metro L1 + Metrobús"),
    ("vista alegre", "san miguelito"): (62, 1.60, "Bus Vista Alegre + Metro L1"),
    ("vista alegre", "juan diaz"): (72, 2.10, "Bus Vista Alegre + Metro L1/L2"),
    ("vista alegre", "pedregal"): (78, 2.10, "Bus Vista Alegre + Metro L1/L2"),
    ("vista alegre", "tocumen"): (85, 2.10, "Bus Vista Alegre + Metro L1/L2"),
    ("vista alegre", "24 de diciembre"): (90, 2.10, "Bus Vista Alegre + Metro L1/L2"),
    ("arraijan", "arraijan"): (12, 0.50, "Ruta interna Arraiján"),
    ("arraijan", "ancon"): (32, 1.00, "Bus Arraiján - Albrook"),
    ("arraijan", "calidonia"): (38, 1.35, "Bus Arraiján + Metro L1"),
    ("arraijan", "bella vista"): (42, 1.35, "Bus Arraiján + Metro L1"),
    ("arraijan", "betania"): (45, 1.35, "Bus Arraiján + Metro L1"),
    ("arraijan", "san francisco"): (48, 1.60, "Bus Arraiján + Metro L1 + Metrobús"),
    ("arraijan", "san miguelito"): (52, 1.35, "Bus Arraiján + Metro L1"),
    ("arraijan", "juan diaz"): (62, 1.85, "Bus Arraiján + Metro L1/L2"),
    ("arraijan", "pedregal"): (68, 1.85, "Bus Arraiján + Metro L1/L2"),
    ("arraijan", "tocumen"): (75, 1.85, "Bus Arraiján + Metro L1/L2"),
    ("arraijan", "24 de diciembre"): (80, 1.85, "Bus Arraiján + Metro L1/L2"),
}


def construir_matriz_default() -> dict[tuple[str, str], InfoTraslado]:
    """Construye la matriz completa de corregimientos de Panamá como fixture explícito de respaldo."""
    matriz: dict[tuple[str, str], InfoTraslado] = {}
    for orig in CORREGIMIENTOS_PANAMA:
        for dest in CORREGIMIENTOS_PANAMA:
            ko = normalizar_corregimiento(orig)
            kd = normalizar_corregimiento(dest)
            if ko == kd:
                min_pico, pasaje_ida, modo = _PARES_EXPLICITOS.get(
                    (ko, kd), (15, 0.50, f"Metrobús / ruta interna {orig}")
                )
            elif (ko, kd) in _PARES_EXPLICITOS:
                min_pico, pasaje_ida, modo = _PARES_EXPLICITOS[(ko, kd)]
            elif (kd, ko) in _PARES_EXPLICITOS:
                min_pico, pasaje_ida, modo = _PARES_EXPLICITOS[(kd, ko)]
            else:
                zona_o, pos_o, base_o = _PERFIL_ZONA[ko]
                zona_d, pos_d, base_d = _PERFIL_ZONA[kd]
                dist = abs(pos_o - pos_d)
                min_pico = max(18, min(95, 15 + dist))
                if "oeste" in zona_o or "oeste" in zona_d:
                    pasaje_ida = round(base_o + base_d, 2)
                    modo = "Bus Oeste + Metro L1/L2"
                elif dist <= 12:
                    pasaje_ida = 0.60
                    modo = "Metrobús / Metro L1"
                else:
                    pasaje_ida = 0.85
                    modo = "Metro L1 + L2 / Metrobús"
            matriz[(ko, kd)] = InfoTraslado(
                origen=orig,
                destino=dest,
                minutos_hora_pico=min_pico,
                pasaje_ida_usd=pasaje_ida,
                modo=modo,
                fuente="fixture_prueba",
                fecha_medicion="2026-10-16",
            )
    return matriz


def cargar_traslados(
    ruta: str | Path | None = None,
) -> dict[tuple[str, str], InfoTraslado]:
    """Carga data/traslados.csv en un diccionario indexado por (origen_norm, destino_norm).

    En producción exige que el archivo CSV exista; si falta lanza FileNotFoundError (#48).
    Para pruebas sin archivo, usar el fixture explícito `construir_matriz_default()`.
    """
    archivo = Path(ruta) if ruta is not None else RUTA_TRASLADOS_DEFAULT
    if not archivo.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo de traslados en '{archivo}'. "
            "Verifique data/traslados.csv o pase el fixture explícito a asignar()."
        )

    matriz: dict[tuple[str, str], InfoTraslado] = {}
    with archivo.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for fila in reader:
            if not fila or not fila.get("origen") or not fila.get("destino"):
                continue
            info = InfoTraslado(
                origen=fila["origen"].strip(),
                destino=fila["destino"].strip(),
                minutos_hora_pico=int(float(fila.get("minutos_hora_pico") or fila.get("minutos") or 0)),
                pasaje_ida_usd=float(fila.get("pasaje_ida_usd") or fila.get("pasaje_ida") or 0.0),
                modo=(fila.get("modo") or "Transporte público").strip(),
                fuente=(fila.get("fuente") or "Google Maps Transit + Tarifas MiBus / Metro de Panamá / ATTT").strip(),
                fecha_medicion=(fila.get("fecha_medicion") or "2026-10-16").strip(),
            )
            ko = normalizar_corregimiento(info.origen)
            kd = normalizar_corregimiento(info.destino)
            matriz[(ko, kd)] = info
            if (kd, ko) not in matriz:
                matriz[(kd, ko)] = InfoTraslado(
                    origen=info.destino,
                    destino=info.origen,
                    minutos_hora_pico=info.minutos_hora_pico,
                    pasaje_ida_usd=info.pasaje_ida_usd,
                    modo=info.modo,
                    fuente=info.fuente,
                    fecha_medicion=info.fecha_medicion,
                )
    return matriz


def indexar_traslados(
    traslados: Any,
) -> dict[tuple[str, str], InfoTraslado]:
    """Convierte cualquier representación de traslados (None, ruta, lista o dict) al índice canónico."""
    if traslados is None or isinstance(traslados, (str, Path)):
        return cargar_traslados(traslados)

    indice: dict[tuple[str, str], InfoTraslado] = {}

    if isinstance(traslados, dict):
        for clave, valor in traslados.items():
            if isinstance(clave, tuple) and len(clave) == 2:
                ko = normalizar_corregimiento(str(clave[0]))
                kd = normalizar_corregimiento(str(clave[1]))
                if isinstance(valor, InfoTraslado):
                    info = valor
                elif isinstance(valor, dict):
                    info = InfoTraslado(
                        origen=str(valor.get("origen", clave[0])),
                        destino=str(valor.get("destino", clave[1])),
                        **{k: v for k, v in valor.items() if k not in ("origen", "destino")},
                    )
                elif isinstance(valor, (tuple, list)) and len(valor) >= 2:
                    info = InfoTraslado(
                        origen=str(clave[0]),
                        destino=str(clave[1]),
                        minutos_hora_pico=int(valor[0]),
                        pasaje_ida_usd=float(valor[1]),
                        modo=str(valor[2]) if len(valor) > 2 else "Transporte público",
                    )
                else:
                    continue
                indice[(ko, kd)] = info
                if (kd, ko) not in indice:
                    indice[(kd, ko)] = InfoTraslado(
                        origen=info.destino,
                        destino=info.origen,
                        minutos_hora_pico=info.minutos_hora_pico,
                        pasaje_ida_usd=info.pasaje_ida_usd,
                        modo=info.modo,
                        fuente=info.fuente,
                        fecha_medicion=info.fecha_medicion,
                    )
            elif isinstance(clave, str) and isinstance(valor, dict):
                ko = normalizar_corregimiento(clave)
                for dest_str, subval in valor.items():
                    kd = normalizar_corregimiento(dest_str)
                    if isinstance(subval, InfoTraslado):
                        info = subval
                    elif isinstance(subval, dict):
                        info = InfoTraslado(
                            origen=clave,
                            destino=dest_str,
                            **{k: v for k, v in subval.items() if k not in ("origen", "destino")},
                        )
                    else:
                        continue
                    indice[(ko, kd)] = info
                    if (kd, ko) not in indice:
                        indice[(kd, ko)] = InfoTraslado(
                            origen=info.destino,
                            destino=info.origen,
                            minutos_hora_pico=info.minutos_hora_pico,
                            pasaje_ida_usd=info.pasaje_ida_usd,
                            modo=info.modo,
                            fuente=info.fuente,
                            fecha_medicion=info.fecha_medicion,
                        )
        return indice

    if isinstance(traslados, (list, tuple, set)):
        for item in traslados:
            if isinstance(item, InfoTraslado):
                info = item
            elif isinstance(item, dict):
                info = InfoTraslado(**item)
            else:
                info = InfoTraslado(
                    origen=getattr(item, "origen"),
                    destino=getattr(item, "destino"),
                    minutos_hora_pico=getattr(
                        item, "minutos_hora_pico", getattr(item, "minutos", 0)
                    ),
                    pasaje_ida_usd=getattr(
                        item, "pasaje_ida_usd", getattr(item, "pasaje_ida", 0.0)
                    ),
                    modo=getattr(item, "modo", "Transporte público"),
                )
            ko = normalizar_corregimiento(info.origen)
            kd = normalizar_corregimiento(info.destino)
            indice[(ko, kd)] = info
            if (kd, ko) not in indice:
                indice[(kd, ko)] = InfoTraslado(
                    origen=info.destino,
                    destino=info.origen,
                    minutos_hora_pico=info.minutos_hora_pico,
                    pasaje_ida_usd=info.pasaje_ida_usd,
                    modo=info.modo,
                    fuente=info.fuente,
                    fecha_medicion=info.fecha_medicion,
                )
        return indice

    return construir_matriz_default()


def obtener_traslado(
    traslados: Any,
    origen: str,
    destino: str,
) -> InfoTraslado | None:
    """Obtiene la información de traslado entre origen y destino (resolviendo alias como Albrook o Panamá Pacífico)."""
    indice = (
        traslados
        if isinstance(traslados, dict)
        and all(isinstance(k, tuple) and isinstance(v, InfoTraslado) for k, v in traslados.items())
        and all(k[0] == normalizar_corregimiento(k[0]) for k in traslados.keys())
        else indexar_traslados(traslados)
    )
    ko = normalizar_corregimiento(origen)
    kd = normalizar_corregimiento(destino)
    if (ko, kd) in indice:
        return indice[(ko, kd)]
    if (kd, ko) in indice:
        inv = indice[(kd, ko)]
        return InfoTraslado(
            origen=origen,
            destino=destino,
            minutos_hora_pico=inv.minutos_hora_pico,
            pasaje_ida_usd=inv.pasaje_ida_usd,
            modo=inv.modo,
            fuente=inv.fuente,
            fecha_medicion=inv.fecha_medicion,
        )

    # Resolver alias hacia nodos canónicos de traslados.csv (ej. Albrook -> Ancón, Panamá Pacífico -> Arraiján)
    ko_alias = resolver_alias_corregimiento(origen)
    kd_alias = resolver_alias_corregimiento(destino)
    if (ko_alias, kd_alias) in indice:
        aliased = indice[(ko_alias, kd_alias)]
        return InfoTraslado(
            origen=origen,
            destino=destino,
            minutos_hora_pico=aliased.minutos_hora_pico,
            pasaje_ida_usd=aliased.pasaje_ida_usd,
            modo=aliased.modo,
            fuente=aliased.fuente,
            fecha_medicion=aliased.fecha_medicion,
        )
    if (kd_alias, ko_alias) in indice:
        inv_a = indice[(kd_alias, ko_alias)]
        return InfoTraslado(
            origen=origen,
            destino=destino,
            minutos_hora_pico=inv_a.minutos_hora_pico,
            pasaje_ida_usd=inv_a.pasaje_ida_usd,
            modo=inv_a.modo,
            fuente=inv_a.fuente,
            fecha_medicion=inv_a.fecha_medicion,
        )

    nodos_conocidos = {k[0] for k in indice} | {k[1] for k in indice}
    if ko and ko == kd and (ko in nodos_conocidos or ko_alias in nodos_conocidos):
        return InfoTraslado(
            origen=origen,
            destino=destino,
            minutos_hora_pico=15,
            pasaje_ida_usd=0.50,
            modo="Ruta interna",
            fuente="estimado",
        )
    return None


def calcular_pasaje_diario(traslado: InfoTraslado | dict[str, Any]) -> float:
    """Calcula el pasaje diario de ida y vuelta en USD."""
    info = traslado if isinstance(traslado, InfoTraslado) else InfoTraslado(**traslado)
    return info.pasaje_diario_usd


def calcular_pasaje_mensual(
    traslado: InfoTraslado | dict[str, Any],
    dias_laborables: int = 22,
) -> float:
    """Calcula el pasaje mensual (ida y vuelta × días laborables) en USD."""
    info = traslado if isinstance(traslado, InfoTraslado) else InfoTraslado(**traslado)
    return info.pasaje_mensual_usd(dias_laborables=dias_laborables)
