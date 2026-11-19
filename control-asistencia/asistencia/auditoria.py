"""Contexto de auditoría por petición (ContextVar) y utilidades."""
import contextvars
import logging

from django.conf import settings

_request_actual = contextvars.ContextVar('request_auditoria', default=None)

logger = logging.getLogger('auditoria')
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
    """(usuario, ip, ruta) de la petición en curso; (None, None, '') fuera de una petición."""
    request = _request_actual.get()
    if request is None:
        return None, None, ''
    user = getattr(request, 'user', None)
    usuario = user if user is not None and user.is_authenticated else None
    return usuario, obtener_ip(request), request.path[:255]


def registrar(accion, modelo, objeto='', objeto_id='', cambios=None, usuario=None, request=None,
              username=''):
    """Crea un RegistroAuditoria con el contexto disponible."""
    from .models import RegistroAuditoria

    ctx_usuario, ip, ruta = contexto_actual()
    if request is not None:
        ip, ruta = obtener_ip(request), request.path[:255]
    usuario = usuario or ctx_usuario
    registro = RegistroAuditoria.objects.create(
        usuario=usuario, username=username or getattr(usuario, 'username', ''),
        accion=accion, modelo=modelo, objeto=str(objeto)[:200], objeto_id=str(objeto_id),
        cambios=cambios or {}, ip=ip, ruta=ruta,
    )
    nivel = logging.WARNING if accion == 'seguridad' else logging.INFO
    (logger_seguridad if accion in ('seguridad', 'sesion') else logger).log(
        nivel, '%s %s #%s | usuario=%s ip=%s | %s',
        accion.upper(), modelo, objeto_id or '-', registro.username or 'sistema', ip,
        ', '.join(cambios) if cambios else objeto,
    )
    return registro
