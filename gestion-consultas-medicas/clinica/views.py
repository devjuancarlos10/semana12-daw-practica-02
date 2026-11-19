"""Vistas del centro médico con autorización por permisos y por objeto."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import informacion_sensible, valida_horario_atencion
from .forms import (
    BusquedaPacienteForm,
    CancelarCitaForm,
    CitaForm,
    ConsultaForm,
    FiltroAgendaForm,
    LoginDiferenciadoForm,
    NotaConsultaForm,
    PacienteForm,
)
from .horarios import describir_horario_centro
from .middleware import CLAVE_BORRADOR_CITA
from .models import (
    AccesoHistorial,
    Cita,
    Consulta,
    Especialidad,
    Medico,
    Paciente,
    RegistroCambio,
)


def _paginar(request, queryset, por_pagina=20):
    return Paginator(queryset, por_pagina).get_page(request.GET.get('pagina'))


def puede_ver_historial(usuario, paciente):
    """ver_historial_clinico: médicos (cualquier paciente) y el propio paciente."""
    if not usuario.has_perm('clinica.ver_historial_clinico'):
        return False
    if usuario.es_medico:
        return True
    if usuario.es_paciente:
        return paciente.usuario_id == usuario.pk
    return False


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class LoginDiferenciadoView(auth_views.LoginView):
    form_class = LoginDiferenciadoForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        rol = self.request.user.get_rol_display()
        messages.success(self.request, f'Bienvenido(a), {self.request.user.first_name}. Ingresó como {rol}.')
        return respuesta


class LogoutAuditadoView(auth_views.LogoutView):
    """Logout solo por POST (protegido con CSRF); la auditoría la hacen las señales."""


# ---------------------------------------------------------------------------
# Panel por rol
# ---------------------------------------------------------------------------
def inicio(request):
    return redirect('clinica:dashboard' if request.user.is_authenticated else 'login')


@login_required
def dashboard(request):
    user = request.user
    hoy = timezone.localdate()
    citas = Cita.objects.con_relaciones().visibles_para(user)
    contexto = {
        'hoy': hoy,
        'citas_hoy': citas.filter(fecha=hoy).exclude(estado=Cita.Estado.CANCELADA),
        'proximas': citas.filter(fecha__gte=hoy, estado=Cita.Estado.PROGRAMADA)[:6],
        'horario_centro': describir_horario_centro(),
    }
    if user.has_perm('clinica.ver_agenda_completa'):
        contexto['indicadores'] = Cita.objects.aggregate(
            hoy=Count('id', filter=Q(fecha=hoy) & ~Q(estado=Cita.Estado.CANCELADA)),
            programadas=Count('id', filter=Q(estado=Cita.Estado.PROGRAMADA, fecha__gte=hoy)),
            completadas_mes=Count('id', filter=Q(
                estado=Cita.Estado.COMPLETADA, fecha__year=hoy.year, fecha__month=hoy.month)),
            inasistencias=Count('id', filter=Q(estado=Cita.Estado.NO_ASISTIO)),
        )
        contexto['total_pacientes'] = Paciente.objects.count()
    if user.es_paciente and user.perfil_paciente:
        contexto['paciente'] = user.perfil_paciente
        contexto['ultima_consulta'] = user.perfil_paciente.consultas.select_related('medico__usuario').first()
    return render(request, 'clinica/dashboard.html', contexto)


# ---------------------------------------------------------------------------
# Citas
# ---------------------------------------------------------------------------
@login_required
@permission_required('clinica.view_cita', raise_exception=True)
def cita_lista(request):
    user = request.user
    citas = Cita.objects.con_relaciones().visibles_para(user)
    form = FiltroAgendaForm(request.GET or None)
    if not user.has_perm('clinica.ver_agenda_completa'):
        del form.fields['medico']
    if form.is_valid():
        if form.cleaned_data.get('fecha'):
            citas = citas.filter(fecha=form.cleaned_data['fecha'])
        if form.cleaned_data.get('medico'):
            citas = citas.filter(medico=form.cleaned_data['medico'])
        if form.cleaned_data.get('estado'):
            citas = citas.filter(estado=form.cleaned_data['estado'])
    orden = '-fecha' if user.es_paciente else 'fecha'
    return render(request, 'clinica/cita_lista.html', {
        'form': form, 'pagina': _paginar(request, citas.order_by(orden, 'hora')),
    })


@login_required
@permission_required('clinica.agendar_cita', raise_exception=True)
@valida_horario_atencion
def cita_nueva(request):
    if request.user.es_paciente and request.user.perfil_paciente is None:
        raise PermissionDenied('Su cuenta no tiene un perfil de paciente asociado.')
    # Datos conservados por el HorarioAtencionMiddleware tras un rechazo
    inicial = request.session.pop(CLAVE_BORRADOR_CITA, None) or {}
    form = CitaForm(request.POST or None, usuario=request.user, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        cita = form.save()
        messages.success(
            request,
            f'Cita agendada para el {cita.fecha:%d/%m/%Y} a las {cita.hora:%H:%M} con {cita.medico}.',
        )
        return redirect('clinica:cita_detalle', pk=cita.pk)
    return render(request, 'clinica/cita_form.html', {
        'form': form,
        'horario_centro': describir_horario_centro(),
        'especialidades': Especialidad.objects.annotate(n=Count('medicos')),
    })


def _cita_autorizada(request, pk):
    cita = get_object_or_404(Cita.objects.con_relaciones(), pk=pk)
    if not Cita.objects.visibles_para(request.user).filter(pk=pk).exists():
        raise PermissionDenied('No tiene acceso a esta cita.')
    return cita


@login_required
@permission_required('clinica.view_cita', raise_exception=True)
def cita_detalle(request, pk):
    cita = _cita_autorizada(request, pk)
    return render(request, 'clinica/cita_detalle.html', {
        'cita': cita,
        'consulta': getattr(cita, 'consulta', None),
        'puede_cancelar': cita.puede_ser_cancelada_por(request.user),
        'form_cancelar': CancelarCitaForm(),
        'puede_ver_historial': puede_ver_historial(request.user, cita.paciente),
    })


@login_required
@permission_required('clinica.cancelar_cita', raise_exception=True)
@require_POST
def cita_cancelar(request, pk):
    cita = _cita_autorizada(request, pk)
    if not cita.puede_ser_cancelada_por(request.user):
        raise PermissionDenied('No puede cancelar esta cita.')
    form = CancelarCitaForm(request.POST)
    if form.is_valid():
        cita.estado = Cita.Estado.CANCELADA
        cita.cancelada_por = request.user
        cita.motivo_cancelacion = form.cleaned_data['motivo']
        cita.fecha_cancelacion = timezone.now()
        cita.save()
        messages.success(request, 'La cita fue cancelada.')
    else:
        messages.error(request, form.errors['motivo'][0])
    return redirect('clinica:cita_detalle', pk=pk)


# ---------------------------------------------------------------------------
# Consultas e historial clínico (información sensible)
# ---------------------------------------------------------------------------
@login_required
@permission_required('clinica.add_consulta', raise_exception=True)
def consulta_registrar(request, cita_id):
    cita = get_object_or_404(Cita.objects.con_relaciones(), pk=cita_id)
    if cita.medico.usuario_id != request.user.pk:
        raise PermissionDenied('Solo el médico asignado registra la consulta.')
    if cita.estado != Cita.Estado.PROGRAMADA or hasattr(cita, 'consulta'):
        messages.warning(request, 'Esta cita ya no admite registro de consulta.')
        return redirect('clinica:cita_detalle', pk=cita.pk)
    if cita.fecha > timezone.localdate():
        messages.warning(request, 'La consulta se registra el día de la cita o después.')
        return redirect('clinica:cita_detalle', pk=cita.pk)
    form = ConsultaForm(request.POST or None, cita=cita)
    if request.method == 'POST' and form.is_valid():
        consulta = form.save()
        messages.success(request, 'Consulta registrada. La cita se marcó como completada.')
        return redirect('clinica:consulta_detalle', pk=consulta.pk)
    return render(request, 'clinica/consulta_form.html', {'form': form, 'cita': cita})


@login_required
@permission_required('clinica.ver_historial_clinico', raise_exception=True)
@informacion_sensible(
    'consulta médica',
    obtener_paciente=lambda kw: Paciente.objects.filter(consultas__pk=kw.get('pk')).first(),
)
def consulta_detalle(request, pk):
    consulta = get_object_or_404(
        Consulta.objects.select_related('paciente', 'medico__usuario', 'medico__especialidad', 'cita'),
        pk=pk,
    )
    request.paciente_auditado = consulta.paciente
    if not puede_ver_historial(request.user, consulta.paciente):
        raise PermissionDenied('No tiene autorización para ver este registro clínico.')

    form_nota = None
    if request.user.has_perm('clinica.add_notaconsulta'):
        form_nota = NotaConsultaForm(request.POST or None)
        if request.method == 'POST' and form_nota.is_valid():
            nota = form_nota.save(commit=False)
            nota.consulta = consulta
            nota.autor = request.user
            nota.save()
            messages.success(request, 'Nota agregada al registro clínico.')
            return redirect('clinica:consulta_detalle', pk=pk)
    elif request.method == 'POST':
        raise PermissionDenied('No puede agregar notas.')
    return render(request, 'clinica/consulta_detalle.html', {
        'consulta': consulta,
        'notas': consulta.notas.select_related('autor'),
        'form_nota': form_nota,
    })


@login_required
@permission_required('clinica.ver_historial_clinico', raise_exception=True)
@informacion_sensible(
    'historial clínico',
    obtener_paciente=lambda kw: Paciente.objects.filter(pk=kw.get('paciente_id')).first(),
)
def historial_paciente(request, paciente_id):
    paciente = get_object_or_404(Paciente, pk=paciente_id)
    request.paciente_auditado = paciente  # El middleware registra el acceso
    if not puede_ver_historial(request.user, paciente):
        raise PermissionDenied('Solo los médicos y el propio paciente pueden ver este historial.')
    consultas = (
        paciente.consultas.select_related('medico__usuario', 'medico__especialidad', 'cita')
        .prefetch_related('notas__autor')
    )
    citas = paciente.citas.select_related('medico__usuario', 'medico__especialidad').order_by('-fecha', '-hora')
    return render(request, 'clinica/historial.html', {
        'paciente': paciente, 'consultas': consultas, 'citas': citas[:10],
    })


@login_required
def mi_historial(request):
    paciente = request.user.perfil_paciente
    if paciente is None:
        raise PermissionDenied('Su cuenta no tiene historial clínico asociado.')
    return redirect('clinica:historial_paciente', paciente_id=paciente.pk)


# ---------------------------------------------------------------------------
# Pacientes (recepción)
# ---------------------------------------------------------------------------
@login_required
@permission_required('clinica.view_paciente', raise_exception=True)
def paciente_lista(request):
    form = BusquedaPacienteForm(request.GET or None)
    pacientes = Paciente.objects.annotate(
        total_citas=Count('citas', distinct=True),
        ultima_consulta=Max('consultas__fecha_registro'),
    )
    if form.is_valid() and form.cleaned_data.get('q'):
        q = form.cleaned_data['q']
        pacientes = pacientes.filter(
            Q(dni__startswith=q) | Q(nombres__icontains=q) | Q(apellidos__icontains=q)
        )
    return render(request, 'clinica/paciente_lista.html', {
        'form': form, 'pagina': _paginar(request, pacientes.order_by('apellidos', 'nombres')),
    })


@login_required
@permission_required('clinica.add_paciente', raise_exception=True)
def paciente_nuevo(request):
    form = PacienteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        paciente = form.save(commit=False)
        paciente.registrado_por = request.user
        paciente.save()
        messages.success(request, f'Paciente {paciente.nombre_completo} registrado.')
        return redirect('clinica:paciente_lista')
    return render(request, 'clinica/paciente_form.html', {'form': form, 'titulo': 'Registrar paciente'})


@login_required
@permission_required('clinica.change_paciente', raise_exception=True)
def paciente_editar(request, pk):
    paciente = get_object_or_404(Paciente, pk=pk)
    form = PacienteForm(request.POST or None, instance=paciente)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Datos del paciente actualizados.')
        return redirect('clinica:paciente_lista')
    return render(request, 'clinica/paciente_form.html', {
        'form': form, 'titulo': f'Editar paciente · {paciente.nombre_completo}',
    })


# ---------------------------------------------------------------------------
# Reportes y auditoría (administración)
# ---------------------------------------------------------------------------
def estadisticas():
    """Estadísticas compartidas por la vista de reportes y el Django Admin."""
    por_especialidad = list(
        Especialidad.objects.annotate(
            total=Count('medicos__citas', distinct=True),
            completadas=Count('medicos__citas', filter=Q(medicos__citas__estado=Cita.Estado.COMPLETADA), distinct=True),
        ).order_by('-total')
    )
    maximo = max((e.total for e in por_especialidad), default=0) or 1
    for e in por_especialidad:
        e.porcentaje = round(e.total * 100 / maximo)
    total = Cita.objects.count() or 1
    inasistencias = Cita.objects.filter(estado=Cita.Estado.NO_ASISTIO).count()
    return {
        'por_especialidad': por_especialidad,
        'top_medicos': Medico.objects.select_related('usuario', 'especialidad').annotate(
            consultas_total=Count('consultas'),
        ).order_by('-consultas_total')[:5],
        'pacientes_frecuentes': Paciente.objects.annotate(
            citas_total=Count('citas', filter=~Q(citas__estado=Cita.Estado.CANCELADA)),
        ).filter(citas_total__gt=0).order_by('-citas_total')[:5],
        'tasa_inasistencia': round(inasistencias * 100 / total, 1),
        'por_estado': Cita.objects.values('estado').annotate(n=Count('id')).order_by('estado'),
    }


@login_required
@permission_required('clinica.generar_reportes', raise_exception=True)
def reportes(request):
    contexto = estadisticas()
    contexto['etiquetas_estado'] = dict(Cita.Estado.choices)
    return render(request, 'clinica/reportes.html', contexto)


@login_required
@permission_required('clinica.view_accesohistorial', raise_exception=True)
def auditoria(request):
    pestana = request.GET.get('ver', 'accesos')
    if pestana == 'cambios':
        registros = RegistroCambio.objects.all()
    else:
        pestana = 'accesos'
        registros = AccesoHistorial.objects.select_related('paciente')
    return render(request, 'clinica/auditoria.html', {
        'pestana': pestana, 'pagina': _paginar(request, registros, 25),
    })


@login_required
def mi_sesion(request):
    return render(request, 'clinica/mi_sesion.html', {
        'expira': request.session.get_expiry_date(),
        'timeout_min': settings.SESSION_TIMEOUT_INACTIVIDAD // 60,
        'cookie': {
            'nombre': settings.SESSION_COOKIE_NAME,
            'httponly': settings.SESSION_COOKIE_HTTPONLY,
            'secure': settings.SESSION_COOKIE_SECURE,
            'samesite': settings.SESSION_COOKIE_SAMESITE,
        },
        'grupos': request.user.groups.all(),
        'permisos': sorted(p.split('.')[1] for p in request.user.get_all_permissions()),
    })


def error_403(request, exception=None):
    return render(request, '403.html', {'mensaje': str(exception or '')}, status=403)


def error_404(request, exception=None):
    return render(request, '404.html', status=404)

