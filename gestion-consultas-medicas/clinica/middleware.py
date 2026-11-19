"""Middlewares especializados del centro médico.

Usan los dos estilos de Django:
- `__call__`: lógica antes y después de la vista (request/response).
- `process_view`: intercepta la vista resuelta (con sus argumentos) antes de
  ejecutarla; aquí se usa para detectar vistas marcadas con decoradores.
"""
import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from .auditoria import (
    establecer_request,
    limpiar_request,
    logger_seguridad,
    obtener_ip,
    registrar_acceso_historial,
)
from .horarios import validar_horario_centro
from .models import AccesoHistorial, Cita

logger_peticiones = logging.getLogger('peticiones')

CLAVE_ULTIMA_ACTIVIDAD = 'ultima_actividad'
CLAVE_ALERTAS = 'alertas_medico_mostradas'
CLAVE_BORRADOR_CITA = 'borrador_cita'
RUTAS_ESTATICAS = ('/static/', '/favicon.ico')


def _es_estatica(request):
    return request.path.startswith(RUTAS_ESTATICAS)


class ContextoAuditoriaMiddleware:
    """Publica el request en una ContextVar para que las señales sepan quién
    realiza cada cambio, y registra cada petición en consola."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = establecer_request(request)
        inicio = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            limpiar_request(token)
        if not _es_estatica(request):
            usuario = request.user.username if request.user.is_authenticated else 'anónimo'
            logger_peticiones.info(
                '%s %s -> %s | usuario=%s ip=%s | %.1f ms',
                request.method, request.path, response.status_code, usuario,
                obtener_ip(request), (time.perf_counter() - inicio) * 1000,
            )
        return response


class InactividadSesionMiddleware:
    """Cierra la sesión tras 20 minutos sin actividad (información sensible)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.limite = getattr(settings, 'SESSION_TIMEOUT_INACTIVIDAD', 1200)

    def __call__(self, request):
        if request.user.is_authenticated and not _es_estatica(request):
            ahora = time.time()
            ultima = request.session.get(CLAVE_ULTIMA_ACTIVIDAD)
            if ultima is not None and ahora - ultima > self.limite:
                request._sesion_expirada = True
                username = request.user.username
                logout(request)
                logger_seguridad.info('SESION EXPIRADA usuario=%s ip=%s', username, obtener_ip(request))
                messages.warning(
                    request,
                    f'Por seguridad su sesión se cerró tras {self.limite // 60} minutos de inactividad.',
                )
                return redirect_to_login(request.get_full_path())
            request.session[CLAVE_ULTIMA_ACTIVIDAD] = ahora
        return self.get_response(request)


class AuditoriaMedicaMiddleware:
    """Registra quién accedió a qué historial clínico (requerimiento legal).

    Las vistas sensibles se marcan con @informacion_sensible. La vista guarda
    en `request.paciente_auditado` el paciente consultado; el middleware, tras
    obtener la respuesta, registra el acceso como permitido (2xx) o denegado
    (403 / intento anónimo). Además deja constancia de cualquier 403.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view_func, view_args, view_kwargs):
        recurso = getattr(view_func, 'recurso_sensible', None)
        if recurso:
            request._recurso_sensible = recurso
            request._paciente_desde_url = (getattr(view_func, 'obtener_paciente', None), view_kwargs)
        return None  # Continúa con la vista normalmente

    def __call__(self, request):
        response = self.get_response(request)
        recurso = getattr(request, '_recurso_sensible', None)
        estado = response.status_code
        if recurso:
            resultado = None
            if 200 <= estado < 300:
                resultado = AccesoHistorial.Resultado.PERMITIDO
            elif estado == 403 or (estado == 302 and not request.user.is_authenticated):
                resultado = AccesoHistorial.Resultado.DENEGADO
            if resultado:
                registrar_acceso_historial(request, recurso, resultado, self._paciente(request))
        elif estado == 403:
            logger_seguridad.warning(
                'ACCESO NO AUTORIZADO usuario=%s ip=%s %s %s',
                request.user.username if request.user.is_authenticated else 'anónimo',
                obtener_ip(request), request.method, request.path,
            )
        return response


    @staticmethod
    def _paciente(request):
        paciente = getattr(request, 'paciente_auditado', None)
        if paciente is None:
            obtener, kwargs = getattr(request, '_paciente_desde_url', (None, {}))
            if obtener:
                paciente = obtener(kwargs)
        return paciente


class HorarioAtencionMiddleware:
    """Bloquea el agendamiento de citas fuera del horario de atención.

    L-V 08:00-18:00 y sábados 08:00-13:00. Se aplica a las vistas marcadas con
    @valida_horario_atencion antes de que se ejecuten. Los datos ingresados se
    conservan en la sesión para no obligar al usuario a reescribirlos.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != 'POST' or not getattr(view_func, 'valida_horario_atencion', False):
            return None
        fecha = parse_date(request.POST.get('fecha', '') or '')
        try:
            hora = parse_time(request.POST.get('hora', '') or '')
        except ValueError:
            hora = None
        if not fecha or not hora:
            return None  # El formulario informará el dato faltante o inválido
        error = validar_horario_centro(fecha, hora)
        if error is None:
            return None
        logger_seguridad.warning(
            'CITA FUERA DE HORARIO bloqueada | usuario=%s fecha=%s hora=%s',
            request.user.username, fecha, hora,
        )
        request.session[CLAVE_BORRADOR_CITA] = {
            k: v for k, v in request.POST.items() if k != 'csrfmiddlewaretoken'
        }
        messages.error(request, f'{error} La cita no fue registrada.')
        return redirect(request.get_full_path())


class AlertasMedicoMiddleware:
    """Al iniciar sesión notifica al médico sus citas pendientes del día."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated and user.es_medico and request.method == 'GET'
            and not _es_estatica(request) and not request.session.get(CLAVE_ALERTAS)
        ):
            request.session[CLAVE_ALERTAS] = True
            ahora = timezone.localtime()
            pendientes = list(
                Cita.objects.filter(
                    medico__usuario=user, fecha=ahora.date(), estado=Cita.Estado.PROGRAMADA,
                ).select_related('paciente').order_by('hora')
            )
            if pendientes:
                proxima = next((c for c in pendientes if c.hora >= ahora.time()), None)
                detalle = (
                    f' Próxima: {proxima.hora:%H:%M} con {proxima.paciente.nombre_completo}.'
                    if proxima else ''
                )
                messages.info(request, f'Tiene {len(pendientes)} cita(s) pendiente(s) hoy.{detalle}')
            else:
                messages.info(request, 'No tiene citas pendientes para hoy.')
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
            # Información médica: el navegador no debe guardar copias en caché.
            response['Cache-Control'] = 'no-store, private'
        return response
