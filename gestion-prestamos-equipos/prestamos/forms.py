"""Formularios: ModelForms con validación avanzada y formularios clásicos."""
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.utils import timezone

from .models import Equipo, Laboratorio, Prestamo, Usuario
from .validators import sanitizar_texto


class FormularioEstilizadoMixin:
    """Aplica clases CSS homogéneas a todos los widgets."""

    def _estilizar(self):
        for campo in self.fields.values():
            widget = campo.widget
            clase = 'select' if isinstance(widget, forms.Select) else 'input'
            if isinstance(widget, forms.Textarea):
                clase = 'textarea'
            widget.attrs.setdefault('class', clase)


class FechaInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%d', **kwargs)


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class LoginForm(FormularioEstilizadoMixin, AuthenticationForm):
    """Login con credenciales institucionales (usuario y contraseña)."""

    error_messages = {
        'invalid_login': 'Usuario o contraseña incorrectos.',
        'inactive': 'La cuenta está desactivada. Contacte al administrador.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Usuario institucional'
        self.fields['username'].widget.attrs.update({'placeholder': 'p. ej. jperez', 'autofocus': True})
        self.fields['password'].widget.attrs.update({'placeholder': '••••••••'})
        self._estilizar()


# ---------------------------------------------------------------------------
# Equipos
# ---------------------------------------------------------------------------
class EquipoForm(FormularioEstilizadoMixin, forms.ModelForm):
    class Meta:
        model = Equipo
        fields = [
            'codigo_patrimonial', 'nombre', 'marca', 'modelo', 'categoria', 'estado',
            'laboratorio', 'cantidad_total', 'fecha_ultimo_mantenimiento', 'descripcion',
        ]
        widgets = {
            'fecha_ultimo_mantenimiento': FechaInput(),
            'descripcion': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_codigo_patrimonial(self):
        # Se normaliza ANTES de la validación del modelo (regex en mayúsculas).
        return self.cleaned_data['codigo_patrimonial'].strip().upper()

    def clean_nombre(self):
        nombre = sanitizar_texto(self.cleaned_data['nombre'], 120)
        if len(nombre) < 3:
            raise forms.ValidationError('El nombre debe tener al menos 3 caracteres.')
        return nombre

    def clean_marca(self):
        return sanitizar_texto(self.cleaned_data['marca'], 60)

    def clean_modelo(self):
        return sanitizar_texto(self.cleaned_data['modelo'], 60)

    def clean_descripcion(self):
        return sanitizar_texto(self.cleaned_data.get('descripcion'), 1000)

    def clean(self):
        datos = super().clean()
        cantidad = datos.get('cantidad_total')
        if self.instance.pk and cantidad:
            comprometidas = self.instance.cantidad_total - self.instance.disponibles
            if cantidad < comprometidas:
                self.add_error(
                    'cantidad_total',
                    f'Hay {comprometidas} unidad(es) prestadas o solicitadas; '
                    'no puede registrar menos unidades.',
                )
        if datos.get('estado') == Equipo.Estado.BAJA and self.instance.pk:
            if self.instance.prestamos.vigentes().exists():
                self.add_error('estado', 'No se puede dar de baja un equipo con préstamos vigentes.')
        return datos


class FiltroCatalogoForm(FormularioEstilizadoMixin, forms.Form):
    """Formulario clásico (no ligado a modelo) para búsqueda y filtros."""

    q = forms.CharField(
        label='Buscar', required=False, max_length=60,
        widget=forms.TextInput(attrs={'placeholder': 'Código, nombre o categoría'}),
    )
    laboratorio = forms.ModelChoiceField(
        queryset=Laboratorio.objects.all(), required=False, empty_label='Todos los laboratorios',
    )
    estado = forms.ChoiceField(
        choices=[('', 'Todos los estados'), *Equipo.Estado.choices], required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_q(self):
        return sanitizar_texto(self.cleaned_data.get('q'), 60)


# ---------------------------------------------------------------------------
# Préstamos
# ---------------------------------------------------------------------------
class EquipoChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, equipo):
        if equipo.es_operativo:
            estado = f'{equipo.disponibles} disp.'
        else:
            estado = equipo.get_estado_display().lower()
        return f'{equipo.codigo_patrimonial} · {equipo.nombre} ({estado})'


class SolicitudPrestamoForm(FormularioEstilizadoMixin, forms.ModelForm):
    """Solicitud/registro de préstamo con validaciones de negocio.

    - Docente: solicita para sí mismo (el campo docente se oculta).
    - Asistente: registra el préstamo de cualquier docente.
    """

    equipo = EquipoChoiceField(queryset=Equipo.objects.none())

    class Meta:
        model = Prestamo
        fields = [
            'docente', 'equipo', 'cantidad', 'fecha_prestamo',
            'fecha_devolucion_prevista', 'observaciones',
        ]
        widgets = {
            'fecha_prestamo': FechaInput(),
            'fecha_devolucion_prevista': FechaInput(),
            'observaciones': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'Uso previsto, curso, aula… (opcional)',
            }),
        }
        help_texts = {
            'fecha_devolucion_prevista': 'Debe ser posterior a la fecha de préstamo.',
        }

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        # Se excluyen equipos dados de baja; los que están en mantenimiento se
        # muestran (con su estado) y se rechazan en clean_equipo con un mensaje claro.
        self.fields['equipo'].queryset = (
            Equipo.objects.exclude(estado=Equipo.Estado.BAJA)
            .con_disponibilidad().select_related('laboratorio')
        )
        if usuario.has_perm('prestamos.gestionar_prestamos'):
            self.fields['docente'].queryset = Usuario.objects.filter(
                rol=Usuario.Rol.DOCENTE, is_active=True,
            )
        else:
            del self.fields['docente']
        self.fields['fecha_prestamo'].initial = timezone.localdate()
        self._estilizar()

    @property
    def docente_seleccionado(self):
        if 'docente' in self.fields:
            return self.cleaned_data.get('docente')
        return self.usuario

    # -- Validaciones por campo ------------------------------------------------
    def clean_equipo(self):
        equipo = self.cleaned_data['equipo']
        if equipo.estado != Equipo.Estado.OPERATIVO:
            raise forms.ValidationError(
                f'El equipo está "{equipo.get_estado_display()}". '
                'Solo se prestan equipos en estado operativo.'
            )
        return equipo

    def clean_fecha_prestamo(self):
        fecha = self.cleaned_data['fecha_prestamo']
        if fecha < timezone.localdate():
            raise forms.ValidationError('La fecha de préstamo no puede estar en el pasado.')
        return fecha

    def clean_observaciones(self):
        return sanitizar_texto(self.cleaned_data.get('observaciones'), 500)

    # -- Validaciones cruzadas -------------------------------------------------
    def clean(self):
        datos = super().clean()
        inicio = datos.get('fecha_prestamo')
        fin = datos.get('fecha_devolucion_prevista')
        equipo = datos.get('equipo')
        cantidad = datos.get('cantidad')
        docente = self.docente_seleccionado

        if inicio and fin:
            if fin <= inicio:
                self.add_error(
                    'fecha_devolucion_prevista',
                    'La fecha de devolución debe ser posterior a la fecha de préstamo.',
                )
            elif fin - inicio > timedelta(days=settings.PRESTAMOS_PLAZO_MAXIMO_DIAS):
                self.add_error(
                    'fecha_devolucion_prevista',
                    f'El plazo máximo es de {settings.PRESTAMOS_PLAZO_MAXIMO_DIAS} días.',
                )

        if docente:
            limite = settings.PRESTAMOS_MAX_ACTIVOS_POR_DOCENTE
            vigentes = Prestamo.objects.vigentes().filter(docente=docente).count()
            if vigentes >= limite:
                raise forms.ValidationError(
                    f'{docente} ya tiene {vigentes} préstamos activos o solicitados. '
                    f'El máximo permitido es {limite}.',
                    code='limite_prestamos',
                )

        if equipo and cantidad and cantidad > equipo.disponibles:
            self.add_error(
                'cantidad',
                f'Stock insuficiente: solicitó {cantidad} y hay {equipo.disponibles} '
                f'unidad(es) disponible(s) de {equipo.codigo_patrimonial}.',
            )
        return datos

    def save(self, commit=True):
        prestamo = super().save(commit=False)
        prestamo.docente = self.docente_seleccionado
        if self.usuario.has_perm('prestamos.gestionar_prestamos'):
            # Registro directo por un asistente: el equipo se entrega en el acto.
            prestamo.estado = Prestamo.Estado.ACTIVO
            prestamo.registrado_por = self.usuario
        else:
            prestamo.estado = Prestamo.Estado.PENDIENTE
        if commit:
            prestamo.save()
        return prestamo


