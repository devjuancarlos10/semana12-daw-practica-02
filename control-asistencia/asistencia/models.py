"""Modelos: usuarios, áreas, empleados, asistencia, permisos y auditoría."""
from datetime import time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinLengthValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone

from .validators import (
    validar_dni,
    validar_email_corporativo,
    validar_fecha_no_futura,
    validar_no_vacio,
)


# ---------------------------------------------------------------------------
# Usuarios y estructura organizacional
# ---------------------------------------------------------------------------
class Usuario(AbstractUser):
    class Rol(models.TextChoices):
        ADMIN_RRHH = 'admin_rrhh', 'Administrador RRHH'
        JEFE_AREA = 'jefe_area', 'Jefe de área'
        EMPLEADO = 'empleado', 'Empleado'
        CONTADOR = 'contador', 'Contador'

    rol = models.CharField(max_length=12, choices=Rol.choices, default=Rol.EMPLEADO)

    class Meta:
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        # RRHH, jefes y contadores usan también el Django Admin (con vistas restringidas).
        self.is_staff = self.is_superuser or self.rol != self.Rol.EMPLEADO
        super().save(*args, **kwargs)
        from .roles import asignar_grupo_por_rol
        asignar_grupo_por_rol(self)

    @property
    def es_rrhh(self):
        return self.is_superuser or self.rol == self.Rol.ADMIN_RRHH

    @property
    def es_jefe(self):
        return self.rol == self.Rol.JEFE_AREA

    @property
    def es_contador(self):
        return self.rol == self.Rol.CONTADOR

    @property
    def perfil(self):
        """Empleado asociado (o None)."""
        try:
            return self.empleado
        except Empleado.DoesNotExist:
            return None

    @property
    def area_a_cargo(self):
        perfil = self.perfil
        if self.es_jefe and perfil:
            return Area.objects.filter(jefe=perfil).first()
        return None


class Area(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    codigo = models.CharField('código', max_length=5, unique=True)
    jefe = models.ForeignKey('Empleado', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='areas_a_cargo')

    class Meta:
        ordering = ['nombre']
        verbose_name = 'área'
        verbose_name_plural = 'áreas'

    def __str__(self):
        return self.nombre


class EmpleadoQuerySet(models.QuerySet):
    def visibles_para(self, usuario):
        if usuario.es_rrhh or usuario.has_perm('asistencia.ver_reportes_globales'):
            return self
        if usuario.es_jefe and usuario.area_a_cargo:
            return self.filter(area=usuario.area_a_cargo)
        return self.filter(usuario=usuario)


class Empleado(models.Model):
    class TipoContrato(models.TextChoices):
        PLANILLA = 'planilla', 'Planilla'
        LOCACION = 'locacion', 'Locación de servicios'

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='empleado')
    dni = models.CharField('DNI', max_length=8, unique=True, validators=[validar_dni])
    nombres = models.CharField(max_length=80)
    apellidos = models.CharField(max_length=80)
    cargo = models.CharField(max_length=80, validators=[validar_no_vacio])
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='empleados')
    fecha_ingreso = models.DateField(validators=[validar_fecha_no_futura])
    tipo_contrato = models.CharField(max_length=10, choices=TipoContrato.choices)
    email_corporativo = models.EmailField(unique=True, validators=[validar_email_corporativo])
    salario = models.DecimalField(max_digits=9, decimal_places=2, validators=[MinValueValidator(0)],
                                  help_text='Confidencial: visible solo para RRHH.')
    hora_entrada = models.TimeField('hora de entrada', default=time(8, 0))
    hora_salida = models.TimeField('hora de salida', default=time(17, 0))
    activo = models.BooleanField(
        'cuenta activa', default=True,
        help_text='Desactivar bloquea el acceso al sistema de inmediato.',
    )

    objects = EmpleadoQuerySet.as_manager()

    class Meta:
        ordering = ['apellidos', 'nombres']
        permissions = [
            ('registrar_asistencia', 'Puede registrar su asistencia'),
            ('solicitar_permiso', 'Puede solicitar permisos y vacaciones'),
            ('aprobar_permiso', 'Puede aprobar o rechazar permisos'),
            ('ver_reportes_area', 'Puede ver reportes de su área'),
            ('ver_reportes_globales', 'Puede ver reportes globales'),
            ('corregir_asistencia', 'Puede corregir registros de asistencia'),
            ('ver_salarios', 'Puede ver salarios y datos personales'),
        ]

    def __str__(self):
        return f'{self.apellidos}, {self.nombres}'

    @property
    def nombre_completo(self):
        return f'{self.nombres} {self.apellidos}'

    @property
    def antiguedad(self):
        """Antigüedad como (años, meses)."""
        hoy = timezone.localdate()
        meses = (hoy.year - self.fecha_ingreso.year) * 12 + hoy.month - self.fecha_ingreso.month
        if hoy.day < self.fecha_ingreso.day:
            meses -= 1
        return divmod(max(meses, 0), 12)

    @property
    def antiguedad_texto(self):
        anios, meses = self.antiguedad
        partes = []
        if anios:
            partes.append(f'{anios} año{"s" if anios != 1 else ""}')
        partes.append(f'{meses} mes{"es" if meses != 1 else ""}')
        return ' y '.join(partes)

    def dias_vacaciones_tomados(self, anio=None, incluir_pendientes=False):
        anio = anio or timezone.localdate().year
        estados = [Permiso.Estado.APROBADO]
        if incluir_pendientes:
            estados.append(Permiso.Estado.PENDIENTE)
        permisos = self.permisos.filter(
            tipo=Permiso.Tipo.VACACIONES, estado__in=estados, fecha_inicio__year=anio,
        ).values_list('fecha_inicio', 'fecha_fin')
        return sum((fin - inicio).days + 1 for inicio, fin in permisos)

    @property
    def dias_vacaciones_disponibles(self):
        return settings.DIAS_VACACIONES_ANUALES - self.dias_vacaciones_tomados()


