"""Modelos de dominio: usuarios, laboratorios, equipos, préstamos y auditoría."""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .validators import (
    validar_codigo_institucional,
    validar_codigo_patrimonial,
    validar_fecha_no_futura,
)


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
class Usuario(AbstractUser):
    """Usuario del sistema. El rol determina el grupo (y por ende los permisos)."""

    class Rol(models.TextChoices):
        ADMIN = 'admin', 'Administrador'
        DOCENTE = 'docente', 'Docente'
        ASISTENTE = 'asistente', 'Asistente'

    rol = models.CharField(max_length=10, choices=Rol.choices, default=Rol.DOCENTE)
    codigo_institucional = models.CharField(
        'código institucional', max_length=12, unique=True, null=True, blank=True,
        validators=[validar_codigo_institucional],
    )
    departamento = models.CharField(max_length=100, blank=True)
    telefono = models.CharField('teléfono', max_length=15, blank=True)

    class Meta:
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        # Administradores y asistentes operan también desde Django Admin.
        if self.rol in (self.Rol.ADMIN, self.Rol.ASISTENTE):
            self.is_staff = True
        super().save(*args, **kwargs)
        from .roles import asignar_grupo_por_rol
        asignar_grupo_por_rol(self)

    @property
    def es_admin(self):
        return self.is_superuser or self.rol == self.Rol.ADMIN

    @property
    def es_docente(self):
        return self.rol == self.Rol.DOCENTE

    @property
    def es_asistente(self):
        return self.rol == self.Rol.ASISTENTE


# ---------------------------------------------------------------------------
# Laboratorios y equipos
# ---------------------------------------------------------------------------
class Laboratorio(models.Model):
    codigo = models.CharField('código', max_length=10, unique=True)
    nombre = models.CharField(max_length=100)
    ubicacion = models.CharField('ubicación', max_length=150)
    capacidad = models.PositiveSmallIntegerField(default=30)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='laboratorios_a_cargo',
    )

    class Meta:
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} · {self.nombre}'


class EquipoQuerySet(models.QuerySet):
    def con_disponibilidad(self):
        """Anota unidades comprometidas (solicitadas o prestadas) y disponibles."""
        return self.annotate(
            comprometidas=Coalesce(
                Sum('prestamos__cantidad', filter=Q(prestamos__estado__in=Prestamo.ESTADOS_VIGENTES)),
                0,
            ),
        ).annotate(unidades_disponibles=F('cantidad_total') - F('comprometidas'))

    def operativos(self):
        return self.filter(estado=Equipo.Estado.OPERATIVO)


