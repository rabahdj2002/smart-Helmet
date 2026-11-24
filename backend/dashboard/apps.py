import sys

from django.apps import AppConfig
from django.conf import settings

SKIP_COMMANDS = {'test', 'migrate', 'makemigrations', 'collectstatic', 'check', 'shell', 'seed_demo', 'createsuperuser'}


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'

    def ready(self):
        if not settings.DASHBOARD_MQTT_ENABLED:
            return
        if len(sys.argv) > 1 and sys.argv[1] in SKIP_COMMANDS:
            return
        from . import mqtt_listener
        mqtt_listener.start_mqtt_listener()
