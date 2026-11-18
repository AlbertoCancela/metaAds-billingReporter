# Meta Ads Billing

Cada **viernes a las 13:00 (hora de México)** genera un comprobante de pago en PDF por razón social (Anytime Fitness y Garken Medical) con los pagos de Meta Ads, y lo envía por correo con un mensaje automático «No responder».

Es **solo lectura respecto a Meta**: no tiene ningún método para crear, pausar ni editar anuncios.

## Cómo evita perder pagos

Cada comprobante incluye los **pagos que todavía no han salido en uno anterior**, no un rango de fechas fijo.

- Los identificadores de transacción ya enviados se guardan en `registros/comprobantes_semanales.json`.
- Cada ejecución consulta desde 3 días antes del corte anterior y descarta lo ya enviado.
- Si Meta cobra un viernes a las 15:00, después del corte, ese pago llega en el envío del viernes siguiente, sin perderse ni duplicarse.
- Si una semana falla o el servidor está apagado, no se marca nada como enviado y el siguiente envío incluye todo lo pendiente. Si falla, se manda un correo de alerta.

> **No borres `registros/`**: sin ese archivo, el siguiente envío repite los pagos de los últimos 7 días.

## Instalar en el servidor

Requisitos: Linux con Docker y Docker Compose, y salida a internet para la API de Meta y para `smtp.gmail.com:465`.

```bash
# 1. Bajar el código
git clone <url-del-repo> Meta_Ads_Billing
cd Meta_Ads_Billing

# 2. Secretos: se crean a mano en el servidor, nunca van en git
cp .env.example .env
nano .env            # tokens de Meta, MAIL_USER, MAIL_PASS (contraseña de aplicación de Google), MAIL_TO y MAIL_CONTACTO
chmod 600 .env

# 3. Construir y probar
docker compose build
docker compose run --rm cli verificar                      # prueba los tokens
docker compose run --rm cli comprobantes --prueba --enviar # PDF y correo de prueba

# 4. Dejarlo corriendo (se reinicia solo con el servidor)
docker compose up -d programador
docker compose logs -f programador                         # bitácora
```

Al arrancar, la bitácora debe mostrar: `Próxima ejecución: viernes AAAA-MM-DD 13:00 (hora de México)`.

## Uso diario

| Qué | Comando |
|---|---|
| Ver la bitácora | `docker compose logs --tail 50 programador` |
| Generar y enviar ahora (cuenta como envío real) | `docker compose run --rm cli comprobantes --enviar` |
| Prueba sin afectar el registro real | `docker compose run --rm cli comprobantes --prueba --enviar` |
| Programar una prueba a una hora | agrega `"--extra", "2026-11-20T10:30"` al `command` del servicio y ejecuta `docker compose up -d programador` |
| Actualizar el código | `git pull && docker compose up -d --build programador` |

Los PDF quedan en `salidas/comprobantes_semanales/<fecha>/comprobante_pago_<portfolio>_<desde>_a_<hasta>.pdf`. Las pruebas van a `salidas/comprobantes_semanales/prueba/`.

## Configuración

| Archivo | Contenido |
|---|---|
| `.env` | `META_TOKEN_ANYTIME`, `META_TOKEN_GARKENRECLU`, `MAIL_USER`, `MAIL_PASS`, `MAIL_TO` (destinatarios separados por coma) y `MAIL_CONTACTO` (correo para dudas del pie, opcional). No se versiona |
| `config/portfolios.yaml` | Portfolios, nombre de la variable de cada token y datos opcionales del encabezado del PDF |
| `config/correo.yaml` | Servidor y puerto SMTP |
| `docker-compose.yml` | Día y hora del envío (`--dia`, `--hora`) |
| `meta_billing/correo.py` | Texto y diseño del correo |

## Seguridad

- Los tokens y la contraseña del correo solo viven en el `.env` del servidor. No se copian a la imagen de Docker ni a git, y no se imprimen en la bitácora: un filtro los oculta.
- El token viaja en el encabezado `Authorization`, no en la URL.
- El repositorio no contiene direcciones de correo internas: destinatarios y contacto viven en el `.env`.
- Los tokens de Meta pueden caducar. Si la bitácora muestra un error de token, genera uno nuevo, cámbialo en `.env` y ejecuta `docker compose restart programador`.

## Pruebas

```bash
docker compose run --rm --entrypoint python cli -m pytest tests -q
```
