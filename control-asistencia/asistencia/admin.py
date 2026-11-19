"""Django Admin para operaciones de RRHH (con vistas restringidas por rol)."""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import format_html

from .models import (
    Area,
    Asistencia,
    CorreccionAsistencia,
    Empleado,
    Notificacion,
    Permiso,
    RegistroAuditoria,
    Usuario,
    horas_por_empleado,
)

COLORES = {
    'pendiente': ('#fef9c3', '#a16207'), 'aprobado': ('#dcfce7', '#15803d'),
    'rechazado': ('#fee2e2', '#b91c1c'), 'extra': ('#e0e7ff', '#4338ca'),
    'ok': ('#dcfce7', '#15803d'), 'alerta': ('#fee2e2', '#b91c1c'),
}


def badge(clave, texto):
    fondo, color = COLORES.get(clave, ('#f1f5f9', '#334155'))
    return format_html('<span style="background:{};color:{};padding:2px 10px;border-radius:999px;'
                       'font-weight:600;font-size:11px;white-space:nowrap">{}</span>', fondo, color, texto)


class AlcanceJerarquicoMixin:
    """Jefes de área solo ven registros de su área; RRHH y contador, todos."""

    ruta_empleado = 'empleado'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        if user.es_rrhh or user.has_perm('asistencia.ver_reportes_globales'):
            return qs
        area = user.area_a_cargo
        if area:
            return qs.filter(**{f'{self.ruta_empleado}__area': area} if self.ruta_empleado else {'area': area})
        return qs.none()


class SoloLecturaMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def exportar_texto(modeladmin, request, queryset):
    """Exportación simulada: genera un archivo de texto con los registros seleccionados."""
    lineas = [f'Exportación de {queryset.model._meta.verbose_name_plural} · '
              f'{timezone.localtime():%d/%m/%Y %H:%M} · {request.user.username}', '-' * 70]
    lineas += [str(obj) for obj in queryset]
    respuesta = HttpResponse('\n'.join(lineas), content_type='text/plain; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="{queryset.model._meta.model_name}.txt"'
    return respuesta


exportar_texto.short_description = 'Exportar seleccionados a texto'


# ---------------------------------------------------------------------------
# Usuarios y áreas
# ---------------------------------------------------------------------------
@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ('username', 'get_full_name', 'rol', 'is_active', 'last_login')
    list_filter = ('rol', 'is_active')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Datos', {'fields': ('first_name', 'last_name', 'email', 'rol')}),
        ('Estado', {'fields': ('is_active', 'is_superuser', 'last_login', 'date_joined')}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': (
        'username', 'first_name', 'last_name', 'email', 'rol', 'password1', 'password2')}),)
    readonly_fields = ('last_login', 'date_joined')


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'jefe', 'total_empleados')
    list_select_related = ('jefe',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_n=Count('empleados', filter=Q(empleados__activo=True)))

    @admin.display(description='Empleados activos', ordering='_n')
    def total_empleados(self, obj):
        return obj._n


# ---------------------------------------------------------------------------
# Empleados
# ---------------------------------------------------------------------------
@admin.register(Empleado)
class EmpleadoAdmin(AlcanceJerarquicoMixin, admin.ModelAdmin):
    ruta_empleado = None
    list_display = ('dni', 'apellidos', 'nombres', 'cargo', 'area', 'tipo_contrato',
                    'antiguedad_col', 'vacaciones_col', 'activo')
    list_filter = ('area', 'tipo_contrato', 'activo')
    search_fields = ('dni', 'nombres', 'apellidos', 'email_corporativo', 'cargo')
    list_select_related = ('area', 'usuario')
    actions = [exportar_texto, 'desactivar_cuentas']

    def get_fields(self, request, obj=None):
        campos = ['usuario', 'dni', 'nombres', 'apellidos', 'cargo', 'area', 'fecha_ingreso',
                  'tipo_contrato', 'email_corporativo', 'salario', 'hora_entrada', 'hora_salida', 'activo']
        if not request.user.has_perm('asistencia.ver_salarios'):
            campos.remove('salario')  # Confidencialidad: salario solo para RRHH
        return campos

    @admin.display(description='Antigüedad', ordering='fecha_ingreso')
    def antiguedad_col(self, obj):
        return obj.antiguedad_texto

    @admin.display(description='Vacaciones disponibles')
    def vacaciones_col(self, obj):
        dias = obj.dias_vacaciones_disponibles
        return badge('ok' if dias >= 10 else 'pendiente', f'{dias} de 30 días')

    @admin.action(description='Desactivar cuentas seleccionadas', permissions=['change'])
    def desactivar_cuentas(self, request, queryset):
        n = 0
        for empleado in queryset.filter(activo=True):
            empleado.activo = False
            empleado.save(update_fields=['activo'])
            n += 1
        self.message_user(request, f'{n} cuenta(s) desactivadas. El acceso se bloquea de inmediato.')


