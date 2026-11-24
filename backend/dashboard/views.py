from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import GPSPoint, Helmet, Incident, Rider, Route, SystemSettings


def mark_stale_helmets_offline():
    cutoff = timezone.now() - timedelta(seconds=settings.HELMET_OFFLINE_AFTER_SECONDS)
    Helmet.objects.filter(is_connected=True, last_seen__lt=cutoff).update(is_connected=False, state='Offline')


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(raw, cast, default, minimum=None):
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return default, False
    if minimum is not None and value < minimum:
        return default, False
    return value, True

@login_required
def dashboard(request):
    mark_stale_helmets_offline()
    total_helmets = Helmet.objects.count()
    connected_helmets = Helmet.objects.filter(is_connected=True).count()
    active_riders = Rider.objects.filter(helmet__is_connected=True).count()
    
    # Network Metrics (Dynamic)
    connected_qs = Helmet.objects.filter(is_connected=True)
    avg_latency = connected_qs.aggregate(Avg('latency_ms'))['latency_ms__avg'] or 0
    p99_latency = connected_qs.aggregate(Max('latency_ms'))['latency_ms__max'] or 0 # Simplified to max for example
    avg_signal = connected_qs.aggregate(Avg('signal_strength'))['signal_strength__avg'] or 0
    
    # New Dashboard Data
    critical_incidents = Incident.objects.filter(resolved=False).count()
    recent_incidents = Incident.objects.order_by('-timestamp')[:5]
    
    settings = SystemSettings.objects.first()
    refresh_rate = settings.map_refresh_rate_seconds if settings else 5
    
    context = {
        'total_helmets': total_helmets,
        'connected_helmets': connected_helmets,
        'active_riders': active_riders,
        'refresh_rate': refresh_rate,
        'critical_incidents': critical_incidents,
        'recent_incidents': recent_incidents,
        'avg_latency': round(avg_latency, 1),
        'p99_latency': p99_latency,
        'avg_signal': round(avg_signal, 1),
    }
    return render(request, 'dashboard/index.html', context)

@login_required
def incidents_list(request):
    incident_id = request.GET.get('id')
    if request.method == 'POST' and 'toggle_id' in request.POST:
        inc = get_object_or_404(Incident, id=_int_or_none(request.POST.get('toggle_id')))
        inc.resolved = not inc.resolved
        inc.save()
        return redirect(f"{reverse('incidents')}?id={inc.id}")

    incidents = Incident.objects.select_related('rider', 'helmet').order_by('-timestamp')
    selected_incident = None
    if _int_or_none(incident_id) is not None:
        selected_incident = Incident.objects.filter(id=int(incident_id)).first()
        
    return render(request, 'dashboard/incidents.html', {
        'incidents': incidents,
        'selected_incident': selected_incident
    })

@login_required
def riders_list(request):
    query = request.GET.get('q')
    if query:
        riders = Rider.objects.filter(
            Q(name__icontains=query) | 
            Q(helmet__helmet_id__icontains=query)
        )
    else:
        riders = Rider.objects.all()
    return render(request, 'dashboard/riders.html', {'riders': riders, 'query': query})

@login_required
def rider_detail(request, rider_id):
    rider = get_object_or_404(Rider, id=rider_id)
    routes = Route.objects.filter(rider=rider).order_by('-start_time')
    return render(request, 'dashboard/rider_detail.html', {'rider': rider, 'routes': routes})