class RechazoPrestamoForm(FormularioEstilizadoMixin, forms.Form):
    motivo = forms.CharField(
        max_length=255, min_length=10, widget=forms.Textarea(attrs={'rows': 2}),
        label='Motivo del rechazo',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estilizar()

    def clean_motivo(self):
        motivo = sanitizar_texto(self.cleaned_data['motivo'], 255)
        if len(motivo) < 10:
            raise forms.ValidationError('Describa el motivo (mínimo 10 caracteres).')
        return motivo


class DevolucionForm(FormularioEstilizadoMixin, forms.Form):
    """Formulario clásico de devolución con registro del estado del equipo."""

    fecha_devolucion = forms.DateField(label='Fecha de devolución', widget=FechaInput())
    estado_equipo = forms.ChoiceField(
        label='Estado del equipo al retornar', choices=Equipo.Estado.choices,
        initial=Equipo.Estado.OPERATIVO,
    )
    observaciones = forms.CharField(
        required=False, max_length=500,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Daños, faltantes, accesorios…'}),
    )

    def __init__(self, *args, prestamo, **kwargs):
        super().__init__(*args, **kwargs)
        self.prestamo = prestamo
        self.fields['fecha_devolucion'].initial = timezone.localdate()
        self._estilizar()

    def clean_fecha_devolucion(self):
        fecha = self.cleaned_data['fecha_devolucion']
        if fecha < self.prestamo.fecha_prestamo:
            raise forms.ValidationError('No puede ser anterior a la fecha del préstamo.')
        if fecha > timezone.localdate():
            raise forms.ValidationError('No puede registrar una devolución futura.')
        return fecha

    def clean_observaciones(self):
        return sanitizar_texto(self.cleaned_data.get('observaciones'), 500)

    def clean(self):
        datos = super().clean()
        if datos.get('estado_equipo') != Equipo.Estado.OPERATIVO and not datos.get('observaciones'):
            self.add_error(
                'observaciones',
                'Describa el problema cuando el equipo no retorna en estado operativo.',
            )
        return datos

    @transaction.atomic
    def guardar(self, usuario):
        prestamo = self.prestamo
        estado_equipo = self.cleaned_data['estado_equipo']
        prestamo.estado = Prestamo.Estado.DEVUELTO
        prestamo.fecha_devolucion_real = self.cleaned_data['fecha_devolucion']
        prestamo.estado_equipo_devolucion = estado_equipo
        prestamo.observaciones_devolucion = self.cleaned_data['observaciones']
        prestamo.recibido_por = usuario
        prestamo.save()

        equipo = prestamo.equipo
        if estado_equipo != Equipo.Estado.OPERATIVO and equipo.estado != estado_equipo:
            equipo.estado = estado_equipo
            equipo.save(update_fields=['estado', 'actualizado'])
        return prestamo
