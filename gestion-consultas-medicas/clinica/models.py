"""Modelos: usuarios, médicos, pacientes, citas, consultas y auditoría."""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .horarios import hora_fin
from .validators import (
    validar_cmp,
    validar_codigo_institucional,
    validar_dni,
    validar_fecha_no_futura,
)


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
class Usuario(AbstractUser):
    class Rol(models.TextChoices):
        ADMIN = 'admin', 'Administrador'
        MEDICO = 'medico', 'Médico'
        RECEPCIONISTA = 'recepcionista', 'Recepcionista'
        PACIENTE = 'paciente', 'Paciente'

    rol = models.CharField(max_length=15, choices=Rol.choices, default=Rol.PACIENTE)

    class Meta:
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        # Administración y recepción operan también desde Django Admin.
        self.is_staff = self.is_superuser or self.rol in (self.Rol.ADMIN, self.Rol.RECEPCIONISTA)
        super().save(*args, **kwargs)
        from .roles import asignar_grupo_por_rol
        asignar_grupo_por_rol(self)

    @property
    def es_medico(self):
        return self.rol == self.Rol.MEDICO

    @property
    def es_paciente(self):
        return self.rol == self.Rol.PACIENTE

    @property
    def es_recepcionista(self):
        return self.rol == self.Rol.RECEPCIONISTA

    @property
    def es_admin(self):
        return self.is_superuser or self.rol == self.Rol.ADMIN

    @property
    def perfil_medico(self):
        return getattr(self, 'medico', None) if self.es_medico else None

    @property
    def perfil_paciente(self):
        return getattr(self, 'paciente', None) if self.es_paciente else None


# ---------------------------------------------------------------------------
# Médicos
# ---------------------------------------------------------------------------
class Especialidad(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.CharField('descripción', max_length=255, blank=True)

    class Meta:
        ordering = ['nombre']
        verbose_name_plural = 'especialidades'

    def __str__(self):
        return self.nombre


class Medico(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='medico', limit_choices_to={'rol': 'medico'})
    cmp = models.CharField('CMP', max_length=6, unique=True, validators=[validar_cmp],
                           help_text='Número de colegiatura del Colegio Médico del Perú.')
    especialidad = models.ForeignKey(Especialidad, on_delete=models.PROTECT, related_name='medicos')
    consultorio = models.CharField(max_length=30)
    telefono = models.CharField('teléfono', max_length=15, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['usuario__last_name']
        verbose_name = 'médico'
        verbose_name_plural = 'médicos'

    def __str__(self):
        return f'Dr(a). {self.usuario.get_full_name()}'

    def atiende_en(self, fecha, hora):
        """True si la cita completa cae dentro de algún horario del médico ese día."""
        fin = hora_fin(hora)
        return self.horarios.filter(
            dia_semana=fecha.weekday(), hora_inicio__lte=hora, hora_fin__gte=fin,
        ).exists()


class HorarioAtencion(models.Model):
    class Dia(models.IntegerChoices):
        LUNES = 0, 'Lunes'
        MARTES = 1, 'Martes'
        MIERCOLES = 2, 'Miércoles'
        JUEVES = 3, 'Jueves'
        VIERNES = 4, 'Viernes'
        SABADO = 5, 'Sábado'

    medico = models.ForeignKey(Medico, on_delete=models.CASCADE, related_name='horarios')
    dia_semana = models.PositiveSmallIntegerField('día', choices=Dia.choices)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()

    class Meta:
        ordering = ['dia_semana', 'hora_inicio']
        verbose_name = 'horario de atención'
        verbose_name_plural = 'horarios de atención'
        constraints = [
            models.UniqueConstraint(fields=['medico', 'dia_semana'], name='horario_unico_por_dia'),
        ]

    def __str__(self):
        return f'{self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fin:%H:%M}'

    def clean(self):
        if self.hora_inicio and self.hora_fin:
            if self.hora_fin <= self.hora_inicio:
                raise ValidationError({'hora_fin': 'Debe ser posterior a la hora de inicio.'})
            apertura, cierre = settings.HORARIO_ATENCION[self.dia_semana]
            if self.hora_inicio < apertura or self.hora_fin > cierre:
                raise ValidationError(
                    f'El horario debe estar dentro del horario del centro '
                    f'({apertura:%H:%M}-{cierre:%H:%M}).'
                )


# ---------------------------------------------------------------------------
# Pacientes
# ---------------------------------------------------------------------------
class Paciente(models.Model):
    class Tipo(models.TextChoices):
        ESTUDIANTE = 'estudiante', 'Estudiante'
        DOCENTE = 'docente', 'Docente'

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='paciente', limit_choices_to={'rol': 'paciente'},
    )
    dni = models.CharField('DNI', max_length=8, unique=True, validators=[validar_dni])
    nombres = models.CharField(max_length=80)
    apellidos = models.CharField(max_length=80)
    fecha_nacimiento = models.DateField(validators=[validar_fecha_no_futura])
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    codigo_institucional = models.CharField(
        'código institucional', max_length=12, unique=True, validators=[validar_codigo_institucional],
    )
    email = models.EmailField('correo institucional')
    telefono = models.CharField('teléfono', max_length=15, blank=True)
    alergias = models.TextField('alergias conocidas', blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='+')
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['apellidos', 'nombres']
        permissions = [
            ('ver_historial_clinico', 'Puede ver historiales clínicos'),
        ]

    def __str__(self):
        return f'{self.apellidos}, {self.nombres} ({self.dni})'

    @property
    def nombre_completo(self):
        return f'{self.nombres} {self.apellidos}'

    @property
    def edad(self):
        hoy = timezone.localdate()
        nac = self.fecha_nacimiento
        return hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))

    def clean(self):
        dominio = settings.DOMINIO_EMAIL_INSTITUCIONAL
        if self.tipo == self.Tipo.ESTUDIANTE and self.email and \
                not self.email.lower().endswith(f'@{dominio}'):
            raise ValidationError({'email': f'Los estudiantes deben usar su correo @{dominio}.'})


