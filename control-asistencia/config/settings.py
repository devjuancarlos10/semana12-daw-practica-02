"""Configuración del Sistema de Control de Asistencia y Permisos."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Carga variables CLAVE=valor desde .env (las del sistema tienen prioridad)."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding='utf-8').splitlines():
        linea = linea.strip()
        if not linea or linea.startswith('#') or '=' not in linea:
            continue
        clave, valor = linea.split('=', 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env(BASE_DIR / '.env')

# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Falta DJANGO_SECRET_KEY. Copie .env.example a .env.')
    SECRET_KEY = get_random_secret_key()

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'asistencia.apps.AsistenciaConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Middlewares de control y auditoría
    'asistencia.middleware.AuditoriaMiddleware',
    'asistencia.middleware.BloqueoCuentaMiddleware',
    'asistencia.middleware.InactividadSesionMiddleware',
    'asistencia.middleware.HorarioMarcacionMiddleware',
    'asistencia.middleware.RecordatorioAsistenciaMiddleware',
    'asistencia.middleware.CabecerasSeguridadMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'asistencia.context_processors.sistema',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = 'asistencia.Usuario'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'asistencia:dashboard'
LOGOUT_REDIRECT_URL = 'login'

# ---------------------------------------------------------------------------
# Sesiones: 45 minutos de inactividad
# ---------------------------------------------------------------------------
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_TIMEOUT_INACTIVIDAD = 45 * 60
SESSION_COOKIE_AGE = SESSION_TIMEOUT_INACTIVIDAD
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_NAME = 'asistencia_sessionid'
MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG

CSRF_COOKIE_NAME = 'asistencia_csrftoken'  # Evita conflictos con otras apps en localhost
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = not DEBUG

X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# ---------------------------------------------------------------------------
# Internacionalización
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'es-pe'
TIME_ZONE = 'America/Lima'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'rrhh@tecnoandes.pe'

# ---------------------------------------------------------------------------
# Reglas de negocio
# ---------------------------------------------------------------------------
DOMINIO_EMAIL_CORPORATIVO = 'tecnoandes.pe'
JORNADA_DIARIA_HORAS = 8
DIAS_VACACIONES_ANUALES = 30
TOLERANCIA_MARCACION_HORAS = 2
VALIDAR_HORARIO_MARCACION = os.environ.get('VALIDAR_HORARIO_MARCACION', '1') == '1'
HORAS_EDICION_EMPLEADO = 24   # El empleado no modifica su registro pasadas 24 h

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
(BASE_DIR / 'logs').mkdir(exist_ok=True)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'auditoria': {
            'format': '[{asctime}] {levelname:<7} {name} | {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'consola': {'class': 'logging.StreamHandler', 'formatter': 'auditoria'},
        'archivo': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'auditoria.log',
            'maxBytes': 2 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'auditoria',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'auditoria': {'handlers': ['consola', 'archivo'], 'level': 'INFO', 'propagate': False},
        'seguridad': {'handlers': ['consola', 'archivo'], 'level': 'INFO', 'propagate': False},
        'peticiones': {'handlers': ['consola'], 'level': 'INFO', 'propagate': False},
    },
}
