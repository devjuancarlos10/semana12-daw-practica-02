"""Formularios con validaciones complejas y cruzadas."""
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.utils import timezone

from .horarios import hora_fin, rangos_se_superponen, validar_horario_centro
from .models import Cita, Consulta, Especialidad, Medico, NotaConsulta, Paciente, Usuario
from .validators import normalizar_nombre, sanitizar_texto


class EstiloMixin:
    def _estilizar(self):
        for campo in self.fields.values():
            w = campo.widget
            if isinstance(w, (forms.RadioSelect, forms.CheckboxInput)):
                continue
            clase = 'textarea' if isinstance(w, forms.Textarea) else (
                'select' if isinstance(w, forms.Select) else 'input')
            w.attrs.setdefault('class', clase)


class FechaInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%d', **kwargs)


# ---------------------------------------------------------------------------
# Login diferenciado por tipo de usuario
# ---------------------------------------------------------------------------
class LoginDiferenciadoForm(EstiloMixin, AuthenticationForm):
    tipo_usuario = forms.ChoiceField(
        label='Ingresar como', choices=Usuario.Rol.choices, initial=Usuario.Rol.PACIENTE,
        widget=forms.RadioSelect,
    )

    error_messages = {
        'invalid_login': 'Usuario o contraseña incorrectos.',
        'inactive': 'La cuenta está desactivada.',
        'tipo_incorrecto': 'Las credenciales no corresponden al tipo de usuario seleccionado.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuario'
        self.fields['username'].widget.attrs.update({'placeholder': 'DNI o usuario asignado'})
        self._estilizar()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.rol != self.cleaned_data.get('tipo_usuario') and not user.is_superuser:
            raise forms.ValidationError(self.error_messages['tipo_incorrecto'], code='tipo_incorrecto')


# ---------------------------------------------------------------------------
# Pacientes
# ---------------------------------------------------------------------------
class PacienteForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Paciente
        fields = ['dni', 'nombres', 'apellidos', 'fecha_nacimiento', 'tipo',
                  'codigo_institucional', 'email', 'telefono', 'alergias']
        widgets = {
            'fecha_nacimiento': FechaInput(),
            'alergias': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Ninguna conocida'}),
            'dni': forms.TextInput(attrs={'inputmode': 'numeric', 'maxlength': 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        dominio = settings.DOMINIO_EMAIL_INSTITUCIONAL
        self.fields['email'].help_text = f'Obligatorio @{dominio} para estudiantes.'
        self._estilizar()

    def clean_dni(self):
        dni = self.cleaned_data['dni'].strip()
        if not dni.isdigit() or len(dni) != 8:
            raise forms.ValidationError('El DNI debe tener exactamente 8 dígitos numéricos.')
        return dni

    def clean_nombres(self):
        return normalizar_nombre(self.cleaned_data['nombres'])

    def clean_apellidos(self):
        return normalizar_nombre(self.cleaned_data['apellidos'])

    def clean_fecha_nacimiento(self):
        fecha = self.cleaned_data['fecha_nacimiento']
        hoy = timezone.localdate()
        if fecha > hoy:
            raise forms.ValidationError('La fecha de nacimiento no puede ser futura.')
        if (hoy - fecha).days > 120 * 365:
            raise forms.ValidationError('Verifique la fecha de nacimiento.')
        return fecha

    def clean_codigo_institucional(self):
        return self.cleaned_data['codigo_institucional'].strip().upper()

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def clean_alergias(self):
        return sanitizar_texto(self.cleaned_data.get('alergias'), 500)

    def clean(self):
        datos = super().clean()
        dominio = settings.DOMINIO_EMAIL_INSTITUCIONAL
        email = datos.get('email')
        if datos.get('tipo') == Paciente.Tipo.ESTUDIANTE and email and not email.endswith(f'@{dominio}'):
            self.add_error('email', f'Los estudiantes deben registrar su correo institucional @{dominio}.')
        return datos


class BusquedaPacienteForm(EstiloMixin, forms.Form):
    q = forms.CharField(
        required=False, max_length=60, label='Buscar',
        widget=forms.TextInput(attrs={'placeholder': 'DNI, nombres o apellidos'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_q(self):
        return sanitizar_texto(self.cleaned_data.get('q'), 60)


# ---------------------------------------------------------------------------
# Citas
# ---------------------------------------------------------------------------
class MedicoSelect(forms.Select):
    """Select que añade data-especialidad a cada opción (filtrado en el cliente)."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcion = super().create_option(name, value, label, selected, index, subindex, attrs)
        instancia = getattr(value, 'instance', None)
        if instancia is not None:
            opcion['attrs']['data-especialidad'] = instancia.especialidad_id
        return opcion


class MedicoChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, medico):
        return f'{medico} · {medico.consultorio}'


class CitaForm(EstiloMixin, forms.ModelForm):
    """Agendamiento con validaciones cruzadas de horario y disponibilidad."""

    especialidad = forms.ModelChoiceField(queryset=Especialidad.objects.all(),
                                          empty_label='Seleccione una especialidad')
    medico = MedicoChoiceField(
        queryset=Medico.objects.none(), widget=MedicoSelect, label='Médico',
        empty_label='Seleccione un médico',
    )

    class Meta:
        model = Cita
        fields = ['paciente', 'especialidad', 'medico', 'fecha', 'hora', 'motivo']
        widgets = {
            'fecha': FechaInput(),
            'hora': forms.TimeInput(attrs={'type': 'time', 'step': 1800}, format='%H:%M'),
            'motivo': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'Describa sus síntomas o el motivo (mínimo 20 caracteres)',
            }),
        }
        help_texts = {'hora': 'Turnos de 30 minutos (ej.: 09:00, 09:30).'}

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.fields['medico'].queryset = (
            Medico.objects.filter(activo=True).select_related('usuario', 'especialidad')
        )
        if usuario.es_paciente:
            del self.fields['paciente']   # El paciente agenda para sí mismo
        else:
            self.fields['paciente'].queryset = Paciente.objects.all()
        self._estilizar()

    @property
    def paciente_seleccionado(self):
        if 'paciente' in self.fields:
            return self.cleaned_data.get('paciente')
        return self.usuario.perfil_paciente

    def clean_motivo(self):
        motivo = sanitizar_texto(self.cleaned_data.get('motivo'), 1000)
        if len(motivo) < 20:
            raise forms.ValidationError(
                f'Describa el motivo con al menos 20 caracteres (ingresó {len(motivo)}).'
            )
        return motivo

    def clean_fecha(self):
        fecha = self.cleaned_data['fecha']
        hoy = timezone.localdate()
        if fecha < hoy:
            raise forms.ValidationError('No se pueden agendar citas en fechas pasadas.')
        if fecha > hoy + timedelta(days=settings.CITA_DIAS_MAXIMOS_ANTICIPACION):
            raise forms.ValidationError(
                f'Solo se agenda con hasta {settings.CITA_DIAS_MAXIMOS_ANTICIPACION} días de anticipación.'
            )
        return fecha

    def clean_hora(self):
        hora = self.cleaned_data['hora']
        if hora.minute % settings.CITA_DURACION_MINUTOS or hora.second:
            raise forms.ValidationError('Seleccione un turno válido (en punto o y media).')
        return hora

    def clean(self):
        datos = super().clean()
        especialidad, medico = datos.get('especialidad'), datos.get('medico')
        fecha, hora = datos.get('fecha'), datos.get('hora')
        paciente = self.paciente_seleccionado

        if paciente is None and 'paciente' not in self.fields:
            raise forms.ValidationError('Su cuenta no tiene un perfil de paciente asociado.')
        if especialidad and medico and medico.especialidad_id != especialidad.pk:
            self.add_error('medico', 'El médico seleccionado no pertenece a esa especialidad.')
            return datos
        if not (fecha and hora):
            return datos

        ahora = timezone.localtime()
        if fecha == ahora.date() and hora <= ahora.time():
            self.add_error('hora', 'La hora seleccionada ya pasó.')
            return datos

        error_centro = validar_horario_centro(fecha, hora)
        if error_centro:
            self.add_error('hora', error_centro)
            return datos

        fin = hora_fin(hora)
        if medico:
            if not medico.atiende_en(fecha, hora):
                horarios = ', '.join(str(h) for h in medico.horarios.all()) or 'sin horario'
                self.add_error('hora', f'{medico} no atiende en ese horario. Horario: {horarios}.')
                return datos
            # Validación: el médico no puede tener citas superpuestas
            for otra in Cita.objects.activas().filter(medico=medico, fecha=fecha).exclude(pk=self.instance.pk):
                if rangos_se_superponen(hora, fin, otra.hora, otra.hora_fin):
                    self.add_error(
                        'hora',
                        f'El turno ya está ocupado ({otra.hora:%H:%M}-{otra.hora_fin:%H:%M}). '
                        'Elija otro horario.',
                    )
                    return datos

        # Validación cruzada: un paciente no puede tener 2 citas el mismo día
        if paciente and Cita.objects.activas().filter(
            paciente=paciente, fecha=fecha, estado=Cita.Estado.PROGRAMADA,
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                f'{paciente.nombre_completo} ya tiene una cita programada el {fecha:%d/%m/%Y}.',
                code='cita_duplicada',
            )
        return datos

    def save(self, commit=True):
        cita = super().save(commit=False)
        cita.paciente = self.paciente_seleccionado
        cita.duracion_minutos = settings.CITA_DURACION_MINUTOS
        cita.creada_por = self.usuario
        if commit:
            cita.save()
        return cita


class CancelarCitaForm(EstiloMixin, forms.Form):
    motivo = forms.CharField(
        label='Motivo de la cancelación', min_length=10, max_length=255,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_motivo(self):
        motivo = sanitizar_texto(self.cleaned_data['motivo'], 255)
        if len(motivo) < 10:
            raise forms.ValidationError('Indique el motivo (mínimo 10 caracteres).')
        return motivo


class FiltroAgendaForm(EstiloMixin, forms.Form):
    fecha = forms.DateField(required=False, widget=FechaInput())
    medico = forms.ModelChoiceField(
        queryset=Medico.objects.select_related('usuario'), required=False, empty_label='Todos',
    )
    estado = forms.ChoiceField(choices=[('', 'Todos'), *Cita.Estado.choices], required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()


# ---------------------------------------------------------------------------
# Consultas (post-atención)
# ---------------------------------------------------------------------------
class ConsultaForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Consulta
        fields = ['diagnostico', 'tratamiento', 'medicamentos', 'proxima_cita_control', 'adjuntos']
        widgets = {
            'diagnostico': forms.Textarea(attrs={'rows': 3}),
            'tratamiento': forms.Textarea(attrs={'rows': 3}),
            'medicamentos': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Medicamento, dosis y frecuencia (uno por línea)'}),
            'adjuntos': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Receta N.º 0012\nHemograma completo'}),
            'proxima_cita_control': FechaInput(),
        }

    def __init__(self, *args, cita, **kwargs):
        super().__init__(*args, **kwargs)
        self.cita = cita
        self._estilizar()

    def _texto(self, campo, minimo=0, maximo=2000):
        valor = sanitizar_texto(self.cleaned_data.get(campo), maximo)
        if minimo and len(valor) < minimo:
            raise forms.ValidationError(f'Ingrese al menos {minimo} caracteres.')
        return valor

    def clean_diagnostico(self):
        return self._texto('diagnostico', minimo=10)

    def clean_tratamiento(self):
        return self._texto('tratamiento', minimo=10)

    def clean_medicamentos(self):
        return self._texto('medicamentos')

    def clean_adjuntos(self):
        return self._texto('adjuntos', maximo=1000)

    def clean_proxima_cita_control(self):
        fecha = self.cleaned_data.get('proxima_cita_control')
        if fecha and fecha <= max(self.cita.fecha, timezone.localdate()):
            raise forms.ValidationError('La cita de control debe ser posterior a hoy.')
        return fecha

    @transaction.atomic
    def save(self, commit=True):
        consulta = super().save(commit=False)
        consulta.cita = self.cita
        consulta.paciente = self.cita.paciente
        consulta.medico = self.cita.medico
        consulta.save()
        self.cita.estado = Cita.Estado.COMPLETADA
        self.cita.save(update_fields=['estado', 'actualizado'])
        return consulta


class NotaConsultaForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = NotaConsulta
        fields = ['texto']
        widgets = {'texto': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Agregar nota aclaratoria…'})}
        labels = {'texto': 'Nueva nota'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_texto(self):
        texto = sanitizar_texto(self.cleaned_data['texto'], 1000)
        if len(texto) < 5:
            raise forms.ValidationError('La nota debe tener al menos 5 caracteres.')
        return texto