# ---------------------------------------------------------------------------
# Citas
# ---------------------------------------------------------------------------
class CitaQuerySet(models.QuerySet):
    def activas(self):
        return self.exclude(estado=Cita.Estado.CANCELADA)

    def con_relaciones(self):
        return self.select_related(
            'paciente', 'medico__usuario', 'medico__especialidad',
        )

    def visibles_para(self, usuario):
        """Restringe el queryset según el rol del usuario."""
        if usuario.has_perm('clinica.ver_agenda_completa'):
            return self
        if usuario.es_medico:
            return self.filter(medico__usuario=usuario)
        if usuario.es_paciente:
            return self.filter(paciente__usuario=usuario)
        return self.none()


class Cita(models.Model):
    class Estado(models.TextChoices):
        PROGRAMADA = 'programada', 'Programada'
        COMPLETADA = 'completada', 'Completada'
        CANCELADA = 'cancelada', 'Cancelada'
        NO_ASISTIO = 'no_asistio', 'No asistió'

    paciente = models.ForeignKey(Paciente, on_delete=models.PROTECT, related_name='citas')
    medico = models.ForeignKey(Medico, on_delete=models.PROTECT, related_name='citas')
    fecha = models.DateField()
    hora = models.TimeField()
    duracion_minutos = models.PositiveSmallIntegerField(default=30)
    motivo = models.TextField('motivo de consulta', validators=[MinLengthValidator(20)])
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PROGRAMADA)

    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')
    cancelada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='+')
    motivo_cancelacion = models.CharField(max_length=255, blank=True)
    fecha_cancelacion = models.DateTimeField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    objects = CitaQuerySet.as_manager()

    class Meta:
        ordering = ['fecha', 'hora']
        permissions = [
            ('agendar_cita', 'Puede agendar citas'),
            ('cancelar_cita', 'Puede cancelar citas'),
            ('ver_agenda_completa', 'Puede ver la agenda de todos los médicos'),
            ('generar_reportes', 'Puede generar reportes y estadísticas'),
        ]
        constraints = [
            # Respaldo a nivel de BD: un médico no puede tener dos citas activas en el mismo slot.
            models.UniqueConstraint(
                fields=['medico', 'fecha', 'hora'], condition=~Q(estado='cancelada'),
                name='cita_slot_medico_unico',
                violation_error_message='El médico ya tiene una cita en ese horario.',
            ),
        ]
        indexes = [models.Index(fields=['fecha', 'estado'])]

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y} {self.hora:%H:%M} · {self.paciente.nombre_completo} · {self.medico}'

    @property
    def hora_fin(self):
        return hora_fin(self.hora, self.duracion_minutos)

    @property
    def es_futura(self):
        ahora = timezone.localtime()
        return (self.fecha, self.hora) >= (ahora.date(), ahora.time())

    @property
    def puede_cancelarse(self):
        return self.estado == self.Estado.PROGRAMADA and self.es_futura

    def puede_ser_cancelada_por(self, usuario):
        if not (usuario.has_perm('clinica.cancelar_cita') and self.puede_cancelarse):
            return False
        if usuario.has_perm('clinica.ver_agenda_completa'):
            return True  # Recepción
        if usuario.es_medico:
            return self.medico.usuario_id == usuario.pk
        if usuario.es_paciente:
            return self.paciente.usuario_id == usuario.pk
        return False


