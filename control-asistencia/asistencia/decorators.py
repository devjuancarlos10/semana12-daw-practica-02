"""Decoradores de autorización y marcadores para middleware."""
from functools import wraps

from django.core.exceptions import PermissionDenied


def valida_horario_marcacion(vista):
    """Marca la vista para que HorarioMarcacionMiddleware valide la hora de marcación."""
    vista.valida_horario_marcacion = True
    return vista


def requiere_algun_permiso(*permisos):
    """Permite el acceso si el usuario tiene AL MENOS uno de los permisos indicados."""
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not any(request.user.has_perm(p) for p in permisos):
                raise PermissionDenied('Su rol no tiene acceso a esta sección.')
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador


def requiere_empleado(vista):
    """La vista necesita que el usuario tenga un perfil de empleado."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if request.user.perfil is None:
            raise PermissionDenied('Su usuario no está vinculado a un empleado.')
        return vista(request, *args, **kwargs)
    return envoltura
