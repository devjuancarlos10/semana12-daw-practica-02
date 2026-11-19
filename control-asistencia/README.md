# Sistema de Control de Asistencia y Permisos · TecnoAndes S.A.C.

Aplicación web en Django que automatiza el registro de asistencia, gestiona solicitudes de permisos y vacaciones con flujo de aprobación y genera reportes para planilla, con autorización jerárquica y auditoría completa de cambios.

> TecnoAndes S.A.C. es una empresa ficticia usada como caso de negocio.

## Contenido

- [Funcionalidades](#funcionalidades)
- [Roles y permisos](#roles-y-permisos)
- [Instalación](#instalación)
- [Usuarios de demostración](#usuarios-de-demostración)
- [Arquitectura](#arquitectura)
- [Requisitos no funcionales](#requisitos-no-funcionales)
- [Pruebas](#pruebas)
- [Guía de verificación](#guía-de-verificación)

Manual breve para empleados: [docs/MANUAL_USUARIO.md](docs/MANUAL_USUARIO.md).

## Funcionalidades

| Módulo | Descripción |
|---|---|
| **Empleados y áreas** | DNI, nombres, cargo, área, fecha de ingreso, tipo de contrato, correo corporativo, salario (confidencial) y horario. Antigüedad calculada en años y meses. Al registrar un empleado se crea su usuario. |
| **Marcación** | Botones de entrada/salida con timestamp automático; cálculo de horas trabajadas y **horas extra** (> 8 h). |
| **Corrección (RRHH)** | Ajuste de entrada/salida con motivo (error de sistema, olvido, otro) y justificación de 50+ caracteres; queda el historial de correcciones. |
| **Permisos y vacaciones** | Solicitud con tipo, fechas, motivo y adjunto simulado; flujo **pendiente → aprobado/rechazado** con notificación al empleado. |
| **Reportes** | Horas trabajadas y extra por empleado (por área o globales) y **exportación a texto** para planilla. |
| **Auditoría** | Todo cambio en asistencia, permisos y empleados con usuario, fecha/hora, IP y valores anterior → nuevo. |

### Validaciones

| Formulario | Regla | Implementación |
|---|---|---|
| Empleado | DNI único de 8 dígitos | `clean_dni()` + `unique=True` |
| Empleado | Correo `@tecnoandes.pe` | `validar_email_corporativo` + `clean_email_corporativo()` |
| Empleado | Fecha de ingreso no futura · cargo no vacío | `clean_fecha_ingreso()`, `clean_cargo()` |
| Marcación | Sin entrada nueva si hay una abierta del mismo día | `MarcacionForm.clean()` |
| Marcación | Salida posterior a la entrada | `clean()` + `CheckConstraint` |
| Corrección | Justificación ≥ 50 caracteres y motivo obligatorio | `CorreccionAsistenciaForm` |
| Permiso | Fin ≥ inicio · motivo ≥ 30 caracteres | `clean()`, `clean_motivo()` + `CheckConstraint` |
| Permiso | Sin permisos superpuestos | `clean()` (validación cruzada) |
| Permiso | Vacaciones solicitadas ≤ disponibles (30 − tomadas − pendientes) | `clean()` |
| Todos | Sanitización de textos libres | `validators.sanitizar_texto()` |

## Roles y permisos

| Permiso | Admin RRHH | Jefe de área | Empleado | Contador |
|---|:-:|:-:|:-:|:-:|
| `registrar_asistencia` | ✔ | ✔ | ✔ | ✔ |
| `solicitar_permiso` | ✔ | ✔ | ✔ | ✔ |
| `aprobar_permiso` | ✔ (todos) | ✔ (solo su área, no los propios) | | |
| `ver_reportes_area` | ✔ | ✔ | | |
| `ver_reportes_globales` | ✔ | | | ✔ (lectura) |
| `corregir_asistencia` | ✔ | | | |
| `ver_salarios` | ✔ | | | |

Grupos: **Administradores RRHH**, **Jefes de Área**, **Empleados**, **Contadores** (definidos en [`asistencia/roles.py`](asistencia/roles.py) y sincronizados en cada `migrate`). La jerarquía se aplica también en el Django Admin: un jefe solo ve empleados, asistencia y permisos de su área, y el campo salario se oculta a quien no tenga `ver_salarios`.

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

> **Horario de marcación:** el middleware solo permite marcar dentro de ±2 h del horario del empleado. Para probar a cualquier hora use los empleados de turno tarde (`rhuaman`, `dleon`: 14:00-22:00) o defina `VALIDAR_HORARIO_MARCACION=0` en `.env`.

## Usuarios de demostración

Contraseña para todos: **`TecnoAndes.2026`**

| Usuario | Rol | Área |
|---|---|---|
| `mvelasquez` | Administrador RRHH | RRHH |
| `jquispe` | Jefe de área | Sistemas |
| `cmamani` | Jefa de área | Contabilidad |
| `psoto` | Jefe de área | Ventas |
| `evargas` | Jefa de área | Logística |
| `lpalacios` | Contador | Contabilidad |
| `aflores`, `rhuaman` | Empleado | Sistemas |
| `nyauri` | Empleado | Contabilidad |
| `gcampos`, `dleon` | Empleado | Ventas |
| `tcruz`, `srios` | Empleado | Logística |
| `bortiz` | Empleado | RRHH |
| `exempleado` | Empleado con **cuenta desactivada** | Ventas |

Datos: 5 áreas, 15 empleados, ~135 registros de asistencia (10 días laborables, con tardanzas y horas extra) y 12 permisos en distintos estados.

## Arquitectura

```
control-asistencia/
├── config/                        # settings (timeout 45 min, reglas de negocio), urls
├── asistencia/
│   ├── models.py                  # Usuario, Area, Empleado, Asistencia, CorreccionAsistencia,
│   │                              # Permiso, Notificacion, RegistroAuditoria
│   ├── forms.py                   # Empleado, Marcación, Corrección, Permiso, Revisión, Reporte
│   ├── middleware.py              # Middlewares de control y auditoría
│   ├── signals.py                 # Auditoría de cambios y eventos de sesión
│   ├── decorators.py              # @valida_horario_marcacion, @requiere_algun_permiso
│   ├── roles.py                   # Rol → grupo → permisos
│   ├── admin.py                   # Admin de RRHH con alcance jerárquico
│   └── management/commands/poblar_datos.py
├── templates/  (incluye admin/asistencia/asistencia/horas_extra.html)
├── static/css/app.css
└── docs/MANUAL_USUARIO.md
```

### Middlewares

| Middleware | Responsabilidad |
|---|---|
| `AuditoriaMiddleware` | Publica el request en una `ContextVar` para que las señales registren **todas las modificaciones** de asistencia, permisos y empleados con usuario e IP; registra los 403 como eventos de seguridad; log por petición marcando las de modificación. |
| `BloqueoCuentaMiddleware` | Impide el acceso a usuarios con **cuenta desactivada**, incluso si ya tenían una sesión abierta. |
| `InactividadSesionMiddleware` | Cierra la sesión tras **45 minutos** sin actividad. |
| `HorarioMarcacionMiddleware` | (`process_view`) Solo permite marcar entrada/salida dentro de **±2 h** del horario del empleado. |
| `RecordatorioAsistenciaMiddleware` | Al iniciar sesión en día laborable, si no se marcó la entrada, muestra un recordatorio. |
| `CabecerasSeguridadMiddleware` | CSP, Permissions-Policy y `Cache-Control: no-store`. |

Logs en consola y en `logs/auditoria.log`.

### Django Admin

- **Asistencia**: vista diaria con filtros por **fecha** y **área**, filtro de horas extra, tardanza calculada, botón **«Reporte de horas extra»** por empleado y acción **«Exportar seleccionados a texto»**.
- **Permisos**: lista filtrada por defecto en **pendientes de aprobación**; acciones **«Aprobar permisos seleccionados»** y «Rechazar…» (respetan el alcance del jefe y notifican al empleado).
- **Empleados**: antigüedad y **días de vacaciones disponibles (30 − tomados)** calculados; acción «Desactivar cuentas»; salario oculto sin `ver_salarios`.
- **Auditoría, correcciones y notificaciones**: solo lectura.

## Requisitos no funcionales

| Requisito | Implementación |
|---|---|
| Integridad | El empleado solo edita la observación de su registro en las primeras **24 h**; luego, solo RRHH mediante corrección justificada. |
| Confidencialidad | Salario visible solo con `ver_salarios` (RRHH), tanto en la web como en el admin; la auditoría registra «(confidencial)» en lugar del valor. |
| Auditabilidad | `RegistroAuditoria` con usuario, timestamp, IP, ruta y campos modificados (anterior → nuevo). |
| Usabilidad | Pantalla de marcación con dos botones grandes y hora visible. |
| Seguridad | CSRF en todos los POST, contraseñas PBKDF2, cookie `asistencia_sessionid` `HttpOnly` + `SameSite=Lax` (+ `Secure` en producción). |

## Pruebas

```bash
python manage.py test
```

32 pruebas: validaciones de empleados, marcación, corrección y permisos; flujo de aprobación; permisos jerárquicos; bloqueo de cuentas; horario de marcación; recordatorio; auditoría y timeout.

## Guía de verificación

| Evidencia | Pasos |
|---|---|
| Formulario de permiso con validaciones | `nyauri` → *Nueva solicitud*: fechas invertidas, motivo corto, fechas que se crucen con su solicitud pendiente o vacaciones que excedan lo disponible |
| Flujo de aprobación | `aflores` solicita un permiso → `jquispe` lo aprueba en *Aprobaciones* → `aflores` ve el aviso |
| Admin con reportes y acciones | `mvelasquez` → Admin → Asistencia → *Reporte de horas extra*; Permisos → *Aprobar permisos seleccionados* |
| Middleware de auditoría | Consola de `runserver` y `mvelasquez` → *Auditoría* |
| Control jerárquico | `jquispe` ve solo Sistemas; `aflores` abre `/reportes/` → 403; `lpalacios` ve reportes globales sin salarios; `exempleado` no puede ingresar |
| Sesión con timeout | DevTools → Cookies → `asistencia_sessionid` (expira a los 45 min); menú *Mi sesión* |
