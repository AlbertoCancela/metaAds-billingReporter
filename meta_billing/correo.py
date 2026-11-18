"""Envío por correo (Gmail SMTP) de los comprobantes semanales. Credenciales solo desde .env, nunca se imprimen."""
from __future__ import annotations

import smtplib
import ssl
from html import escape
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import yaml
from dotenv import dotenv_values

from .config import RAIZ

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
RUTA_CONFIG = RAIZ / "config" / "correo.yaml"


def texto_periodo(desde: date, hasta: date) -> str:
    """'del 31 de octubre al 08 de noviembre' (agrega el año si cambia)."""
    def f(d: date, anio: bool) -> str:
        return f"{d.day:02d} de {MESES[d.month - 1]}" + (f" de {d.year}" if anio else "")
    distinto = desde.year != hasta.year
    return f"del {f(desde, distinto)} al {f(hasta, distinto)}"


def texto_periodo_corto(desde: date, hasta: date) -> str:
    """'11 al 18 de noviembre de 2026' / '31 de octubre al 8 de noviembre de 2026'."""
    if (desde.year, desde.month) == (hasta.year, hasta.month):
        return f"{desde.day} al {hasta.day} de {MESES[hasta.month - 1]} de {hasta.year}"
    ini = f"{desde.day} de {MESES[desde.month - 1]}" + (f" de {desde.year}" if desde.year != hasta.year else "")
    return f"{ini} al {hasta.day} de {MESES[hasta.month - 1]} de {hasta.year}"


def cuerpo_resumen(desde: date, hasta: date, filas: list[dict], generado) -> tuple[str, str]:
    """(texto plano, HTML) del correo automático, sin importes. filas: [{'nombre', 'pagos', 'archivo', 'lista'}]."""
    periodo = texto_periodo_corto(desde, hasta)
    dudas = contacto()
    n_total = sum(f["pagos"] for f in filas)
    plural = lambda n: f"{n} pago{'s' if n != 1 else ''}"
    cuando = f"{generado.day} de {MESES[generado.month - 1]} de {generado.year}, {generado:%H:%M} hrs"

    def _f(fecha: str) -> str:
        return f"{int(fecha[8:10]):02d}/{int(fecha[5:7]):02d}/{fecha[:4]}"

    texto = ("Mensaje automático. No responder.\n\n"
             "¡Buen día!\n\n"
             "Se adjuntan los comprobantes de pago de las cuentas publicitarias de Garken Medical Reclutamiento y "
             f"Anytime Fitness correspondientes al periodo del {periodo} ({plural(n_total)}).\n\n"
             + "".join(f"{f['nombre']} · {plural(f['pagos'])}\n"
                       + ("".join(f"  {_f(x['fecha'])}  {x['transaccion']}"
                                  f"{'  (reembolso)' if x['tipo'] == 'Reembolso' else ''}\n" for x in f["lista"])
                          or "  Sin pagos en este periodo\n") + "\n"
                       for f in filas)
             + "Los pagos que Meta registre después del corte se incluyen en el siguiente envío.\n\n"
             f"Generado el {cuando}." + (f" Para dudas: {dudas}" if dudas else "") + "\n")

    gris, tenue, linea, acento, oscuro = "#4B5563", "#9CA3AF", "#E6ECEE", "#43B9C3", "#1F2428"

    def bloque(f) -> str:
        renglones = "".join(
            f"<tr><td style='padding:4px 0;color:{gris};font-size:12px;width:90px'>{_f(x['fecha'])}</td>"
            f"<td style='padding:4px 0;color:{tenue};font-size:12px;font-family:Consolas,Menlo,monospace'>"
            f"{escape(str(x['transaccion']))}{' · reembolso' if x['tipo'] == 'Reembolso' else ''}</td></tr>"
            for x in f["lista"]) or f"<tr><td style='padding:4px 0;color:{tenue};font-size:12px'>Sin pagos en este periodo</td></tr>"
        return (f"<div style='margin:0 0 22px'>"
                f"<div style='font-size:14px;color:{oscuro};font-weight:600;border-left:3px solid {acento};padding-left:10px'>"
                f"{escape(f['nombre'])} <span style='color:{tenue};font-weight:400'>· {plural(f['pagos'])}</span></div>"
                f"<table cellpadding='0' cellspacing='0' style='margin:8px 0 0 13px'>{renglones}</table></div>")

    html = f"""<div style='background:#F2F6F7;padding:32px 16px;font-family:Segoe UI, Arial, Helvetica, sans-serif'>
<table width='100%' cellpadding='0' cellspacing='0' style='max-width:600px;margin:auto;background:#FFFFFF;border:1px solid {linea};border-radius:12px;overflow:hidden'>
  <tr><td style='background:{oscuro};padding:18px 28px'>
    <table width='100%' cellpadding='0' cellspacing='0'><tr>
      <td><img src='https://garkenmedical.com/assets/img/main/logos/logo-garken.png' width='70' alt='Garken Medical' style='display:block'></td>
      <td style='text-align:right;color:#8EC8D0;font-size:11px;letter-spacing:2px;text-transform:uppercase'>No responder</td>
    </tr></table>
  </td></tr>
  <tr><td style='height:3px;background:{acento};line-height:3px;font-size:0'>&nbsp;</td></tr>
  <tr><td style='padding:28px'>
    <div style='color:{tenue};font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px'>Facturación Meta Ads</div>
    <div style='color:{oscuro};font-size:20px;font-weight:600;margin-bottom:18px'>Comprobantes de pago · {periodo}</div>
    <p style='margin:0 0 22px;color:{gris};font-size:14px;line-height:1.7'>¡Buen día! Se adjuntan los comprobantes de pago de las cuentas publicitarias de Garken Medical Reclutamiento y Anytime Fitness correspondientes al periodo del <b>{periodo}</b> ({plural(n_total)}).</p>
    {"".join(bloque(f) for f in filas)}
    <p style='margin:6px 0 0;color:{tenue};font-size:12px;line-height:1.6'>Los pagos que Meta registre después del corte se incluyen en el siguiente envío.</p>
  </td></tr>
  <tr><td style='padding:16px 28px;border-top:1px solid {linea};background:#FAFCFC;color:{tenue};font-size:11px;line-height:1.6'>
    Mensaje automático generado el {cuando}. Por favor no respondas a este correo{f"; para dudas escribe a {escape(dudas)}" if dudas else ""}.<br>
    <span style='color:{acento};font-weight:600'>Garken Medical</span>
  </td></tr>
</table></div>"""
    return texto, html


