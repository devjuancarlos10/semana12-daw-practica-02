# Sistema de Gestión de Préstamos de Equipos de Laboratorio

Aplicación web en Django que digitaliza el préstamo de equipos de laboratorio (laptops, proyectores, cámaras, kits Arduino, routers y switches) desde la solicitud hasta la devolución. Incluye trazabilidad completa, control por roles, alertas de vencimiento y auditoría de acciones críticas.

## Contenido

- [Funcionalidades](#funcionalidades)
- [Roles y permisos](#roles-y-permisos)
- [Instalación](#instalación)
- [Usuarios de demostración](#usuarios-de-demostración)
- [Arquitectura](#arquitectura)
- [Seguridad](#seguridad)
- [Pruebas](#pruebas)
- [Guía de verificación](#guía-de-verificación)

## Funcionalidades

| Módulo | Descripción |
|---|---|
| **Catálogo público** | Búsqueda por código, nombre o categoría; filtros por laboratorio y estado; disponibilidad en tiempo real. |
| **Gestión de equipos** | CRUD de equipos con código patrimonial validado (`LAP-000123`), estado operativo / mantenimiento / baja y laboratorio asignado. |
| **Préstamos** | Solicitud (docente) o registro directo (asistente), aprobación/rechazo, devolución con estado del equipo al retornar. |
| **Reportes** | Uso por docente, por laboratorio y por categoría de equipo. |
| **Auditoría** | Registro de préstamos, devoluciones, eliminaciones, inicios de sesión e intentos de acceso no autorizado (usuario, fecha/hora, IP, acción). |
| **Django Admin** | Listados con campos calculados, filtros, búsquedas y acciones masivas. |

### Validaciones del formulario de préstamo

| Regla | Implementación |
|---|---|
| Máximo 3 préstamos vigentes por docente | `SolicitudPrestamoForm.clean()` (configurable: `PRESTAMOS_MAX_ACTIVOS_POR_DOCENTE`) |
| Devolución posterior a la fecha de préstamo | `clean()` + `CheckConstraint` en base de datos |
| Equipo en estado operativo | `clean_equipo()` |
| Stock disponible ≥ cantidad solicitada | `clean()` (validación cruzada equipo/cantidad) |
| Fecha de préstamo no pasada, plazo máximo 30 días | `clean_fecha_prestamo()` / `clean()` |
| Sanitización de observaciones | `clean_observaciones()` → `validators.sanitizar_texto()` |

La devolución usa un formulario clásico (`DevolucionForm`) que exige observaciones cuando el equipo no retorna operativo y actualiza el estado del equipo en el inventario.

## Roles y permisos

Cada rol corresponde a un **grupo** de Django; los permisos se asignan al grupo (ver [`prestamos/roles.py`](prestamos/roles.py)) y se sincronizan automáticamente tras `migrate`.

| Rol | Grupo | Puede |
|---|---|---|
| Administrador | `Administradores` | CRUD de equipos, laboratorios y usuarios; **único que elimina equipos**; reportes; auditoría |
| Docente | `Docentes` | Solicitar préstamos; ver **solo sus propios** préstamos |
| Asistente | `Asistentes` | Registrar, aprobar y rechazar préstamos; **único que registra devoluciones** |
| Invitado | — (anónimo) | Ver el catálogo (solo lectura) |

Permisos personalizados del modelo `Prestamo`: `ver_todos_prestamos`, `gestionar_prestamos`, `registrar_devolucion`, `ver_reportes`.

## Instalación

Requisitos: Python 3.11+.

```bash
# 1. Entorno virtual
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux / macOS

# 2. Dependencias
pip install -r requirements.txt

# 3. Variables de entorno
copy .env.example .env         # Windows  (cp en Linux/macOS)
# Genere una clave y péguela en DJANGO_SECRET_KEY:
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"

# 4. Base de datos y datos de prueba
python manage.py migrate
python manage.py poblar_datos

# 5. Ejecutar
python manage.py runserver
```

Abrir <http://127.0.0.1:8000/>. El panel de administración está en <http://127.0.0.1:8000/admin/>.

Para recargar los datos de demostración: `python manage.py poblar_datos --reset`.
Para mantenimiento técnico puede crear un superusuario: `python manage.py createsuperuser`.

## Usuarios de demostración

Contraseña para todos: **`Equipos.2026`**

| Usuario | Rol | Escenario |
|---|---|---|
| `admin` | Administrador | CRUD de equipos, reportes, auditoría, Django Admin |
| `jperez` | Docente | Tiene un préstamo **vencido** → alerta al iniciar sesión |
| `mrojas` | Docente | Préstamo vencido + solicitud pendiente |
| `lquispe` | Docente | Dos préstamos en curso |
| `cflores` | Docente | Préstamo en curso + solicitud pendiente |
| `ahuaman` | Asistente | Aprobar solicitudes, registrar devoluciones, enviar recordatorios |
| `rtorres` | Asistente | Ídem |

Datos cargados: 5 laboratorios, 14 equipos, 7 usuarios y 18 préstamos (devueltos, en curso, vencidos, solicitados y rechazados).

## Arquitectura

```
gestion-prestamos-equipos/
├── config/                  # settings, urls, wsgi/asgi
├── prestamos/
│   ├── models.py            # Usuario, Laboratorio, Equipo, Prestamo, RegistroAuditoria
│   ├── forms.py             # ModelForms y formularios clásicos con validación
│   ├── validators.py        # Validadores reutilizables y sanitización
│   ├── views.py             # Vistas protegidas (@login_required / @permission_required)
│   ├── middleware.py        # Auditoría, inactividad, alerta de vencidos, cabeceras
│   ├── auditoria.py         # API para marcar acciones auditables
│   ├── signals.py           # Login / logout / login fallido → auditoría
│   ├── roles.py             # Rol → grupo → permisos
│   ├── admin.py             # Django Admin personalizado
│   ├── management/commands/poblar_datos.py
│   └── tests.py
├── templates/               # Plantillas (base, formularios, errores 403/404)
└── static/css/app.css
```

### Middlewares propios

Registrados en `settings.MIDDLEWARE` después de `AuthenticationMiddleware` y `MessageMiddleware`:

| Middleware | Fase | Responsabilidad |
|---|---|---|
| `AuditoriaMiddleware` | request + response | Persiste las acciones críticas marcadas por vistas/señales; registra respuestas **403** y redirecciones al login de anónimos como accesos no autorizados; escribe una línea de log por petición con duración. |
| `InactividadSesionMiddleware` | request | Cierra la sesión tras **30 min** sin actividad, muestra un aviso y lo audita. |
| `AlertaPrestamosVencidosMiddleware` | request | Al iniciar sesión verifica préstamos vencidos y muestra una alerta (una vez por sesión). |
| `CabecerasSeguridadMiddleware` | response | `Content-Security-Policy`, `Permissions-Policy` y `Cache-Control: no-store` en páginas privadas. |

Los logs se muestran en consola y se guardan en `logs/auditoria.log` (rotativo). Los registros de auditoría también se consultan en `/auditoria/` y en el admin, **solo para administradores**, y son de solo lectura.

### Django Admin

- **Equipos**: `list_display` con estado coloreado, disponibilidad y *días desde el último mantenimiento*; búsqueda por código, nombre o categoría; filtros por laboratorio, estado y categoría; acciones **«Marcar como en mantenimiento»** y «Marcar como operativo».
- **Préstamos**: docente, equipo, fechas, estado y campo calculado **«Días de retraso»**; filtros por situación (vencidos), estado, fechas, docente y laboratorio; búsqueda por código de equipo o nombre del docente; jerarquía por fecha; acción **«Enviar recordatorio de devolución»** (correo, mostrado en consola en desarrollo).
- **Auditoría**: solo lectura; no se puede crear, editar ni eliminar.
- Consultas optimizadas con `list_select_related` y anotaciones (sin N+1).

## Seguridad

| Aspecto | Configuración |
|---|---|
| CSRF | `CsrfViewMiddleware` + `{% csrf_token %}` en todos los formularios POST (incluido el logout) |
| Contraseñas | Hash PBKDF2 (por defecto de Django) + validadores de complejidad |
| Cookie de sesión | `HttpOnly`, `SameSite=Lax`, `Secure` en producción, nombre `equipos_sessionid` |
| Expiración | `SESSION_COOKIE_AGE=1800` + `SESSION_SAVE_EVERY_REQUEST` (expiración deslizante de 30 min) |
| Sanitización | Eliminación de HTML/scripts y caracteres de control en textos libres + escape automático en plantillas |
| Cabeceras | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS en producción |
| Secretos | `SECRET_KEY` desde `.env` (excluido de Git) |
| Integridad | Equipos con historial no se eliminan (`on_delete=PROTECT`): se dan de baja |

## Pruebas

```bash
python manage.py test
```

23 pruebas cubren las validaciones de formularios, la autorización por rol, los middlewares (auditoría, alerta de vencidos, timeout de sesión) y las cookies de sesión.

## Guía de verificación

| Evidencia | Cómo obtenerla |
|---|---|
| Formulario válido | `jperez` → *Solicitar préstamo* con un equipo operativo y fechas correctas |
| Formulario inválido | Elegir el equipo `LAP-000103` (en mantenimiento), una devolución igual a la fecha de préstamo o una cantidad mayor al stock; con `mrojas` (2 vigentes) registrar una solicitud más e intentar una cuarta para ver el límite de 3 |
| Admin con filtros y acciones | `admin` → `/admin/prestamos/equipo/` (acción *Marcar como en mantenimiento*); `ahuaman` → `/admin/prestamos/prestamo/?situacion=vencido` (acción *Enviar recordatorio*) |
| Middleware en consola | Observar la terminal de `runserver` durante el login y al registrar un préstamo |
| Login / vista protegida | Abrir `/prestamos/` sin sesión → redirección al login; con `jperez` abrir `/reportes/` → 403 |
| Cookies de sesión | DevTools → Application → Cookies → `equipos_sessionid` (HttpOnly, SameSite=Lax, expiración a 30 min) |
| Datos de sesión | Menú de usuario → *Mi sesión* |
