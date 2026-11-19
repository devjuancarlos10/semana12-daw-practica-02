from django.urls import path

from . import views

app_name = 'clinica'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('panel/', views.dashboard, name='dashboard'),
    # Citas
    path('citas/', views.cita_lista, name='cita_lista'),
    path('citas/nueva/', views.cita_nueva, name='cita_nueva'),
    path('citas/<int:pk>/', views.cita_detalle, name='cita_detalle'),
    path('citas/<int:pk>/cancelar/', views.cita_cancelar, name='cita_cancelar'),
    path('citas/<int:cita_id>/consulta/', views.consulta_registrar, name='consulta_registrar'),
    # Información clínica (auditada)
    path('consultas/<int:pk>/', views.consulta_detalle, name='consulta_detalle'),
    path('pacientes/<int:paciente_id>/historial/', views.historial_paciente, name='historial_paciente'),
    path('mi-historial/', views.mi_historial, name='mi_historial'),
    # Pacientes
    path('pacientes/', views.paciente_lista, name='paciente_lista'),
    path('pacientes/nuevo/', views.paciente_nuevo, name='paciente_nuevo'),
    path('pacientes/<int:pk>/editar/', views.paciente_editar, name='paciente_editar'),
    # Administración
    path('reportes/', views.reportes, name='reportes'),
    path('auditoria/', views.auditoria, name='auditoria'),
    path('mi-sesion/', views.mi_sesion, name='mi_sesion'),
]