# ---------------------------------------------------------------------------
# Asistencia: vista diaria, reporte de horas extra y exportación
# ---------------------------------------------------------------------------
class HorasExtraFilter(admin.SimpleListFilter):
    title = 'horas extra'
    parameter_name = 'extra'

    def lookups(self, request, model_admin):
        return [('si', 'Con horas extra'), ('no', 'Sin horas extra'), ('abierta', 'Sin salida')]

    def queryset(self, request, queryset):
        if self.value() == 'si':
            return queryset.filter(horas_extra__gt=0)
        if self.value() == 'no':
            return queryset.filter(horas_extra=0, hora_salida__isnull=False)
        if self.value() == 'abierta':
            return queryset.filter(hora_salida__isnull=True)
        return queryset


@admin.register(Asistencia)
class AsistenciaAdmin(AlcanceJerarquicoMixin, admin.ModelAdmin):
    change_list_template = 'admin/asistencia/asistencia/change_list.html'
    list_display = ('fecha', 'empleado', 'area', 'entrada', 'salida', 'horas_trabajadas',
                    'extra_col', 'tardanza_col', 'corregida')
    list_filter = ('fecha', 'empleado__area', HorasExtraFilter, 'corregida')
    search_fields = ('empleado__dni', 'empleado__nombres', 'empleado__apellidos')
    date_hierarchy = 'fecha'
    list_select_related = ('empleado__area',)
    readonly_fields = ('horas_trabajadas', 'horas_extra', 'creado', 'actualizado')
    actions = [exportar_texto]
    list_per_page = 50

    @admin.display(description='Área', ordering='empleado__area__nombre')
    def area(self, obj):
        return obj.empleado.area

    @admin.display(description='Entrada', ordering='hora_entrada')
    def entrada(self, obj):
        return timezone.localtime(obj.hora_entrada).strftime('%H:%M')

    @admin.display(description='Salida', ordering='hora_salida')
    def salida(self, obj):
        return timezone.localtime(obj.hora_salida).strftime('%H:%M') if obj.hora_salida else badge('pendiente', 'En curso')

    @admin.display(description='Horas extra', ordering='horas_extra')
    def extra_col(self, obj):
        return badge('extra', f'+{obj.horas_extra} h') if obj.horas_extra else '—'

    @admin.display(description='Tardanza')
    def tardanza_col(self, obj):
        minutos = obj.tardanza_minutos
        return badge('alerta', f'{minutos} min') if minutos > 10 else '—'

    def get_urls(self):
        return [
            path('horas-extra/', self.admin_site.admin_view(self.reporte_horas_extra),
                 name='asistencia_asistencia_horas_extra'),
        ] + super().get_urls()

    def reporte_horas_extra(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        hoy = timezone.localdate()
        desde = parse_date(request.GET.get('desde', '')) or hoy.replace(day=1)
        hasta = parse_date(request.GET.get('hasta', '')) or hoy
        qs = self.get_queryset(request).filter(fecha__range=(desde, hasta))
        filas = [f for f in horas_por_empleado(qs) if f['total_extra']]
        filas.sort(key=lambda f: f['total_extra'], reverse=True)
        contexto = {
            **self.admin_site.each_context(request), 'opts': self.model._meta,
            'title': 'Reporte de horas extra por empleado', 'desde': desde, 'hasta': hasta,
            'filas': filas, 'total': qs.aggregate(t=Sum('horas_extra'))['t'] or 0,
        }
        return TemplateResponse(request, 'admin/asistencia/asistencia/horas_extra.html', contexto)

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}),
                         'url_horas_extra': reverse('admin:asistencia_asistencia_horas_extra'),
                         'url_hoy': f"?fecha__gte={timezone.localdate():%Y-%m-%d}"}
        return super().changelist_view(request, extra_context)


