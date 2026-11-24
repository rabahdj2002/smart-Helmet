import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from dashboard import mqtt_listener
from dashboard.models import GPSPoint, Helmet, Incident, Rider, Route, SystemSettings

import simulate_telemetry


class AuthenticatedTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('admin', 'admin@example.com', 'pw-12345')
        cls.rider = Rider.objects.create(name='Karim', email='karim@example.com', bike_id='H1')
        cls.helmet = Helmet.objects.create(helmet_id='H1', rider=cls.rider, latitude=36.36, longitude=6.61)

    def setUp(self):
        self.client.force_login(self.admin)


class PageTests(AuthenticatedTestCase):
    def test_all_pages_render(self):
        incident = Incident.objects.create(rider=self.rider, helmet=self.helmet, type='CRASH', latitude=1, longitude=2)
        urls = [
            reverse('dashboard'), reverse('riders'), reverse('riders') + '?q=kar', reverse('rider_detail', args=[self.rider.id]),
            reverse('incidents'), reverse('incidents') + f'?id={incident.id}', reverse('mqtt_docs'),
            reverse('settings'), reverse('add_rider'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_pages_require_login(self):
        self.client.logout()
        for name in ['dashboard', 'riders', 'incidents', 'mqtt_docs', 'settings', 'add_rider', 'live_data']:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])

    def test_header_shows_connected_count_on_every_page(self):
        Helmet.objects.filter(pk='H1').update(is_connected=True)
        self.assertContains(self.client.get(reverse('incidents')), 'Online - 1 devices')

    def test_map_tiles_get_a_referer(self):
        self.assertEqual(self.client.get(reverse('dashboard'))['Referrer-Policy'], 'strict-origin-when-cross-origin')

    def test_unknown_rider_is_404(self):
        self.assertEqual(self.client.get(reverse('rider_detail', args=[999])).status_code, 404)

    def test_invalid_query_params_do_not_crash(self):
        self.assertEqual(self.client.get(reverse('incidents') + '?id=abc').status_code, 200)
        self.assertEqual(self.client.get(reverse('live_data') + '?rider_id=abc').json(), [])
        self.assertEqual(self.client.post(reverse('incidents'), {'toggle_id': 'abc'}).status_code, 404)

    def test_login_and_logout(self):
        self.client.logout()
        response = self.client.post(reverse('login'), {'username': 'admin', 'password': 'pw-12345'})
        self.assertRedirects(response, reverse('dashboard'))
        self.assertRedirects(self.client.post(reverse('logout')), reverse('login'))


class LiveDataTests(AuthenticatedTestCase):
    def test_live_data_includes_rider_id_and_history(self):
        route = Route.objects.create(rider=self.rider)
        GPSPoint.objects.create(route=route, latitude=1, longitude=2, speed=5)
        data = self.client.get(reverse('live_data') + f'?rider_id={self.rider.id}').json()
        self.assertEqual(data[0]['rider_id'], self.rider.id)
        self.assertEqual(len(data[0]['history']), 1)

    @override_settings(HELMET_OFFLINE_AFTER_SECONDS=30)
    def test_stale_helmet_is_reported_offline(self):
        Helmet.objects.filter(pk='H1').update(is_connected=True, state='Online', last_seen=timezone.now() - timedelta(minutes=5))
        data = self.client.get(reverse('live_data')).json()
        self.assertFalse(data[0]['is_connected'])
        self.assertEqual(data[0]['state'], 'Offline')

    def test_fresh_helmet_stays_online(self):
        mqtt_listener.handle_status_update('H1', {'lat': 1, 'lon': 2})
        self.assertTrue(self.client.get(reverse('live_data')).json()[0]['is_connected'])