# ---------------------------------------------------------------------------
# Consultas (historial clínico): inmutables, solo admiten notas
# ---------------------------------------------------------------------------
class RegistroInmutableMixin:
    MENSAJE_INMUTABLE = 'Este registro clínico no puede modificarse ni eliminarse.'

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise PermissionDenied(self.MENSAJE_INMUTABLE)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied(self.MENSAJE_INMUTABLE)


class Consulta(RegistroInmutableMixin, models.Model):
    MENSAJE_INMUTABLE = 'Las consultas registradas no pueden modificarse; agregue una nota.'

    cita = models.OneToOneField(Cita, on_delete=models.PROTECT, related_name='consulta')
    paciente = models.ForeignKey(Paciente, on_delete=models.PROTECT, related_name='consultas')
    medico = models.ForeignKey(Medico, on_delete=models.PROTECT, related_name='consultas')
    diagnostico = models.TextField('diagnóstico')
    tratamiento = models.TextField()
    medicamentos = models.TextField('medicamentos recetados', blank=True)
    proxima_cita_control = models.DateField('próxima cita de control', null=True, blank=True)
    adjuntos = models.TextField(
        'documentos adjuntos', blank=True,
        help_text='Descripción de recetas, exámenes u otros documentos (uno por línea).',
    )
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_registro']

    def __str__(self):
        return f'Consulta {self.cita.fecha:%d/%m/%Y} · {self.paciente.nombre_completo}'

    @property
    def lista_adjuntos(self):
        return [linea for linea in self.adjuntos.splitlines() if linea.strip()]


class NotaConsulta(RegistroInmutableMixin, models.Model):
    consulta = models.ForeignKey(Consulta, on_delete=models.PROTECT, related_name='notas')
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    texto = models.TextField(validators=[MinLengthValidator(5)])
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['fecha']
        verbose_name = 'nota de consulta'
        verbose_name_plural = 'notas de consulta'

    def __str__(self):
        return f'Nota de {self.autor} ({self.fecha:%d/%m/%Y %H:%M})'


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
class AccesoHistorial(models.Model):
    """Registro legal de quién accedió a qué historial clínico."""

    class Resultado(models.TextChoices):
        PERMITIDO = 'permitido', 'Permitido'
        DENEGADO = 'denegado', 'Denegado'

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, related_name='+')
    username = models.CharField(max_length=150)
    rol = models.CharField(max_length=15, blank=True)
    paciente = models.ForeignKey(Paciente, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='accesos')
    recurso = models.CharField(max_length=80)
    resultado = models.CharField(max_length=10, choices=Resultado.choices)
    ip = models.GenericIPAddressField(null=True, blank=True)
    ruta = models.CharField(max_length=255)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'acceso a historial clínico'
        verbose_name_plural = 'accesos a historiales clínicos'

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y %H:%M} · {self.username} → {self.paciente or "-"}'


class RegistroCambio(models.Model):
    """Trazabilidad: quién, cuándo y qué cambió en cada registro."""

    class Accion(models.TextChoices):
        CREACION = 'creacion', 'Creación'
        MODIFICACION = 'modificacion', 'Modificación'
        ELIMINACION = 'eliminacion', 'Eliminación'
        SESION = 'sesion', 'Evento de sesión'

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name='+')
    username = models.CharField(max_length=150, blank=True)
    accion = models.CharField(max_length=15, choices=Accion.choices)
    modelo = models.CharField(max_length=50)
    objeto_id = models.CharField(max_length=40, blank=True)
    objeto = models.CharField(max_length=200, blank=True)
    cambios = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'registro de cambio'
        verbose_name_plural = 'registros de cambios'

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y %H:%M} · {self.username or "sistema"} · {self.get_accion_display()} {self.modelo}'

    @property
    def cambios_legibles(self):
        lineas = []
        for campo, valor in (self.cambios or {}).items():
            if isinstance(valor, list) and len(valor) == 2:
                lineas.append(f'{campo}: {valor[0]} → {valor[1]}')
            else:
                lineas.append(f'{campo}: {valor}')
        return lineas