# ---------------------------------------------------------------------------
# Permisos: pendientes, aprobación masiva
# ---------------------------------------------------------------------------
class EstadoPermisoFilter(admin.SimpleListFilter):
    """Por defecto muestra los permisos pendientes de aprobación."""
    title = 'estado'
    parameter_name = 'estado'

    def lookups(self, request, model_admin):
        return [*Permiso.Estado.choices, ('todos', 'Todos')]

    def choices(self, changelist):
        for valor, etiqueta in self.lookup_choices:
            yield {
                'selected': (self.value() or 'pendiente') == valor,
                'query_string': changelist.get_query_string({self.parameter_name: valor}),
                'display': etiqueta,
            }

    def queryset(self, request, queryset):
        valor = self.value() or 'pendiente'
        return queryset if valor == 'todos' else queryset.filter(estado=valor)


@admin.register(Permiso)
class PermisoAdmin(AlcanceJerarquicoMixin, admin.ModelAdmin):
    list_display = ('empleado', 'area', 'tipo', 'fecha_inicio', 'fecha_fin', 'dias_col',
                    'estado_col', 'revisado_por', 'creado')
    list_filter = (EstadoPermisoFilter, 'tipo', 'empleado__area', 'fecha_inicio')
    search_fields = ('empleado__dni', 'empleado__apellidos', 'motivo')
    list_select_related = ('empleado__area', 'revisado_por')
    readonly_fields = ('empleado', 'tipo', 'fecha_inicio', 'fecha_fin', 'motivo', 'adjunto_descripcion',
                       'estado', 'revisado_por', 'fecha_revision', 'comentario_revision', 'creado')
    actions = ['aprobar_seleccionados', 'rechazar_seleccionados', exportar_texto]

    def has_add_permission(self, request):
        return False

    @admin.display(description='Área')
    def area(self, obj):
        return obj.empleado.area

    @admin.display(description='Días')
    def dias_col(self, obj):
        return obj.dias

    @admin.display(description='Estado', ordering='estado')
    def estado_col(self, obj):
        return badge(obj.estado, obj.get_estado_display())

    def _resolver(self, request, queryset, aprobado):
        if not request.user.has_perm('asistencia.aprobar_permiso'):
            raise PermissionDenied
        revisables = Permiso.objects.revisables_por(request.user).filter(
            pk__in=queryset.values('pk'), estado=Permiso.Estado.PENDIENTE)
        n = 0
        for permiso in revisables.select_related('empleado__usuario'):
            permiso.resolver(request.user, aprobado, 'Resuelto desde el panel de RRHH.')
            n += 1
        omitidos = queryset.count() - n
        verbo = 'aprobados' if aprobado else 'rechazados'
        self.message_user(request, f'{n} permiso(s) {verbo}. Se notificó a cada empleado.', messages.SUCCESS)
        if omitidos:
            self.message_user(request, f'{omitidos} omitido(s): ya resueltos o fuera de su ámbito.',
                              messages.WARNING)

    @admin.action(description='Aprobar permisos seleccionados', permissions=['change'])
    def aprobar_seleccionados(self, request, queryset):
        self._resolver(request, queryset, True)

    @admin.action(description='Rechazar permisos seleccionados', permissions=['change'])
    def rechazar_seleccionados(self, request, queryset):
        self._resolver(request, queryset, False)


# ---------------------------------------------------------------------------
# Auditoría y otros (solo lectura)
# ---------------------------------------------------------------------------
@admin.register(CorreccionAsistencia)
class CorreccionAsistenciaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('fecha', 'asistencia', 'motivo', 'corregido_por')
    list_filter = ('motivo', 'fecha')
    list_select_related = ('asistencia__empleado', 'corregido_por')


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('fecha', 'username', 'accion', 'modelo', 'objeto', 'ip')
    list_filter = ('accion', 'modelo', 'fecha')
    search_fields = ('username', 'objeto', 'ip')
    date_hierarchy = 'fecha'


@admin.register(Notificacion)
class NotificacionAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('fecha', 'usuario', 'mensaje', 'leida')
    list_filter = ('leida',)