class Equipo(models.Model):
    class Categoria(models.TextChoices):
        LAPTOP = 'laptop', 'Laptop'
        PROYECTOR = 'proyector', 'Proyector'
        CAMARA = 'camara', 'Cámara'
        ARDUINO = 'arduino', 'Kit Arduino'
        ROUTER = 'router', 'Router'
        SWITCH = 'switch', 'Switch'
        OTRO = 'otro', 'Otro'

    class Estado(models.TextChoices):
        OPERATIVO = 'operativo', 'Operativo'
        MANTENIMIENTO = 'mantenimiento', 'En mantenimiento'
        BAJA = 'baja', 'Dado de baja'

    codigo_patrimonial = models.CharField(
        'código patrimonial', max_length=12, unique=True,
        validators=[validar_codigo_patrimonial],
        help_text='Ej.: LAP-000123',
    )
    nombre = models.CharField(max_length=120)
    marca = models.CharField(max_length=60)
    modelo = models.CharField(max_length=60)
    categoria = models.CharField('categoría', max_length=12, choices=Categoria.choices)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.OPERATIVO)
    laboratorio = models.ForeignKey(Laboratorio, on_delete=models.PROTECT, related_name='equipos')
    cantidad_total = models.PositiveSmallIntegerField(
        'unidades', default=1, validators=[MinValueValidator(1)],
        help_text='Número de unidades idénticas registradas bajo este código.',
    )
    fecha_ultimo_mantenimiento = models.DateField(
        'último mantenimiento', null=True, blank=True, validators=[validar_fecha_no_futura],
    )
    descripcion = models.TextField('descripción', blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    objects = EquipoQuerySet.as_manager()

    class Meta:
        ordering = ['codigo_patrimonial']
        indexes = [models.Index(fields=['estado', 'categoria'])]

    def __str__(self):
        return f'{self.codigo_patrimonial} · {self.nombre}'

    @property
    def disponibles(self) -> int:
        """Unidades libres. Usa la anotación si existe para evitar otra consulta."""
        if hasattr(self, 'unidades_disponibles'):
            return max(self.unidades_disponibles, 0)
        comprometidas = self.prestamos.filter(
            estado__in=Prestamo.ESTADOS_VIGENTES
        ).aggregate(t=Coalesce(Sum('cantidad'), 0))['t']
        return max(self.cantidad_total - comprometidas, 0)

    @property
    def dias_desde_mantenimiento(self):
        if not self.fecha_ultimo_mantenimiento:
            return None
        return (timezone.localdate() - self.fecha_ultimo_mantenimiento).days

    @property
    def es_operativo(self):
        return self.estado == self.Estado.OPERATIVO


# ---------------------------------------------------------------------------
# Préstamos
# ---------------------------------------------------------------------------
class PrestamoQuerySet(models.QuerySet):
    def vigentes(self):
        return self.filter(estado__in=Prestamo.ESTADOS_VIGENTES)

    def vencidos(self):
        return self.filter(
            estado=Prestamo.Estado.ACTIVO,
            fecha_devolucion_prevista__lt=timezone.localdate(),
        )

    def con_relaciones(self):
        return self.select_related('docente', 'equipo', 'equipo__laboratorio', 'registrado_por')


class Prestamo(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'pendiente', 'Solicitado'
        ACTIVO = 'activo', 'En curso'
        DEVUELTO = 'devuelto', 'Devuelto'
        RECHAZADO = 'rechazado', 'Rechazado'

    ESTADOS_VIGENTES = (Estado.PENDIENTE, Estado.ACTIVO)

    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='prestamos',
        limit_choices_to={'rol': Usuario.Rol.DOCENTE},
    )
    equipo = models.ForeignKey(Equipo, on_delete=models.PROTECT, related_name='prestamos')
    cantidad = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    fecha_prestamo = models.DateField('fecha de préstamo', default=timezone.localdate)
    fecha_devolucion_prevista = models.DateField('devolución prevista')
    fecha_devolucion_real = models.DateField('devolución real', null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    observaciones = models.TextField(blank=True)

    estado_equipo_devolucion = models.CharField(
        'estado del equipo al retornar', max_length=15, choices=Equipo.Estado.choices, blank=True,
    )
    observaciones_devolucion = models.TextField('observaciones de devolución', blank=True)
    motivo_rechazo = models.CharField(max_length=255, blank=True)

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='registrado/aprobado por',
    )
    recibido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='devolución recibida por',
    )
    recordatorios_enviados = models.PositiveSmallIntegerField(default=0)
    ultimo_recordatorio = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    objects = PrestamoQuerySet.as_manager()

    class Meta:
        ordering = ['-fecha_prestamo', '-id']
        verbose_name = 'préstamo'
        verbose_name_plural = 'préstamos'
        permissions = [
            ('ver_todos_prestamos', 'Puede ver los préstamos de todos los docentes'),
            ('gestionar_prestamos', 'Puede registrar, aprobar y rechazar préstamos'),
            ('registrar_devolucion', 'Puede registrar devoluciones'),
            ('ver_reportes', 'Puede ver reportes de uso'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(fecha_devolucion_prevista__gt=F('fecha_prestamo')),
                name='prestamo_devolucion_posterior',
                violation_error_message='La devolución debe ser posterior al préstamo.',
            ),
        ]
        indexes = [models.Index(fields=['estado', 'fecha_devolucion_prevista'])]

    def __str__(self):
        return f'Préstamo #{self.pk} · {self.equipo.codigo_patrimonial} · {self.docente}'

    @property
    def esta_vencido(self):
        return (
            self.estado == self.Estado.ACTIVO
            and self.fecha_devolucion_prevista < timezone.localdate()
        )

    @property
    def dias_retraso(self) -> int:
        if self.estado == self.Estado.DEVUELTO and self.fecha_devolucion_real:
            referencia = self.fecha_devolucion_real
        elif self.estado == self.Estado.ACTIVO:
            referencia = timezone.localdate()
        else:
            return 0
        return max((referencia - self.fecha_devolucion_prevista).days, 0)

    @property
    def estado_visual(self):
        """Clave de estilo para la plantilla (incluye 'vencido' como estado derivado)."""
        return 'vencido' if self.esta_vencido else self.estado


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
class RegistroAuditoria(models.Model):
    class Accion(models.TextChoices):
        LOGIN = 'login', 'Inicio de sesión'
        LOGOUT = 'logout', 'Cierre de sesión'
        LOGIN_FALLIDO = 'login_fallido', 'Inicio de sesión fallido'
        SESION_EXPIRADA = 'sesion_expirada', 'Sesión expirada por inactividad'
        PRESTAMO_SOLICITADO = 'prestamo_solicitado', 'Préstamo solicitado'
        PRESTAMO_REGISTRADO = 'prestamo_registrado', 'Préstamo registrado'
        PRESTAMO_APROBADO = 'prestamo_aprobado', 'Préstamo aprobado'
        PRESTAMO_RECHAZADO = 'prestamo_rechazado', 'Préstamo rechazado'
        DEVOLUCION = 'devolucion', 'Devolución registrada'
        EQUIPO_CREADO = 'equipo_creado', 'Equipo registrado'
        EQUIPO_EDITADO = 'equipo_editado', 'Equipo modificado'
        EQUIPO_ELIMINADO = 'equipo_eliminado', 'Equipo eliminado'
        MANTENIMIENTO = 'mantenimiento', 'Equipos enviados a mantenimiento'
        RECORDATORIO = 'recordatorio', 'Recordatorio de devolución'
        ACCESO_DENEGADO = 'acceso_denegado', 'Acceso no autorizado'
        ACCESO_ANONIMO = 'acceso_anonimo', 'Acceso sin autenticar a vista protegida'

    ACCIONES_SEGURIDAD = (
        Accion.LOGIN_FALLIDO, Accion.ACCESO_DENEGADO, Accion.ACCESO_ANONIMO,
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    username = models.CharField('usuario (texto)', max_length=150, blank=True)
    accion = models.CharField('acción', max_length=25, choices=Accion.choices)
    descripcion = models.CharField('descripción', max_length=255, blank=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    metodo = models.CharField('método', max_length=8, blank=True)
    ruta = models.CharField(max_length=255, blank=True)
    fecha = models.DateTimeField('fecha/hora', auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'registro de auditoría'
        verbose_name_plural = 'registros de auditoría'

    def __str__(self):
        return f'{self.fecha:%Y-%m-%d %H:%M} · {self.username or "anónimo"} · {self.get_accion_display()}'

    @property
    def es_evento_seguridad(self):
        return self.accion in self.ACCIONES_SEGURIDAD
