from django.contrib import admin
from django.urls import include, path

from clinica.views import LoginDiferenciadoView, LogoutAuditadoView

admin.site.site_header = 'Centro Médico · Gestión Operativa'
admin.site.site_title = 'Consultas Médicas'
admin.site.index_title = 'Panel de administración'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cuenta/login/', LoginDiferenciadoView.as_view(), name='login'),
    path('cuenta/logout/', LogoutAuditadoView.as_view(), name='logout'),
    path('', include('clinica.urls')),
]

handler403 = 'clinica.views.error_403'
handler404 = 'clinica.views.error_404'
