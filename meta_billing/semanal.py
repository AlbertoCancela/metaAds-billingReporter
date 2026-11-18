"""Comprobantes semanales de pago por razón social (uno por portfolio), automáticos.

Problema del corte: si el corte es el viernes a las 13:00 y Meta cobra ese mismo viernes por la tarde,
un rango por fechas lo perdería (la semana siguiente empieza el sábado). Solución:
- Cada ejecución consulta una ventana con traslape (desde el corte anterior menos MARGEN_DIAS) y
- solo incluye transacciones que NO hayan salido ya en un comprobante anterior (registro por ID).
Así el cobro del viernes 15:00 entra en el comprobante del viernes siguiente, sin duplicarse,
y también se cubren eventos que Meta publica con retraso.

Registro: registros/comprobantes_semanales.json (o ..._prueba.json en modo prueba). Sin tokens.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .config import DIR_SALIDAS, RAIZ
from .periodos import ZONA_POR_DEFECTO, Periodo

MARGEN_DIAS = 3            # traslape con la ventana anterior (eventos tardíos)
VENTANA_INICIAL_DIAS = 7   # primera ejecución sin registro
TIPOS_PAGO = {"Cobro", "Reembolso"}
DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5,
        "sábado": 5, "domingo": 6}
DIR_COMPROBANTES = DIR_SALIDAS / "comprobantes_semanales"


def _ruta_registro(prueba: bool) -> Path:
    return RAIZ / "registros" / ("comprobantes_semanales_prueba.json" if prueba else "comprobantes_semanales.json")


def _leer_registro(prueba: bool) -> dict:
    ruta = _ruta_registro(prueba)
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}


def _guardar_registro(datos: dict, prueba: bool) -> None:
    ruta = _ruta_registro(prueba)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ruta)


def _clave(m: pd.Series) -> str:
    """ID de la transacción; si Meta no lo da, cuenta + hora UTC + tipo + monto."""
    if m.get("transaccion"):
        return str(m["transaccion"])
    return f"{m['cuenta_id']}|{pd.Timestamp(m['fecha_utc']).isoformat()}|{m['tipo']}|{m['monto']}"


@dataclass
class Resultado:
    portfolio: str
    ruta: Path
    nuevos: int
    total: float
    desde: date
    hasta: date
    pagos: list = None   # [{'fecha', 'transaccion', 'monto', 'tipo'}] más reciente primero


def generar_comprobante(cliente, api_version: str, ahora: datetime, prueba: bool = False) -> Resultado:
    from .facturacion import cobros, estado_cuentas
    from .recibo import Emisor, SeccionCuenta, generar_recibo

    clave_p = cliente.portfolio.clave
    registro = _leer_registro(prueba)
    previo = registro.get(clave_p, {})
    vistos = set(previo.get("transacciones", []))
    hoy = ahora.date()
    if previo.get("ultimo_corte"):
        inicio = datetime.fromisoformat(previo["ultimo_corte"]).date() - timedelta(days=MARGEN_DIAS)
    else:
        inicio = hoy - timedelta(days=VENTANA_INICIAL_DIAS)

    estado, _ = estado_cuentas([cliente], None)
    if estado.empty:
        raise ValueError(f"{cliente.portfolio.nombre}: no hay cuentas para el comprobante.")
    movs, avisos = cobros([cliente], None, Periodo(desde=inicio, hasta=hoy))
    if avisos:
        raise ValueError("; ".join(avisos))   # no marcar nada como reportado si la consulta falló
    if not movs.empty:
        movs = movs[movs["tipo"].isin(TIPOS_PAGO)]
        movs = movs[movs["fecha_utc"] <= pd.Timestamp(ahora)]           # solo hasta el momento del corte
        movs = movs[~movs.apply(_clave, axis=1).isin(vistos)]          # lo ya reportado no se repite

    # El periodo impreso va desde el pago nuevo más antiguo (o el corte anterior) hasta hoy
    if previo.get("ultimo_corte"):
        desde = datetime.fromisoformat(previo["ultimo_corte"]).date()
    else:
        desde = inicio
    if not movs.empty:
        desde = min(desde, min(datetime.strptime(str(f)[:10], "%Y-%m-%d").date() for f in movs["fecha"]))
    periodo = Periodo(desde=desde, hasta=hoy)

    secciones = []
    for _, e in estado.iterrows():
        m = movs[movs["cuenta_id"] == e["cuenta_id"]] if not movs.empty else movs
        secciones.append(SeccionCuenta(
            portfolio=cliente.portfolio.nombre, cuenta=e["cuenta"], cuenta_id=e["cuenta_id"], moneda=e["moneda"],
            metodo_pago=e.get("metodo_pago"), modalidad=e.get("modalidad", ""),
            saldo_pendiente=e.get("saldo_pendiente"),
            gasto_periodo=0.0, movimientos=m))
    datos = cliente.portfolio.recibo or {}
    logo = datos.get("logo")
    emisor = Emisor(nombre=cliente.portfolio.nombre, razon_social=datos.get("razon_social"), rfc=datos.get("rfc"),
                    direccion=datos.get("direccion"), logo=(RAIZ / logo) if logo else None)
    carpeta = DIR_COMPROBANTES / ("prueba" if prueba else "") / hoy.isoformat()
    ruta = carpeta / f"comprobante_pago_{clave_p}_{periodo.desde}_a_{periodo.hasta}.pdf"
    generar_recibo(secciones, emisor, periodo, ruta, api_version, detalle=False)

    # Registrar solo después de generar el PDF
    nuevas = [] if movs.empty else list(movs.apply(_clave, axis=1))
    registro[clave_p] = {"ultimo_corte": ahora.isoformat(), "transacciones": sorted(vistos | set(nuevas))}
    _guardar_registro(registro, prueba)
    neto = 0.0 if movs.empty else float(movs["neto"].sum())
    pagos = [] if movs.empty else [
        {"fecha": str(m["fecha"]), "transaccion": m.get("transaccion") or "—", "tipo": m["tipo"],
         "monto": (m["monto"] or 0) * (-1 if m["tipo"] == "Reembolso" else 1)}
        for _, m in movs.sort_values("fecha_utc", ascending=False).iterrows()]
    return Resultado(cliente.portfolio.nombre, ruta, len(nuevas), neto, periodo.desde, periodo.hasta, pagos)


def ejecutar(config, clientes, prueba: bool = False, log=print, enviar_correo: bool = False) -> list[Resultado]:
    ahora = datetime.now(ZoneInfo(ZONA_POR_DEFECTO))
    resultados, errores = [], []
    for cl in clientes:
        try:
            r = generar_comprobante(cl, config.api_version, ahora, prueba)
            resultados.append(r)
            log(f"✓ {r.portfolio}: {r.nuevos} pago(s) nuevo(s), neto ${r.total:,.2f} · {r.desde} a {r.hasta} "
                f"→ {r.ruta.relative_to(RAIZ)}")
        except Exception as e:   # un portfolio con error no detiene al otro
            from .errores import ocultar_secretos
            errores.append(f"{cl.portfolio.nombre}: {ocultar_secretos(e)}")
            log(f"✗ {errores[-1]}")
    if enviar_correo:
        _enviar(resultados, errores, prueba, log)
    return resultados


def _enviar(resultados: list[Resultado], errores: list[str], prueba: bool, log) -> None:
    from .correo import cuerpo_resumen, enviar, texto_periodo_corto
    from .errores import ocultar_secretos
    pre = "[Prueba] " if prueba else ""
    try:
        if errores or not resultados:   # alerta: no se manda un resumen incompleto
            cuerpo = ("No se pudieron generar todos los comprobantes semanales de pago de Meta Ads.\n\n"
                      + "\n".join(f"- {e}" for e in errores or ["Sin resultados."])
                      + "\n\nRevisa: docker compose logs programador. Se pueden reenviar con ./meta comprobantes --enviar.\n")
            dest = enviar(f"{pre}[No responder] ⚠ Error en comprobantes semanales de Meta Ads", cuerpo)
            log(f"⚠ Correo de alerta enviado a {', '.join(dest)}")
            return
        desde, hasta = min(r.desde for r in resultados), max(r.hasta for r in resultados)
        asunto = f"{pre}[No responder] Resumen de facturación Meta Ads · {texto_periodo_corto(desde, hasta)}"
        filas = [{"nombre": r.portfolio, "pagos": r.nuevos, "total": r.total, "archivo": r.ruta.name,
                  "lista": r.pagos or []} for r in resultados]
        texto, html = cuerpo_resumen(desde, hasta, filas, datetime.now(ZoneInfo(ZONA_POR_DEFECTO)))
        dest = enviar(asunto, texto, [r.ruta for r in resultados], html=html)
        log(f"✉ Correo enviado a {', '.join(dest)} con {len(resultados)} PDF")
    except Exception as e:
        log(f"✗ No se pudo enviar el correo: {ocultar_secretos(e)}")


def proxima_ejecucion(ahora: datetime, dia: int, hora: int, minuto: int) -> datetime:
    objetivo = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    objetivo += timedelta(days=(dia - ahora.weekday()) % 7)
    if objetivo <= ahora:
        objetivo += timedelta(days=7)
    return objetivo


def programador(dia: str, hora: str, extra: list[str], tarea, log=print) -> None:
    """Bucle: ejecuta `tarea(prueba)` cada semana en dia/hora (zona de México) y en las fechas extra (prueba)."""
    zona = ZoneInfo(ZONA_POR_DEFECTO)
    d = DIAS[dia.lower()]
    h, m = (int(x) for x in hora.split(":"))
    pendientes_extra = sorted(datetime.fromisoformat(x).replace(tzinfo=zona) for x in extra)
    while True:
        ahora = datetime.now(zona)
        pendientes_extra = [x for x in pendientes_extra if x > ahora - timedelta(minutes=1)]
        semanal = proxima_ejecucion(ahora, d, h, m)
        siguiente, prueba = (pendientes_extra[0], True) if pendientes_extra and pendientes_extra[0] < semanal \
            else (semanal, False)
        nombre_dia = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][siguiente.weekday()]
        log(f"Próxima ejecución{' (PRUEBA)' if prueba else ''}: {nombre_dia} {siguiente:%Y-%m-%d %H:%M} (hora de México)")
        while (espera := (siguiente - datetime.now(zona)).total_seconds()) > 0:
            time.sleep(min(espera, 60))
        tarea(prueba)
        if prueba:
            pendientes_extra.pop(0)
