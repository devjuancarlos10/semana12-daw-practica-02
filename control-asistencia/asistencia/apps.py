from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AsistenciaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'asistencia'
    verbose_name = 'Asistencia y permisos'

    def ready(self):
        from . import signals  # noqa: F401
        from .roles import sincronizar_grupos

        post_migrate.connect(sincronizar_grupos, sender=self)
