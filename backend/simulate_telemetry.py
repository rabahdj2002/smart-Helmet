"""Simulated HALO helmets that speak the real MQTT protocol.

Usage:
    python simulate_telemetry.py --host localhost --helmets 4
    python simulate_telemetry.py --crash HALO-04 --crash-after 30
"""
import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

SITE_CENTER = (36.3650, 6.6147)  # Constantine


def telemetry(index, tick, rng, drunk=False, crash=False):
    """One status payload for helmet `index` at time step `tick`."""
    angle = tick / 20 + index * math.pi / 2
    radius = 0.002 + 0.0005 * index
    payload = {
        'lat': round(SITE_CENTER[0] + radius * math.sin(angle), 6),
        'lon': round(SITE_CENTER[1] + radius * math.cos(angle), 6),
        'speed': round(max(0.0, 4 + rng.uniform(-1.5, 1.5)), 1),
        'alc': round(rng.uniform(0.55, 0.7) if drunk else rng.uniform(0.0, 0.08), 3),
        'tilt': round(rng.uniform(70, 90) if crash else rng.uniform(-10, 10), 1),
        'worn': True,
        'strapped': True,
        'bat': max(5, 100 - index * 7 - tick // 30),
        'rssi': rng.randint(60, 98),
        'latency': rng.randint(15, 60),
    }
    if crash:
        payload['crash'] = True
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=1883)
    parser.add_argument('--helmets', type=int, default=4)
    parser.add_argument('--interval', type=float, default=3.0, help='seconds between status messages')
    parser.add_argument('--drunk', default='HALO-03', help='helmet that reports alcohol above the limit ("" for none)')
    parser.add_argument('--crash', default='', help='helmet that reports a crash once')
    parser.add_argument('--crash-after', type=int, default=20, help='seconds before the crash is reported')
    args = parser.parse_args()

    ids = [f'HALO-{i + 1:02d}' for i in range(args.helmets)]
    rng = random.Random()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='halo-simulator')

    configured = set()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f'Connected to {args.host}:{args.port}, simulating {", ".join(ids)}')
        client.subscribe('helmet/+/settings')

    def on_message(client, userdata, msg):
        configured.add(msg.topic.split('/')[1])
        print(f'{msg.topic}: {msg.payload.decode()}')

    client.on_connect = on_connect
    client.on_message = on_message
    while True:
        try:
            client.connect(args.host, args.port, 60)
            break
        except OSError as exc:
            print(f'Broker not reachable ({exc}), retrying...')
            time.sleep(2)
    client.loop_start()

    started, tick, crashed = time.time(), 0, False
    try:
        while True:
            for index, helmet_id in enumerate(ids):
                if helmet_id not in configured:
                    # Like the firmware: ask for the limits until the platform answers.
                    client.publish(f'helmet/{helmet_id}/request', json.dumps({'type': 'get_settings'}), qos=1)
                crash = helmet_id == args.crash and not crashed and time.time() - started >= args.crash_after
                crashed = crashed or crash
                payload = telemetry(index, tick, rng, drunk=helmet_id == args.drunk, crash=crash)
                client.publish(f'helmet/{helmet_id}/status', json.dumps(payload), qos=1)
            tick += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('Simulation stopped.')
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
