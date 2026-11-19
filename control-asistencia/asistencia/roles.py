"""Roles jerárquicos → grupos → permisos.

| Permiso                | Admin RRHH | Jefe de área | Empleado | Contador |
|------------------------|:---:|:---:|:---:|:---:|
| registrar_asistencia   |  ✔  |  ✔  |  ✔  |  ✔  |
| solicitar_permiso      |  ✔  |  ✔  |  ✔  |  ✔  |
| aprobar_permiso        |  ✔  |  ✔ (su área) |  |  |
| ver_reportes_area      |  ✔  |  ✔  |     |     |
| ver_reportes_globales  |  ✔  |     |     |  ✔  |
| corregir_asistencia    |  ✔  |     |     |     |
| ver_salarios           |  ✔  |     |     |     |
"""

GRUPO_POR_ROL = {
    'admin_rrhh': 'Administradores RRHH',
    'jefe_area': 'Jefes de Área',
    'empleado': 'Empleados',
    'contador': 'Contadores',
}

BASICOS = ['asistencia.registrar_asistencia', 'asistencia.solicitar_permiso']

PERMISOS_POR_GRUPO = {
    'Administradores RRHH': BASICOS + [
        'asistencia.aprobar_permiso', 'asistencia.ver_reportes_area',
        'asistencia.ver_reportes_globales', 'asistencia.corregir_asistencia',
        'asistencia.ver_salarios',
        'asistencia.add_empleado', 'asistencia.change_empleado', 'asistencia.view_empleado',
        'asistencia.add_area', 'asistencia.change_area', 'asistencia.view_area',
        'asistencia.add_usuario', 'asistencia.change_usuario', 'asistencia.view_usuario',
        'asistencia.view_asistencia', 'asistencia.change_asistencia',
        'asistencia.view_permiso', 'asistencia.change_permiso',
        'asistencia.view_correccionasistencia', 'asistencia.view_registroauditoria',
        'asistencia.view_notificacion',
    ],
    'Jefes de Área': BASICOS + [
        'asistencia.aprobar_permiso', 'asistencia.ver_reportes_area',
        'asistencia.view_empleado', 'asistencia.view_asistencia',
        'asistencia.view_permiso', 'asistencia.change_permiso',
    ],
    'Empleados': BASICOS,
    'Contadores': BASICOS + [
        'asistencia.ver_reportes_globales',
        'asistencia.view_asistencia', 'asistencia.view_empleado',
    ],
}


def sincronizar_grupos(sender=None, **kwargs):
    from django.contrib.auth.models import Group, Permission

    for nombre, permisos in PERMISOS_POR_GRUPO.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        objetos = []
        for permiso in permisos:
            app_label, codename = permiso.split('.')
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
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
