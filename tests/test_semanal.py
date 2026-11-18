"""Comprobantes semanales: horario del programador y que un pago tardío del viernes no se pierda ni se duplique."""
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from meta_billing import semanal

MX = ZoneInfo("America/Mexico_City")


def test_proxima_ejecucion_viernes_13():
    mie = datetime(2026, 11, 18, 16, 5, tzinfo=MX)
    assert semanal.proxima_ejecucion(mie, 4, 13, 0) == datetime(2026, 11, 20, 13, 0, tzinfo=MX)
    vie_despues = datetime(2026, 11, 20, 13, 0, 1, tzinfo=MX)
    assert semanal.proxima_ejecucion(vie_despues, 4, 13, 0) == datetime(2026, 11, 27, 13, 0, tzinfo=MX)


def _mov(tx, cuando_mx, monto):
    return {"portfolio": "garken", "cuenta": "C", "cuenta_id": "act_1", "fecha": cuando_mx.strftime("%Y-%m-%d %H:%M"),
            "fecha_utc": cuando_mx.astimezone(timezone.utc), "tipo": "Cobro", "monto": monto, "moneda": "MXN",
            "neto": monto, "transaccion": tx, "actor": None}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Simula Meta: devuelve todos los cobros de `movs`; captura los PDF generados en vez de dibujarlos."""
    movs, pdfs = [], []
    monkeypatch.setattr(semanal, "RAIZ", tmp_path)
    monkeypatch.setattr(semanal, "DIR_COMPROBANTES", tmp_path / "salidas")
    import meta_billing.facturacion as fac, meta_billing.recibo as rec
    monkeypatch.setattr(fac, "estado_cuentas", lambda c, x: (pd.DataFrame([{
        "cuenta": "C", "cuenta_id": "act_1", "moneda": "MXN", "metodo_pago": "Visa *1", "modalidad": "Pospago",
        "saldo_pendiente": 0.0}]), []))
    monkeypatch.setattr(fac, "cobros", lambda c, x, p: (pd.DataFrame(movs), []))
    monkeypatch.setattr(rec, "generar_recibo", lambda secs, em, per, ruta, *a, **k: pdfs.append(
        (per, list(secs[0].movimientos.get("transaccion", [])))) or ruta)
    cliente = SimpleNamespace(portfolio=SimpleNamespace(clave="garken", nombre="Garken", recibo=None))
    return movs, pdfs, cliente


def test_pago_del_viernes_por_la_tarde_entra_la_semana_siguiente(entorno):
    movs, pdfs, cliente = entorno
    movs.append(_mov("T1", datetime(2026, 11, 18, 9, 0, tzinfo=MX), 500.0))
    corte1 = datetime(2026, 11, 20, 13, 0, tzinfo=MX)
    semanal.generar_comprobante(cliente, "v26.0", corte1)
    # Meta cobra el mismo viernes a las 15:00, después del corte
    movs.append(_mov("T2", datetime(2026, 11, 20, 15, 0, tzinfo=MX), 800.0))
    movs.append(_mov("T3", datetime(2026, 11, 25, 10, 0, tzinfo=MX), 300.0))
    r2 = semanal.generar_comprobante(cliente, "v26.0", datetime(2026, 11, 27, 13, 0, tzinfo=MX))
    assert pdfs[0][1] == ["T1"]                    # el primer corte no ve el cobro de las 15:00
    assert sorted(pdfs[1][1]) == ["T2", "T3"]      # el segundo lo incluye y no repite T1
    assert r2.nuevos == 2 and r2.total == 1100.0
    assert pdfs[1][0].desde.isoformat() == "2026-11-20"


def test_prueba_no_afecta_registro_real(entorno):
    movs, pdfs, cliente = entorno
    movs.append(_mov("T1", datetime(2026, 11, 18, 9, 0, tzinfo=MX), 500.0))
    semanal.generar_comprobante(cliente, "v26.0", datetime(2026, 11, 18, 16, 5, tzinfo=MX), prueba=True)
    semanal.generar_comprobante(cliente, "v26.0", datetime(2026, 11, 20, 13, 0, tzinfo=MX))
    assert pdfs[1][1] == ["T1"]
