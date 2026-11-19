"""Decoradores que marcan vistas para que los middlewares las intercepten.

Los atributos se propagan a través de otros decoradores (login_required,
permission_required) porque `functools.wraps` copia el __dict__ de la función.
"""


def informacion_sensible(recurso, obtener_paciente=None):
    """Marca la vista como acceso a información clínica (auditoría médica).

    `obtener_paciente(kwargs)` permite al middleware identificar al paciente
    incluso si la vista no llegó a ejecutarse (p. ej. acceso denegado).
    """
    def decorador(vista):
        vista.recurso_sensible = recurso
        vista.obtener_paciente = obtener_paciente
        return vista
    return decorador


def valida_horario_atencion(vista):
    """El HorarioAtencionMiddleware valida fecha/hora del POST antes de la vista."""
    vista.valida_horario_atencion = True
    return vista
