"""Utilidades de auditoría.

Las vistas y señales *marcan* acciones críticas sobre el request con
`marcar_accion()`. El `AuditoriaMiddleware` las persiste al final del ciclo
request/response, de modo que una acción solo se registra si la respuesta fue
exitosa y toda la escritura de auditoría queda centralizada en un único punto.
"""
import logging

from django.conf import settings

logger = logging.getLogger('auditoria')
logger_seguridad = logging.getLogger('seguridad')

ATRIBUTO_ACCIONES = '_acciones_auditoria'


def obtener_ip(request):
    """IP del cliente. X-Forwarded-For solo se usa detrás de un proxy confiable."""
    if getattr(settings, 'USAR_X_FORWARDED_FOR', False):
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR')
        if reenviada:
            return reenviada.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def marcar_accion(request, accion, descripcion='', usuario=None, inmediata=False):
    """Agenda una acción para que el middleware la registre.

    `inmediata=True` la persiste en el acto (p. ej. eventos que no deben
    perderse aunque la respuesta sea un error, como un login fallido).
    """
    datos = {'accion': accion, 'descripcion': descripcion[:255], 'usuario': usuario}
    if inmediata:
        persistir(request, **datos)
        return
    acciones = getattr(request, ATRIBUTO_ACCIONES, None)
    if acciones is None:
        acciones = []
        setattr(request, ATRIBUTO_ACCIONES, acciones)
    acciones.append(datos)


def persistir(request, accion, descripcion='', usuario=None):
    from .models import RegistroAuditoria

    if usuario is None:
        user = getattr(request, 'user', None)
        usuario = user if user is not None and user.is_authenticated else None
    username = getattr(usuario, 'username', '') if usuario else ''
    registro = RegistroAuditoria.objects.create(
        usuario=usuario,
        username=username,
        accion=accion,
        descripcion=descripcion,
        ip=obtener_ip(request),
        metodo=request.method,
        ruta=request.path[:255],
    )
    nivel_logger = logger_seguridad if registro.es_evento_seguridad else logger
    nivel = logging.WARNING if registro.es_evento_seguridad else logging.INFO
    nivel_logger.log(
        nivel, 'usuario=%s ip=%s accion=%s %s %s | %s',
        username or 'anónimo', registro.ip, accion, request.method, request.path, descripcion,
    )
    return registro
