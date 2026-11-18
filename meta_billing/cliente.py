"""Cliente reutilizable de la Graph API de Meta.

- Autenticación por portfolio con encabezado Authorization (el token nunca va en la URL).
- Paginación automática por cursores (paging.next).
- Reintentos con espera exponencial ante límites de llamadas y errores temporales.
- Frenado preventivo leyendo los encabezados de uso de Meta.
- SOLO LECTURA: este proyecto no tiene ningún método de escritura hacia Meta.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Iterator

import requests

from .config import Portfolio
from .errores import ErrorMeta, ocultar_secretos, traducir_error

log = logging.getLogger(__name__)

URL_BASE = "https://graph.facebook.com"
ESPERA_MAXIMA = 300          # segundos máximos por espera individual
ESPERA_TOLERABLE = 15 * 60   # si Meta pide esperar más que esto, se aborta con mensaje claro
UMBRAL_USO = 85              # % de uso a partir del cual se frena preventivamente


class MetaCliente:
    def __init__(self, portfolio: Portfolio, api_version: str, max_reintentos: int = 5,
                 timeout: int = 60):
        self.portfolio = portfolio
        self.api_version = api_version
        self.max_reintentos = max_reintentos
        self.timeout = timeout
        self._sesion = requests.Session()
        self._sesion.headers.update({
            "Authorization": f"Bearer {portfolio.token}",
            "User-Agent": "meta-ads-mkt/0.1",
        })

    def __repr__(self) -> str:  # nunca incluye el token
        return f"MetaCliente(portfolio={self.portfolio.clave!r}, version={self.api_version!r})"

    # ------------------------------------------------------------------ público
    def get(self, ruta: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{URL_BASE}/{self.api_version}/{ruta.lstrip('/')}"
        return self._solicitar("GET", url, params=self._normalizar(params))

    def paginar(self, ruta: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Itera todos los elementos de un edge, siguiendo paging.next hasta el final."""
        respuesta = self.get(ruta, params)
        while True:
            yield from respuesta.get("data", [])
            siguiente = (respuesta.get("paging") or {}).get("next")
            if not siguiente:
                break
            # paging.next ya incluye todos los parámetros
            respuesta = self._solicitar("GET", siguiente)

    def todos(self, ruta: str, params: dict[str, Any] | None = None) -> list[dict]:
        return list(self.paginar(ruta, params))

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _normalizar(params: dict[str, Any] | None) -> dict[str, Any]:
        salida: dict[str, Any] = {}
        for k, v in (params or {}).items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)) and k == "fields":
                v = ",".join(v)
            elif isinstance(v, (dict, list, tuple)):
                v = json.dumps(v)
            salida[k] = v
        return salida

    def _solicitar(self, metodo: str, url: str, params: dict | None = None, data: dict | None = None,
                   reintentar_inciertos: bool = True) -> dict:
        intento = 0
        while True:
            intento += 1
            try:
                resp = self._sesion.request(metodo, url, params=params, data=data, timeout=self.timeout)
            except requests.RequestException as exc:
                # El mensaje de requests puede incluir la URL; se oculta cualquier secreto
                error = ErrorMeta(f"Fallo de conexión con Meta: {ocultar_secretos(type(exc).__name__)}",
                                  estado_http=0)
                if not reintentar_inciertos:
                    raise ErrorMeta("Fallo de conexión durante una operación de creación: el resultado es "
                                    "INCIERTO. Revisa en el Administrador de anuncios antes de repetirla.",
                                    estado_http=0) from None
                if intento > self.max_reintentos:
                    raise error from None
                self._esperar(intento, None, f"{error}")
                continue

            self._frenado_preventivo(resp)

            try:
                cuerpo = resp.json()
            except ValueError:
                cuerpo = None

            if resp.ok and not (isinstance(cuerpo, dict) and "error" in cuerpo):
                return cuerpo if isinstance(cuerpo, dict) else {}

            error = traducir_error(cuerpo, resp.status_code)
            # Un rechazo por límite de llamadas significa que NO se aplicó: es seguro reintentar.
            # Un 5xx/error genérico en una creación es incierto: no se reintenta.
            incierto = not error.es_limite
            if not error.es_temporal or intento > self.max_reintentos or (incierto and not reintentar_inciertos):
                if incierto and not reintentar_inciertos and error.es_temporal:
                    raise ErrorMeta(f"{error} El resultado de la creación es INCIERTO: revisa en el "
                                    "Administrador de anuncios antes de repetirla.", error.codigo, error.subcodigo,
                                    error.estado_http, error.original, error.fbtrace_id) from None
                raise error from None

            self._esperar(intento, self._espera_sugerida(resp), error.detalle())

    def _esperar(self, intento: int, sugerida: float | None, motivo: str) -> None:
        if sugerida is not None and sugerida > ESPERA_TOLERABLE:
            raise ErrorMeta(
                f"Meta pide esperar ~{int(sugerida // 60)} min para recuperar acceso. "
                "Intenta de nuevo más tarde.")
        espera = sugerida if sugerida else min(ESPERA_MAXIMA, (2 ** intento) * 2)
        espera += random.uniform(0, 1.5)
        log.warning("[%s] %s → reintento %d/%d en %.0f s",
                    self.portfolio.clave, motivo, intento, self.max_reintentos, espera)
        time.sleep(espera)

    @staticmethod
    def _leer_json_encabezado(resp: requests.Response, *nombres: str) -> Any:
        for nombre in nombres:
            valor = resp.headers.get(nombre)
            if valor:
                try:
                    return json.loads(valor)
                except ValueError:
                    return None
        return None

    def _espera_sugerida(self, resp: requests.Response) -> float | None:
        """Segundos sugeridos por Meta antes de reintentar, si los indica."""
        segundos: list[float] = []
        buc = self._leer_json_encabezado(resp, "X-Business-Use-Case-Usage", "X-Business-Use-Case")
        if isinstance(buc, dict):
            for entradas in buc.values():
                for e in entradas if isinstance(entradas, list) else []:
                    minutos = e.get("estimated_time_to_regain_access")
                    if minutos:
                        segundos.append(float(minutos) * 60)
        cuenta = self._leer_json_encabezado(resp, "X-Ad-Account-Usage")
        if isinstance(cuenta, dict) and cuenta.get("reset_time_duration"):
            segundos.append(float(cuenta["reset_time_duration"]))
        return max(segundos) if segundos else None

    def _frenado_preventivo(self, resp: requests.Response) -> None:
        """Si el uso reportado por Meta se acerca al límite, pausa unos segundos."""
        porcentajes: list[float] = []
        for nombre in ("X-App-Usage", "X-Ad-Account-Usage", "X-FB-Ads-Insights-Throttle"):
            datos = self._leer_json_encabezado(resp, nombre)
            if isinstance(datos, dict):
                porcentajes += [float(v) for k, v in datos.items()
                                if isinstance(v, (int, float)) and ("pct" in k or k in
                                {"call_count", "total_time", "total_cputime"})]
        buc = self._leer_json_encabezado(resp, "X-Business-Use-Case-Usage", "X-Business-Use-Case")
        if isinstance(buc, dict):
            for entradas in buc.values():
                for e in entradas if isinstance(entradas, list) else []:
                    porcentajes += [float(e.get(k, 0)) for k in ("call_count", "total_time", "total_cputime")]
        uso = max(porcentajes, default=0)
        if uso >= UMBRAL_USO:
            espera = 5 + (uso - UMBRAL_USO) * 2
            log.warning("[%s] Uso de API al %.0f%% → pausa preventiva de %.0f s",
                        self.portfolio.clave, uso, espera)
            time.sleep(espera)