# ---------------------------------------------------------------------------
# Asistencia
# ---------------------------------------------------------------------------
class Asistencia(models.Model):
    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name='asistencias')
    fecha = models.DateField(db_index=True)
    hora_entrada = models.DateTimeField()
    hora_salida = models.DateTimeField(null=True, blank=True)
    horas_trabajadas = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    horas_extra = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    observacion = models.CharField('observación', max_length=255, blank=True)
    corregida = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha', '-hora_entrada']
        verbose_name = 'registro de asistencia'
        verbose_name_plural = 'registros de asistencia'
        constraints = [
            models.CheckConstraint(
                condition=Q(hora_salida__isnull=True) | Q(hora_salida__gt=F('hora_entrada')),
                name='asistencia_salida_posterior',
                violation_error_message='La hora de salida debe ser posterior a la de entrada.',
            ),
        ]

    def __str__(self):
        return f'{self.empleado} · {self.fecha:%d/%m/%Y}'

    def save(self, *args, **kwargs):
        self.calcular_horas()
        super().save(*args, **kwargs)

    def calcular_horas(self):
        """Cálculo automático de horas trabajadas y horas extra (> jornada)."""
        if self.hora_salida and self.hora_entrada:
            segundos = (self.hora_salida - self.hora_entrada).total_seconds()
            horas = Decimal(segundos / 3600).quantize(Decimal('0.01'), ROUND_HALF_UP)
            self.horas_trabajadas = horas
            self.horas_extra = max(horas - settings.JORNADA_DIARIA_HORAS, Decimal('0'))
        else:
            self.horas_trabajadas = None
            self.horas_extra = Decimal('0')

    @property
    def en_curso(self):
        return self.hora_salida is None

    @property
    def tardanza_minutos(self):
        entrada = timezone.localtime(self.hora_entrada)
        programada = entrada.replace(hour=self.empleado.hora_entrada.hour,
                                     minute=self.empleado.hora_entrada.minute, second=0, microsecond=0)
        return max(int((entrada - programada).total_seconds() // 60), 0)

    @property
    def editable_por_empleado(self):
        limite = timedelta(hours=settings.HORAS_EDICION_EMPLEADO)
        return timezone.now() - self.creado <= limite


class CorreccionAsistencia(models.Model):
    class Motivo(models.TextChoices):
        ERROR_SISTEMA = 'error_sistema', 'Error de sistema'
        OLVIDO = 'olvido', 'Olvido de marcación'
        OTRO = 'otro', 'Otro'

    asistencia = models.ForeignKey(Asistencia, on_delete=models.PROTECT, related_name='correcciones')
    corregido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    motivo = models.CharField(max_length=15, choices=Motivo.choices)
    justificacion = models.TextField('justificación', validators=[MinLengthValidator(50)])
    entrada_anterior = models.DateTimeField()
    salida_anterior = models.DateTimeField(null=True, blank=True)
    entrada_nueva = models.DateTimeField()
    salida_nueva = models.DateTimeField(null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'corrección de asistencia'
        verbose_name_plural = 'correcciones de asistencia'

    def __str__(self):
        return f'Corrección {self.asistencia} ({self.get_motivo_display()})'


# ---------------------------------------------------------------------------
# Permisos y vacaciones
# ---------------------------------------------------------------------------
class PermisoQuerySet(models.QuerySet):
    def vigentes(self):
        return self.exclude(estado=Permiso.Estado.RECHAZADO)

    def revisables_por(self, usuario):
        if usuario.es_rrhh:
            return self
        area = usuario.area_a_cargo
        if usuario.has_perm('asistencia.aprobar_permiso') and area:
            return self.filter(empleado__area=area).exclude(empleado__usuario=usuario)
        return self.none()


class Permiso(models.Model):
    class Tipo(models.TextChoices):
        VACACIONES = 'vacaciones', 'Vacaciones'
        PERSONAL = 'personal', 'Personal'
        MEDICO = 'medico', 'Médico'
        DUELO = 'duelo', 'Duelo'
        MATERNIDAD_PATERNIDAD = 'maternidad', 'Maternidad / paternidad'
        OTRO = 'otro', 'Otro'

    class Estado(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        APROBADO = 'aprobado', 'Aprobado'
        RECHAZADO = 'rechazado', 'Rechazado'

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name='permisos')
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    motivo = models.TextField(validators=[MinLengthValidator(30)])
    adjunto_descripcion = models.CharField(
        'documento adjunto', max_length=255, blank=True,
        help_text='Descripción del documento sustentatorio (certificado médico, acta, etc.).',
    )
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='+')
    fecha_revision = models.DateTimeField(null=True, blank=True)
    comentario_revision = models.CharField(max_length=255, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    objects = PermisoQuerySet.as_manager()

    class Meta:
        ordering = ['-creado']
        verbose_name = 'permiso'
        verbose_name_plural = 'permisos'
        constraints = [
            models.CheckConstraint(condition=Q(fecha_fin__gte=F('fecha_inicio')),
                                   name='permiso_fin_mayor_igual_inicio'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.empleado} · {self.fecha_inicio:%d/%m}-{self.fecha_fin:%d/%m}'

    @property
    def dias(self):
        return (self.fecha_fin - self.fecha_inicio).days + 1

    def puede_ser_revisado_por(self, usuario):
        if self.estado != self.Estado.PENDIENTE:
            return False
        return Permiso.objects.revisables_por(usuario).filter(pk=self.pk).exists()

    def resolver(self, usuario, aprobado, comentario=''):
        """Aplica la decisión y notifica al empleado."""
        self.estado = self.Estado.APROBADO if aprobado else self.Estado.RECHAZADO
        self.revisado_por = usuario
        self.fecha_revision = timezone.now()
        self.comentario_revision = comentario
        self.save()
        Notificacion.objects.create(
            usuario=self.empleado.usuario,
            mensaje=(f'Su solicitud de {self.get_tipo_display().lower()} del '
                     f'{self.fecha_inicio:%d/%m/%Y} al {self.fecha_fin:%d/%m/%Y} fue '
                     f'{self.get_estado_display().lower()} por {usuario}.'
                     + (f' Comentario: {comentario}' if comentario else '')),
            permiso=self,
        )


class Notificacion(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='notificaciones')
    mensaje = models.CharField(max_length=400)
    permiso = models.ForeignKey(Permiso, on_delete=models.SET_NULL, null=True, blank=True)
    leida = models.BooleanField(default=False)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'notificación'
        verbose_name_plural = 'notificaciones'

    def __str__(self):
        return self.mensaje[:60]


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
class RegistroAuditoria(models.Model):
    class Accion(models.TextChoices):
        CREACION = 'creacion', 'Creación'
        MODIFICACION = 'modificacion', 'Modificación'
        ELIMINACION = 'eliminacion', 'Eliminación'
        SESION = 'sesion', 'Sesión'
        SEGURIDAD = 'seguridad', 'Seguridad'

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='+')
    username = models.CharField(max_length=150, blank=True)
    accion = models.CharField(max_length=15, choices=Accion.choices)
    modelo = models.CharField(max_length=60)
    objeto_id = models.CharField(max_length=40, blank=True)
    objeto = models.CharField(max_length=200, blank=True)
    cambios = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    ruta = models.CharField(max_length=255, blank=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'registro de auditoría'
        verbose_name_plural = 'registros de auditoría'

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y %H:%M} · {self.username or "sistema"} · {self.get_accion_display()}'

    @property
    def cambios_legibles(self):
        lineas = []
        for campo, valor in (self.cambios or {}).items():
            if isinstance(valor, list) and len(valor) == 2:
                lineas.append(f'{campo}: {valor[0]} → {valor[1]}')
            else:
                lineas.append(f'{campo}: {valor}')
        return lineas


def horas_por_empleado(asistencias):
    """Agrega horas trabajadas y extra por empleado para reportes."""
    return (
        asistencias.values('empleado__id', 'empleado__apellidos', 'empleado__nombres',
                           'empleado__area__nombre')
        .annotate(total_horas=Sum('horas_trabajadas'), total_extra=Sum('horas_extra'),
                  dias=models.Count('fecha', distinct=True))
        .order_by('empleado__area__nombre', 'empleado__apellidos')
    )
