"""Informe de facturación en PDF (resumen interno de pagos de publicidad en Meta).

Sigue la estructura del informe de facturación (cuenta, periodo, método de pago, tabla de pagos y total)
con la identidad del portfolio (nombre y, si se configuran, razón social/RFC/dirección/logo propios).
No usa logotipos, razón social ni datos fiscales de Meta, y el pie indica que no es un documento de Meta. Los comprobantes oficiales
se descargan en Meta Business Suite → Facturación y pagos.

Datos: estado de la cuenta (amount_spent, balance, funding_source_details) y cobros del registro de
actividad (ad_account_billing_*), más el gasto del periodo según Insights para conciliar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from zoneinfo import ZoneInfo

from .periodos import ZONA_POR_DEFECTO, Periodo


ESTADO_PAGO = {
    "Cobro": "Pagado", "Reembolso": "Reembolsado", "Cobro fallido": "Rechazado", "Pago rechazado": "Rechazado",
    "Contracargo": "Contracargo", "Reversión de contracargo": "Contracargo revertido",
}
AVISO = ("Documento interno de control. No es un comprobante fiscal ni un documento emitido por Meta. "
         "Los comprobantes oficiales se descargan en Meta Business Suite → Facturación y pagos.")


@dataclass
class SeccionCuenta:
    portfolio: str
    cuenta: str
    cuenta_id: str
    moneda: str
    metodo_pago: str | None
    modalidad: str
    saldo_pendiente: float | None
    gasto_periodo: float | None
    movimientos: pd.DataFrame          # columnas de facturacion.cobros()


@dataclass
class Emisor:
    nombre: str
    razon_social: str | None = None
    rfc: str | None = None
    direccion: str | None = None
    logo: Path | None = None
    lineas: list[str] = field(default_factory=list)


def _dinero(v: float | None, moneda: str) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    signo = "-" if v < 0 else ""
    return f"{signo}${abs(v):,.2f} {moneda}"


# Paleta sobria del informe: texto negro, bordes grises finos, fondo blanco
NEGRO = colors.HexColor("#1C1E21")
GRIS_TEXTO = colors.HexColor("#606770")
LINEA = colors.HexColor("#C8CCD0")


def _estilos():
    base = getSampleStyleSheet()
    def e(nombre, **kw):
        return ParagraphStyle(nombre, parent=base["Normal"], fontName=kw.pop("fn", "Helvetica"),
                              fontSize=kw.pop("fs", 9), textColor=kw.pop("tc", NEGRO), leading=kw.pop("ld", 12), **kw)
    return {
        "emisor": e("emisor", fs=7.5, ld=12),
        "linea": e("linea", fs=7.5, ld=12),
        "derecha": e("derecha", fs=7.5, alignment=TA_RIGHT),
        "titulo": e("titulo", fs=15, ld=18, spaceAfter=18),
        "bloque": e("bloque", fs=10, ld=12),
        "metodo": e("metodo", fs=10, ld=13),
        "normal": e("normal"),
        "gris": e("gris", tc=GRIS_TEXTO),
        "seccion": e("seccion", fn="Helvetica-Bold", fs=10, spaceBefore=14, spaceAfter=4),
        "nota": e("nota", fs=7.5, tc=GRIS_TEXTO, ld=10),
    }


def _encabezado(emisor: Emisor, s: "SeccionCuenta", periodo: Periodo, est) -> list:
    """Misma disposición del informe de facturación: emisor a la izquierda, cuenta a la derecha."""
    izq = []
    if emisor.logo and emisor.logo.exists():
        izq.append(Image(str(emisor.logo), width=34 * mm, height=14 * mm, kind="proportional", hAlign="LEFT"))
        izq.append(Spacer(1, 2 * mm))
    izq.append(Paragraph(emisor.razon_social or emisor.nombre, est["emisor"]))
    for linea in filter(None, [*(emisor.direccion or "").split("\n"), f"RFC: {emisor.rfc}" if emisor.rfc else None,
                               *emisor.lineas]):
        izq.append(Paragraph(linea, est["linea"]))
    der = [Paragraph(f"Cuenta: {s.cuenta_id.replace('act_', '')}", est["derecha"])]
    t = Table([[izq, der]], colWidths=[120 * mm, 60 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return [t, Spacer(1, 1 * mm),
            Paragraph(f"Informe de facturación: {periodo.desde.day}/{periodo.desde.month}/{periodo.desde.year} - "
                      f"{periodo.hasta.day}/{periodo.hasta.month}/{periodo.hasta.year}", est["titulo"]),
            Paragraph("Pago de Anuncios de Meta", est["bloque"]),
            Paragraph(f"Método de pago: {(s.metodo_pago or '—').replace('*', '···· ')}", est["metodo"]),
            Spacer(1, 2 * mm)]


def _seccion(s: SeccionCuenta, est, detalle: bool) -> list:
    movs = s.movimientos
    pagos = movs[movs["tipo"].isin(ESTADO_PAGO)] if not movs.empty else movs
    filas = [["Fecha", "Identificador de la transacción", "Importe", "Estado del pago"]]
    if pagos.empty:
        filas.append(["—", "Sin pagos en el periodo", "—", "—"])
    else:
        for _, m in pagos.sort_values("fecha_utc", ascending=False).iterrows():   # más reciente primero
            f = str(m["fecha"])[:10]
            importe = m["monto"] if m["tipo"] != "Reembolso" else -(m["monto"] or 0)
            filas.append([f"{int(f[8:10])}/{int(f[5:7])}/{f[:4]}", m.get("transaccion") or "—",
                          _dinero(importe, m.get("moneda") or s.moneda), ESTADO_PAGO.get(m["tipo"], m["tipo"])])
    cobrado = float(pagos.loc[pagos["tipo"] == "Cobro", "monto"].fillna(0).sum()) if not pagos.empty else 0.0
    reembolsado = float(pagos.loc[pagos["tipo"] == "Reembolso", "monto"].fillna(0).sum()) if not pagos.empty else 0.0
    n_datos = len(filas)
    if reembolsado:
        filas.append(["", "", "Reembolsos", _dinero(-reembolsado, s.moneda)])
    filas.append(["", "", "Importe total facturado", _dinero(cobrado - reembolsado, s.moneda)])
    estilo = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), NEGRO),
        ("ALIGN", (2, 0), (3, -1), "RIGHT"),
        ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
        ("INNERGRID", (0, 0), (-1, n_datos - 1), 0.6, LINEA),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for r in range(n_datos, len(filas)):   # filas de total: celda combinada, etiqueta + importe a la derecha
        estilo += [("SPAN", (0, r), (2, r)), ("LINEABOVE", (0, r), (-1, r), 0.6, LINEA),
                   ("FONTSIZE", (0, r), (-1, r), 8)]
        filas[r][0], filas[r][2] = filas[r][2], ""
    for r in range(n_datos, len(filas)):
        estilo.append(("ALIGN", (0, r), (2, r), "RIGHT"))
    t = Table(filas, colWidths=[30 * mm, 62 * mm, 44 * mm, 44 * mm], repeatRows=1)
    t.setStyle(TableStyle(estilo))
    flujo = [t]

    if detalle:   # información adicional de control (opcional)
        conc = [["Gasto en anuncios del periodo", _dinero(s.gasto_periodo, s.moneda)],
                ["Facturado en el periodo", _dinero(cobrado - reembolsado, s.moneda)],
                ["Diferencia", _dinero((s.gasto_periodo or 0) - (cobrado - reembolsado), s.moneda)],
                ["Saldo pendiente de cobro", _dinero(s.saldo_pendiente, s.moneda)]]
        tc = Table(conc, colWidths=[70 * mm, 42 * mm], hAlign="LEFT")
        tc.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                                ("TEXTCOLOR", (0, 0), (-1, -1), GRIS_TEXTO), ("ALIGN", (1, 0), (1, -1), "RIGHT")]))
        flujo += [Paragraph("Conciliación (control interno)", est["seccion"]), tc]
        estados = movs[movs["tipo"].str.startswith("Estado:")] if not movs.empty else movs
        if not estados.empty:
            flujo.append(Paragraph("Eventos de la cuenta", est["seccion"]))
            for _, m in estados.sort_values("fecha_utc").iterrows():
                flujo.append(Paragraph(f"{m['fecha']} · {m['tipo'].replace('→', 'a')}", est["gris"]))
    return flujo


def generar_recibo(secciones: list[SeccionCuenta], emisor: Emisor, periodo: Periodo, ruta: Path,
                   api_version: str, detalle: bool = False) -> Path:
    est = _estilos()
    generado = datetime.now(ZoneInfo(ZONA_POR_DEFECTO))
    ruta.parent.mkdir(parents=True, exist_ok=True)

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRIS_TEXTO)
        canvas.drawString(15 * mm, 10 * mm, f"Resumen generado el {generado:%d/%m/%Y %H:%M} con datos de Meta Ads "
                                            f"(Marketing API {api_version}). No es comprobante fiscal ni documento emitido por Meta.")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(ruta), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"Informe de facturación {periodo.desde} a {periodo.hasta}",
                            author=emisor.razon_social or emisor.nombre, creator="meta-ads-mkt")
    flujo: list = []
    for i, s in enumerate(secciones):
        if i:
            flujo.append(PageBreak())
        flujo += _encabezado(emisor, s, periodo, est) + _seccion(s, est, detalle)
    doc.build(flujo, onFirstPage=pie, onLaterPages=pie)
    return ruta
