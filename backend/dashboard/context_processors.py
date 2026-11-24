from .models import Helmet, Incident, SystemSettings


def app_settings(request):
    return {
        'app_settings': SystemSettings.objects.first(),
        'global_critical_incidents': Incident.objects.filter(resolved=False).count(),
        'connected_helmets': Helmet.objects.filter(is_connected=True).count(),
    }
