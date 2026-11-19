"""Formularios de asistencia, permisos y empleados."""
import secrets
from datetime import datetime, timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.utils import timezone

from .models import Area, Asistencia, CorreccionAsistencia, Empleado, Permiso, Usuario
from .validators import normalizar_nombre, sanitizar_texto


class EstiloMixin:
    def _estilizar(self):
        for campo in self.fields.values():
            w = campo.widget
            if isinstance(w, (forms.RadioSelect, forms.CheckboxInput, forms.HiddenInput)):
                continue
            clase = 'textarea' if isinstance(w, forms.Textarea) else (
                'select' if isinstance(w, forms.Select) else 'input')
            w.attrs.setdefault('class', clase)


class FechaInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%d', **kwargs)


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class LoginForm(EstiloMixin, AuthenticationForm):
    error_messages = {
        'invalid_login': 'Usuario o contraseña incorrectos.',
        'inactive': 'La cuenta está desactivada. Comuníquese con Recursos Humanos.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuario corporativo'
        self.fields['username'].widget.attrs['placeholder'] = 'p. ej. jquispe'
        self._estilizar()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        perfil = user.perfil
        if perfil is not None and not perfil.activo:
            raise forms.ValidationError(self.error_messages['inactive'], code='inactive')


# ---------------------------------------------------------------------------
# Empleados (RRHH)
# ---------------------------------------------------------------------------
class EmpleadoForm(EstiloMixin, forms.ModelForm):
    rol = forms.ChoiceField(choices=Usuario.Rol.choices, initial=Usuario.Rol.EMPLEADO,
                            help_text='Define los permisos del usuario en el sistema.')

    class Meta:
        model = Empleado
        fields = ['dni', 'nombres', 'apellidos', 'cargo', 'area', 'fecha_ingreso', 'tipo_contrato',
                  'email_corporativo', 'salario', 'hora_entrada', 'hora_salida']
        widgets = {
            'fecha_ingreso': FechaInput(),
            'hora_entrada': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'hora_salida': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['rol'].initial = self.instance.usuario.rol
        self._estilizar()

    def clean_dni(self):
        dni = self.cleaned_data['dni'].strip()
        if not (dni.isdigit() and len(dni) == 8):
            raise forms.ValidationError('El DNI debe tener exactamente 8 dígitos.')
        if Empleado.objects.filter(dni=dni).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un empleado registrado con este DNI.')
        return dni

    def clean_nombres(self):
        return normalizar_nombre(self.cleaned_data['nombres'])

    def clean_apellidos(self):
        return normalizar_nombre(self.cleaned_data['apellidos'])

    def clean_cargo(self):
        cargo = sanitizar_texto(self.cleaned_data.get('cargo'), 80)
        if not cargo:
            raise forms.ValidationError('El cargo no puede estar vacío.')
        return cargo

    def clean_email_corporativo(self):
        email = self.cleaned_data['email_corporativo'].strip().lower()
        dominio = settings.DOMINIO_EMAIL_CORPORATIVO
        if not email.endswith(f'@{dominio}'):
            raise forms.ValidationError(f'El correo debe ser corporativo (@{dominio}).')
        username = email.split('@')[0]
        usuario_actual = self.instance.usuario_id if self.instance.pk else None
        if Usuario.objects.filter(username=username).exclude(pk=usuario_actual).exists():
            raise forms.ValidationError('Ya existe un usuario con ese correo corporativo.')
        return email

    def clean_fecha_ingreso(self):
        fecha = self.cleaned_data['fecha_ingreso']
        if fecha > timezone.localdate():
            raise forms.ValidationError('La fecha de ingreso no puede ser futura.')
        return fecha

    def clean(self):
        datos = super().clean()
        entrada, salida = datos.get('hora_entrada'), datos.get('hora_salida')
        if entrada and salida and salida <= entrada:
            self.add_error('hora_salida', 'La hora de salida debe ser posterior a la de entrada.')
        return datos

    @transaction.atomic
    def save(self, commit=True):
        """Crea/actualiza el empleado y su usuario. Devuelve (empleado, clave_temporal)."""
        empleado = super().save(commit=False)
        clave = None
        if empleado.pk:
            usuario = empleado.usuario
        else:
            username = empleado.email_corporativo.split('@')[0]
            usuario = Usuario(username=username)
            clave = secrets.token_urlsafe(9)
            usuario.set_password(clave)
        usuario.first_name, usuario.last_name = empleado.nombres, empleado.apellidos
        usuario.email = empleado.email_corporativo
        usuario.rol = self.cleaned_data['rol']
        usuario.save()
        empleado.usuario = usuario
        empleado.save()
        return empleado, clave


# ---------------------------------------------------------------------------
# Asistencia
# ---------------------------------------------------------------------------
class MarcacionForm(forms.Form):
    """Marcación de entrada/salida. El timestamp lo pone el servidor al enviar."""

    tipo = forms.ChoiceField(choices=[('entrada', 'Entrada'), ('salida', 'Salida')],
                             widget=forms.HiddenInput)
    observacion = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, empleado, **kwargs):
        super().__init__(*args, **kwargs)
        self.empleado = empleado
        self.momento = timezone.now()   # Registro automático del timestamp
        self.abierta = Asistencia.objects.filter(
            empleado=empleado, fecha=timezone.localdate(), hora_salida__isnull=True,
        ).first()

    def clean_observacion(self):
        return sanitizar_texto(self.cleaned_data.get('observacion'), 255)

    def clean(self):
        datos = super().clean()
        tipo = datos.get('tipo')
        if tipo == 'entrada' and self.abierta:
            raise forms.ValidationError(
                f'Ya registró su entrada a las {timezone.localtime(self.abierta.hora_entrada):%H:%M} '
                'y aún no marca la salida.'
            )
        if tipo == 'salida':
            if not self.abierta:
                raise forms.ValidationError('No tiene una entrada abierta hoy: marque primero su entrada.')
            if self.momento <= self.abierta.hora_entrada:
                raise forms.ValidationError('La hora de salida debe ser posterior a la de entrada.')
        return datos

    def guardar(self):
        observacion = self.cleaned_data.get('observacion', '')
        if self.cleaned_data['tipo'] == 'entrada':
            return Asistencia.objects.create(
                empleado=self.empleado, fecha=timezone.localdate(self.momento),
                hora_entrada=self.momento, observacion=observacion,
            )
        registro = self.abierta
        registro.hora_salida = self.momento
        if observacion:
            registro.observacion = observacion
        registro.save()  # calcula horas trabajadas y extra
        return registro


class ObservacionAsistenciaForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Asistencia
        fields = ['observacion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_observacion(self):
        return sanitizar_texto(self.cleaned_data.get('observacion'), 255)


class CorreccionAsistenciaForm(EstiloMixin, forms.Form):
    """Corrección (solo RRHH) con justificación obligatoria de 50+ caracteres."""

    hora_entrada = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'))
    hora_salida = forms.TimeField(required=False,
                                  widget=forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'))
    motivo = forms.ChoiceField(choices=CorreccionAsistencia.Motivo.choices)
    justificacion = forms.CharField(
        label='Justificación', min_length=50, widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Mínimo 50 caracteres. Quedará registrada en la auditoría.',
    )

    def __init__(self, *args, asistencia, **kwargs):
        super().__init__(*args, **kwargs)
        self.asistencia = asistencia
        self.fields['hora_entrada'].initial = timezone.localtime(asistencia.hora_entrada).time()
        if asistencia.hora_salida:
            self.fields['hora_salida'].initial = timezone.localtime(asistencia.hora_salida).time()
        self._estilizar()

    def clean_justificacion(self):
        texto = sanitizar_texto(self.cleaned_data['justificacion'], 1000)
        if len(texto) < 50:
            raise forms.ValidationError(f'La justificación debe tener al menos 50 caracteres ({len(texto)}).')
        return texto

    def _combinar(self, hora):
        return timezone.make_aware(datetime.combine(self.asistencia.fecha, hora)) if hora else None

    def clean(self):
        datos = super().clean()
        entrada = self._combinar(datos.get('hora_entrada'))
        salida = self._combinar(datos.get('hora_salida'))
        if entrada and salida and salida <= entrada:
            self.add_error('hora_salida', 'La hora de salida debe ser posterior a la de entrada.')
        if entrada and entrada == self.asistencia.hora_entrada and salida == self.asistencia.hora_salida:
            raise forms.ValidationError('No hay cambios que corregir.')
        datos['entrada_dt'], datos['salida_dt'] = entrada, salida
        return datos

    @transaction.atomic
    def guardar(self, usuario):
        a = self.asistencia
        CorreccionAsistencia.objects.create(
            asistencia=a, corregido_por=usuario, motivo=self.cleaned_data['motivo'],
            justificacion=self.cleaned_data['justificacion'],
            entrada_anterior=a.hora_entrada, salida_anterior=a.hora_salida,
            entrada_nueva=self.cleaned_data['entrada_dt'], salida_nueva=self.cleaned_data['salida_dt'],
        )
        a.hora_entrada = self.cleaned_data['entrada_dt']
        a.hora_salida = self.cleaned_data['salida_dt']
        a.corregida = True
        a.save()
        return a


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------
class PermisoForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Permiso
        fields = ['tipo', 'fecha_inicio', 'fecha_fin', 'motivo', 'adjunto_descripcion']
        widgets = {
            'fecha_inicio': FechaInput(),
            'fecha_fin': FechaInput(),
            'motivo': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Explique el motivo (mínimo 30 caracteres)'}),
            'adjunto_descripcion': forms.TextInput(attrs={'placeholder': 'Ej.: Certificado médico N.º 1234'}),
        }

    def __init__(self, *args, empleado, **kwargs):
        super().__init__(*args, **kwargs)
        self.empleado = empleado
        self._estilizar()

    def clean_motivo(self):
        motivo = sanitizar_texto(self.cleaned_data.get('motivo'), 1000)
        if len(motivo) < 30:
            raise forms.ValidationError(f'Detalle el motivo con al menos 30 caracteres (ingresó {len(motivo)}).')
        return motivo

    def clean_adjunto_descripcion(self):
        return sanitizar_texto(self.cleaned_data.get('adjunto_descripcion'), 255)

    def clean_fecha_inicio(self):
        fecha = self.cleaned_data['fecha_inicio']
        if fecha < timezone.localdate() - timedelta(days=7):
            raise forms.ValidationError('Solo se aceptan solicitudes con hasta 7 días de retroactividad.')
        return fecha

    def clean(self):
        datos = super().clean()
        inicio, fin, tipo = datos.get('fecha_inicio'), datos.get('fecha_fin'), datos.get('tipo')
        if not (inicio and fin):
            return datos
        if fin < inicio:
            self.add_error('fecha_fin', 'La fecha de fin debe ser igual o posterior a la de inicio.')
            return datos

        superpuesto = Permiso.objects.vigentes().filter(
            empleado=self.empleado, fecha_inicio__lte=fin, fecha_fin__gte=inicio,
        ).exclude(pk=self.instance.pk).first()
        if superpuesto:
            raise forms.ValidationError(
                f'Se superpone con su solicitud de {superpuesto.get_tipo_display().lower()} del '
                f'{superpuesto.fecha_inicio:%d/%m/%Y} al {superpuesto.fecha_fin:%d/%m/%Y} '
                f'({superpuesto.get_estado_display().lower()}).',
                code='superpuesto',
            )

        if tipo == Permiso.Tipo.VACACIONES:
            solicitados = (fin - inicio).days + 1
            comprometidos = self.empleado.dias_vacaciones_tomados(inicio.year, incluir_pendientes=True)
            disponibles = settings.DIAS_VACACIONES_ANUALES - comprometidos
            if solicitados > disponibles:
                self.add_error(
                    'fecha_fin',
                    f'Solicita {solicitados} días de vacaciones y solo tiene {disponibles} disponibles '
                    '(se descuentan las solicitudes pendientes).',
                )
        return datos

    def save(self, commit=True):
        permiso = super().save(commit=False)
        permiso.empleado = self.empleado
        if commit:
            permiso.save()
        return permiso


class RevisionPermisoForm(EstiloMixin, forms.Form):
    decision = forms.ChoiceField(choices=[('aprobar', 'Aprobar'), ('rechazar', 'Rechazar')],
                                 widget=forms.HiddenInput)
    comentario = forms.CharField(required=False, max_length=255,
                                 widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_comentario(self):
        return sanitizar_texto(self.cleaned_data.get('comentario'), 255)

    def clean(self):
        datos = super().clean()
        if datos.get('decision') == 'rechazar' and len(datos.get('comentario', '')) < 10:
            self.add_error('comentario', 'Indique el motivo del rechazo (mínimo 10 caracteres).')
        return datos


class FiltroReporteForm(EstiloMixin, forms.Form):
    desde = forms.DateField(widget=FechaInput())
    hasta = forms.DateField(widget=FechaInput())
    area = forms.ModelChoiceField(queryset=Area.objects.all(), required=False, empty_label='Todas')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hoy = timezone.localdate()
        self.fields['desde'].initial = hoy.replace(day=1)
        self.fields['hasta'].initial = hoy
        self._estilizar()

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['hasta'] < datos['desde']:
            self.add_error('hasta', 'Debe ser igual o posterior a la fecha inicial.')
        return datos
