from django.conf import settings


def sistema(request):
    contexto = {
        'NOMBRE_SISTEMA': 'TecnoAndes · Asistencia',
        'TIMEOUT_SESION_MIN': settings.SESSION_TIMEOUT_INACTIVIDAD // 60,
    }
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        contexto['NOTIFICACIONES_NUEVAS'] = user.notificaciones.filter(leida=False).count()
    return contexto
