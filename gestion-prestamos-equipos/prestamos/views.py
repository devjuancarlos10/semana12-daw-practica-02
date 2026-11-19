"""Vistas web del sistema.

Autorización en dos niveles:
1. Decoradores (`@login_required`, `@permission_required`) por vista.
2. Comprobaciones a nivel de objeto (un docente solo ve sus préstamos).
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .auditoria import marcar_accion
from .forms import (
    DevolucionForm,
    EquipoForm,
    FiltroCatalogoForm,
    RechazoPrestamoForm,
    SolicitudPrestamoForm,
)
from .models import Equipo, Laboratorio, Prestamo, RegistroAuditoria, Usuario

Accion = RegistroAuditoria.Accion


def _paginar(request, queryset, por_pagina=15):
    return Paginator(queryset, por_pagina).get_page(request.GET.get('pagina'))


# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------
def inicio(request):
    if request.user.is_authenticated:
        return redirect('prestamos:dashboard')
    return redirect('prestamos:catalogo')


@login_required
def dashboard(request):
    user = request.user
    prestamos = Prestamo.objects.con_relaciones()
    if not user.has_perm('prestamos.ver_todos_prestamos'):
        prestamos = prestamos.filter(docente=user)

    resumen = prestamos.aggregate(
        pendientes=Count('id', filter=Q(estado=Prestamo.Estado.PENDIENTE)),
        activos=Count('id', filter=Q(estado=Prestamo.Estado.ACTIVO)),
        vencidos=Count('id', filter=Q(
            estado=Prestamo.Estado.ACTIVO, fecha_devolucion_prevista__lt=timezone.localdate(),
        )),
        devueltos=Count('id', filter=Q(estado=Prestamo.Estado.DEVUELTO)),
    )
    equipos = Equipo.objects.aggregate(
        total=Count('id'),
        operativos=Count('id', filter=Q(estado=Equipo.Estado.OPERATIVO)),
        mantenimiento=Count('id', filter=Q(estado=Equipo.Estado.MANTENIMIENTO)),
    )
    contexto = {
        'resumen': resumen,
        'equipos': equipos,
        'recientes': prestamos.vigentes()[:8],
        'por_aprobar': (
            prestamos.filter(estado=Prestamo.Estado.PENDIENTE)[:5]
            if user.has_perm('prestamos.gestionar_prestamos') else []
        ),
        'limite_prestamos': settings.PRESTAMOS_MAX_ACTIVOS_POR_DOCENTE,
    }
    return render(request, 'prestamos/dashboard.html', contexto)


# ---------------------------------------------------------------------------
# Catálogo (público, solo lectura)
# ---------------------------------------------------------------------------
def catalogo(request):
    form = FiltroCatalogoForm(request.GET or None)
    equipos = (
        Equipo.objects.exclude(estado=Equipo.Estado.BAJA)
        .select_related('laboratorio').con_disponibilidad()
        .order_by('codigo_patrimonial')
    )
    if form.is_valid():
        q = form.cleaned_data.get('q')
        if q:
            categorias = [v for v, etiqueta in Equipo.Categoria.choices if q.lower() in etiqueta.lower()]
            equipos = equipos.filter(
                Q(codigo_patrimonial__icontains=q) | Q(nombre__icontains=q)
                | Q(marca__icontains=q) | Q(categoria__in=categorias)
            )
        if form.cleaned_data.get('laboratorio'):
            equipos = equipos.filter(laboratorio=form.cleaned_data['laboratorio'])
        if form.cleaned_data.get('estado'):
            equipos = equipos.filter(estado=form.cleaned_data['estado'])
    return render(request, 'prestamos/catalogo.html', {
        'form': form,
        'pagina': _paginar(request, equipos, 12),
        'total': equipos.count(),
    })


def equipo_detalle(request, pk):
    equipo = get_object_or_404(
        Equipo.objects.select_related('laboratorio').con_disponibilidad(), pk=pk,
    )
    historial = []
    if request.user.has_perm('prestamos.ver_todos_prestamos'):
        historial = equipo.prestamos.select_related('docente')[:10]
    return render(request, 'prestamos/equipo_detalle.html', {
        'equipo': equipo, 'historial': historial,
    })


# ---------------------------------------------------------------------------
# CRUD de equipos (administrador)
# ---------------------------------------------------------------------------
@login_required
@permission_required('prestamos.add_equipo', raise_exception=True)
def equipo_crear(request):
    form = EquipoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        equipo = form.save()
        marcar_accion(request, Accion.EQUIPO_CREADO, str(equipo))
        messages.success(request, f'Equipo {equipo.codigo_patrimonial} registrado correctamente.')
        return redirect('prestamos:equipo_detalle', pk=equipo.pk)
    return render(request, 'prestamos/equipo_form.html', {'form': form, 'titulo': 'Registrar equipo'})


@login_required
@permission_required('prestamos.change_equipo', raise_exception=True)
def equipo_editar(request, pk):
    equipo = get_object_or_404(Equipo, pk=pk)
    form = EquipoForm(request.POST or None, instance=equipo)
    if request.method == 'POST' and form.is_valid():
        equipo = form.save()
        cambios = ', '.join(form.changed_data) or 'sin cambios'
        marcar_accion(request, Accion.EQUIPO_EDITADO, f'{equipo.codigo_patrimonial}: {cambios}')
        messages.success(request, 'Cambios guardados.')
        return redirect('prestamos:equipo_detalle', pk=equipo.pk)
    return render(request, 'prestamos/equipo_form.html', {
        'form': form, 'titulo': f'Editar {equipo.codigo_patrimonial}', 'equipo': equipo,
    })


@login_required
@permission_required('prestamos.delete_equipo', raise_exception=True)
def equipo_eliminar(request, pk):
    """Solo administradores. Equipos con historial no se borran: se dan de baja."""
    equipo = get_object_or_404(Equipo, pk=pk)
    if request.method == 'POST':
        descripcion = str(equipo)
        try:
            equipo.delete()
        except ProtectedError:
            messages.error(
                request,
                'El equipo tiene préstamos registrados y no puede eliminarse para '
                'preservar la trazabilidad. Cámbielo al estado "Dado de baja".',
            )
            return redirect('prestamos:equipo_detalle', pk=pk)
        marcar_accion(request, Accion.EQUIPO_ELIMINADO, descripcion)
        messages.success(request, f'Equipo {descripcion} eliminado.')
        return redirect('prestamos:catalogo')
    return render(request, 'prestamos/equipo_confirmar_eliminar.html', {'equipo': equipo})


# ---------------------------------------------------------------------------
# Préstamos
# ---------------------------------------------------------------------------
@login_required
@permission_required('prestamos.view_prestamo', raise_exception=True)
def prestamo_lista(request):
    user = request.user
    prestamos = Prestamo.objects.con_relaciones()
    ve_todos = user.has_perm('prestamos.ver_todos_prestamos')
    if not ve_todos:
        prestamos = prestamos.filter(docente=user)  # Docentes: solo los propios

    estado = request.GET.get('estado', '')
    if estado == 'vencido':
        prestamos = prestamos.vencidos()
    elif estado in Prestamo.Estado.values:
        prestamos = prestamos.filter(estado=estado)

    q = request.GET.get('q', '').strip()[:60]
    if q and ve_todos:
        prestamos = prestamos.filter(
            Q(equipo__codigo_patrimonial__icontains=q) | Q(docente__first_name__icontains=q)
            | Q(docente__last_name__icontains=q) | Q(docente__username__icontains=q)
        )
    return render(request, 'prestamos/prestamo_lista.html', {
        'pagina': _paginar(request, prestamos),
        'estados': Prestamo.Estado.choices,
        'estado_actual': estado,
        'q': q,
        've_todos': ve_todos,
    })


def _obtener_prestamo_autorizado(request, pk):
    prestamo = get_object_or_404(Prestamo.objects.con_relaciones(), pk=pk)
    if prestamo.docente_id != request.user.pk and not request.user.has_perm(
        'prestamos.ver_todos_prestamos'
    ):
        raise PermissionDenied('No puede ver préstamos de otros docentes.')
    return prestamo


@login_required
@permission_required('prestamos.view_prestamo', raise_exception=True)
def prestamo_detalle(request, pk):
    prestamo = _obtener_prestamo_autorizado(request, pk)
    return render(request, 'prestamos/prestamo_detalle.html', {
        'prestamo': prestamo,
        'form_rechazo': RechazoPrestamoForm(),
    })


@login_required
@permission_required('prestamos.add_prestamo', raise_exception=True)
def prestamo_nuevo(request):
    inicial = {}
    if request.GET.get('equipo', '').isdigit():
        inicial['equipo'] = request.GET['equipo']
        inicial['fecha_devolucion_prevista'] = timezone.localdate() + timedelta(days=7)
    form = SolicitudPrestamoForm(request.POST or None, usuario=request.user, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            prestamo = form.save()
        if prestamo.estado == Prestamo.Estado.ACTIVO:
            marcar_accion(request, Accion.PRESTAMO_REGISTRADO, str(prestamo))
            messages.success(request, f'Préstamo #{prestamo.pk} registrado y entregado.')
        else:
            marcar_accion(request, Accion.PRESTAMO_SOLICITADO, str(prestamo))
            messages.success(
                request, f'Solicitud #{prestamo.pk} enviada. Un asistente la atenderá en breve.'
            )
        return redirect('prestamos:prestamo_detalle', pk=prestamo.pk)
    return render(request, 'prestamos/prestamo_form.html', {
        'form': form,
        'limite': settings.PRESTAMOS_MAX_ACTIVOS_POR_DOCENTE,
        'plazo': settings.PRESTAMOS_PLAZO_MAXIMO_DIAS,
    })


@login_required
@permission_required('prestamos.gestionar_prestamos', raise_exception=True)
@require_POST
def prestamo_aprobar(request, pk):
    with transaction.atomic():
        prestamo = get_object_or_404(
            Prestamo.objects.select_for_update().select_related('equipo', 'docente'),
            pk=pk, estado=Prestamo.Estado.PENDIENTE,
        )
        # Se revalida: el estado del equipo o su stock pudo cambiar desde la solicitud.
        equipo = prestamo.equipo
        disponibles = equipo.disponibles + prestamo.cantidad  # excluye la propia solicitud
        if not equipo.es_operativo:
            messages.error(request, 'El equipo ya no está operativo. Rechace la solicitud.')
        elif prestamo.cantidad > disponibles:
            messages.error(request, f'Stock insuficiente ({disponibles} disponibles).')
        else:
            prestamo.estado = Prestamo.Estado.ACTIVO
            prestamo.registrado_por = request.user
            prestamo.save(update_fields=['estado', 'registrado_por', 'actualizado'])
            marcar_accion(request, Accion.PRESTAMO_APROBADO, str(prestamo))
            messages.success(request, f'Préstamo #{prestamo.pk} aprobado y entregado.')
    return redirect('prestamos:prestamo_detalle', pk=pk)


@login_required
@permission_required('prestamos.gestionar_prestamos', raise_exception=True)
@require_POST
def prestamo_rechazar(request, pk):
    prestamo = get_object_or_404(Prestamo, pk=pk, estado=Prestamo.Estado.PENDIENTE)
    form = RechazoPrestamoForm(request.POST)
    if form.is_valid():
        prestamo.estado = Prestamo.Estado.RECHAZADO
        prestamo.motivo_rechazo = form.cleaned_data['motivo']
        prestamo.registrado_por = request.user
        prestamo.save(update_fields=['estado', 'motivo_rechazo', 'registrado_por', 'actualizado'])
        marcar_accion(request, Accion.PRESTAMO_RECHAZADO, f'{prestamo}: {prestamo.motivo_rechazo}')
        messages.info(request, f'Solicitud #{prestamo.pk} rechazada.')
    else:
        messages.error(request, form.errors['motivo'][0])
    return redirect('prestamos:prestamo_detalle', pk=pk)


@login_required
@permission_required('prestamos.registrar_devolucion', raise_exception=True)
def prestamo_devolver(request, pk):
    """Solo asistentes registran devoluciones."""
    prestamo = get_object_or_404(
        Prestamo.objects.con_relaciones(), pk=pk, estado=Prestamo.Estado.ACTIVO,
    )
    form = DevolucionForm(request.POST or None, prestamo=prestamo)
    if request.method == 'POST' and form.is_valid():
        form.guardar(request.user)
        marcar_accion(
            request, Accion.DEVOLUCION,
            f'{prestamo} · estado equipo: {prestamo.get_estado_equipo_devolucion_display()} '
            f'· retraso: {prestamo.dias_retraso} d',
        )
        messages.success(request, f'Devolución del préstamo #{prestamo.pk} registrada.')
        return redirect('prestamos:prestamo_detalle', pk=pk)
    return render(request, 'prestamos/prestamo_devolver.html', {'form': form, 'prestamo': prestamo})


# ---------------------------------------------------------------------------
# Reportes y auditoría (administrador)
# ---------------------------------------------------------------------------
@login_required
@permission_required('prestamos.ver_reportes', raise_exception=True)
def reportes(request):
    filtro_vencido = Q(
        prestamos__estado=Prestamo.Estado.ACTIVO,
        prestamos__fecha_devolucion_prevista__lt=timezone.localdate(),
    )
    por_docente = (
        Usuario.objects.filter(rol=Usuario.Rol.DOCENTE)
        .annotate(
            total=Count('prestamos'),
            activos=Count('prestamos', filter=Q(prestamos__estado=Prestamo.Estado.ACTIVO)),
            vencidos=Count('prestamos', filter=filtro_vencido),
            unidades=Sum('prestamos__cantidad'),
        )
        .order_by('-total')
    )
    por_laboratorio = (
        Laboratorio.objects.annotate(
            equipos_total=Count('equipos', distinct=True),
            prestamos_total=Count('equipos__prestamos'),
            en_mantenimiento=Count(
                'equipos', filter=Q(equipos__estado=Equipo.Estado.MANTENIMIENTO), distinct=True,
            ),
        ).order_by('-prestamos_total')
    )
    por_categoria = (
        Prestamo.objects.values('equipo__categoria')
        .annotate(total=Count('id')).order_by('-total')
    )
    etiquetas = dict(Equipo.Categoria.choices)
    maximo = max((c['total'] for c in por_categoria), default=1) or 1
    categorias = [
        {'nombre': etiquetas.get(c['equipo__categoria']), 'total': c['total'],
         'porcentaje': round(c['total'] * 100 / maximo)}
        for c in por_categoria
    ]
    return render(request, 'prestamos/reportes.html', {
        'por_docente': por_docente,
        'por_laboratorio': por_laboratorio,
        'categorias': categorias,
    })


@login_required
@permission_required('prestamos.view_registroauditoria', raise_exception=True)
def auditoria(request):
    registros = RegistroAuditoria.objects.select_related('usuario')
    accion = request.GET.get('accion', '')
    if accion in RegistroAuditoria.Accion.values:
        registros = registros.filter(accion=accion)
    if request.GET.get('seguridad') == '1':
        registros = registros.filter(accion__in=RegistroAuditoria.ACCIONES_SEGURIDAD)
    return render(request, 'prestamos/auditoria.html', {
        'pagina': _paginar(request, registros, 25),
        'acciones': RegistroAuditoria.Accion.choices,
        'accion_actual': accion,
        'solo_seguridad': request.GET.get('seguridad') == '1',
    })


@login_required
def mi_sesion(request):
    """Muestra los parámetros de la sesión actual (útil para verificar el timeout)."""
    return render(request, 'prestamos/mi_sesion.html', {
        'expira': request.session.get_expiry_date(),
        'timeout_min': settings.SESSION_TIMEOUT_INACTIVIDAD // 60,
        'cookie': {
            'nombre': settings.SESSION_COOKIE_NAME,
            'httponly': settings.SESSION_COOKIE_HTTPONLY,
            'secure': settings.SESSION_COOKIE_SECURE,
            'samesite': settings.SESSION_COOKIE_SAMESITE,
        },
        'grupos': request.user.groups.all(),
    })


# ---------------------------------------------------------------------------
# Páginas de error
# ---------------------------------------------------------------------------
def error_403(request, exception=None):
    return render(request, '403.html', {'mensaje': str(exception or '')}, status=403)


def error_404(request, exception=None):
    return render(request, '404.html', status=404)
