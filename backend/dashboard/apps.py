import os
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
        command = sys.argv[1] if len(sys.argv) > 1 else ''
        if command in SKIP_COMMANDS:
            return
        # runserver's autoreloader parent also calls ready(); only the serving child should listen.
        if command == 'runserver' and os.environ.get('RUN_MAIN') != 'true' and '--noreload' not in sys.argv:
            return
        from . import mqtt_listener
        mqtt_listener.start_mqtt_listener()
