"""Periodos: presets en español, rangos personalizados, incrementos y periodos de comparación."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Alias en español → date_preset de Meta (verificado en la referencia de Ad Account Insights)
PRESETS = {
    "hoy": "today",
    "ayer": "yesterday",
    "3d": "last_3d",
    "7d": "last_7d",
    "14d": "last_14d",
    "28d": "last_28d",
    "30d": "last_30d",
    "90d": "last_90d",
    "esta-semana": "this_week_mon_today",
    "semana-pasada": "last_week_mon_sun",
    "este-mes": "this_month",
    "mes-pasado": "last_month",
    "este-trimestre": "this_quarter",
    "trimestre-pasado": "last_quarter",
    "este-anio": "this_year",
    "anio-pasado": "last_year",
    "maximo": "maximum",
}

INCREMENTOS = {"total": "all_days", "dia": 1, "semana": 7, "mes": "monthly"}
ZONA_POR_DEFECTO = "America/Mexico_City"


class ErrorPeriodo(ValueError):
    pass


def hoy_en(zona: str = ZONA_POR_DEFECTO) -> date:
    return datetime.now(ZoneInfo(zona)).date()


def _sumar_meses(d: date, meses: int) -> date:
    total = d.year * 12 + (d.month - 1) + meses
    anio, mes = divmod(total, 12)
    mes += 1
    return date(anio, mes, min(d.day, calendar.monthrange(anio, mes)[1]))


def _inicio_trimestre(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def rango_preset(alias: str, hoy: date) -> tuple[date, date]:
    """Fechas concretas (inclusive) equivalentes a un preset, igual que los calcula Meta."""
    ultimos = {"3d": 3, "7d": 7, "14d": 14, "28d": 28, "30d": 30, "90d": 90}
    if alias in ultimos:                       # Meta: los últimos N días completos, sin hoy
        return hoy - timedelta(days=ultimos[alias]), hoy - timedelta(days=1)
    lunes = hoy - timedelta(days=hoy.weekday())
    ini_mes = hoy.replace(day=1)
    ini_trim = _inicio_trimestre(hoy)
    rangos = {
        "hoy": (hoy, hoy),
        "ayer": (hoy - timedelta(days=1), hoy - timedelta(days=1)),
        "esta-semana": (lunes, hoy),
        "semana-pasada": (lunes - timedelta(days=7), lunes - timedelta(days=1)),
        "este-mes": (ini_mes, hoy),
        "mes-pasado": (_sumar_meses(ini_mes, -1), ini_mes - timedelta(days=1)),
        "este-trimestre": (ini_trim, hoy),
        "trimestre-pasado": (_sumar_meses(ini_trim, -3), ini_trim - timedelta(days=1)),
        "este-anio": (date(hoy.year, 1, 1), hoy),
        "anio-pasado": (date(hoy.year - 1, 1, 1), date(hoy.year - 1, 12, 31)),
    }
    if alias not in rangos:
        raise ErrorPeriodo(f"El periodo '{alias}' no se puede convertir a fechas concretas aquí. "
                           "Usa otro preset o --desde/--hasta.")
    return rangos[alias]


@dataclass
class Periodo:
    preset: str | None = None          # alias en español
    desde: date | None = None
    hasta: date | None = None
    cada: str = "total"                # total | dia | semana | mes

    def validar(self) -> None:
        if self.desde or self.hasta:
            if not (self.desde and self.hasta):
                raise ErrorPeriodo("Para un rango personalizado indica --desde y --hasta.")
            if self.desde > self.hasta:
                raise ErrorPeriodo("--desde no puede ser posterior a --hasta.")
        elif (self.preset or "7d") not in PRESETS:
            raise ErrorPeriodo(f"Periodo '{self.preset}' no reconocido. Opciones: {', '.join(PRESETS)}")
        if self.cada not in INCREMENTOS:
            raise ErrorPeriodo(f"Incremento '{self.cada}' no válido. Opciones: {', '.join(INCREMENTOS)}")

    def params(self) -> dict:
        self.validar()
        p: dict = {}
        if self.desde:
            p["time_range"] = {"since": self.desde.isoformat(), "until": self.hasta.isoformat()}
        else:
            p["date_preset"] = PRESETS[self.preset or "7d"]
        p["time_increment"] = INCREMENTOS[self.cada]
        return p

    def concretar(self, hoy: date) -> "Periodo":
        """Devuelve el mismo periodo expresado con fechas concretas."""
        self.validar()
        if self.desde:
            return self
        desde, hasta = rango_preset(self.preset or "7d", hoy)
        return Periodo(preset=self.preset, desde=desde, hasta=hasta, cada=self.cada)

    @property
    def dias(self) -> int:
        return (self.hasta - self.desde).days + 1 if self.desde else 0

    def descripcion(self) -> str:
        if self.desde and self.preset:
            base = f"{self.preset} ({self.desde} a {self.hasta})"
        elif self.desde:
            base = f"{self.desde} a {self.hasta}"
        else:
            base = f"preset {self.preset or '7d'}"
        return base + ("" if self.cada == "total" else f", por {self.cada}")


# Modos de comparación para --contra
CONTRAS = ["anterior", "anio-anterior"]


def periodo_comparacion(actual: Periodo, contra: str | None, hoy: date,
                        contra_desde: date | None = None, contra_hasta: date | None = None) -> Periodo:
    """Calcula el periodo contra el que se compara.

    - anterior: periodo inmediato anterior de la misma duración. Para periodos "hasta hoy"
      (esta-semana, este-mes, este-trimestre, este-anio) usa los mismos días del periodo previo,
      para que la comparación sea justa (p. ej. lunes–miércoles contra lunes–miércoles).
    - anio-anterior: mismas fechas un año antes.
    - un preset (p. ej. semana-pasada) o --contra-desde/--contra-hasta.
    """
    a = actual.concretar(hoy)
    if contra_desde or contra_hasta:
        p = Periodo(desde=contra_desde, hasta=contra_hasta)
        p.validar()
        return p
    contra = contra or "anterior"
    if contra == "anterior":
        if a.preset == "esta-semana":
            return Periodo(desde=a.desde - timedelta(days=7), hasta=a.hasta - timedelta(days=7))
        meses = {"este-mes": 1, "este-trimestre": 3, "este-anio": 12}.get(a.preset or "")
        if meses:
            return Periodo(desde=_sumar_meses(a.desde, -meses), hasta=_sumar_meses(a.hasta, -meses))
        if a.preset in ("mes-pasado",):
            ini = _sumar_meses(a.desde, -1)
            return Periodo(desde=ini, hasta=a.desde - timedelta(days=1))
        if a.preset in ("trimestre-pasado",):
            return Periodo(desde=_sumar_meses(a.desde, -3), hasta=a.desde - timedelta(days=1))
        if a.preset in ("anio-pasado",):
            return Periodo(desde=_sumar_meses(a.desde, -12), hasta=_sumar_meses(a.hasta, -12))
        hasta = a.desde - timedelta(days=1)
        return Periodo(desde=hasta - timedelta(days=a.dias - 1), hasta=hasta)
    if contra == "anio-anterior":
        return Periodo(desde=_sumar_meses(a.desde, -12), hasta=_sumar_meses(a.hasta, -12))
    if contra in PRESETS:
        return Periodo(preset=contra).concretar(hoy)
    raise ErrorPeriodo(f"--contra '{contra}' no válido. Usa: {', '.join(CONTRAS)} o un preset ({', '.join(PRESETS)}).")


def parsear_fecha(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ErrorPeriodo(f"Fecha inválida '{texto}'. Usa el formato AAAA-MM-DD.") from None
