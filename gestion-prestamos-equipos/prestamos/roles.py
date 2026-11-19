"""Definición centralizada de roles → grupos → permisos.

Cada rol del sistema corresponde a un grupo de Django. Los permisos se asignan al
grupo, nunca directamente al usuario, lo que facilita auditar quién puede qué.
"""

GRUPO_POR_ROL = {
    'admin': 'Administradores',
    'docente': 'Docentes',
    'asistente': 'Asistentes',
}

PERMISOS_POR_GRUPO = {
    'Administradores': [
        # CRUD completo de equipos, laboratorios y usuarios
        'prestamos.add_equipo', 'prestamos.change_equipo',
        'prestamos.delete_equipo', 'prestamos.view_equipo',
        'prestamos.add_laboratorio', 'prestamos.change_laboratorio',
        'prestamos.delete_laboratorio', 'prestamos.view_laboratorio',
        'prestamos.add_usuario', 'prestamos.change_usuario',
        'prestamos.delete_usuario', 'prestamos.view_usuario',
        # Supervisión de préstamos y reportes
        'prestamos.view_prestamo', 'prestamos.change_prestamo',
        'prestamos.ver_todos_prestamos', 'prestamos.ver_reportes',
        # Logs: solo administradores
        'prestamos.view_registroauditoria',
    ],
    'Asistentes': [
        'prestamos.view_equipo', 'prestamos.view_laboratorio',
        'prestamos.add_prestamo', 'prestamos.view_prestamo', 'prestamos.change_prestamo',
        'prestamos.ver_todos_prestamos', 'prestamos.gestionar_prestamos',
        'prestamos.registrar_devolucion',
    ],
    'Docentes': [
        'prestamos.view_equipo',
        'prestamos.add_prestamo',   # solicitar préstamo
        'prestamos.view_prestamo',  # solo los propios (filtrado en la vista)
    ],
}


def sincronizar_grupos(sender=None, **kwargs):
    """Crea los grupos y les asigna exactamente los permisos definidos arriba."""
    from django.contrib.auth.models import Group, Permission

    for nombre_grupo, permisos in PERMISOS_POR_GRUPO.items():
        grupo, _ = Group.objects.get_or_create(name=nombre_grupo)
        objetos = []
        for permiso in permisos:
            app_label, codename = permiso.split('.')
            try:
                objetos.append(Permission.objects.get(
                    content_type__app_label=app_label, codename=codename,
                ))
            except Permission.DoesNotExist:
                # Puede ocurrir en la primera migración antes de crear permisos.
                continue
        grupo.permissions.set(objetos)


def asignar_grupo_por_rol(usuario):
    """Mantiene al usuario solo en el grupo correspondiente a su rol."""
    from django.contrib.auth.models import Group

    nombre = GRUPO_POR_ROL.get(usuario.rol)
    if not nombre:
        return
    grupo, _ = Group.objects.get_or_create(name=nombre)
    otros = usuario.groups.filter(name__in=GRUPO_POR_ROL.values()).exclude(pk=grupo.pk)
    if otros.exists():
        usuario.groups.remove(*otros)
    usuario.groups.add(grupo)
