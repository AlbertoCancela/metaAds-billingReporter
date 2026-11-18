"""Errores de la API de Meta traducidos al español y ocultamiento de tokens."""
from __future__ import annotations

import logging
import re

# Los tokens de Meta empiezan con "EAA"; además se ocultan los valores exactos registrados.
_PATRON_TOKEN = re.compile(r"EAA[A-Za-z0-9]{20,}")
_PATRON_PARAM = re.compile(r"(access_token=)[^&\s]+", re.IGNORECASE)
_TOKENS_CONOCIDOS: set[str] = set()


def registrar_secreto(valor: str) -> None:
    """Registra un token para que se oculte en cualquier texto mostrado o registrado."""
    if valor:
        _TOKENS_CONOCIDOS.add(valor)


def ocultar_secretos(texto: object) -> str:
    texto = str(texto)
    for token in _TOKENS_CONOCIDOS:
        texto = texto.replace(token, "***")
    texto = _PATRON_PARAM.sub(r"\1***", texto)
    return _PATRON_TOKEN.sub("***", texto)


class FiltroSecretos(logging.Filter):
    """Filtro de logging que oculta tokens en todos los mensajes."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = ocultar_secretos(record.getMessage())
        record.args = None
        return True


def instalar_filtro_logging() -> None:
    raiz = logging.getLogger()
    for handler in raiz.handlers or []:
        handler.addFilter(FiltroSecretos())
    raiz.addFilter(FiltroSecretos())


# Códigos de límite de llamadas (rate limit) según
# https://developers.facebook.com/docs/marketing-api/overview/rate-limiting
CODIGOS_LIMITE = {4, 17, 32, 613, *range(80000, 80015)}
CODIGOS_TEMPORALES = {1, 2}

_MENSAJES = {
    190: "El token no es válido o fue revocado. Genera uno nuevo para el usuario del sistema y actualiza el .env.",
    102: "La sesión del token no es válida. Revisa el token en el .env.",
    10: "La app no tiene permiso para esta acción. Revisa los permisos del token.",
    100: "Parámetro inválido en la solicitud (campo, ID o valor no reconocido).",
    803: "El objeto solicitado no existe o el token no tiene acceso a él.",
    368: "Meta bloqueó temporalmente la acción por políticas. Espera antes de reintentar.",
    2635: "La versión de la API ya no está disponible. Actualiza api_version en config/portfolios.yaml.",
    3018: "La fecha de inicio supera el máximo de 37 meses de historial permitido.",
    1: "Error desconocido de Meta (posiblemente temporal).",
    2: "Servicio de Meta temporalmente no disponible.",
}


class ErrorMeta(Exception):
    """Error devuelto por la API de Meta, con mensaje en español y sin tokens."""

    def __init__(self, mensaje: str, codigo: int | None = None, subcodigo: int | None = None,
                 estado_http: int | None = None, original: str | None = None,
                 fbtrace_id: str | None = None):
        self.codigo = codigo
        self.subcodigo = subcodigo
        self.estado_http = estado_http
        self.original = ocultar_secretos(original) if original else None
        self.fbtrace_id = fbtrace_id
        super().__init__(ocultar_secretos(mensaje))

    @property
    def es_limite(self) -> bool:
        return self.codigo in CODIGOS_LIMITE

    @property
    def es_temporal(self) -> bool:
        return self.es_limite or self.codigo in CODIGOS_TEMPORALES or (self.estado_http or 0) >= 500

    def detalle(self) -> str:
        partes = [str(self)]
        if self.codigo is not None:
            partes.append(f"código {self.codigo}" + (f"/{self.subcodigo}" if self.subcodigo else ""))
        if self.original:
            partes.append(f"Meta dice: {self.original}")
        if self.fbtrace_id:
            partes.append(f"fbtrace_id {self.fbtrace_id}")
        return " | ".join(partes)


def traducir_error(cuerpo: dict | None, estado_http: int) -> ErrorMeta:
    err = (cuerpo or {}).get("error", {}) if isinstance(cuerpo, dict) else {}
    codigo = err.get("code")
    subcodigo = err.get("error_subcode")
    original = err.get("error_user_msg") or err.get("message")

    if codigo in CODIGOS_LIMITE:
        mensaje = "Se alcanzó el límite de llamadas de Meta (rate limit)."
    elif codigo in _MENSAJES:
        mensaje = _MENSAJES[codigo]
    elif codigo is not None and 200 <= codigo <= 299:
        mensaje = "Permiso insuficiente: el token no tiene acceso a este recurso o le falta un permiso."
    elif estado_http >= 500:
        mensaje = f"Error del servidor de Meta (HTTP {estado_http})."
    else:
        mensaje = f"Error de la API de Meta (HTTP {estado_http})."

    return ErrorMeta(mensaje, codigo, subcodigo, estado_http, original, err.get("fbtrace_id"))
