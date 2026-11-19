"""Auditabilidad: todo cambio en asistencia, permisos y empleados queda registrado."""
from datetime import date, datetime, time
from decimal import Decimal

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .auditoria import registrar
from .models import Area, Asistencia, CorreccionAsistencia, Empleado, Permiso, RegistroAuditoria

MODELOS_AUDITADOS = (Asistencia, Permiso, Empleado, CorreccionAsistencia, Area)
CAMPOS_IGNORADOS = {'actualizado'}
CAMPOS_CONFIDENCIALES = {'salario'}  # Se registra que cambió, no el valor

Accion = RegistroAuditoria.Accion


def _serializar(valor):
    if isinstance(valor, datetime):
        from django.utils import timezone
        return timezone.localtime(valor).strftime('%Y-%m-%d %H:%M') if timezone.is_aware(valor) else str(valor)
    if isinstance(valor, (date, time, Decimal)):
        return str(valor)
    return valor


def _valores(instancia):
    return {f.attname: _serializar(getattr(instancia, f.attname))
            for f in instancia._meta.concrete_fields if f.attname not in CAMPOS_IGNORADOS}


@receiver(pre_save)
def capturar_previos(sender, instance, raw=False, **kwargs):
    if sender in MODELOS_AUDITADOS and not raw and instance.pk:
        anterior = sender.objects.filter(pk=instance.pk).first()
        instance._previos = _valores(anterior) if anterior else {}


@receiver(post_save)
def auditar_guardado(sender, instance, created, raw=False, **kwargs):
    if sender not in MODELOS_AUDITADOS or raw:
        return
    modelo = instance._meta.verbose_name.capitalize()
    if created:
        registrar(Accion.CREACION, modelo, instance, instance.pk)
        return
    previos = getattr(instance, '_previos', {})
    cambios = {}
    for campo, nuevo in _valores(instance).items():
        if previos.get(campo) != nuevo:
            cambios[campo] = '(confidencial)' if campo in CAMPOS_CONFIDENCIALES else [previos.get(campo), nuevo]
    if cambios:
        registrar(Accion.MODIFICACION, modelo, instance, instance.pk, cambios)


@receiver(post_delete)
def auditar_eliminacion(sender, instance, **kwargs):
    if sender in MODELOS_AUDITADOS:
        registrar(Accion.ELIMINACION, instance._meta.verbose_name.capitalize(), instance, instance.pk)


@receiver(user_logged_in)
def al_iniciar_sesion(sender, request, user, **kwargs):
    registrar(Accion.SESION, 'Sesión', 'Inicio de sesión', usuario=user, request=request)


@receiver(user_logged_out)
def al_cerrar_sesion(sender, request, user, **kwargs):
    if user is not None:
        motivo = getattr(request, '_motivo_logout', 'Cierre de sesión')
        registrar(Accion.SESION, 'Sesión', motivo, usuario=user, request=request)


@receiver(user_login_failed)
def al_fallar_login(sender, credentials, request=None, **kwargs):
    registrar(Accion.SEGURIDAD, 'Sesión', 'Inicio de sesión fallido', request=request,
              username=str(credentials.get('username', ''))[:150])
