"""CLI de Meta Ads Billing: comprobantes semanales y programador."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import typer

from .config import cargar_config
from .cuentas import crear_clientes, listar_cuentas
from .errores import ErrorMeta, instalar_filtro_logging, ocultar_secretos

app = typer.Typer(help="Comprobantes semanales de pago de Meta Ads y envío por correo.", no_args_is_help=True,
                  pretty_exceptions_show_locals=False, context_settings={"help_option_names": ["-h", "--ayuda", "--help"]})
instalar_filtro_logging()


def _log(t: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {t}", flush=True)


def _seguro(func):
    try:
        func()
    except ErrorMeta as e:
        _log(f"✗ {e.detalle()}"); raise typer.Exit(2)
    except Exception as e:
        _log(f"✗ {ocultar_secretos(e)}"); raise typer.Exit(2)


@app.command("verificar")
def verificar():
    """Prueba los tokens (lista las cuentas publicitarias) sin generar nada."""
    def run():
        config = cargar_config()
        for cl in crear_clientes(config):
            try:
                cuentas = listar_cuentas(cl)
                _log(f"✓ {cl.portfolio.nombre}: {len(cuentas)} cuenta(s) · " + ", ".join(c.get('name', c['id']) for c in cuentas))
            except ErrorMeta as e:
                _log(f"✗ {cl.portfolio.nombre}: {e.detalle()}")
    _seguro(run)


@app.command()
def comprobantes(
    portfolio: Optional[List[str]] = typer.Option(None, "--portfolio", "-p", help="anytime, garken… (repetible)."),
    prueba: bool = typer.Option(False, "--prueba", help="Carpeta y registro de prueba (no afecta a los envíos reales)."),
    enviar: bool = typer.Option(False, "--enviar", help="Envía los PDF por correo (destinatarios: MAIL_TO del .env)."),
):
    """Genera ahora el comprobante de pago por razón social con los pagos aún no reportados."""
    def run():
        from .semanal import ejecutar
        config = cargar_config()
        ejecutar(config, crear_clientes(config, portfolio or None), prueba, log=_log, enviar_correo=enviar)
    _seguro(run)


@app.command()
def programador(
    dia: str = typer.Option("viernes", "--dia", help="Día de la semana."),
    hora: str = typer.Option("13:00", "--hora", help="Hora HH:MM (hora de México)."),
    extra: Optional[List[str]] = typer.Option(None, "--extra", help="Ejecución de PRUEBA adicional, AAAA-MM-DDTHH:MM (repetible)."),
):
    """Bucle que genera y envía los comprobantes cada semana (servicio de Docker)."""
    from .semanal import ejecutar, programador as bucle

    def tarea(es_prueba: bool):
        _log(f"Generando comprobantes{' de PRUEBA' if es_prueba else ''}…")
        try:
            config = cargar_config()
            ejecutar(config, crear_clientes(config), es_prueba, log=_log, enviar_correo=True)
        except Exception as e:
            _log(f"✗ {ocultar_secretos(e)}")
            try:
                from .correo import enviar as enviar_correo
                enviar_correo("[No responder] ⚠ Error en comprobantes semanales de Meta Ads", f"Error: {ocultar_secretos(e)}\n")
            except Exception as e2:
                _log(f"✗ Tampoco se pudo enviar la alerta: {ocultar_secretos(e2)}")

    _log(f"Meta Ads Billing · programador iniciado ({dia} {hora}, hora de México)")
    bucle(dia, hora, extra or [], tarea, log=_log)


def main():
    app()
