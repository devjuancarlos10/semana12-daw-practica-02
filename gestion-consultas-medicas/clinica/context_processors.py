from django.conf import settings


def sistema(request):
    return {
        'NOMBRE_SISTEMA': 'Consultas Médicas',
        'TIMEOUT_SESION_MIN': settings.SESSION_TIMEOUT_INACTIVIDAD // 60,
    }
