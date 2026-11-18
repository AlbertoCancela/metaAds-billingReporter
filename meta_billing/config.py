"""Carga de configuración: portfolios (YAML) y tokens (.env), sin exponer secretos."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import dotenv_values

from .errores import registrar_secreto

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = RAIZ / "config" / "portfolios.yaml"
RUTA_ENV = RAIZ / ".env"
DIR_SALIDAS = RAIZ / "salidas"


class ErrorConfig(Exception):
    pass


@dataclass
class Portfolio:
    clave: str
    nombre: str
    variable_token: str
    cuentas: list[str] = field(default_factory=list)
    recibo: dict = field(default_factory=dict)   # datos opcionales del emisor para el resumen PDF
    # repr=False: el token nunca aparece al imprimir el objeto
    token: str = field(default="", repr=False)


@dataclass
class Config:
    api_version: str
    portfolios: dict[str, Portfolio]


def cargar_config(ruta_config: Path = RUTA_CONFIG, ruta_env: Path = RUTA_ENV) -> Config:
    if not ruta_config.exists():
        raise ErrorConfig(f"No se encontró el archivo de configuración: {ruta_config}")
    if not ruta_env.exists():
        raise ErrorConfig(f"No se encontró el archivo .env en {ruta_env}")

    datos = yaml.safe_load(ruta_config.read_text(encoding="utf-8")) or {}
    # dotenv_values no exporta los tokens a os.environ (no se heredan a subprocesos)
    entorno = {k.strip(): (v or "").strip() for k, v in dotenv_values(ruta_env).items()}

    portfolios: dict[str, Portfolio] = {}
    for clave, p in (datos.get("portfolios") or {}).items():
        variable = p["variable_token"]
        token = entorno.get(variable, "")
        if not token:
            raise ErrorConfig(
                f"El portfolio '{clave}' espera la variable {variable} en el .env, pero no existe o está vacía."
            )
        registrar_secreto(token)
        portfolios[clave] = Portfolio(
            clave=clave,
            nombre=p.get("nombre", clave),
            variable_token=variable,
            cuentas=[c if str(c).startswith("act_") else f"act_{c}" for c in (p.get("cuentas") or [])],
            token=token,
            recibo=p.get("recibo") or {},
        )

    if not portfolios:
        raise ErrorConfig("No hay portfolios definidos en config/portfolios.yaml")

    return Config(api_version=str(datos.get("api_version", "v26.0")), portfolios=portfolios)
