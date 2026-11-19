"""Middlewares propios del sistema.

Orden (ver settings.MIDDLEWARE): en el request se ejecutan de arriba hacia
abajo y en el response en orden inverso. `AuditoriaMiddleware` va primero para
envolver a los demás y poder registrar también lo que ellos marquen (p. ej. la
expiración de sesión).
"""
import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url

from .auditoria import ATRIBUTO_ACCIONES, marcar_accion, obtener_ip, persistir
from .models import Prestamo, RegistroAuditoria

logger_peticiones = logging.getLogger('peticiones')
logger_seguridad = logging.getLogger('seguridad')

CLAVE_ULTIMA_ACTIVIDAD = 'ultima_actividad'
CLAVE_ALERTA_VENCIDOS = 'alerta_vencidos_verificada'
RUTAS_ESTATICAS = ('/static/', '/favicon.ico')

Accion = RegistroAuditoria.Accion


def _es_estatica(request):
    return request.path.startswith(RUTAS_ESTATICAS)


class AuditoriaMiddleware:
    """Registra acciones críticas e intentos de acceso no autorizado.

    - Request: marca el inicio para medir la duración.
    - Response: persiste las acciones marcadas por vistas/señales (solo si la
      respuesta fue exitosa), detecta respuestas 403 y redirecciones al login
      de usuarios anónimos, y escribe una línea de log por petición.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.url_login = resolve_url(settings.LOGIN_URL)

    def __call__(self, request):
        inicio = time.perf_counter()
        response = self.get_response(request)
        if _es_estatica(request):
            return response

        self._detectar_accesos_no_autorizados(request, response)
        exitosa = response.status_code < 400
        for accion in getattr(request, ATRIBUTO_ACCIONES, []):
            if exitosa or accion['accion'] in RegistroAuditoria.ACCIONES_SEGURIDAD:
                persistir(request, **accion)

        duracion_ms = (time.perf_counter() - inicio) * 1000
        usuario = request.user.username if request.user.is_authenticated else 'anónimo'
        logger_peticiones.info(
            '%s %s -> %s | usuario=%s ip=%s | %.1f ms',
            request.method, request.path, response.status_code,
            usuario, obtener_ip(request), duracion_ms,
        )
        return response

    def _detectar_accesos_no_autorizados(self, request, response):
        if response.status_code == 403:
            marcar_accion(request, Accion.ACCESO_DENEGADO, f'{request.method} {request.path}')
        elif (
            response.status_code == 302
            and not request.user.is_authenticated
            and not getattr(request, '_sesion_expirada', False)
            and response.get('Location', '').startswith(f'{self.url_login}?')
        ):
            marcar_accion(request, Accion.ACCESO_ANONIMO, f'Intento de abrir {request.path}')


class InactividadSesionMiddleware:
    """Cierra la sesión tras `SESSION_TIMEOUT_INACTIVIDAD` segundos sin actividad.

    Complementa a SESSION_COOKIE_AGE (que hace expirar la cookie) mostrando un
    mensaje explícito y dejando constancia en la auditoría.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.limite = getattr(settings, 'SESSION_TIMEOUT_INACTIVIDAD', 1800)

    def __call__(self, request):
        if request.user.is_authenticated and not _es_estatica(request):
            ahora = time.time()
            ultima = request.session.get(CLAVE_ULTIMA_ACTIVIDAD)
            if ultima is not None and ahora - ultima > self.limite:
                usuario = request.user
                request._sesion_expirada = True
                logout(request)
                marcar_accion(
                    request, Accion.SESION_EXPIRADA,
                    f'Inactivo {int((ahora - ultima) // 60)} min', usuario=usuario,
                )
                messages.warning(
                    request,
                    f'Su sesión se cerró tras {self.limite // 60} minutos de inactividad. '
                    'Ingrese nuevamente.',
                )
                return redirect_to_login(request.get_full_path())
            request.session[CLAVE_ULTIMA_ACTIVIDAD] = ahora
        return self.get_response(request)


class AlertaPrestamosVencidosMiddleware:
    """Al iniciar sesión verifica préstamos vencidos y muestra una alerta.

    La verificación se hace una sola vez por sesión (bandera en la sesión,
    que se reinicia en cada login mediante la señal user_logged_in).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.method == 'GET'
            and not _es_estatica(request)
            and not request.session.get(CLAVE_ALERTA_VENCIDOS)
        ):
            self._alertar(request)
            request.session[CLAVE_ALERTA_VENCIDOS] = True
        return self.get_response(request)

    @staticmethod
    def _alertar(request):
        user = request.user
        vencidos = Prestamo.objects.vencidos()
        if user.es_docente:
            propios = list(vencidos.filter(docente=user).select_related('equipo'))
            if propios:
                detalle = ', '.join(
                    f'{p.equipo.codigo_patrimonial} ({p.dias_retraso} d)' for p in propios
                )
                messages.error(
                    request,
                    f'Tiene {len(propios)} préstamo(s) vencido(s): {detalle}. '
                    'Regularice la devolución a la brevedad.',
                )
                logger_seguridad.warning(
                    'usuario=%s tiene %s préstamo(s) vencido(s)', user.username, len(propios)
                )
        elif user.has_perm('prestamos.ver_todos_prestamos'):
            total = vencidos.count()
            if total:
                messages.warning(
                    request, f'Hay {total} préstamo(s) vencido(s) pendientes de devolución.'
                )


class CabecerasSeguridadMiddleware:
    """Añade cabeceras de seguridad que Django no incluye por defecto."""

    CSP = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', self.CSP)
        response.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        if request.user.is_authenticated:
            # Evita que el botón "atrás" muestre páginas privadas tras el logout.
            response['Cache-Control'] = 'no-store'
        return response
