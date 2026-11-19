from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ClinicaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'clinica'
    verbose_name = 'Centro médico'

    def ready(self):
        from . import signals  # noqa: F401  (auditoría de cambios y de sesión)
        from .roles import sincronizar_grupos

        post_migrate.connect(sincronizar_grupos, sender=self)
