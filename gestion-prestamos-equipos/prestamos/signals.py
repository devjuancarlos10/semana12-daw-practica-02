"""Receptores de señales de autenticación → auditoría."""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .auditoria import marcar_accion
from .middleware import CLAVE_ALERTA_VENCIDOS, CLAVE_ULTIMA_ACTIVIDAD
from .models import RegistroAuditoria

Accion = RegistroAuditoria.Accion


@receiver(user_logged_in)
def al_iniciar_sesion(sender, request, user, **kwargs):
    # Sesión nueva: se fuerza la verificación de préstamos vencidos y se
    # reinicia el reloj de inactividad.
    request.session.pop(CLAVE_ALERTA_VENCIDOS, None)
    request.session.pop(CLAVE_ULTIMA_ACTIVIDAD, None)
    marcar_accion(request, Accion.LOGIN, f'Rol: {user.get_rol_display()}', usuario=user)


@receiver(user_logged_out)
def al_cerrar_sesion(sender, request, user, **kwargs):
    # Si la sesión expiró, el middleware registra SESION_EXPIRADA en su lugar.
    if request is not None and user is not None and not getattr(request, '_sesion_expirada', False):
        marcar_accion(request, Accion.LOGOUT, usuario=user)


@receiver(user_login_failed)
def al_fallar_login(sender, credentials, request=None, **kwargs):
    if request is None:
        return
    # Nunca se registra la contraseña: solo el nombre de usuario intentado.
    username = str(credentials.get('username', ''))[:150]
    marcar_accion(request, Accion.LOGIN_FALLIDO, f'Usuario intentado: {username}', inmediata=True)
