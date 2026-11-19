# Plataforma de Gestión de Consultas Médicas

Aplicación web en Django para un centro médico: agendamiento de citas, historial clínico electrónico, control de acceso por roles médicos y auditoría legal de los accesos a información sensible.

## Contenido

- [Funcionalidades](#funcionalidades)
- [Roles y permisos](#roles-y-permisos)
- [Instalación](#instalación)
- [Usuarios de demostración](#usuarios-de-demostración)
- [Arquitectura](#arquitectura)
- [Seguridad y cumplimiento](#seguridad-y-cumplimiento)
- [Pruebas](#pruebas)
- [Guía de verificación](#guía-de-verificación)

## Funcionalidades

| Módulo | Descripción |
|---|---|
| **Pacientes** | Registro con DNI, nombres, fecha de nacimiento, tipo (estudiante/docente), código institucional, correo y alergias. Búsqueda por DNI o nombre. |
| **Médicos** | CMP, especialidad, consultorio y horarios de atención por día (inline en el admin). |
| **Agendamiento** | Selección de especialidad → médico (filtrado en el navegador), fecha, turno y motivo. |
| **Consulta médica** | Diagnóstico, tratamiento, medicamentos, próxima cita de control y documentos adjuntos (descripción). **Inmutable**: solo admite notas. |
| **Historial clínico** | Consultas, notas, alergias y últimas citas del paciente. Cada acceso queda auditado. |
| **Reportes** | Citas por especialidad, médicos con más consultas, pacientes frecuentes, tasa de inasistencia. |
| **Auditoría** | Accesos a historiales (permitidos/denegados) y registro de cambios (quién, cuándo, qué). |

### Validaciones

| Formulario | Regla | Dónde |
|---|---|---|
| Paciente | DNI de 8 dígitos numéricos | `validar_dni` + `clean_dni()` |
| Paciente | Fecha de nacimiento no futura | `validar_fecha_no_futura` + `clean_fecha_nacimiento()` |
| Paciente | Correo `@<dominio institucional>` obligatorio para estudiantes | `PacienteForm.clean()` + `Paciente.clean()` |
| Paciente | Nombres normalizados y alergias sanitizadas | `clean_nombres()`, `clean_alergias()` |
| Médico | CMP con formato válido (5-6 dígitos, ej. `12345`) | `validar_cmp` |
| Cita | Médico pertenece a la especialidad elegida | `CitaForm.clean()` |
| Cita | Dentro del horario del centro (L-V 08:00-18:00, Sáb 08:00-13:00) | `clean()` + `HorarioAtencionMiddleware` |
| Cita | Dentro del horario del médico | `Medico.atiende_en()` |
| Cita | El médico no puede tener citas superpuestas | `clean()` + `UniqueConstraint` en BD |
| Cita | Un paciente no puede tener 2 citas el mismo día | `clean()` (validación cruzada) |
| Cita | Motivo de al menos 20 caracteres, fecha no pasada, máximo 60 días de anticipación | `clean_motivo()`, `clean_fecha()` |
| Consulta | Diagnóstico y tratamiento mínimos; control posterior a hoy | `ConsultaForm` |

El dominio del correo institucional se configura con `DOMINIO_EMAIL_INSTITUCIONAL` en `.env`.

## Roles y permisos

Grupos: **Admins**, **Médicos**, **Recepcionistas**, **Pacientes**. Permisos granulares: `ver_historial_clinico`, `agendar_cita`, `cancelar_cita`, `generar_reportes` (más `ver_agenda_completa`).

Diagrama y matriz completos en [docs/roles_y_permisos.md](docs/roles_y_permisos.md).

**Login diferenciado:** el usuario elige su tipo (Paciente, Médico, Recepcionista, Administrador); si las credenciales no corresponden a ese tipo el acceso se rechaza.

## Instalación

Requisitos: Python 3.11+.

```bash
python -m venv venv
venv\Scripts\activate              # Windows  (source venv/bin/activate en Linux/macOS)
pip install -r requirements.txt

copy .env.example .env             # cp en Linux/macOS; completar DJANGO_SECRET_KEY
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"

python manage.py migrate
python manage.py poblar_datos
python manage.py runserver
```

Aplicación: <http://127.0.0.1:8000/> · Admin: <http://127.0.0.1:8000/admin/>
Recargar datos: `python manage.py poblar_datos --reset`.

## Usuarios de demostración

Contraseña para todos: **`Clinica.2026`**. En el login seleccione el tipo de usuario correspondiente.

| Usuario | Tipo | Notas |
|---|---|---|
| `director` | Administrador | Reportes, auditoría, Django Admin (agenda y estadísticas) |
| `recepcion1`, `recepcion2` | Recepcionista | Agendar/cancelar citas, registrar pacientes |
| `mgarcia`, `lcastro`, `ppalomino`, `rsalas` | Médico | Tienen citas **hoy** → alerta al iniciar sesión |
| `emendoza`, `jvargas`, `scardenas`, `alopez` | Médico | |
| `70123456` … `79012345` | Paciente (estudiante) | Usuario = DNI |
| `40123456` … `44567890` | Paciente (docente) | Usuario = DNI |

Datos: 8 especialidades, 8 médicos con horarios, 15 pacientes y 44 citas (completadas con su consulta, programadas, canceladas y con inasistencia).

## Arquitectura

```
gestion-consultas-medicas/
├── config/                        # settings (timeout 20 min, horario del centro), urls
├── clinica/
│   ├── models.py                  # Usuario, Especialidad, Medico, HorarioAtencion, Paciente,
│   │                              # Cita, Consulta, NotaConsulta, AccesoHistorial, RegistroCambio
│   ├── forms.py                   # Login diferenciado, Paciente, Cita, Consulta, Nota
│   ├── horarios.py                # Reglas de horario compartidas (formularios y middleware)
│   ├── middleware.py              # Middlewares especializados
│   ├── decorators.py              # @informacion_sensible, @valida_horario_atencion
│   ├── signals.py                 # Trazabilidad de cambios y eventos de sesión
│   ├── auditoria.py               # Contexto de auditoría (ContextVar) y registro de accesos
│   ├── roles.py                   # Rol → grupo → permisos
│   ├── admin.py                   # Admin: agenda diaria, estadísticas, acciones masivas
│   └── management/commands/poblar_datos.py
├── templates/  (incluye admin/clinica/cita/agenda.html y estadisticas.html)
├── static/     (css/app.css, js/cita.js)
└── docs/roles_y_permisos.md
```

### Middlewares

| Middleware | Hook | Responsabilidad |
|---|---|---|
| `ContextoAuditoriaMiddleware` | `__call__` | Publica el request en una `ContextVar` para que las señales sepan quién cambia cada registro; log de cada petición. |
| `InactividadSesionMiddleware` | `__call__` | Cierra la sesión tras **20 minutos** sin actividad. |
| `AuditoriaMedicaMiddleware` | `process_view` + `__call__` | Detecta vistas marcadas con `@informacion_sensible` y registra **quién accedió a qué historial**, con resultado (permitido/denegado), IP y fecha. Registra además cualquier 403. |
| `HorarioAtencionMiddleware` | `process_view` | Antes de la vista de agendamiento, rechaza fechas/horas fuera del horario de atención y conserva lo ingresado en la sesión. |
| `AlertasMedicoMiddleware` | `__call__` | Al iniciar sesión notifica al médico sus citas pendientes del día y la próxima. |
| `CabecerasSeguridadMiddleware` | `__call__` | CSP, Permissions-Policy y `Cache-Control: no-store` para información médica. |

Los logs se muestran en consola y se guardan en `logs/auditoria_medica.log`.

### Django Admin

- **Citas**: filtros por periodo (hoy / 7 días / pasadas), estado, especialidad, médico y fecha; búsqueda por DNI o nombre; acciones **«Marcar citas como completadas»** y «Marcar como No asistió»; botones **Agenda diaria** (agrupada por médico, con navegación por fecha) y **Estadísticas**.
- **Pacientes**: búsqueda por DNI o nombre; campos calculados **edad** y **días desde la última consulta**.
- **Médicos**: horarios en línea, citas de hoy y total de consultas.
- **Consultas**: solo lectura; únicamente se agregan notas.
- **Auditoría**: accesos a historiales y registro de cambios, de solo lectura.

## Seguridad y cumplimiento

| Requisito | Implementación |
|---|---|
| Confidencialidad | Acceso a historiales restringido a médicos y al propio paciente; cada acceso (permitido o denegado) queda en `AccesoHistorial`. |
| Integridad | `Consulta` y `NotaConsulta` son inmutables (`save()`/`delete()` bloqueados tras la creación); el admin las muestra en solo lectura. |
| Trazabilidad | Señales `pre_save`/`post_save`/`post_delete` registran usuario, fecha, IP y campos modificados (valor anterior → nuevo). El contenido clínico se registra como «(modificado)» sin exponer su valor. |
| Sesión | 20 min de inactividad, cookie `clinica_sessionid` `HttpOnly`, `SameSite=Strict`, `Secure` en producción. |
| CSRF | Todos los formularios POST, incluido el logout. |
| Contraseñas | PBKDF2 + validadores (mínimo 10 caracteres). |
| Cabeceras | CSP, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `no-store`. |

## Pruebas

```bash
python manage.py test
```

27 pruebas: validaciones de citas y pacientes, permisos granulares, login diferenciado, inmutabilidad de consultas, middleware de horario, alertas, trazabilidad y timeout de sesión.

## Guía de verificación

| Evidencia | Pasos |
|---|---|
| Formulario de cita con validaciones | `recepcion1` → *Agendar cita*: probar un domingo (bloqueo del middleware), un motivo corto, un turno ocupado o un segundo turno el mismo día para el mismo paciente |
| Admin con agenda y filtros | `director` → Admin → Citas → *Agenda diaria* / *Estadísticas*; acción *Marcar citas como completadas* |
| Middleware de auditoría | `mgarcia` abre un historial; `70123456` intenta abrir `/pacientes/<id de otro>/historial/` → 403; revisar consola y `director` → *Auditoría* |
| Control de permisos | `recepcion1` abre `/reportes/` o un historial → 403 |
| Cookies con timeout | DevTools → Application → Cookies → `clinica_sessionid` (expira a los 20 min); menú *Mi sesión* |
