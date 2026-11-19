"""Django Admin para la gestión operativa del centro médico."""
from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import format_html

from .models import (
    AccesoHistorial,
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

COLORES = {
    'programada': ('#fef9c3', '#a16207'),
    'completada': ('#dcfce7', '#15803d'),
    'cancelada': ('#fee2e2', '#b91c1c'),
    'no_asistio': ('#fde2e2', '#7f1d1d'),
    'permitido': ('#dcfce7', '#15803d'),
    'denegado': ('#fee2e2', '#b91c1c'),
}


def badge(clave, texto):
    fondo, color = COLORES.get(clave, ('#f1f5f9', '#334155'))
    return format_html(
        '<span style="background:{};color:{};padding:2px 10px;border-radius:999px;'
        'font-weight:600;font-size:11px;white-space:nowrap">{}</span>', fondo, color, texto,
    )


class SoloLecturaMixin:
    """Registros de auditoría: nadie puede crearlos, editarlos ni borrarlos."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Usuarios, especialidades y médicos
# ---------------------------------------------------------------------------
@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ('username', 'get_full_name', 'rol', 'email', 'is_active', 'last_login')
    list_filter = ('rol', 'is_active')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Datos personales', {'fields': ('first_name', 'last_name', 'email')}),
        ('Rol (define grupo y permisos)', {'fields': ('rol',)}),
        ('Estado', {'fields': ('is_active', 'is_superuser', 'last_login', 'date_joined')}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': (
        'username', 'first_name', 'last_name', 'email', 'rol', 'password1', 'password2')}),)
    readonly_fields = ('last_login', 'date_joined')


@admin.register(Especialidad)
class EspecialidadAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'medicos_activos', 'descripcion')
    search_fields = ('nombre',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _medicos=Count('medicos', filter=Q(medicos__activo=True)))

    @admin.display(description='Médicos activos', ordering='_medicos')
    def medicos_activos(self, obj):
        return obj._medicos


class HorarioInline(admin.TabularInline):
    model = HorarioAtencion
    extra = 0
    min_num = 1


@admin.register(Medico)
class MedicoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'cmp', 'especialidad', 'consultorio', 'citas_hoy',
                    'total_consultas', 'activo')
    list_filter = ('especialidad', 'activo')
    search_fields = ('cmp', 'usuario__first_name', 'usuario__last_name')
    list_select_related = ('usuario', 'especialidad')
    inlines = [HorarioInline]

    def get_queryset(self, request):
        hoy = timezone.localdate()
        return super().get_queryset(request).annotate(
            _hoy=Count('citas', filter=Q(citas__fecha=hoy) & ~Q(citas__estado='cancelada'), distinct=True),
            _consultas=Count('consultas', distinct=True),
        )

    @admin.display(description='Médico', ordering='usuario__last_name')
    def nombre(self, obj):
        return str(obj)

    @admin.display(description='Citas hoy', ordering='_hoy')
    def citas_hoy(self, obj):
        return obj._hoy

    @admin.display(description='Consultas', ordering='_consultas')
    def total_consultas(self, obj):
        return obj._consultas


# ---------------------------------------------------------------------------
# Pacientes
# ---------------------------------------------------------------------------
@admin.register(Paciente)
class PacienteAdmin(admin.ModelAdmin):
    list_display = ('dni', 'apellidos', 'nombres', 'tipo', 'edad_col', 'codigo_institucional',
                    'total_citas', 'dias_ultima_consulta')
    list_filter = ('tipo',)
    search_fields = ('dni', 'nombres', 'apellidos', 'codigo_institucional')
    readonly_fields = ('creado', 'registrado_por')
    list_per_page = 25

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _citas=Count('citas', distinct=True),
            _ultima=Max('consultas__fecha_registro'),
        )

    @admin.display(description='Edad', ordering='-fecha_nacimiento')
    def edad_col(self, obj):
        return f'{obj.edad} años'

    @admin.display(description='Citas', ordering='_citas')
    def total_citas(self, obj):
        return obj._citas

    @admin.display(description='Días desde última consulta', ordering='-_ultima')
    def dias_ultima_consulta(self, obj):
        if not obj._ultima:
            return '—'
        return (timezone.localdate() - timezone.localtime(obj._ultima).date()).days

    def save_model(self, request, obj, form, change):
        if not change:
            obj.registrado_por = request.user
        super().save_model(request, obj, form, change)


# ---------------------------------------------------------------------------
# Citas: agenda diaria, estadísticas y acciones masivas
# ---------------------------------------------------------------------------
class PeriodoFilter(admin.SimpleListFilter):
    title = 'periodo'
    parameter_name = 'periodo'

    def lookups(self, request, model_admin):
        return [('hoy', 'Hoy'), ('semana', 'Próximos 7 días'), ('pasadas', 'Pasadas')]

    def queryset(self, request, queryset):
        hoy = timezone.localdate()
        if self.value() == 'hoy':
            return queryset.filter(fecha=hoy)
        if self.value() == 'semana':
            return queryset.filter(fecha__range=(hoy, hoy + timedelta(days=7)))
        if self.value() == 'pasadas':
            return queryset.filter(fecha__lt=hoy)
        return queryset


@admin.register(Cita)
class CitaAdmin(admin.ModelAdmin):
    change_list_template = 'admin/clinica/cita/change_list.html'
    list_display = ('fecha', 'hora', 'medico', 'especialidad', 'paciente_col', 'estado_col', 'creada_por')
    list_filter = (PeriodoFilter, 'estado', 'medico__especialidad', 'medico', 'fecha')
    search_fields = ('paciente__dni', 'paciente__nombres', 'paciente__apellidos',
                     'medico__usuario__last_name', 'medico__cmp')
    date_hierarchy = 'fecha'
    list_select_related = ('paciente', 'medico__usuario', 'medico__especialidad', 'creada_por')
    raw_id_fields = ('paciente',)
    readonly_fields = ('creada_por', 'cancelada_por', 'fecha_cancelacion', 'creado', 'actualizado')
    actions = ('marcar_completadas', 'marcar_no_asistio')
    ordering = ('-fecha', 'hora')

    @admin.display(description='Especialidad', ordering='medico__especialidad__nombre')
    def especialidad(self, obj):
        return obj.medico.especialidad

    @admin.display(description='Paciente', ordering='paciente__apellidos')
    def paciente_col(self, obj):
        return f'{obj.paciente.nombre_completo} ({obj.paciente.dni})'

    @admin.display(description='Estado', ordering='estado')
    def estado_col(self, obj):
        return badge(obj.estado, obj.get_estado_display())

    def _actualizar(self, request, queryset, estado, etiqueta):
        hoy = timezone.localdate()
        aplicables = queryset.filter(estado=Cita.Estado.PROGRAMADA, fecha__lte=hoy)
        cantidad = 0
        for cita in aplicables:  # save() individual → queda en el registro de cambios
            cita.estado = estado
            cita.save(update_fields=['estado', 'actualizado'])
            cantidad += 1
        self.message_user(request, f'{cantidad} cita(s) marcadas como {etiqueta}.', messages.SUCCESS)
        omitidas = queryset.count() - cantidad
        if omitidas:
            self.message_user(
                request, f'{omitidas} omitida(s): solo aplica a citas programadas de hoy o anteriores.',
                messages.WARNING,
            )

    @admin.action(description='Marcar citas como completadas', permissions=['change'])
    def marcar_completadas(self, request, queryset):
        self._actualizar(request, queryset, Cita.Estado.COMPLETADA, 'completadas')

    @admin.action(description='Marcar como «No asistió»', permissions=['change'])
    def marcar_no_asistio(self, request, queryset):
        self._actualizar(request, queryset, Cita.Estado.NO_ASISTIO, 'inasistencias')

    # -- Vistas adicionales del admin ------------------------------------------
    def get_urls(self):
        propias = [
            path('agenda/', self.admin_site.admin_view(self.agenda_view), name='clinica_cita_agenda'),
            path('estadisticas/', self.admin_site.admin_view(self.estadisticas_view),
                 name='clinica_cita_estadisticas'),
        ]
        return propias + super().get_urls()

    def agenda_view(self, request):
        """Agenda diaria agrupada por médico."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        fecha = parse_date(request.GET.get('fecha', '')) or timezone.localdate()
        citas = (
            Cita.objects.filter(fecha=fecha).select_related(
                'paciente', 'medico__usuario', 'medico__especialidad')
            .order_by('medico__usuario__last_name', 'hora')
        )
        agenda = {}
        for cita in citas:
            agenda.setdefault(cita.medico, []).append(cita)
        contexto = {
            **self.admin_site.each_context(request),
            'title': f'Agenda diaria · {fecha:%d/%m/%Y}',
            'opts': self.model._meta,
            'fecha': fecha,
            'anterior': fecha - timedelta(days=1),
            'siguiente': fecha + timedelta(days=1),
            'agenda': agenda,
            'total': citas.count(),
        }
        return TemplateResponse(request, 'admin/clinica/cita/agenda.html', contexto)

    def estadisticas_view(self, request):
        if not request.user.has_perm('clinica.generar_reportes'):
            raise PermissionDenied
        from .views import estadisticas
        contexto = {
            **self.admin_site.each_context(request),
            'title': 'Estadísticas de atención',
            'opts': self.model._meta,
            'etiquetas_estado': dict(Cita.Estado.choices),
            **estadisticas(),
        }
        return TemplateResponse(request, 'admin/clinica/cita/estadisticas.html', contexto)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['url_agenda'] = reverse('admin:clinica_cita_agenda')
        if request.user.has_perm('clinica.generar_reportes'):
            extra_context['url_estadisticas'] = reverse('admin:clinica_cita_estadisticas')
        return super().changelist_view(request, extra_context)


# ---------------------------------------------------------------------------
# Consultas: inmutables (solo se agregan notas)
# ---------------------------------------------------------------------------
class NotaInline(admin.TabularInline):
    model = NotaConsulta
    extra = 1
    fields = ('texto', 'autor', 'fecha')
    readonly_fields = ('autor', 'fecha')
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False  # Las notas existentes no se editan; solo se agregan nuevas


@admin.register(Consulta)
class ConsultaAdmin(admin.ModelAdmin):
    list_display = ('cita', 'paciente', 'medico', 'fecha_registro', 'proxima_cita_control')
    list_select_related = ('cita', 'paciente', 'medico__usuario', 'cita__medico__usuario', 'cita__paciente')
    search_fields = ('paciente__dni', 'paciente__apellidos')
    inlines = [NotaInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def save_formset(self, request, form, formset, change):
        notas = formset.save(commit=False)
        for nota in notas:
            nota.autor = request.user
            nota.save()


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
@admin.register(AccesoHistorial)
class AccesoHistorialAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('fecha', 'username', 'rol', 'recurso', 'paciente', 'resultado_col', 'ip')
    list_filter = ('resultado', 'rol', 'recurso', 'fecha')
    search_fields = ('username', 'paciente__dni', 'paciente__apellidos', 'ip')
    date_hierarchy = 'fecha'
    list_select_related = ('paciente',)

    @admin.display(description='Resultado', ordering='resultado')
    def resultado_col(self, obj):
        return badge(obj.resultado, obj.get_resultado_display())


@admin.register(RegistroCambio)
class RegistroCambioAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('fecha', 'username', 'accion', 'modelo', 'objeto', 'ip')
    list_filter = ('accion', 'modelo', 'fecha')
    search_fields = ('username', 'objeto', 'objeto_id')
    date_hierarchy = 'fecha'
