"""Middlewares de control y auditoría."""
import logging
import time
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .auditoria import establecer_request, limpiar_request, obtener_ip, registrar
from .models import Asistencia, RegistroAuditoria

logger_peticiones = logging.getLogger('peticiones')
logger_seguridad = logging.getLogger('seguridad')

CLAVE_ULTIMA_ACTIVIDAD = 'ultima_actividad'
CLAVE_RECORDATORIO = 'recordatorio_asistencia_mostrado'
RUTAS_ESTATICAS = ('/static/', '/favicon.ico')
METODOS_MODIFICACION = ('POST', 'PUT', 'PATCH', 'DELETE')


def _es_estatica(request):
    return request.path.startswith(RUTAS_ESTATICAS)


class AuditoriaMiddleware:
    """Middleware de auditoría.

    - Publica el request en una ContextVar: las señales de modelo registran
      todas las modificaciones de asistencia y permisos con usuario, IP y hora.
    - Registra cada respuesta 403 como intento de acceso no autorizado.
    - Escribe en consola una línea por petición (las de modificación, en INFO).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = establecer_request(request)
        inicio = time.perf_counter()
        try:
            response = self.get_response(request)
            if response.status_code == 403:
                registrar(RegistroAuditoria.Accion.SEGURIDAD, 'Acceso',
                          f'Acceso denegado a {request.method} {request.path}')
        finally:
            limpiar_request(token)
        if not _es_estatica(request):
            usuario = request.user.username if request.user.is_authenticated else 'anónimo'
            logger_peticiones.info(
                '%s %s -> %s | usuario=%s ip=%s | %.1f ms%s',
                request.method, request.path, response.status_code, usuario, obtener_ip(request),
                (time.perf_counter() - inicio) * 1000,
                ' | MODIFICACIÓN' if request.method in METODOS_MODIFICACION else '',
            )
        return response


class BloqueoCuentaMiddleware:
    """Impide el acceso de usuarios cuya cuenta de empleado fue desactivada.

    Actúa incluso sobre sesiones ya abiertas: si RRHH desactiva la cuenta, la
    siguiente petición del empleado cierra su sesión.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not _es_estatica(request):
            perfil = user.perfil
            if not user.is_active or (perfil is not None and not perfil.activo):
                username = user.username
                request._motivo_logout = 'Sesión cerrada: cuenta desactivada'
                logout(request)
                registrar(RegistroAuditoria.Accion.SEGURIDAD, 'Acceso',
                          'Intento de acceso con cuenta desactivada', request=request, username=username)
                messages.error(request, 'Su cuenta está desactivada. Comuníquese con Recursos Humanos.')
                return redirect('login')
        return self.get_response(request)


class InactividadSesionMiddleware:
    """Cierra la sesión tras 45 minutos sin actividad."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.limite = getattr(settings, 'SESSION_TIMEOUT_INACTIVIDAD', 2700)

    def __call__(self, request):
        if request.user.is_authenticated and not _es_estatica(request):
            ahora = time.time()
            ultima = request.session.get(CLAVE_ULTIMA_ACTIVIDAD)
            if ultima is not None and ahora - ultima > self.limite:
                request._motivo_logout = 'Sesión expirada por inactividad'
                logout(request)
                messages.warning(request, f'Su sesión expiró tras {self.limite // 60} minutos de inactividad.')
                return redirect_to_login(request.get_full_path())
            request.session[CLAVE_ULTIMA_ACTIVIDAD] = ahora
        return self.get_response(request)


class HorarioMarcacionMiddleware:
    """Solo permite marcar asistencia dentro de ±2 horas del horario del empleado.

    Se aplica a las vistas marcadas con @valida_horario_marcacion (POST).
    Entrada: respecto a la hora de entrada programada; salida: respecto a la de salida.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if (
            request.method != 'POST'
            or not getattr(view_func, 'valida_horario_marcacion', False)
            or not settings.VALIDAR_HORARIO_MARCACION
            or not request.user.is_authenticated
        ):
            return None
        empleado = request.user.perfil
        if empleado is None:
            return None
        tipo = request.POST.get('tipo')
        programada = empleado.hora_entrada if tipo == 'entrada' else empleado.hora_salida
        ahora = timezone.localtime()
        referencia = timezone.make_aware(datetime.combine(ahora.date(), programada))
        tolerancia = timedelta(hours=settings.TOLERANCIA_MARCACION_HORAS)
        if abs(ahora - referencia) <= tolerancia:
            return None
        desde = (referencia - tolerancia).strftime('%H:%M')
        hasta = (referencia + tolerancia).strftime('%H:%M')
        logger_seguridad.warning(
            'MARCACION FUERA DE HORARIO bloqueada | usuario=%s tipo=%s hora=%s permitido=%s-%s',
            request.user.username, tipo, ahora.strftime('%H:%M'), desde, hasta,
        )
        messages.error(
            request,
            f'Fuera del horario de marcación de {tipo or "asistencia"}: '
            f'solo se permite entre {desde} y {hasta}. Si es un error, solicite una corrección a RRHH.',
        )
        return redirect('asistencia:dashboard')


class RecordatorioAsistenciaMiddleware:
    """Al iniciar sesión en día laborable, recuerda marcar la entrada si aún no se hizo."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated and request.method == 'GET' and not _es_estatica(request)
            and not request.session.get(CLAVE_RECORDATORIO)
        ):
            request.session[CLAVE_RECORDATORIO] = True
            empleado = user.perfil
            hoy = timezone.localdate()
            if empleado and hoy.weekday() < 5 and not Asistencia.objects.filter(
                empleado=empleado, fecha=hoy,
            ).exists():
                messages.warning(request, format_html(
                    'Aún no registra su entrada de hoy. <a href="{}">Marcar ahora</a>',
                    reverse('asistencia:dashboard'),
                ))
        return self.get_response(request)


class CabecerasSeguridadMiddleware:
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
            response['Cache-Control'] = 'no-store'
        return response
