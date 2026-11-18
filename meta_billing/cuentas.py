"""Clientes por portfolio y cuentas publicitarias (solo lectura)."""
from __future__ import annotations

import unicodedata

from .cliente import MetaCliente
from .config import Config

MONEDAS_SIN_DECIMALES = {"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"}


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def crear_clientes(config: Config, portfolios: list[str] | None = None) -> list[MetaCliente]:
    claves = portfolios or list(config.portfolios)
    desconocidos = [c for c in claves if c not in config.portfolios]
    if desconocidos:
        raise ValueError(f"Portfolio desconocido: {', '.join(desconocidos)}. Opciones: {', '.join(config.portfolios)}")
    return [MetaCliente(config.portfolios[c], config.api_version) for c in claves]


def listar_cuentas(cliente: MetaCliente) -> list[dict]:
    campos = ["id", "account_id", "name", "account_status", "currency", "timezone_name", "business{name}"]
    cuentas = cliente.todos("me/adaccounts", {"fields": campos, "limit": 100})
    permitidas = cliente.portfolio.cuentas
    if permitidas:
        cuentas = [c for c in cuentas if c["id"] in permitidas]
    return cuentas


def resolver_cuentas(cliente: MetaCliente, cuenta: str | None) -> list[dict]:
    cuentas = listar_cuentas(cliente)
    if not cuenta:
        return cuentas
    objetivo = cuenta if cuenta.startswith("act_") else f"act_{cuenta}"
    por_id = [c for c in cuentas if c["id"] == objetivo]
    if por_id:
        return por_id
    texto = sin_acentos(cuenta)
    return [c for c in cuentas if texto in sin_acentos(c.get("name", ""))]
