"""Datos de demostración: 5 áreas, 14 empleados, ~140 registros de asistencia y 12 permisos.

Uso:
    python manage.py poblar_datos
    python manage.py poblar_datos --reset
"""
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from asistencia.models import (
    Area,
    Asistencia,
    CorreccionAsistencia,
    Empleado,
    Notificacion,
    Permiso,
    RegistroAuditoria,
    Usuario,
)
from asistencia.roles import sincronizar_grupos

CLAVE_DEMO = 'TecnoAndes.2026'

AREAS = [('Sistemas', 'SIS'), ('Contabilidad', 'CON'), ('Ventas', 'VEN'),
         ('Logística', 'LOG'), ('RRHH', 'RRH')]

# usuario, nombres, apellidos, dni, cargo, área, rol, ingreso, contrato, salario, entrada, salida
EMPLEADOS = [
    ('mvelasquez', 'Mónica', 'Velásquez Ore', '45871236', 'Gerente de Recursos Humanos', 'RRHH',
     'admin_rrhh', '2016-03-01', 'planilla', 9800, '08:00', '17:00'),
    ('jquispe', 'Jorge', 'Quispe Lazo', '42369874', 'Jefe de Sistemas', 'Sistemas',
     'jefe_area', '2018-07-16', 'planilla', 8500, '08:00', '17:00'),
    ('aflores', 'Andrea', 'Flores Canchari', '47125896', 'Desarrolladora backend', 'Sistemas',
     'empleado', '2021-02-01', 'planilla', 5200, '08:00', '17:00'),
    ('rhuaman', 'Renato', 'Huamán Poma', '48963251', 'Analista de soporte', 'Sistemas',
     'empleado', '2023-09-11', 'locacion', 3200, '14:00', '22:00'),
    ('cmamani', 'Carla', 'Mamani Ticona', '41258963', 'Jefa de Contabilidad', 'Contabilidad',
     'jefe_area', '2017-01-09', 'planilla', 8200, '08:00', '17:00'),
    ('lpalacios', 'Luis', 'Palacios Ríos', '46325874', 'Contador de planillas', 'Contabilidad',
     'contador', '2019-05-20', 'planilla', 5600, '08:00', '17:00'),
    ('nyauri', 'Nadia', 'Yauri Cárdenas', '47896541', 'Asistente contable', 'Contabilidad',
     'empleado', '2022-08-01', 'planilla', 2900, '08:00', '17:00'),
    ('psoto', 'Pedro', 'Soto Aliaga', '43698521', 'Jefe de Ventas', 'Ventas',
     'jefe_area', '2018-11-05', 'planilla', 8000, '08:00', '17:00'),
    ('gcampos', 'Gabriela', 'Campos Meza', '48521473', 'Ejecutiva comercial', 'Ventas',
     'empleado', '2020-06-15', 'planilla', 4100, '08:00', '17:00'),
    ('dleon', 'Diego', 'León Rojas', '49632587', 'Ejecutivo comercial', 'Ventas',
     'empleado', '2024-01-08', 'locacion', 3500, '14:00', '22:00'),
    ('evargas', 'Elsa', 'Vargas Ñaupari', '44785236', 'Jefa de Logística', 'Logística',
     'jefe_area', '2019-03-18', 'planilla', 7800, '08:00', '17:00'),
    ('tcruz', 'Tomás', 'Cruz Inga', '45698712', 'Almacenero', 'Logística',
     'empleado', '2021-10-04', 'planilla', 2600, '07:00', '16:00'),
    ('srios', 'Sara', 'Ríos Pariona', '46985231', 'Analista de compras', 'Logística',
     'empleado', '2022-04-25', 'planilla', 3800, '08:00', '17:00'),
    ('bortiz', 'Bruno', 'Ortiz Salcedo', '43125478', 'Asistente de RRHH', 'RRHH',
     'empleado', '2020-12-01', 'planilla', 3300, '08:00', '17:00'),
]

# usuario, tipo, inicio (días desde hoy), duración, estado, motivo
PERMISOS = [
    ('aflores', 'vacaciones', -40, 5, 'aprobado', 'Vacaciones programadas con la familia fuera de la ciudad.'),
    ('aflores', 'medico', 6, 1, 'pendiente', 'Cita con especialista para control de tratamiento médico.'),
    ('rhuaman', 'personal', 3, 1, 'pendiente', 'Trámite personal en entidad pública que requiere presencia.'),
    ('nyauri', 'vacaciones', 10, 7, 'pendiente', 'Vacaciones de fin de año ya coordinadas con mi jefatura.'),
    ('gcampos', 'medico', -12, 2, 'aprobado', 'Descanso médico por cuadro gripal con certificado adjunto.'),
    ('dleon', 'duelo', -20, 3, 'aprobado', 'Fallecimiento de familiar directo, sepelio en provincia.'),
    ('tcruz', 'personal', -5, 1, 'rechazado', 'Asunto personal impostergable relacionado con mi vivienda.'),
    ('srios', 'vacaciones', 0, 2, 'aprobado', 'Vacaciones cortas para descanso luego del cierre de inventario.'),
    ('srios', 'otro', 14, 1, 'pendiente', 'Participación en capacitación externa sobre gestión de compras.'),
    ('bortiz', 'maternidad', 20, 10, 'pendiente', 'Licencia por paternidad por el nacimiento de mi primer hijo.'),
    ('jquispe', 'vacaciones', 25, 5, 'pendiente', 'Vacaciones anuales programadas desde inicio del año.'),
    ('lpalacios', 'medico', -30, 1, 'aprobado', 'Chequeo médico preventivo anual con examen de laboratorio.'),
]


