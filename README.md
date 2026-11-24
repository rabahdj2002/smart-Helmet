# HALO – Smart Safety Helmet

[![CI](https://github.com/rabahdj2002/smart-Helmet/actions/workflows/ci.yml/badge.svg)](https://github.com/rabahdj2002/smart-Helmet/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.1-092E20?logo=django&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-Mosquitto-660066?logo=eclipsemosquitto&logoColor=white)

![HALO Smart Safety Helmet](docs/thumbnail.png)

**An IoT safety helmet for construction workers, with a real-time supervisor dashboard.**
The helmet detects falls and crashes, measures gas/alcohol levels and tracks GPS position, then streams everything over MQTT to a Django web platform where supervisors monitor every worker live and receive instant incident alerts.

> Bachelor's final-year project – Control Systems Engineering, University of Constantine 1 (2024).

---

## Try it in one command

```bash
docker compose up --build
```

This starts the dashboard, a Mosquitto MQTT broker and **four simulated helmets** that speak the real MQTT protocol. One helmet reports alcohol above the limit, and another reports a crash after 45 seconds, so you can watch the alerts arrive live. Open http://localhost:8000 and log in with `demo` / `demo12345`.

![Live dashboard with simulated helmets](docs/dashboard.png)

## Highlights

- **Fall and crash detection** from the helmet's MPU (accelerometer and gyroscope), with **96.7% detection accuracy** in testing.
- **Gas and alcohol monitoring** with an MQ135 sensor; configurable threshold triggers an automatic alert.
- **GPS tracking** with full route history per worker.
- **Helmet status**: worn/strapped detection, tilt angle, battery level, Wi-Fi signal (RSSI) and latency from the ESP module.
- **Priority status logic**: `Accident > Drunk > Online > Offline`, so the most critical state is always shown first.
- **Incident management**: automatic incident records with location, plus resolve/acknowledge workflow.
- **Remote configuration**: helmets request their settings (alcohol limit, speed limit, refresh rate) over MQTT at startup.
- **In-app MQTT protocol docs** for firmware developers.

## Architecture

```mermaid
flowchart LR
    H["Smart helmet<br/>ESP + MPU + MQ135 + GPS"] -- "helmet/&lt;id&gt;/status" --> B[(MQTT broker)]
    H -- "helmet/&lt;id&gt;/request" --> B
    B -- "helmet/&lt;id&gt;/settings" --> H
    B <--> D["Django backend<br/>MQTT listener + incident logic"]
    D --> DB[(Database)]
    D --> UI["Supervisor dashboard<br/>live map, riders, incidents"]
```

## Tech stack

| Layer | Technologies |
|---|---|
| Helmet hardware | ESP microcontroller, MPU (IMU), MQ135 gas sensor, GPS module, battery |
| Messaging | MQTT (paho-mqtt), WebSocket Secure for the browser |
| Backend | Python, Django 5.1, Gunicorn, WhiteNoise |
| Database | SQLite (development), PostgreSQL-ready (`psycopg2`) |
| Reports | ReportLab (PDF), openpyxl (Excel) |
| Deployment | Docker Compose, Mosquitto, GitHub Actions CI, Cloudflare |

## MQTT protocol

**Helmet → platform**: publish telemetry to `helmet/<helmet_id>/status`

```json
{
  "lat": 36.365, "lon": 6.614, "speed": 4.2,
  "alc": 0.02, "tilt": 12.5,
  "worn": true, "strapped": true,
  "bat": 87, "rssi": -61, "latency": 120
}
```

Add `"crash": true` once when the MPU detects a fall or impact. The platform opens a crash incident and shows the helmet as **Accident** until a supervisor resolves it. Alcohol readings above the limit open one alcohol incident per helmet (not one per message), and a helmet that stops reporting for 30 seconds is shown as offline.

**Helmet requests its settings**: publish `{"type": "get_settings"}` to `helmet/<helmet_id>/request`.
The platform replies on `helmet/<helmet_id>/settings`:

```json
{ "allowed_alcohol_level": 0.5, "speed_limit": 60, "refresh_rate": 5 }
```

## Dashboard pages

| Page | Purpose |
|---|---|
| Dashboard | Live map and status of every helmet |
| Riders | Worker list, rider details and route history |
| Incidents | Crash and alcohol alerts with location |
| Settings | Safety thresholds, speed limit, map refresh rate, MQTT broker |
| MQTT docs | Protocol reference for helmet firmware |

## Quick start

```bash
python -m venv venv
source venv/bin/activate          # Windows: .\venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt

cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

The MQTT listener starts automatically with the server. Set the broker host and port on the **Settings** page.

To test without hardware, run the helmet simulator against your broker:

```bash
python simulate_telemetry.py --host localhost --helmets 4 --crash HALO-04
```

## Testing

The test suite covers every page, login protection, form validation, MQTT ingestion and status priority, incident de-duplication, malformed payloads, the settings request/response, stale-helmet detection, the demo seeder and the simulator. CI runs lint, Django checks, a migrations check, the tests with coverage and a Docker build on every push.

```bash
cd backend
pip install -r requirements.txt ruff coverage
ruff check .
DASHBOARD_MQTT_ENABLED=False coverage run manage.py test dashboard && coverage report
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev-only key | **Set in production** |
| `DJANGO_DEBUG` | `True` | Set `False` in production |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated host names |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Cloudflare/Pages wildcards | e.g. `https://helmet.example.com` |
| `DJANGO_SQLITE_PATH` | `backend/db.sqlite3` | Database file location |
| `DASHBOARD_MQTT_ENABLED` | `True` | Start the MQTT listener with the server |
| `DASHBOARD_MQTT_BROKER_HOST` / `_PORT` | `127.0.0.1` / `1883` | Broker used unless one is saved on the Settings page |
| `DASHBOARD_MQTT_USERNAME` / `_PASSWORD` | empty | Broker credentials |
| `HELMET_OFFLINE_AFTER_SECONDS` | `30` | Silence before a helmet is shown offline |

## Remote hosting

1. Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` for your domain.
2. Set the **Public WebSocket Host** on the Settings page.
3. Make sure the MQTT broker accepts WSS (port 443 works well behind Cloudflare), and require broker authentication. The bundled Mosquitto config is for local demos only.

## Author

**Rabah Djebbes** – Automation & Control Engineer
[LinkedIn](https://www.linkedin.com/in/rabahdjebbes) · [GitHub](https://github.com/rabahdj2002) · Rabahwork@gmail.com

## License

Proprietary – all rights reserved.
