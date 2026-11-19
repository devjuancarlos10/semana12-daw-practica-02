"""Reglas de horario compartidas por formularios y middleware."""
from datetime import date, datetime, time, timedelta

from django.conf import settings

DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def hora_fin(hora: time, minutos: int | None = None) -> time:
    minutos = minutos or settings.CITA_DURACION_MINUTOS
    return (datetime.combine(date.today(), hora) + timedelta(minutes=minutos)).time()


def validar_horario_centro(fecha: date, hora: time, minutos: int | None = None) -> str | None:
    """Devuelve un mensaje de error si la cita cae fuera del horario de atención.

    L-V 08:00-18:00 y sábados 08:00-13:00 (configurable en settings.HORARIO_ATENCION).
    La cita completa (inicio + duración) debe caber dentro del horario.
    """
    rango = settings.HORARIO_ATENCION.get(fecha.weekday())
    if rango is None:
        return f'El centro médico no atiende los {DIAS[fecha.weekday()]}.'
    apertura, cierre = rango
    if hora < apertura or hora_fin(hora, minutos) > cierre:
        return (
            f'Fuera del horario de atención: los {DIAS[fecha.weekday()]} se atiende de '
            f'{apertura:%H:%M} a {cierre:%H:%M}.'
        )
    return None


def describir_horario_centro() -> list[str]:
    filas = []
    for dia, (inicio, fin) in sorted(settings.HORARIO_ATENCION.items()):
        filas.append(f'{DIAS[dia].capitalize()}: {inicio:%H:%M} – {fin:%H:%M}')
    return filas


def rangos_se_superponen(inicio_a: time, fin_a: time, inicio_b: time, fin_b: time) -> bool:
    return inicio_a < fin_b and inicio_b < fin_a
