"""Validadores y sanitización."""
import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.html import strip_tags

validar_dni = RegexValidator(r'^\d{8}$', 'El DNI debe tener exactamente 8 dígitos numéricos.')


def validar_email_corporativo(valor):
    dominio = settings.DOMINIO_EMAIL_CORPORATIVO
    if not valor or not valor.lower().endswith(f'@{dominio}'):
        raise ValidationError(f'Use el correo corporativo con formato usuario@{dominio}.')


def validar_fecha_no_futura(valor):
    if valor and valor > timezone.localdate():
        raise ValidationError('La fecha no puede ser futura.')


def validar_no_vacio(valor):
    if not valor or not str(valor).strip():
        raise ValidationError('Este campo no puede estar vacío.')


_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitizar_texto(valor, max_longitud=None) -> str:
    """Elimina HTML/scripts y caracteres de control; normaliza espacios."""
    if not valor:
        return ''
    texto = unicodedata.normalize('NFC', str(valor))
    texto = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', texto, flags=re.I | re.S)
    texto = strip_tags(texto)
    texto = _CONTROL.sub('', texto)
    lineas = [re.sub(r'[ \t]+', ' ', linea).strip() for linea in texto.splitlines()]
    texto = re.sub(r'\n{3,}', '\n\n', '\n'.join(lineas)).strip()
    return texto[:max_longitud] if max_longitud else texto


def normalizar_nombre(valor):
    return ' '.join(p.capitalize() for p in sanitizar_texto(valor).split())