@login_required
def live_data(request):
    mark_stale_helmets_offline()
    rider_id = request.GET.get('rider_id')
    if rider_id:
        helmets = Helmet.objects.select_related('rider').filter(rider_id=_int_or_none(rider_id))
    else:
        helmets = Helmet.objects.select_related('rider')
    
    data = []
    for h in helmets:
        # Get history for graphs if specific rider
        history = []
        if rider_id:
            # Fix: GPSPoint filters by route, then route filters by rider
            active_route = Route.objects.filter(rider=h.rider, is_active=True).first()
            if active_route:
                points = GPSPoint.objects.filter(route=active_route).order_by('-timestamp')[:20]
                for p in points:
                    history.append({
                        'time': p.timestamp.strftime('%H:%M:%S'),
                        'speed': p.speed,
                        'alc': p.alcohol_level or 0,
                        'tilt': p.tilt_angle or 0
                    })
                history.reverse()

        data.append({
            'id': h.helmet_id,
            'rider': h.rider.name if h.rider else 'Unassigned',
            'rider_id': h.rider_id,
            'lat': h.latitude,
            'lon': h.longitude,
            'speed': h.speed,
            'alc': h.alcohol_level,
            'worn': h.is_worn,
            'bat': h.battery_level,
            'state': h.state,
            'tilt': h.tilt_angle,
            'strapped': h.is_strapped,
            'is_connected': h.is_connected,
            'latency': h.latency_ms,
            'signal': h.signal_strength,
            'history': history
        })
    return JsonResponse(data, safe=False)

@login_required
def mqtt_docs(request):
    return render(request, 'dashboard/mqtt_docs.html')

@login_required
def system_settings(request):
    settings_obj, _ = SystemSettings.objects.get_or_create(id=1)
    errors = []
    if request.method == 'POST':
        post = request.POST
        fields = {
            'mqtt_broker_port': (int, 1883, 1),
            'mqtt_websocket_port': (int, 443, 1),
            'map_refresh_rate_seconds': (int, 5, 1),
            'allowed_alcohol_level': (float, 0.5, 0),
            'speed_limit': (float, 60.0, 0),
        }
        for name, (cast, default, minimum) in fields.items():
            raw = post.get(name, '').strip()
            if not raw:
                continue
            value, ok = _number(raw, cast, default, minimum)
            if not ok:
                errors.append(f"{name.replace('_', ' ').capitalize()} must be a number of at least {minimum}.")
            setattr(settings_obj, name, value)
        settings_obj.platform_name = post.get('platform_name', '').strip() or 'HeisenHelmet'
        settings_obj.mqtt_broker_host = post.get('mqtt_broker_host', '').strip() or 'localhost'
        settings_obj.mqtt_topic_helmet_status = post.get('mqtt_topic_helmet_status', '').strip() or 'helmet/+/status'
        settings_obj.mqtt_topic_helmet_command = post.get('mqtt_topic_helmet_command', '').strip() or 'helmet/+/command'
        settings_obj.mqtt_websocket_host = post.get('mqtt_websocket_host', '').strip()
        settings_obj.mqtt_use_ssl = post.get('mqtt_use_ssl') == 'on'
        if not errors:
            settings_obj.save()
            return redirect('settings')
    return render(request, 'dashboard/settings.html', {'settings': settings_obj, 'errors': errors}, status=400 if errors else 200)


@login_required
def add_rider(request):
    errors = []
    form = {}
    if request.method == 'POST':
        form = {key: request.POST.get(key, '').strip() for key in ('name', 'email', 'helmet_id')}
        if not all(form.values()):
            errors.append('Name, email and helmet ID are required.')
        if form['email'] and Rider.objects.filter(email__iexact=form['email']).exists():
            errors.append('A rider with this email already exists.')
        if form['helmet_id'] and (
            Rider.objects.filter(bike_id=form['helmet_id']).exists()
            or Helmet.objects.filter(helmet_id=form['helmet_id'], rider__isnull=False).exists()
        ):
            errors.append('This helmet is already assigned to a rider.')
        if not errors:
            rider = Rider.objects.create(name=form['name'], email=form['email'], bike_id=form['helmet_id'])
            helmet, _ = Helmet.objects.get_or_create(helmet_id=form['helmet_id'])
            helmet.rider = rider
            helmet.save(update_fields=['rider'])
            return redirect('riders')
    return render(request, 'dashboard/add_rider.html', {'errors': errors, 'form': form}, status=400 if errors else 200)
