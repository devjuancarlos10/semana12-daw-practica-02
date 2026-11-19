"""Validadores de dominio y sanitización."""
import re
import unicodedata
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.html import strip_tags

validar_dni = RegexValidator(
    regex=r'^\d{8}$', message='El DNI debe tener exactamente 8 dígitos numéricos.',
)

validar_cmp = RegexValidator(
    regex=r'^\d{5,6}$', message='El CMP debe tener 5 o 6 dígitos numéricos (ej.: 12345).',
)

validar_codigo_institucional = RegexValidator(
    regex=r'^[A-Za-z0-9]{6,12}$',
    message='El código institucional debe tener entre 6 y 12 caracteres alfanuméricos.',
)


def validar_fecha_no_futura(valor: date):
    if valor and valor > timezone.localdate():
        raise ValidationError('La fecha no puede ser futura.')


_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitizar_texto(valor, max_longitud=None) -> str:
    """Elimina HTML, scripts y caracteres de control; normaliza espacios."""
    if not valor:
        return ''
    texto = unicodedata.normalize('NFC', str(valor))
    texto = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', texto, flags=re.I | re.S)
    texto = strip_tags(texto)
    texto = _CONTROL.sub('', texto)
    lineas = [re.sub(r'[ \t]+', ' ', linea).strip() for linea in texto.splitlines()]
    texto = re.sub(r'\n{3,}', '\n\n', '\n'.join(lineas)).strip()
    return texto[:max_longitud] if max_longitud else texto


def normalizar_nombre(valor: str) -> str:
    """'  juan   PÉREZ ' -> 'Juan Pérez'."""
    return ' '.join(p.capitalize() for p in sanitizar_texto(valor).split())
