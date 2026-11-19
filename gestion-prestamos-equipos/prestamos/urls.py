from django.urls import path

from . import views

app_name = 'prestamos'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('panel/', views.dashboard, name='dashboard'),
    # Catálogo público
    path('catalogo/', views.catalogo, name='catalogo'),
    path('equipos/<int:pk>/', views.equipo_detalle, name='equipo_detalle'),
    # Gestión de equipos
    path('equipos/nuevo/', views.equipo_crear, name='equipo_crear'),
    path('equipos/<int:pk>/editar/', views.equipo_editar, name='equipo_editar'),
    path('equipos/<int:pk>/eliminar/', views.equipo_eliminar, name='equipo_eliminar'),
    # Préstamos
    path('prestamos/', views.prestamo_lista, name='prestamo_lista'),
    path('prestamos/nuevo/', views.prestamo_nuevo, name='prestamo_nuevo'),
    path('prestamos/<int:pk>/', views.prestamo_detalle, name='prestamo_detalle'),
    path('prestamos/<int:pk>/aprobar/', views.prestamo_aprobar, name='prestamo_aprobar'),
    path('prestamos/<int:pk>/rechazar/', views.prestamo_rechazar, name='prestamo_rechazar'),
    path('prestamos/<int:pk>/devolucion/', views.prestamo_devolver, name='prestamo_devolver'),
    # Administración
    path('reportes/', views.reportes, name='reportes'),
    path('auditoria/', views.auditoria, name='auditoria'),
    path('mi-sesion/', views.mi_sesion, name='mi_sesion'),
]
