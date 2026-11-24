import json
import logging
import re
import threading
import time

import paho.mqtt.client as mqtt
from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

STATUS_TOPIC = 'helmet/+/status'
REQUEST_TOPIC = 'helmet/+/request'
PLACEHOLDER_HOSTS = {'', 'localhost', '127.0.0.1'}
HELMET_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,50}$')

_mqtt_started = False
_mqtt_lock = threading.Lock()


def _number(data, key, current, cast=float):
    try:
        return cast(data[key]) if key in data else current
    except (TypeError, ValueError):
        return current


def _flag(data, key, current):
    value = data.get(key, current)
    return value if isinstance(value, bool) else current


def _alcohol_limit():
    from .models import SystemSettings
    config = SystemSettings.objects.first()
    return config.allowed_alcohol_level if config else 0.5


def _open_incident(helmet, incident_type):
    """Create one unresolved incident per helmet and type; repeated readings do not spam alerts."""
    from .models import Incident
    if helmet.rider is None:
        return None
    incident, _ = Incident.objects.get_or_create(
        helmet=helmet,
        type=incident_type,
        resolved=False,
        defaults={'rider': helmet.rider, 'latitude': helmet.latitude, 'longitude': helmet.longitude},
    )
    return incident


def handle_status_update(helmet_id, data):
    from .models import GPSPoint, Helmet, Incident, Route

    helmet, _ = Helmet.objects.get_or_create(helmet_id=helmet_id)
    helmet.is_connected = True
    helmet.speed = _number(data, 'speed', helmet.speed)
    helmet.latitude = _number(data, 'lat', helmet.latitude)
    helmet.longitude = _number(data, 'lon', helmet.longitude)
    helmet.alcohol_level = _number(data, 'alc', helmet.alcohol_level)
    helmet.tilt_angle = _number(data, 'tilt', helmet.tilt_angle)
    helmet.battery_level = _number(data, 'bat', helmet.battery_level, int)
    helmet.latency_ms = _number(data, 'latency', helmet.latency_ms, int)
    helmet.signal_strength = _number(data, 'rssi', helmet.signal_strength, int)
    helmet.is_worn = _flag(data, 'worn', helmet.is_worn)
    helmet.is_strapped = _flag(data, 'strapped', helmet.is_strapped)

    if data.get('crash') is True:
        _open_incident(helmet, 'CRASH')
    if helmet.alcohol_level > _alcohol_limit():
        _open_incident(helmet, 'ALC')

    # Priority: Accident > Drunk > explicit firmware state > Online
    if Incident.objects.filter(helmet=helmet, type='CRASH', resolved=False).exists():
        helmet.state = 'Accident'
    elif helmet.alcohol_level > _alcohol_limit():
        helmet.state = 'Drunk'
    elif isinstance(data.get('state'), str) and data['state'].strip():
        helmet.state = data['state'].strip()[:20]
    else:
        helmet.state = 'Online'
    helmet.save()

    if helmet.rider and 'lat' in data and 'lon' in data:
        route = Route.objects.filter(rider=helmet.rider, is_active=True).first() or Route.objects.create(rider=helmet.rider)
        GPSPoint.objects.create(
            route=route,
            latitude=helmet.latitude,
            longitude=helmet.longitude,
            speed=helmet.speed,
            alcohol_level=helmet.alcohol_level,
            tilt_angle=helmet.tilt_angle,
        )
    return helmet


def settings_payload():
    from .models import SystemSettings
    config = SystemSettings.objects.first() or SystemSettings.objects.create()
    return {
        'allowed_alcohol_level': config.allowed_alcohol_level,
        'speed_limit': config.speed_limit,
        'refresh_rate': config.map_refresh_rate_seconds,
    }


def handle_request(client, helmet_id, data):
    if data.get('type') == 'get_settings':
        client.publish(f'helmet/{helmet_id}/settings', json.dumps(settings_payload()), qos=1)


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        logger.info('Connected to MQTT broker.')
        client.subscribe([(STATUS_TOPIC, 1), (REQUEST_TOPIC, 1)])
    else:
        logger.error('MQTT connection refused: %s', reason_code)


def on_message(client, userdata, msg):
    close_old_connections()
    try:
        parts = msg.topic.split('/')
        if len(parts) != 3 or not HELMET_ID_PATTERN.match(parts[1]):
            return
        payload = json.loads(msg.payload.decode())
        if not isinstance(payload, dict):
            return
        if parts[2] == 'status':
            handle_status_update(parts[1], payload)
        elif parts[2] == 'request':
            handle_request(client, parts[1], payload)
    except (UnicodeDecodeError, ValueError):
        logger.warning('Ignoring malformed MQTT payload on %s', msg.topic)
    except Exception:
        logger.exception('Error processing MQTT message on %s', msg.topic)


def broker_address():
    """Host and port saved on the Settings page win, unless they are still the defaults."""
    from .models import SystemSettings
    host, port = settings.DASHBOARD_MQTT_BROKER_HOST, settings.DASHBOARD_MQTT_BROKER_PORT
    try:
        config = SystemSettings.objects.first()
    except Exception:
        config = None
    if config and (config.mqtt_broker_host or '').strip() not in PLACEHOLDER_HOSTS:
        host, port = config.mqtt_broker_host.strip(), config.mqtt_broker_port or port
    return host, port


def _mqtt_loop():
    while True:
        host, port = broker_address()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='django-backend-listener')
        if settings.DASHBOARD_MQTT_USERNAME:
            client.username_pw_set(settings.DASHBOARD_MQTT_USERNAME, settings.DASHBOARD_MQTT_PASSWORD)
        client.on_connect = on_connect
        client.on_message = on_message
        try:
            logger.info('Connecting to MQTT at %s:%s', host, port)
            client.connect(host, int(port), settings.DASHBOARD_MQTT_KEEPALIVE_SECONDS)
            client.loop_forever()
        except Exception as exc:
            logger.error('MQTT connection error: %s. Retrying.', exc)
        time.sleep(settings.DASHBOARD_MQTT_RECONNECT_DELAY_SECONDS)


def start_mqtt_listener():
    global _mqtt_started
    with _mqtt_lock:
        if _mqtt_started:
            return
        _mqtt_started = True
    threading.Thread(target=_mqtt_loop, daemon=True, name='mqtt-listener').start()
    logger.info('MQTT listener thread started.')
