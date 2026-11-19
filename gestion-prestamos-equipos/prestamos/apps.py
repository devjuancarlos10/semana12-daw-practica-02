from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PrestamosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'prestamos'
    verbose_name = 'Préstamos de equipos'

    def ready(self):
        from . import signals  # noqa: F401  (registra receptores de login/logout)
        from .roles import sincronizar_grupos

        # Los grupos y sus permisos se crean/actualizan tras cada migrate.
        post_migrate.connect(sincronizar_grupos, sender=self)
