from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import GPSPoint, Helmet, Incident, Rider, Route, SystemSettings

WORKERS = [
    ('Karim Bouzid', 'karim.bouzid@halo.demo'),
    ('Amel Saadi', 'amel.saadi@halo.demo'),
    ('Yacine Mebarki', 'yacine.mebarki@halo.demo'),
    ('Sofia Rahal', 'sofia.rahal@halo.demo'),
]
SITE_CENTER = (36.3650, 6.6147)


class Command(BaseCommand):
    help = 'Load demo workers, helmets, route history and incidents, plus a demo admin (skipped if riders exist).'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='demo')
        parser.add_argument('--password', default='demo12345')

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.filter(username=options['username']).exists():
            User.objects.create_superuser(options['username'], 'demo@halo.demo', options['password'])
        SystemSettings.objects.get_or_create(id=1, defaults={'platform_name': 'HALO Safety'})

        if Rider.objects.exists():
            self.stdout.write('Existing riders found, skipping demo data.')
            return
        with transaction.atomic():
            self._load()
        self.stdout.write(self.style.SUCCESS(
            f'Demo data loaded. Log in as {options["username"]} / {options["password"]}.'
        ))

    def _load(self):
        now = timezone.now()
        for index, (name, email) in enumerate(WORKERS):
            helmet_id = f'HALO-{index + 1:02d}'
            rider = Rider.objects.create(name=name, email=email, bike_id=helmet_id)
            lat, lon = SITE_CENTER[0] + 0.001 * index, SITE_CENTER[1] - 0.001 * index
            helmet = Helmet.objects.create(
                helmet_id=helmet_id, rider=rider, latitude=lat, longitude=lon,
                battery_level=92 - index * 7, is_worn=True, is_strapped=True, state='Offline',
            )
            for day in (2, 1):
                start = now - timedelta(days=day, hours=index)
                route = Route.objects.create(rider=rider, start_time=start, end_time=start + timedelta(hours=1), is_active=False)
                for step in range(20):
                    point = GPSPoint.objects.create(
                        route=route, latitude=lat + 0.0001 * step, longitude=lon + 0.00008 * step,
                        speed=3.5 + (step % 4) * 0.4, alcohol_level=0.02, tilt_angle=(step % 5) - 2,
                    )
                    GPSPoint.objects.filter(pk=point.pk).update(timestamp=start + timedelta(minutes=3 * step))
            if index == 1:
                incident = Incident.objects.create(rider=rider, helmet=helmet, type='CRASH', latitude=lat, longitude=lon, resolved=True)
                Incident.objects.filter(pk=incident.pk).update(timestamp=now - timedelta(days=1, hours=3))
