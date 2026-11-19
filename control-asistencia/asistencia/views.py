"""Vistas del sistema de asistencia y permisos con autorización jerárquica."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .decorators import requiere_algun_permiso, requiere_empleado, valida_horario_marcacion
from .forms import (
    CorreccionAsistenciaForm,
    EmpleadoForm,
    FiltroReporteForm,
    MarcacionForm,
    ObservacionAsistenciaForm,
    PermisoForm,
    RevisionPermisoForm,
)
from .models import (
    Area,
    Asistencia,
    Empleado,
    Notificacion,
    Permiso,
    RegistroAuditoria,
    horas_por_empleado,
)


def _paginar(request, queryset, por_pagina=20):
    return Paginator(queryset, por_pagina).get_page(request.GET.get('pagina'))


# ---------------------------------------------------------------------------
# Panel y marcación rápida
# ---------------------------------------------------------------------------
def inicio(request):
    return redirect('asistencia:dashboard' if request.user.is_authenticated else 'login')


@login_required
def dashboard(request):
    user = request.user
    empleado = user.perfil
    hoy = timezone.localdate()
    contexto = {'hoy': hoy, 'empleado': empleado}
    if empleado:
        contexto.update({
            'registros_hoy': empleado.asistencias.filter(fecha=hoy).order_by('hora_entrada'),
            'abierta': empleado.asistencias.filter(fecha=hoy, hora_salida__isnull=True).first(),
            'mis_pendientes': empleado.permisos.filter(estado=Permiso.Estado.PENDIENTE).count(),
            'mes': empleado.asistencias.filter(fecha__year=hoy.year, fecha__month=hoy.month).aggregate(
                horas=Sum('horas_trabajadas'), extra=Sum('horas_extra'), dias=Count('fecha', distinct=True)),
        })
    if user.has_perm('asistencia.aprobar_permiso'):
        contexto['por_aprobar'] = (
            Permiso.objects.revisables_por(user).filter(estado=Permiso.Estado.PENDIENTE)
            .select_related('empleado__area')[:6]
        )
    if user.es_rrhh or user.has_perm('asistencia.ver_reportes_globales') or user.area_a_cargo:
        empleados = Empleado.objects.visibles_para(user).filter(activo=True)
        presentes = Asistencia.objects.filter(fecha=hoy, empleado__in=empleados).values('empleado').distinct().count()
        contexto['equipo'] = {
            'total': empleados.count(),
            'presentes': presentes,
            'ausentes': empleados.count() - presentes,
            'en_permiso': Permiso.objects.filter(
                empleado__in=empleados, estado=Permiso.Estado.APROBADO,
                fecha_inicio__lte=hoy, fecha_fin__gte=hoy).count(),
        }
    return render(request, 'asistencia/dashboard.html', contexto)


@login_required
@permission_required('asistencia.registrar_asistencia', raise_exception=True)
@requiere_empleado
@valida_horario_marcacion
@require_POST
def marcar(request):
    form = MarcacionForm(request.POST, empleado=request.user.perfil)
    if form.is_valid():
        registro = form.guardar()
        if form.cleaned_data['tipo'] == 'entrada':
            hora = timezone.localtime(registro.hora_entrada)
            tarde = f' (tardanza: {registro.tardanza_minutos} min)' if registro.tardanza_minutos else ''
            messages.success(request, f'Entrada registrada a las {hora:%H:%M:%S}{tarde}.')
        else:
            extra = f' · {registro.horas_extra} h extra' if registro.horas_extra else ''
            messages.success(request, f'Salida registrada. Horas trabajadas: {registro.horas_trabajadas}{extra}.')
    else:
        for error in form.non_field_errors():
            messages.error(request, error)
    return redirect('asistencia:dashboard')


@login_required
@requiere_empleado
def mi_asistencia(request):
    registros = request.user.perfil.asistencias.all()
    return render(request, 'asistencia/mi_asistencia.html', {
        'pagina': _paginar(request, registros),
        'horas_edicion': settings.HORAS_EDICION_EMPLEADO,
    })


@login_required
@requiere_empleado
def asistencia_observacion(request, pk):
    """El empleado solo puede editar su registro dentro de las primeras 24 horas."""
    registro = get_object_or_404(Asistencia, pk=pk, empleado=request.user.perfil)
    if not registro.editable_por_empleado:
        raise PermissionDenied(
            f'Los registros no pueden modificarse después de {settings.HORAS_EDICION_EMPLEADO} horas. '
            'Solicite una corrección a RRHH.'
        )
    form = ObservacionAsistenciaForm(request.POST or None, instance=registro)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Observación actualizada.')
        return redirect('asistencia:mi_asistencia')
    return render(request, 'asistencia/observacion_form.html', {'form': form, 'registro': registro})


# ---------------------------------------------------------------------------
# Asistencia del personal (RRHH / jefes) y corrección (solo RRHH)
# ---------------------------------------------------------------------------
@login_required
@requiere_algun_permiso('asistencia.ver_reportes_area', 'asistencia.ver_reportes_globales')
def asistencia_diaria(request):
    fecha = parse_date(request.GET.get('fecha', '')) or timezone.localdate()
    empleados = Empleado.objects.visibles_para(request.user).filter(activo=True).select_related('area')
    area_id = request.GET.get('area', '')
    if area_id.isdigit():
        empleados = empleados.filter(area_id=area_id)
    registros = {}
    for a in Asistencia.objects.filter(fecha=fecha, empleado__in=empleados).order_by('hora_entrada'):
        registros.setdefault(a.empleado_id, []).append(a)
    permisos = {
        p.empleado_id: p for p in Permiso.objects.filter(
            empleado__in=empleados, estado=Permiso.Estado.APROBADO,
            fecha_inicio__lte=fecha, fecha_fin__gte=fecha)
    }
    filas = [{'empleado': e, 'registros': registros.get(e.pk, []), 'permiso': permisos.get(e.pk)}
             for e in empleados]
    return render(request, 'asistencia/asistencia_diaria.html', {
        'fecha': fecha, 'filas': filas,
        'areas': Area.objects.filter(empleados__in=empleados).distinct(),
        'area_actual': area_id,
    })


@login_required
@permission_required('asistencia.corregir_asistencia', raise_exception=True)
def asistencia_corregir(request, pk):
    registro = get_object_or_404(Asistencia.objects.select_related('empleado'), pk=pk)
    form = CorreccionAsistenciaForm(request.POST or None, asistencia=registro)
    if request.method == 'POST' and form.is_valid():
        form.guardar(request.user)
        messages.success(request, f'Registro de {registro.empleado} corregido. Horas: {registro.horas_trabajadas}.')
        return redirect(f"{reverse('asistencia:asistencia_diaria')}?fecha={registro.fecha:%Y-%m-%d}")
    return render(request, 'asistencia/correccion_form.html', {
        'form': form, 'registro': registro, 'historial': registro.correcciones.select_related('corregido_por'),
    })


# ---------------------------------------------------------------------------
# Permisos y vacaciones
# ---------------------------------------------------------------------------
@login_required
@permission_required('asistencia.solicitar_permiso', raise_exception=True)
@requiere_empleado
def permiso_nuevo(request):
    empleado = request.user.perfil
    form = PermisoForm(request.POST or None, empleado=empleado)
    if request.method == 'POST' and form.is_valid():
        permiso = form.save()
        messages.success(request, f'Solicitud enviada ({permiso.dias} día(s)). Estado: pendiente de aprobación.')
        return redirect('asistencia:mis_permisos')
    return render(request, 'asistencia/permiso_form.html', {
        'form': form, 'empleado': empleado,
        'comprometidos': empleado.dias_vacaciones_tomados(incluir_pendientes=True),
    })


@login_required
@requiere_empleado
def mis_permisos(request):
    empleado = request.user.perfil
    return render(request, 'asistencia/mis_permisos.html', {
        'pagina': _paginar(request, empleado.permisos.select_related('revisado_por')),
        'empleado': empleado,
    })


@login_required
@permission_required('asistencia.aprobar_permiso', raise_exception=True)
def bandeja_permisos(request):
    estado = request.GET.get('estado', Permiso.Estado.PENDIENTE)
    permisos = Permiso.objects.revisables_por(request.user).select_related(
        'empleado__area', 'revisado_por')
    if estado in Permiso.Estado.values:
        permisos = permisos.filter(estado=estado)
    return render(request, 'asistencia/bandeja_permisos.html', {
        'pagina': _paginar(request, permisos), 'estado_actual': estado,
        'estados': Permiso.Estado.choices, 'area': request.user.area_a_cargo,
    })


@login_required
def permiso_detalle(request, pk):
    permiso = get_object_or_404(Permiso.objects.select_related('empleado__area', 'revisado_por'), pk=pk)
    es_propio = permiso.empleado.usuario_id == request.user.pk
    puede_revisar = permiso.puede_ser_revisado_por(request.user)
    ve_como_revisor = Permiso.objects.revisables_por(request.user).filter(pk=pk).exists()
    if not (es_propio or ve_como_revisor):
        raise PermissionDenied('Solo puede ver sus permisos o los de su equipo.')
    form = RevisionPermisoForm(request.POST or None)
    if request.method == 'POST':
        if not puede_revisar:
            raise PermissionDenied('No puede resolver esta solicitud.')
        if form.is_valid():
            aprobado = form.cleaned_data['decision'] == 'aprobar'
            permiso.resolver(request.user, aprobado, form.cleaned_data['comentario'])
            messages.success(request, f'Solicitud {permiso.get_estado_display().lower()}. Se notificó al empleado.')
            return redirect('asistencia:bandeja_permisos')
    return render(request, 'asistencia/permiso_detalle.html', {
        'permiso': permiso, 'puede_revisar': puede_revisar, 'form': form,
        'disponibles': permiso.empleado.dias_vacaciones_disponibles,
    })


@login_required
def notificaciones(request):
    lista = request.user.notificaciones.select_related('permiso')
    if request.method == 'POST':
        lista.filter(leida=False).update(leida=True)
        return redirect('asistencia:notificaciones')
    return render(request, 'asistencia/notificaciones.html', {'pagina': _paginar(request, lista)})


# ---------------------------------------------------------------------------
# Empleados
# ---------------------------------------------------------------------------
@login_required
@permission_required('asistencia.view_empleado', raise_exception=True)
def empleado_lista(request):
    empleados = Empleado.objects.visibles_para(request.user).select_related('area', 'usuario')
    q = request.GET.get('q', '').strip()[:60]
    if q:
        empleados = empleados.filter(Q(dni__startswith=q) | Q(nombres__icontains=q) | Q(apellidos__icontains=q))
    return render(request, 'asistencia/empleado_lista.html', {
        'pagina': _paginar(request, empleados), 'q': q,
        'ver_salarios': request.user.has_perm('asistencia.ver_salarios'),
    })


@login_required
@permission_required('asistencia.add_empleado', raise_exception=True)
def empleado_nuevo(request):
    form = EmpleadoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        empleado, clave = form.save()
        messages.success(
            request,
            f'Empleado {empleado.nombre_completo} registrado. Usuario: {empleado.usuario.username} · '
            f'contraseña temporal: {clave} (entréguela de forma segura).',
        )
        return redirect('asistencia:empleado_lista')
    return render(request, 'asistencia/empleado_form.html', {'form': form, 'titulo': 'Registrar empleado'})


@login_required
@permission_required('asistencia.change_empleado', raise_exception=True)
def empleado_editar(request, pk):
    empleado = get_object_or_404(Empleado, pk=pk)
    form = EmpleadoForm(request.POST or None, instance=empleado)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Datos actualizados.')
        return redirect('asistencia:empleado_lista')
    return render(request, 'asistencia/empleado_form.html', {
        'form': form, 'titulo': f'Editar · {empleado.nombre_completo}'})


# ---------------------------------------------------------------------------
# Reportes y exportación
# ---------------------------------------------------------------------------
def _datos_reporte(request):
    user = request.user
    form = FiltroReporteForm(request.GET or None)
    if not user.has_perm('asistencia.ver_reportes_globales'):
        del form.fields['area']  # Jefe: solo su área
    hoy = timezone.localdate()
    desde, hasta = hoy.replace(day=1), hoy
    asistencias = Asistencia.objects.filter(empleado__in=Empleado.objects.visibles_para(user))
    if form.is_valid():
        desde, hasta = form.cleaned_data['desde'], form.cleaned_data['hasta']
        if form.cleaned_data.get('area'):
            asistencias = asistencias.filter(empleado__area=form.cleaned_data['area'])
    asistencias = asistencias.filter(fecha__range=(desde, hasta))
    filas = list(horas_por_empleado(asistencias))
    totales = asistencias.aggregate(horas=Sum('horas_trabajadas'), extra=Sum('horas_extra'))
    return form, desde, hasta, filas, totales


@login_required
@requiere_algun_permiso('asistencia.ver_reportes_area', 'asistencia.ver_reportes_globales')
def reportes(request):
    form, desde, hasta, filas, totales = _datos_reporte(request)
    alcance = 'global' if request.user.has_perm('asistencia.ver_reportes_globales') else (
        request.user.area_a_cargo or 'sin área asignada')
    return render(request, 'asistencia/reportes.html', {
        'form': form, 'desde': desde, 'hasta': hasta, 'filas': filas, 'totales': totales, 'alcance': alcance,
    })


@login_required
@requiere_algun_permiso('asistencia.ver_reportes_area', 'asistencia.ver_reportes_globales')
def exportar_reporte(request):
    """Exportación para planilla en texto plano (ancho fijo)."""
    _, desde, hasta, filas, totales = _datos_reporte(request)
    lineas = [
        'TECNOANDES S.A.C. - REPORTE DE ASISTENCIA PARA PLANILLA',
        f'Periodo: {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}',
        f'Generado: {timezone.localtime():%d/%m/%Y %H:%M} por {request.user.username}',
        '=' * 86,
        f'{"ÁREA":<14}{"EMPLEADO":<38}{"DÍAS":>6}{"HORAS":>12}{"H. EXTRA":>12}',
        '-' * 86,
    ]
    for f in filas:
        nombre = f"{f['empleado__apellidos']}, {f['empleado__nombres']}"[:36]
        lineas.append(f"{f['empleado__area__nombre'][:13]:<14}{nombre:<38}{f['dias']:>6}"
                      f"{f['total_horas'] or 0:>12}{f['total_extra'] or 0:>12}")
    lineas += ['-' * 86, f"{'TOTAL':<58}{totales['horas'] or 0:>12}{totales['extra'] or 0:>12}"]
    respuesta = HttpResponse('\n'.join(lineas) + '\n', content_type='text/plain; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="asistencia_{desde:%Y%m%d}_{hasta:%Y%m%d}.txt"'
    return respuesta


@login_required
@permission_required('asistencia.view_registroauditoria', raise_exception=True)
def auditoria(request):
    registros = RegistroAuditoria.objects.all()
    accion = request.GET.get('accion', '')
    if accion in RegistroAuditoria.Accion.values:
        registros = registros.filter(accion=accion)
    return render(request, 'asistencia/auditoria.html', {
        'pagina': _paginar(request, registros, 30), 'acciones': RegistroAuditoria.Accion.choices,
        'accion_actual': accion,
    })


@login_required
def mi_sesion(request):
    return render(request, 'asistencia/mi_sesion.html', {
        'expira': request.session.get_expiry_date(),
        'timeout_min': settings.SESSION_TIMEOUT_INACTIVIDAD // 60,
        'cookie': {
            'nombre': settings.SESSION_COOKIE_NAME, 'httponly': settings.SESSION_COOKIE_HTTPONLY,
            'secure': settings.SESSION_COOKIE_SECURE, 'samesite': settings.SESSION_COOKIE_SAMESITE,
        },
        'grupos': request.user.groups.all(),
        'permisos': sorted(p.split('.')[1] for p in request.user.get_all_permissions()
                           if p.split('.')[1] in {
                               'registrar_asistencia', 'solicitar_permiso', 'aprobar_permiso',
                               'ver_reportes_area', 'ver_reportes_globales', 'corregir_asistencia',
                               'ver_salarios'}),
    })


def error_403(request, exception=None):
    return render(request, '403.html', {'mensaje': str(exception or '')}, status=403)


def error_404(request, exception=None):
    return render(request, '404.html', status=404)
