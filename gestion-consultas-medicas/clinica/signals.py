"""Trazabilidad: todo cambio queda registrado con quién, cuándo y qué cambió."""
from datetime import date, datetime, time
from decimal import Decimal

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .auditoria import contexto_actual, logger_cambios, logger_seguridad, obtener_ip
from .models import (
    Cita,
    Consulta,
    Especialidad,
    HorarioAtencion,
    Medico,
    NotaConsulta,
    Paciente,
    RegistroCambio,
    Usuario,
)

MODELOS_AUDITADOS = (Paciente, Medico, HorarioAtencion, Especialidad, Cita, Consulta,
                     NotaConsulta, Usuario)

# Contenido clínico/sensible: se registra QUE cambió, nunca su valor.
CAMPOS_SENSIBLES = {'diagnostico', 'tratamiento', 'medicamentos', 'alergias', 'texto',
                    'adjuntos', 'motivo', 'password'}
CAMPOS_IGNORADOS = {'actualizado', 'last_login'}

Accion = RegistroCambio.Accion


def _serializar(valor):
    if isinstance(valor, (date, datetime, time, Decimal)):
        return str(valor)
    return valor


def _valores(instancia):
    return {
        campo.attname: _serializar(getattr(instancia, campo.attname))
        for campo in instancia._meta.concrete_fields
        if campo.attname not in CAMPOS_IGNORADOS
    }


def _registrar(instancia, accion, cambios):
    usuario, ip = contexto_actual()
    RegistroCambio.objects.create(
        usuario=usuario, username=getattr(usuario, 'username', ''), accion=accion,
        modelo=instancia._meta.verbose_name.capitalize(), objeto_id=str(instancia.pk),
        objeto=str(instancia)[:200], cambios=cambios, ip=ip,
    )
    logger_cambios.info(
        '%s %s #%s | usuario=%s ip=%s | campos=%s',
        accion.upper(), instancia._meta.model_name, instancia.pk,
        getattr(usuario, 'username', 'sistema'), ip, ', '.join(cambios) or '-',
    )


@receiver(pre_save)
def capturar_valores_previos(sender, instance, raw=False, **kwargs):
    if sender not in MODELOS_AUDITADOS or raw or not instance.pk:
        return
    anterior = sender.objects.filter(pk=instance.pk).first()
    instance._valores_previos = _valores(anterior) if anterior else None


@receiver(post_save)
def registrar_guardado(sender, instance, created, raw=False, **kwargs):
    if sender not in MODELOS_AUDITADOS or raw:
        return
    if created:
        _registrar(instance, Accion.CREACION, {})
        return
    previos = getattr(instance, '_valores_previos', None) or {}
    cambios = {}
    for campo, nuevo in _valores(instance).items():
        viejo = previos.get(campo)
        if viejo != nuevo:
            cambios[campo] = '(modificado)' if campo in CAMPOS_SENSIBLES else [viejo, nuevo]
    if cambios:
        _registrar(instance, Accion.MODIFICACION, cambios)


@receiver(post_delete)
def registrar_eliminacion(sender, instance, **kwargs):
    if sender in MODELOS_AUDITADOS:
        _registrar(instance, Accion.ELIMINACION, {})


# -- Eventos de sesión --------------------------------------------------------
def _evento_sesion(request, usuario, evento, username=''):
    RegistroCambio.objects.create(
        usuario=usuario, username=username or getattr(usuario, 'username', ''),
        accion=Accion.SESION, modelo='Sesión', objeto=evento, ip=obtener_ip(request),
    )


@receiver(user_logged_in)
def al_iniciar_sesion(sender, request, user, **kwargs):
    _evento_sesion(request, user, 'Inicio de sesión')
    logger_seguridad.info('LOGIN usuario=%s rol=%s ip=%s', user.username, user.rol, obtener_ip(request))


@receiver(user_logged_out)
def al_cerrar_sesion(sender, request, user, **kwargs):
    if user is not None:
        evento = 'Sesión expirada' if getattr(request, '_sesion_expirada', False) else 'Cierre de sesión'
        _evento_sesion(request, user, evento)


@receiver(user_login_failed)
def al_fallar_login(sender, credentials, request=None, **kwargs):
    username = str(credentials.get('username', ''))[:150]
    if request is not None:
        _evento_sesion(request, None, 'Inicio de sesión fallido', username=username)
    logger_seguridad.warning('LOGIN FALLIDO usuario=%s ip=%s', username, obtener_ip(request))
