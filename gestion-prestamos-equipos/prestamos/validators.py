"""Validadores reutilizables y sanitización de texto libre."""
import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.html import strip_tags

validar_codigo_patrimonial = RegexValidator(
    regex=r'^[A-Z]{2,4}-\d{4,6}$',
    message='Formato inválido. Use 2-4 letras, guion y 4-6 dígitos (ej.: LAP-000123).',
)

validar_codigo_institucional = RegexValidator(
    regex=r'^[A-Za-z0-9]{6,12}$',
    message='El código institucional debe tener entre 6 y 12 caracteres alfanuméricos.',
)


def validar_fecha_no_futura(valor):
    if valor and valor > timezone.localdate():
        raise ValidationError('La fecha no puede ser posterior a hoy.')


_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_ESPACIOS = re.compile(r'[ \t]+')


def sanitizar_texto(valor: str, max_longitud: int | None = None) -> str:
    """Limpia texto ingresado por el usuario antes de persistirlo.

    - Elimina etiquetas HTML/scripts (defensa en profundidad: las plantillas
      además escapan la salida).
    - Normaliza Unicode (NFC) y elimina caracteres de control.
    - Colapsa espacios repetidos y líneas en blanco sobrantes.
    """
    if not valor:
        return ''
    texto = unicodedata.normalize('NFC', str(valor))
    texto = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', texto, flags=re.I | re.S)
    texto = strip_tags(texto)
    texto = _CONTROL.sub('', texto)
    lineas = [_ESPACIOS.sub(' ', linea).strip() for linea in texto.splitlines()]
    texto = re.sub(r'\n{3,}', '\n\n', '\n'.join(lineas)).strip()
    if max_longitud:
        texto = texto[:max_longitud]
    return texto