class Command(BaseCommand):
    help = 'Carga datos de demostración de asistencia y permisos.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true')

    @transaction.atomic
    def handle(self, *args, **opciones):
        if opciones['reset']:
            Notificacion.objects.all().delete()
            CorreccionAsistencia.objects.all().delete()
            Asistencia.objects.all().delete()
            Permiso.objects.all().delete()
            Area.objects.update(jefe=None)
            Empleado.objects.all().delete()
            Area.objects.all().delete()
            Usuario.objects.filter(is_superuser=False).delete()
            RegistroAuditoria.objects.all().delete()
        elif Empleado.objects.exists():
            self.stdout.write(self.style.WARNING('Ya existen datos. Use --reset para recargar.'))
            return

        sincronizar_grupos()
        aleatorio = random.Random(2026)  # Datos reproducibles
        hoy = timezone.localdate()

        areas = {nombre: Area.objects.create(nombre=nombre, codigo=codigo) for nombre, codigo in AREAS}
        empleados = {}
        for (user, nom, ape, dni, cargo, area, rol, ingreso, contrato, salario,
             entrada, salida) in EMPLEADOS:
            usuario = Usuario(username=user, first_name=nom, last_name=ape, rol=rol,
                              email=f'{user}@tecnoandes.pe')
            usuario.set_password(CLAVE_DEMO)
            usuario.save()
            empleados[user] = Empleado.objects.create(
                usuario=usuario, dni=dni, nombres=nom, apellidos=ape, cargo=cargo, area=areas[area],
                fecha_ingreso=date.fromisoformat(ingreso), tipo_contrato=contrato,
                email_corporativo=f'{user}@tecnoandes.pe', salario=Decimal(salario),
                hora_entrada=time.fromisoformat(entrada), hora_salida=time.fromisoformat(salida),
            )
        for nombre, jefe in [('Sistemas', 'jquispe'), ('Contabilidad', 'cmamani'),
                             ('Ventas', 'psoto'), ('Logística', 'evargas'), ('RRHH', 'mvelasquez')]:
            areas[nombre].jefe = empleados[jefe]
            areas[nombre].save()

        # Cuenta desactivada para demostrar el middleware de bloqueo
        bloqueado = Usuario(username='exempleado', first_name='Iván', last_name='Paucar Lima',
                            rol='empleado', email='exempleado@tecnoandes.pe')
        bloqueado.set_password(CLAVE_DEMO)
        bloqueado.save()
        Empleado.objects.create(
            usuario=bloqueado, dni='40011223', nombres='Iván', apellidos='Paucar Lima',
            cargo='Practicante', area=areas['Ventas'], fecha_ingreso=date(2024, 3, 1),
            tipo_contrato='locacion', email_corporativo='exempleado@tecnoandes.pe',
            salario=Decimal('1200'), activo=False,
        )

        # Asistencia de los últimos 10 días laborables
        dias = []
        cursor = hoy - timedelta(days=1)
        while len(dias) < 10:
            if cursor.weekday() < 5:
                dias.append(cursor)
            cursor -= timedelta(days=1)
        registros = 0
        for empleado in empleados.values():
            for dia in dias:
                if aleatorio.random() < 0.06:
                    continue  # inasistencia ocasional
                entrada = datetime.combine(dia, empleado.hora_entrada) + timedelta(
                    minutes=aleatorio.choice([-15, -10, -5, 0, 0, 5, 12, 25]))
                duracion = timedelta(hours=9, minutes=aleatorio.choice([-30, 0, 0, 15, 45, 90, 150]))
                Asistencia.objects.create(
                    empleado=empleado, fecha=dia,
                    hora_entrada=timezone.make_aware(entrada),
                    hora_salida=timezone.make_aware(entrada + duracion),
                )
                registros += 1
        # Hoy: algunos ya marcaron entrada (otros verán el recordatorio)
        if hoy.weekday() < 5:
            for user in ['aflores', 'cmamani', 'gcampos', 'tcruz']:
                e = empleados[user]
                Asistencia.objects.create(
                    empleado=e, fecha=hoy,
                    hora_entrada=timezone.make_aware(datetime.combine(hoy, e.hora_entrada) + timedelta(minutes=3)),
                )
                registros += 1

        revisores = {'jquispe': 'mvelasquez'}
        for user, tipo, inicio, duracion, estado, motivo in PERMISOS:
            empleado = empleados[user]
            permiso = Permiso.objects.create(
                empleado=empleado, tipo=tipo, motivo=motivo,
                fecha_inicio=hoy + timedelta(days=inicio),
                fecha_fin=hoy + timedelta(days=inicio + duracion - 1),
                adjunto_descripcion='Certificado médico CMP 45120' if tipo == 'medico' else '',
            )
            if estado != 'pendiente':
                jefe = revisores.get(user) or empleado.area.jefe.usuario.username
                permiso.resolver(Usuario.objects.get(username=jefe), estado == 'aprobado',
                                 '' if estado == 'aprobado' else 'Fecha de alta demanda en el área.')

        self.stdout.write(self.style.SUCCESS(
            f'Datos cargados: {Area.objects.count()} áreas, {Empleado.objects.count()} empleados, '
            f'{registros} registros de asistencia, {Permiso.objects.count()} permisos.'
        ))
        self.stdout.write(f'Contraseña de todos los usuarios de demostración: {CLAVE_DEMO}')