def _env() -> dict:
    return {k.strip(): (v or "").strip() for k, v in dotenv_values(RAIZ / ".env").items()}


def contacto() -> str:
    """Correo para dudas que aparece en el pie del mensaje (MAIL_CONTACTO en .env; opcional)."""
    return _env().get("MAIL_CONTACTO", "")


def _config() -> dict:
    """Servidor en config/correo.yaml; cuenta, contraseña y destinatarios SOLO en .env (no se versionan)."""
    datos = yaml.safe_load(RUTA_CONFIG.read_text(encoding="utf-8")) if RUTA_CONFIG.exists() else {}
    env = _env()
    if not env.get("MAIL_USER") or not env.get("MAIL_PASS"):
        raise ValueError("Faltan MAIL_USER o MAIL_PASS en el .env.")
    destinatarios = [d.strip() for d in env.get("MAIL_TO", "").split(",") if d.strip()]
    if not destinatarios:
        raise ValueError("Falta MAIL_TO en el .env (destinatarios separados por coma).")
    return {**(datos or {}), "destinatarios": destinatarios, "usuario": env["MAIL_USER"], "clave": env["MAIL_PASS"]}


def enviar(asunto: str, cuerpo: str, adjuntos: list[Path] = (), html: str | None = None) -> list[str]:
    c = _config()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, c["usuario"], ", ".join(c["destinatarios"])
    msg.set_content(cuerpo)
    if html:
        msg.add_alternative(html, subtype="html")
    for ruta in adjuntos:
        msg.add_attachment(Path(ruta).read_bytes(), maintype="application", subtype="pdf", filename=Path(ruta).name)
    with smtplib.SMTP_SSL(c.get("servidor", "smtp.gmail.com"), int(c.get("puerto", 465)),
                          context=ssl.create_default_context(), timeout=60) as s:
        s.login(c["usuario"], c["clave"])
        s.send_message(msg)
    return list(c["destinatarios"])