class FormTests(AuthenticatedTestCase):
    def test_add_rider_assigns_helmet(self):
        response = self.client.post(reverse('add_rider'), {'name': 'Amel', 'email': 'amel@example.com', 'helmet_id': 'H2'})
        self.assertRedirects(response, reverse('riders'))
        self.assertEqual(Helmet.objects.get(pk='H2').rider.name, 'Amel')

    def test_add_rider_claims_unassigned_helmet(self):
        Helmet.objects.create(helmet_id='H9')
        self.client.post(reverse('add_rider'), {'name': 'Sofia', 'email': 'sofia@example.com', 'helmet_id': 'H9'})
        self.assertEqual(Helmet.objects.get(pk='H9').rider.name, 'Sofia')

    def test_add_rider_rejects_duplicates_and_blanks(self):
        cases = [
            {'name': 'X', 'email': 'KARIM@example.com', 'helmet_id': 'H5'},
            {'name': 'X', 'email': 'x@example.com', 'helmet_id': 'H1'},
            {'name': '', 'email': '', 'helmet_id': ''},
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.client.post(reverse('add_rider'), data)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.context['errors'])
        self.assertEqual(Rider.objects.count(), 1)

    def test_settings_saves_valid_values(self):
        response = self.client.post(reverse('settings'), {
            'platform_name': 'HALO', 'mqtt_broker_host': 'broker.example.com', 'mqtt_broker_port': '8883',
            'map_refresh_rate_seconds': '10', 'allowed_alcohol_level': '0.3', 'speed_limit': '25', 'mqtt_use_ssl': 'on',
        })
        self.assertRedirects(response, reverse('settings'))
        config = SystemSettings.objects.get(id=1)
        self.assertEqual((config.mqtt_broker_host, config.mqtt_broker_port, config.allowed_alcohol_level), ('broker.example.com', 8883, 0.3))

    def test_settings_rejects_invalid_values_without_saving(self):
        for data in [{'speed_limit': 'fast'}, {'map_refresh_rate_seconds': 'abc'}, {'allowed_alcohol_level': '-1'}]:
            with self.subTest(data=data):
                self.assertEqual(self.client.post(reverse('settings'), data).status_code, 400)
        self.assertEqual(SystemSettings.objects.get(id=1).platform_name, 'HeisenHelmet')

    def test_settings_blank_numbers_keep_current_values(self):
        self.assertRedirects(self.client.post(reverse('settings'), {'platform_name': 'HALO'}), reverse('settings'))
        self.assertEqual(SystemSettings.objects.get(id=1).map_refresh_rate_seconds, 5)

    def test_toggle_incident(self):
        incident = Incident.objects.create(rider=self.rider, helmet=self.helmet, type='ALC', latitude=1, longitude=2)
        self.client.post(reverse('incidents'), {'toggle_id': incident.id})
        incident.refresh_from_db()
        self.assertTrue(incident.resolved)


class MqttIngestionTests(TestCase):
    def setUp(self):
        self.rider = Rider.objects.create(name='Karim', email='karim@example.com', bike_id='H1')
        self.helmet = Helmet.objects.create(helmet_id='H1', rider=self.rider)

    def _message(self, topic, payload):
        msg = MagicMock(topic=topic)
        msg.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return msg

    def test_status_updates_helmet_and_records_route(self):
        payload = simulate_telemetry.telemetry(0, 0, __import__('random').Random(1))
        mqtt_listener.on_message(MagicMock(), None, self._message('helmet/H1/status', payload))
        helmet = Helmet.objects.get(pk='H1')
        self.assertEqual((helmet.latitude, helmet.state, helmet.is_connected), (payload['lat'], 'Online', True))
        self.assertEqual(GPSPoint.objects.filter(route__rider=self.rider).count(), 1)

    def test_telemetry_does_not_overwrite_rider_assigned_meanwhile(self):
        stale = Helmet.objects.create(helmet_id='H7')
        newcomer = Rider.objects.create(name='Amel', email='amel@example.com', bike_id='H7')
        Helmet.objects.filter(pk='H7').update(rider=newcomer)
        original_get_or_create = Helmet.objects.get_or_create
        Helmet.objects.get_or_create = lambda **kwargs: (stale, False)
        try:
            mqtt_listener.handle_status_update('H7', {'lat': 1, 'lon': 2})
        finally:
            Helmet.objects.get_or_create = original_get_or_create
        self.assertEqual(Helmet.objects.get(pk='H7').rider, newcomer)

    def test_unknown_helmet_is_registered_without_rider(self):
        mqtt_listener.handle_status_update('NEW', {'alc': 0.9, 'crash': True})
        self.assertEqual(Helmet.objects.get(pk='NEW').state, 'Drunk')
        self.assertFalse(Incident.objects.exists())

    def test_alcohol_above_limit_opens_one_incident(self):
        for _ in range(3):
            mqtt_listener.handle_status_update('H1', {'alc': 0.8})
        self.assertEqual(Incident.objects.filter(type='ALC', resolved=False).count(), 1)
        self.assertEqual(Helmet.objects.get(pk='H1').state, 'Drunk')

    def test_reopened_duplicate_alerts_do_not_block_telemetry(self):
        for _ in range(2):
            Incident.objects.create(rider=self.rider, helmet=self.helmet, type='ALC', latitude=0, longitude=0)
        mqtt_listener.handle_status_update('H1', {'alc': 0.8, 'lat': 5, 'lon': 6})
        self.assertEqual(Helmet.objects.get(pk='H1').latitude, 5)
        self.assertEqual(Incident.objects.filter(type='ALC').count(), 2)

    def test_crash_has_priority_until_resolved(self):
        mqtt_listener.handle_status_update('H1', {'crash': True, 'alc': 0.8})
        self.assertEqual(Helmet.objects.get(pk='H1').state, 'Accident')
        Incident.objects.filter(type='CRASH').update(resolved=True)
        mqtt_listener.handle_status_update('H1', {'alc': 0.0, 'state': 'Cruising'})
        self.assertEqual(Helmet.objects.get(pk='H1').state, 'Cruising')

    def test_malformed_payloads_are_ignored(self):
        client = MagicMock()
        for topic, payload in [
            ('helmet/H1/status', b'not json'), ('helmet/H1/status', b'[1, 2]'), ('helmet', b'{}'),
            ('helmet/<img src=x onerror=alert(1)>/status', {'lat': 1}),
            ('helmet/H1/status', {'lat': 'north', 'bat': None, 'worn': 'yes'}),
        ]:
            mqtt_listener.on_message(client, None, self._message(topic, payload))
        helmet = Helmet.objects.get(pk='H1')
        self.assertEqual((helmet.latitude, helmet.battery_level, helmet.is_worn), (0.0, 100, False))
        self.assertEqual(Helmet.objects.count(), 1)

    def test_settings_request_publishes_response(self):
        SystemSettings.objects.create(id=1, allowed_alcohol_level=0.4, speed_limit=20, map_refresh_rate_seconds=7)
        client = MagicMock()
        mqtt_listener.on_message(client, None, self._message('helmet/H1/request', {'type': 'get_settings'}))
        topic, body = client.publish.call_args.args
        self.assertEqual(topic, 'helmet/H1/settings')
        self.assertEqual(json.loads(body), {'allowed_alcohol_level': 0.4, 'speed_limit': 20.0, 'refresh_rate': 7})

    @override_settings(DASHBOARD_MQTT_BROKER_HOST='mqtt', DASHBOARD_MQTT_BROKER_PORT=1883)
    def test_broker_address_prefers_saved_host_over_default(self):
        SystemSettings.objects.create(id=1)
        self.assertEqual(mqtt_listener.broker_address(), ('mqtt', 1883))
        SystemSettings.objects.filter(id=1).update(mqtt_broker_host='broker.example.com', mqtt_broker_port=8883)
        self.assertEqual(mqtt_listener.broker_address(), ('broker.example.com', 8883))


