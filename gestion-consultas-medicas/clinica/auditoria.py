"""Contexto de auditoría por petición y utilidades de registro.

El `ContextoAuditoriaMiddleware` guarda el request actual en una ContextVar
(segura para hilos y asyncio). Así las señales de modelo pueden saber QUIÉN
hizo un cambio y desde qué IP sin acoplar los modelos a las vistas.
"""
import contextvars
import logging

from django.conf import settings

_request_actual = contextvars.ContextVar('request_auditoria', default=None)

logger_medico = logging.getLogger('auditoria.medica')
logger_cambios = logging.getLogger('auditoria.cambios')
logger_seguridad = logging.getLogger('seguridad')


def obtener_ip(request):
    if request is None:
        return None
    if getattr(settings, 'USAR_X_FORWARDED_FOR', False):
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR')
        if reenviada:
            return reenviada.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def establecer_request(request):
    return _request_actual.set(request)


def limpiar_request(token):
    _request_actual.reset(token)


def contexto_actual():
    """(usuario autenticado o None, ip) de la petición en curso."""
    request = _request_actual.get()
    if request is None:
        return None, None
    user = getattr(request, 'user', None)
    usuario = user if user is not None and user.is_authenticated else None
    return usuario, obtener_ip(request)


def registrar_acceso_historial(request, recurso, resultado, paciente=None):
    from .models import AccesoHistorial

    user = request.user
    autenticado = user.is_authenticated
    acceso = AccesoHistorial.objects.create(
        usuario=user if autenticado else None,
        username=user.username if autenticado else 'anónimo',
        rol=getattr(user, 'rol', '') if autenticado else '',
        paciente=paciente,
        recurso=recurso,
        resultado=resultado,
        ip=obtener_ip(request),
        ruta=request.path[:255],
    )
    nivel = logging.INFO if resultado == AccesoHistorial.Resultado.PERMITIDO else logging.WARNING
    logger_medico.log(
        nivel, 'ACCESO %s | usuario=%s rol=%s ip=%s | recurso=%s paciente=%s',
        resultado.upper(), acceso.username, acceso.rol or '-', acceso.ip, recurso,
        paciente.dni if paciente else '-',
    )
    return acceso
