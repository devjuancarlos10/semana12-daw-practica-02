from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from asistencia.forms import LoginForm

admin.site.site_header = 'TecnoAndes · Recursos Humanos'
admin.site.site_title = 'Asistencia y Permisos'
admin.site.index_title = 'Operaciones de RRHH'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cuenta/login/', auth_views.LoginView.as_view(
        authentication_form=LoginForm, redirect_authenticated_user=True), name='login'),
    path('cuenta/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('asistencia.urls')),
]

handler403 = 'asistencia.views.error_403'
handler404 = 'asistencia.views.error_404'