@override_settings(DASHBOARD_MQTT_BROKER_HOST='mqtt', DASHBOARD_MQTT_USERNAME='svc', DASHBOARD_MQTT_PASSWORD='secret')
class BrokerClientTests(TestCase):
    def test_credentials_only_sent_to_configured_broker(self):
        with patch.object(mqtt_listener.mqtt, 'Client') as client_cls:
            mqtt_listener.build_client('mqtt')
            client_cls.return_value.username_pw_set.assert_called_once_with('svc', 'secret')
            client_cls.reset_mock()
            mqtt_listener.build_client('attacker.example.com')
            client_cls.return_value.username_pw_set.assert_not_called()
            self.assertIs(client_cls.call_args.kwargs['clean_session'], False)


class SeedDemoTests(TestCase):
    def test_seed_demo_is_idempotent_and_pages_render(self):
        call_command('seed_demo', stdout=StringIO())
        call_command('seed_demo', stdout=StringIO())
        self.assertEqual(Rider.objects.count(), 4)
        self.assertEqual(Helmet.objects.filter(rider__isnull=False).count(), 4)
        self.assertTrue(self.client.login(username='demo', password='demo12345'))
        for name in ['dashboard', 'riders', 'incidents', 'live_data']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_seed_demo_skips_existing_data(self):
        Rider.objects.create(name='Real', email='real@example.com', bike_id='R1')
        call_command('seed_demo', stdout=StringIO())
        self.assertEqual(Rider.objects.count(), 1)


class SimulatorTests(TestCase):
    def test_telemetry_matches_protocol(self):
        rng = __import__('random').Random(0)
        normal = simulate_telemetry.telemetry(0, 5, rng)
        self.assertEqual(set(normal), {'lat', 'lon', 'speed', 'alc', 'tilt', 'worn', 'strapped', 'bat', 'rssi', 'latency'})
        self.assertLess(normal['alc'], 0.5)
        self.assertGreater(simulate_telemetry.telemetry(2, 5, rng, drunk=True)['alc'], 0.5)
        self.assertTrue(simulate_telemetry.telemetry(3, 5, rng, crash=True)['crash'])
