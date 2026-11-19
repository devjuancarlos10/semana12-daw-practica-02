"""Rol → grupo → permisos (fuente única de verdad de la autorización).

Permisos granulares solicitados:
- ver_historial_clinico: médicos y el propio paciente (el filtro por objeto se
  aplica en la vista). Los administradores NO lo tienen: confidencialidad.
- agendar_cita: recepcionistas y pacientes.
- cancelar_cita: recepcionistas, médicos y el propio paciente.
- generar_reportes: solo administradores.
"""

GRUPO_POR_ROL = {
    'admin': 'Admins',
    'medico': 'Médicos',
    'recepcionista': 'Recepcionistas',
    'paciente': 'Pacientes',
}

PERMISOS_POR_GRUPO = {
    'Admins': [
        'clinica.add_usuario', 'clinica.change_usuario', 'clinica.view_usuario',
        'clinica.add_especialidad', 'clinica.change_especialidad', 'clinica.view_especialidad',
        'clinica.delete_especialidad',
        'clinica.add_medico', 'clinica.change_medico', 'clinica.view_medico',
        'clinica.add_horarioatencion', 'clinica.change_horarioatencion',
        'clinica.view_horarioatencion', 'clinica.delete_horarioatencion',
        'clinica.add_paciente', 'clinica.change_paciente', 'clinica.view_paciente',
        'clinica.view_cita', 'clinica.change_cita', 'clinica.ver_agenda_completa',
        'clinica.generar_reportes',
        'clinica.view_accesohistorial', 'clinica.view_registrocambio',
    ],
    'Médicos': [
        'clinica.view_cita', 'clinica.cancelar_cita',
        'clinica.view_paciente', 'clinica.ver_historial_clinico',
        'clinica.add_consulta', 'clinica.view_consulta',
        'clinica.add_notaconsulta', 'clinica.view_notaconsulta',
    ],
    'Recepcionistas': [
        'clinica.view_cita', 'clinica.change_cita',
        'clinica.agendar_cita', 'clinica.cancelar_cita', 'clinica.ver_agenda_completa',
        'clinica.add_paciente', 'clinica.change_paciente', 'clinica.view_paciente',
        'clinica.view_medico', 'clinica.view_especialidad', 'clinica.view_horarioatencion',
    ],
    'Pacientes': [
        'clinica.view_cita', 'clinica.agendar_cita', 'clinica.cancelar_cita',
        'clinica.ver_historial_clinico',
    ],
}


def sincronizar_grupos(sender=None, **kwargs):
    from django.contrib.auth.models import Group, Permission

    for nombre, permisos in PERMISOS_POR_GRUPO.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        objetos = []
        for permiso in permisos:
            app_label, codename = permiso.split('.')
            perm = Permission.objects.filter(
                content_type__app_label=app_label, codename=codename,
            ).first()
            if perm:
                objetos.append(perm)
        grupo.permissions.set(objetos)


def asignar_grupo_por_rol(usuario):
    from django.contrib.auth.models import Group

    nombre = GRUPO_POR_ROL.get(usuario.rol)
    if not nombre:
        return
    grupo, _ = Group.objects.get_or_create(name=nombre)
    otros = usuario.groups.filter(name__in=GRUPO_POR_ROL.values()).exclude(pk=grupo.pk)
    if otros.exists():
        usuario.groups.remove(*otros)
    usuario.groups.add(grupo)
