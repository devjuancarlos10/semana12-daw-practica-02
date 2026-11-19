from django.urls import path

from . import views

app_name = 'asistencia'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('panel/', views.dashboard, name='dashboard'),
    # Asistencia
    path('marcar/', views.marcar, name='marcar'),
    path('mi-asistencia/', views.mi_asistencia, name='mi_asistencia'),
    path('mi-asistencia/<int:pk>/observacion/', views.asistencia_observacion, name='asistencia_observacion'),
    path('asistencia/', views.asistencia_diaria, name='asistencia_diaria'),
    path('asistencia/<int:pk>/corregir/', views.asistencia_corregir, name='asistencia_corregir'),
    # Permisos
    path('permisos/nuevo/', views.permiso_nuevo, name='permiso_nuevo'),
    path('permisos/mios/', views.mis_permisos, name='mis_permisos'),
    path('permisos/bandeja/', views.bandeja_permisos, name='bandeja_permisos'),
    path('permisos/<int:pk>/', views.permiso_detalle, name='permiso_detalle'),
    path('notificaciones/', views.notificaciones, name='notificaciones'),
    # Empleados
    path('empleados/', views.empleado_lista, name='empleado_lista'),
    path('empleados/nuevo/', views.empleado_nuevo, name='empleado_nuevo'),
    path('empleados/<int:pk>/editar/', views.empleado_editar, name='empleado_editar'),
    # Reportes
    path('reportes/', views.reportes, name='reportes'),
    path('reportes/exportar/', views.exportar_reporte, name='exportar_reporte'),
    path('auditoria/', views.auditoria, name='auditoria'),
    path('mi-sesion/', views.mi_sesion, name='mi_sesion'),
]
