from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from prestamos.forms import LoginForm

admin.site.site_header = 'Gestión de Préstamos de Equipos'
admin.site.site_title = 'Préstamos de Equipos'
admin.site.index_title = 'Panel de administración'

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'cuenta/login/',
        auth_views.LoginView.as_view(
            authentication_form=LoginForm, redirect_authenticated_user=True
        ),
        name='login',
    ),
    path('cuenta/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('prestamos.urls')),
]

handler403 = 'prestamos.views.error_403'
handler404 = 'prestamos.views.error_404'
