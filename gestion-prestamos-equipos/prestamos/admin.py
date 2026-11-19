"""Django Admin personalizado."""
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html

from .auditoria import marcar_accion
from .models import Equipo, Laboratorio, Prestamo, RegistroAuditoria, Usuario

Accion = RegistroAuditoria.Accion

COLORES_ESTADO = {
    # estado: (fondo, texto)
    'operativo': ('#dcfce7', '#166534'),
    'mantenimiento': ('#fef3c7', '#92400e'),
    'baja': ('#fee2e2', '#991b1b'),
    'pendiente': ('#e0e7ff', '#3730a3'),
    'activo': ('#dbeafe', '#1e40af'),
    'devuelto': ('#f1f5f9', '#334155'),
    'rechazado': ('#fee2e2', '#991b1b'),
    'vencido': ('#fecaca', '#7f1d1d'),
}


def badge(clave, texto):
    fondo, color = COLORES_ESTADO.get(clave, ('#f1f5f9', '#334155'))
    return format_html(
        '<span style="background:{};color:{};padding:2px 10px;border-radius:999px;'
        'font-weight:600;font-size:11px;white-space:nowrap">{}</span>',
        fondo, color, texto,
    )


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ('username', 'get_full_name', 'rol', 'email', 'codigo_institucional',
                    'prestamos_vigentes', 'is_active')
    list_filter = ('rol', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'email', 'codigo_institucional')
    ordering = ('last_name',)
    # Los grupos se gestionan automáticamente a partir del rol.
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Datos personales', {'fields': ('first_name', 'last_name', 'email', 'telefono')}),
        ('Datos institucionales', {'fields': ('rol', 'codigo_institucional', 'departamento')}),
        ('Estado', {'fields': ('is_active', 'is_superuser', 'last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'first_name', 'last_name', 'email', 'rol',
                       'codigo_institucional', 'password1', 'password2'),
        }),
    )
    readonly_fields = ('last_login', 'date_joined')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _vigentes=Count('prestamos', filter=Q(prestamos__estado__in=Prestamo.ESTADOS_VIGENTES))
        )

    @admin.display(description='Préstamos vigentes', ordering='_vigentes')
    def prestamos_vigentes(self, obj):
        return obj._vigentes


# ---------------------------------------------------------------------------
# Laboratorios
# ---------------------------------------------------------------------------
@admin.register(Laboratorio)
class LaboratorioAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'ubicacion', 'responsable', 'total_equipos')
    search_fields = ('codigo', 'nombre')
    list_select_related = ('responsable',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_total=Count('equipos'))

    @admin.display(description='Equipos', ordering='_total')
    def total_equipos(self, obj):
        return obj._total


