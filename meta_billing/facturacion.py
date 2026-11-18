"""Facturación (solo lectura): estado de cuenta y cobros desde el registro de actividad.

Fuentes verificadas:
- Campos de Ad Account: amount_spent, balance, spend_cap, funding_source_details, is_prepay_account.
- /act_<id>/activities (AdActivity): eventos ad_account_billing_* con el monto en extra_data.
  Acepta since/until (until es exclusivo), aunque la referencia no lo documenta; probado en vivo.
- /<business>/business_invoices solo aplica a facturación mensual a crédito (aquí pagan con tarjeta).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .cliente import MetaCliente
from .cuentas import MONEDAS_SIN_DECIMALES, resolver_cuentas
from .errores import ErrorMeta
from .periodos import ZONA_POR_DEFECTO, Periodo

EVENTOS_FACTURACION = {
    "ad_account_billing_charge": ("Cobro", 1),
    "ad_account_billing_refund": ("Reembolso", -1),
    "ad_account_billing_charge_failed": ("Cobro fallido", 0),
    "ad_account_billing_decline": ("Pago rechazado", 0),
    "ad_account_billing_chargeback": ("Contracargo", 0),
    "ad_account_billing_chargeback_reversal": ("Reversión de contracargo", 0),
    "billing_event": ("Evento de facturación", 0),
    "add_funding_source": ("Método de pago agregado", 0),
    "remove_funding_source": ("Método de pago eliminado", 0),
    "ad_account_update_spend_limit": ("Límite de gasto actualizado", 0),
    "ad_account_reset_spend_limit": ("Límite de gasto reiniciado", 0),
    "ad_account_remove_spend_limit": ("Límite de gasto eliminado", 0),
    "account_spending_limit_reached": ("Límite de gasto alcanzado", 0),
    "ad_account_update_status": ("Estado de la cuenta", 0),   # p. ej. Activa → Pago necesario
}
CAMPOS_CUENTA = ["id", "name", "currency", "timezone_name", "amount_spent", "balance", "spend_cap",
                 "is_prepay_account", "funding_source_details"]


def _monto(valor, moneda: str) -> float | None:
    if valor in (None, ""):
        return None
    return float(valor) / (1 if moneda in MONEDAS_SIN_DECIMALES else 100)


def _metodo_pago(fsd: dict | None) -> str | None:
    # display_string ya viene enmascarado por Meta (p. ej. "Mastercard *9333")
    return (fsd or {}).get("display_string")


def estado_cuentas(clientes: list[MetaCliente], cuenta: str | None) -> tuple[pd.DataFrame, list[str]]:
    filas, avisos = [], []
    for cl in clientes:
        try:
            for c in resolver_cuentas(cl, cuenta):
                d = cl.get(c["id"], {"fields": CAMPOS_CUENTA})
                moneda = d.get("currency", "")
                limite = _monto(d.get("spend_cap"), moneda)
                gastado = _monto(d.get("amount_spent"), moneda)
                filas.append({
                    "portfolio": cl.portfolio.clave, "cuenta": d.get("name"), "cuenta_id": d["id"],
                    "moneda": moneda, "zona": d.get("timezone_name"),
                    "gasto_acumulado": gastado,
                    "saldo_pendiente": _monto(d.get("balance"), moneda),
                    "limite_gasto": limite or None,
                    "disponible_limite": (limite - gastado) if limite else None,
                    "metodo_pago": _metodo_pago(d.get("funding_source_details")),
                    "modalidad": "Prepago" if d.get("is_prepay_account") else "Pospago",
                })
        except ErrorMeta as e:
            avisos.append(f"{cl.portfolio.nombre}: {e.detalle()}")
    return pd.DataFrame(filas), avisos


def cobros(clientes: list[MetaCliente], cuenta: str | None, periodo: Periodo,
           todos_los_eventos: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """Cobros, reembolsos y eventos de facturación del periodo (fechas concretas)."""
    params = {
        "fields": ["event_type", "event_time", "date_time_in_timezone", "translated_event_type",
                   "extra_data", "actor_name"],
        # Meta interpreta since/until en UTC y until es exclusivo: se pide un día extra de margen a cada
        # lado y luego se filtra por la fecha LOCAL de la cuenta.
        "since": (periodo.desde - timedelta(days=1)).isoformat(),
        "until": (periodo.hasta + timedelta(days=2)).isoformat(),
        "limit": 500,
    }
    filas, avisos = [], []
    for cl in clientes:
        try:
            cuentas = resolver_cuentas(cl, cuenta)
        except ErrorMeta as e:
            avisos.append(f"{cl.portfolio.nombre}: {e.detalle()}")
            continue
        for c in cuentas:
            moneda_cuenta = c.get("currency", "")
            zona = ZoneInfo(c.get("timezone_name") or ZONA_POR_DEFECTO)
            try:
                for ev in cl.paginar(f"{c['id']}/activities", params):
                    tipo = ev.get("event_type")
                    if tipo not in EVENTOS_FACTURACION and not (todos_los_eventos and "billing" in (tipo or "")):
                        continue
                    etiqueta, signo = EVENTOS_FACTURACION.get(tipo, (ev.get("translated_event_type") or tipo, 0))
                    try:
                        extra = json.loads(ev.get("extra_data") or "{}")
                    except ValueError:
                        extra = {}
                    if tipo == "ad_account_update_status" and extra.get("new_value"):
                        etiqueta = f"Estado: {extra.get('old_value', '?')} → {extra['new_value']}"
                        extra = {}
                    moneda = extra.get("currency") or moneda_cuenta
                    valor = extra.get("new_value")
                    monto = _monto(valor, moneda) if isinstance(valor, (int, float)) or (
                        isinstance(valor, str) and valor.replace(".", "", 1).isdigit()) else None
                    fecha_utc = datetime.strptime(ev["event_time"], "%Y-%m-%dT%H:%M:%S%z")
                    if not (periodo.desde <= fecha_utc.astimezone(zona).date() <= periodo.hasta):
                        continue
                    filas.append({
                        "portfolio": cl.portfolio.clave, "cuenta": c.get("name"), "cuenta_id": c["id"],
                        "fecha": fecha_utc.astimezone(zona).strftime("%Y-%m-%d %H:%M"),
                        "fecha_utc": fecha_utc,
                        "tipo": etiqueta, "monto": monto, "moneda": moneda,
                        "neto": (monto or 0) * signo,
                        "transaccion": extra.get("transaction_id"),
                        "actor": ev.get("actor_name"),
                    })
            except ErrorMeta as e:
                avisos.append(f"{cl.portfolio.nombre} / {c.get('name')}: {e.detalle()}")
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.sort_values("fecha_utc", ascending=False).reset_index(drop=True)
    return df, avisos
