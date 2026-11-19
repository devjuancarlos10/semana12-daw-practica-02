from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def url_con_parametros(context, **kwargs):
    """Conserva los parámetros GET actuales (filtros) al cambiar de página."""
    parametros = context['request'].GET.copy()
    for clave, valor in kwargs.items():
        parametros[clave] = valor
    return f'?{parametros.urlencode()}'


@register.filter
def clase_mensaje(tags):
    return {'error': 'danger', 'debug': 'info'}.get(tags, tags)