# ---------------------------------------------------------------------------
# Equipos
# ---------------------------------------------------------------------------
@admin.register(Equipo)
class EquipoAdmin(admin.ModelAdmin):
    list_display = ('codigo_patrimonial', 'nombre', 'categoria', 'laboratorio',
                    'estado_visual', 'disponibilidad', 'dias_mantenimiento')
    list_filter = ('laboratorio', 'estado', 'categoria')
    search_fields = ('codigo_patrimonial', 'nombre', 'categoria', 'marca', 'modelo')
    list_select_related = ('laboratorio',)
    list_per_page = 25
    actions = ('marcar_mantenimiento', 'marcar_operativo')
    readonly_fields = ('creado', 'actualizado')
    fieldsets = (
        ('Identificación', {'fields': ('codigo_patrimonial', 'nombre', 'marca', 'modelo', 'categoria')}),
        ('Ubicación y estado', {'fields': ('laboratorio', 'estado', 'cantidad_total',
                                           'fecha_ultimo_mantenimiento')}),
        ('Otros', {'fields': ('descripcion', 'creado', 'actualizado')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).con_disponibilidad()

    # -- Campos calculados ----------------------------------------------------
    @admin.display(description='Estado', ordering='estado')
    def estado_visual(self, obj):
        return badge(obj.estado, obj.get_estado_display())

    @admin.display(description='Disponibles', ordering='unidades_disponibles')
    def disponibilidad(self, obj):
        return f'{obj.disponibles} / {obj.cantidad_total}'

    @admin.display(description='Días desde mant.', ordering='-fecha_ultimo_mantenimiento')
    def dias_mantenimiento(self, obj):
        dias = obj.dias_desde_mantenimiento
        if dias is None:
            return badge('baja', 'Sin registro')
        clave = 'operativo' if dias <= 180 else 'mantenimiento' if dias <= 365 else 'baja'
        return badge(clave, f'{dias} días')

    # -- Acciones masivas -----------------------------------------------------
    @admin.action(description='Marcar como en mantenimiento', permissions=['change'])
    def marcar_mantenimiento(self, request, queryset):
        seleccionados = Equipo.objects.filter(pk__in=list(queryset.values_list('pk', flat=True)))
        omitidos = list(seleccionados.filter(
            prestamos__estado=Prestamo.Estado.ACTIVO
        ).values_list('codigo_patrimonial', flat=True).distinct())
        candidatos = seleccionados.exclude(
            Q(codigo_patrimonial__in=omitidos) | Q(estado=Equipo.Estado.BAJA)
        )
        codigos = list(candidatos.values_list('codigo_patrimonial', flat=True))
        actualizados = candidatos.update(estado=Equipo.Estado.MANTENIMIENTO, actualizado=timezone.now())
        if actualizados:
            marcar_accion(request, Accion.MANTENIMIENTO, ', '.join(codigos))
            self.message_user(request, f'{actualizados} equipo(s) enviados a mantenimiento.',
                              messages.SUCCESS)
        if omitidos:
            self.message_user(
                request, f'Omitidos por tener préstamos en curso: {", ".join(omitidos)}.',
                messages.WARNING,
            )

    @admin.action(description='Marcar como operativo (mantenimiento concluido)', permissions=['change'])
    def marcar_operativo(self, request, queryset):
        actualizados = Equipo.objects.filter(
            pk__in=list(queryset.values_list('pk', flat=True)),
            estado=Equipo.Estado.MANTENIMIENTO,
        ).update(
            estado=Equipo.Estado.OPERATIVO,
            fecha_ultimo_mantenimiento=timezone.localdate(),
            actualizado=timezone.now(),
        )
        self.message_user(request, f'{actualizados} equipo(s) operativos nuevamente.')

    # -- Auditoría de eliminaciones desde el admin -------------------------
    def delete_model(self, request, obj):
        descripcion = str(obj)
        super().delete_model(request, obj)
        marcar_accion(request, Accion.EQUIPO_ELIMINADO, f'[admin] {descripcion}')

    def delete_queryset(self, request, queryset):
        descripcion = ', '.join(queryset.values_list('codigo_patrimonial', flat=True))
        super().delete_queryset(request, queryset)
        marcar_accion(request, Accion.EQUIPO_ELIMINADO, f'[admin] {descripcion}')


# ---------------------------------------------------------------------------
# Préstamos
# ---------------------------------------------------------------------------
class VencidoFilter(admin.SimpleListFilter):
    title = 'situación'
    parameter_name = 'situacion'

    def lookups(self, request, model_admin):
        return [('vencido', 'Vencidos'), ('a_tiempo', 'En curso a tiempo')]

    def queryset(self, request, queryset):
        hoy = timezone.localdate()
        if self.value() == 'vencido':
            return queryset.filter(estado=Prestamo.Estado.ACTIVO, fecha_devolucion_prevista__lt=hoy)
        if self.value() == 'a_tiempo':
            return queryset.filter(estado=Prestamo.Estado.ACTIVO, fecha_devolucion_prevista__gte=hoy)
        return queryset


@admin.register(Prestamo)
class PrestamoAdmin(admin.ModelAdmin):
    list_display = ('id', 'docente', 'equipo_codigo', 'cantidad', 'fecha_prestamo',
                    'fecha_devolucion_prevista', 'fecha_devolucion_real', 'estado_visual',
                    'dias_retraso_col', 'recordatorios_enviados')
    list_display_links = ('id', 'docente')
    list_filter = (VencidoFilter, 'estado', 'fecha_prestamo', 'fecha_devolucion_prevista',
                   ('docente', admin.RelatedOnlyFieldListFilter), 'equipo__laboratorio')
    search_fields = ('equipo__codigo_patrimonial', 'equipo__nombre', 'docente__first_name',
                     'docente__last_name', 'docente__username')
    date_hierarchy = 'fecha_prestamo'
    list_select_related = ('docente', 'equipo', 'equipo__laboratorio')
    raw_id_fields = ('equipo',)
    actions = ('enviar_recordatorio',)
    list_per_page = 25
    readonly_fields = ('registrado_por', 'recibido_por', 'recordatorios_enviados',
                       'ultimo_recordatorio', 'creado', 'actualizado')
    fieldsets = (
        ('Préstamo', {'fields': ('docente', 'equipo', 'cantidad', 'estado', 'observaciones')}),
        ('Fechas', {'fields': ('fecha_prestamo', 'fecha_devolucion_prevista', 'fecha_devolucion_real')}),
        ('Devolución', {'fields': ('estado_equipo_devolucion', 'observaciones_devolucion',
                                   'recibido_por')}),
        ('Trazabilidad', {'classes': ('collapse',), 'fields': (
            'motivo_rechazo', 'registrado_por', 'recordatorios_enviados',
            'ultimo_recordatorio', 'creado', 'actualizado')}),
    )

    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        # Solo quien puede registrar devoluciones edita los datos de retorno.
        if not request.user.has_perm('prestamos.registrar_devolucion'):
            campos += ['fecha_devolucion_real', 'estado_equipo_devolucion',
                       'observaciones_devolucion', 'estado']
        return campos

    @admin.display(description='Equipo', ordering='equipo__codigo_patrimonial')
    def equipo_codigo(self, obj):
        return f'{obj.equipo.codigo_patrimonial} · {obj.equipo.nombre}'

    @admin.display(description='Estado', ordering='estado')
    def estado_visual(self, obj):
        texto = 'Vencido' if obj.esta_vencido else obj.get_estado_display()
        return badge(obj.estado_visual, texto)

    @admin.display(description='Días de retraso')
    def dias_retraso_col(self, obj):
        dias = obj.dias_retraso
        if not dias:
            return '—'
        return format_html('<strong style="color:#b91c1c">{} día(s)</strong>', dias)

    @admin.action(description='Enviar recordatorio de devolución', permissions=['change'])
    def enviar_recordatorio(self, request, queryset):
        activos = queryset.filter(estado=Prestamo.Estado.ACTIVO).select_related('docente', 'equipo')
        enviados = 0
        for prestamo in activos:
            docente = prestamo.docente
            if not docente.email:
                continue
            situacion = (
                f'Está vencido hace {prestamo.dias_retraso} día(s).' if prestamo.esta_vencido
                else f'Vence el {prestamo.fecha_devolucion_prevista:%d/%m/%Y}.'
            )
            send_mail(
                subject=f'Recordatorio de devolución · {prestamo.equipo.codigo_patrimonial}',
                message=(
                    f'Estimado(a) {docente.get_full_name()}:\n\n'
                    f'Le recordamos devolver el equipo {prestamo.equipo} '
                    f'(cantidad: {prestamo.cantidad}). {situacion}\n\n'
                    'Coordinación de Laboratorios'
                ),
                from_email=None,
                recipient_list=[docente.email],
                fail_silently=False,
            )
            prestamo.recordatorios_enviados += 1
            prestamo.ultimo_recordatorio = timezone.now()
            prestamo.save(update_fields=['recordatorios_enviados', 'ultimo_recordatorio'])
            enviados += 1
        omitidos = queryset.count() - enviados
        if enviados:
            marcar_accion(request, Accion.RECORDATORIO, f'{enviados} recordatorio(s) enviados')
        self.message_user(request, f'Recordatorios enviados: {enviados}.', messages.SUCCESS)
        if omitidos:
            self.message_user(
                request, f'{omitidos} préstamo(s) omitidos (no están en curso o sin email).',
                messages.WARNING,
            )


# ---------------------------------------------------------------------------
# Auditoría (solo lectura, solo administradores)
# ---------------------------------------------------------------------------
@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'username', 'accion_visual', 'ip', 'metodo', 'ruta', 'descripcion')
    list_filter = ('accion', 'fecha')
    search_fields = ('username', 'ip', 'descripcion', 'ruta')
    date_hierarchy = 'fecha'
    list_per_page = 50

    @admin.display(description='Acción', ordering='accion')
    def accion_visual(self, obj):
        return badge('vencido' if obj.es_evento_seguridad else 'activo', obj.get_accion_display())

    # Los registros de auditoría son inmutables.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
