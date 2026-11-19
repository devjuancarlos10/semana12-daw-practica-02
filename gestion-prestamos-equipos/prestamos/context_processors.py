from django.conf import settings


def sistema(request):
    """Variables globales disponibles en todas las plantillas."""
    return {
        'NOMBRE_SISTEMA': 'Préstamos de Equipos',
        'TIMEOUT_SESION_MIN': settings.SESSION_TIMEOUT_INACTIVIDAD // 60,
    }
