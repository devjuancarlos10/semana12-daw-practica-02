"""Carga datos de demostración: 5 laboratorios, 14 equipos, 7 usuarios y 18 préstamos.

Uso:
    python manage.py poblar_datos            # agrega datos si la BD está vacía
    python manage.py poblar_datos --reset    # borra préstamos/equipos y recarga
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from prestamos.models import Equipo, Laboratorio, Prestamo, RegistroAuditoria, Usuario
from prestamos.roles import sincronizar_grupos

CLAVE_DEMO = 'Equipos.2026'

LABORATORIOS = [
    ('LAB01', 'Laboratorio de Redes', 'Pabellón B, 2.º piso'),
    ('LAB02', 'Laboratorio de Software', 'Pabellón B, 3.er piso'),
    ('LAB03', 'Laboratorio de Hardware', 'Pabellón C, 1.er piso'),
    ('LAB04', 'Laboratorio de Robótica', 'Pabellón C, 2.º piso'),
    ('LAB05', 'Laboratorio Multimedia', 'Pabellón A, 1.er piso'),
]

USUARIOS = [
    # username, nombre, apellido, rol, código, departamento
    ('admin', 'Rosa', 'Mendoza Salazar', Usuario.Rol.ADMIN, 'ADM00001', 'Jefatura de Laboratorios'),
    ('jperez', 'Jorge', 'Pérez Huamán', Usuario.Rol.DOCENTE, 'DOC10021', 'Redes y Comunicaciones'),
    ('mrojas', 'María', 'Rojas Ccanto', Usuario.Rol.DOCENTE, 'DOC10045', 'Ingeniería de Software'),
    ('lquispe', 'Luis', 'Quispe Mayta', Usuario.Rol.DOCENTE, 'DOC10077', 'Sistemas Embebidos'),
    ('cflores', 'Carmen', 'Flores Ríos', Usuario.Rol.DOCENTE, 'DOC10090', 'Multimedia'),
    ('ahuaman', 'Ana', 'Huamán Torres', Usuario.Rol.ASISTENTE, 'AST20011', 'Laboratorios'),
    ('rtorres', 'Raúl', 'Torres Vílchez', Usuario.Rol.ASISTENTE, 'AST20012', 'Laboratorios'),
]

EQUIPOS = [
    # código, nombre, marca, modelo, categoría, lab, unidades, estado, días desde mant.
    ('LAP-000101', 'Laptop de desarrollo', 'Lenovo', 'ThinkPad E14', 'laptop', 'LAB02', 8, 'operativo', 40),
    ('LAP-000102', 'Laptop de alto rendimiento', 'Dell', 'Precision 3581', 'laptop', 'LAB02', 4, 'operativo', 95),
    ('LAP-000103', 'Laptop ultraligera', 'HP', 'ProBook 440', 'laptop', 'LAB05', 6, 'mantenimiento', 400),
    ('PRY-000201', 'Proyector multimedia', 'Epson', 'PowerLite X49', 'proyector', 'LAB05', 3, 'operativo', 60),
    ('PRY-000202', 'Proyector portátil', 'ViewSonic', 'M1 Pro', 'proyector', 'LAB01', 2, 'operativo', 210),
    ('CAM-000301', 'Cámara réflex', 'Canon', 'EOS 250D', 'camara', 'LAB05', 2, 'operativo', 30),
    ('CAM-000302', 'Cámara de documentos', 'IPEVO', 'V4K', 'camara', 'LAB05', 3, 'operativo', 300),
    ('ARD-000401', 'Kit Arduino Uno', 'Arduino', 'Starter Kit R3', 'arduino', 'LAB04', 15, 'operativo', 20),
    ('ARD-000402', 'Kit Arduino Mega', 'Arduino', 'Mega 2560', 'arduino', 'LAB04', 10, 'operativo', 120),
    ('ARD-000403', 'Kit de sensores IoT', 'Elegoo', '37 en 1', 'arduino', 'LAB04', 8, 'operativo', None),
    ('RTR-000501', 'Router empresarial', 'Cisco', 'ISR 1100', 'router', 'LAB01', 4, 'operativo', 150),
    ('RTR-000502', 'Router inalámbrico', 'MikroTik', 'hAP ax2', 'router', 'LAB01', 6, 'mantenimiento', 380),
    ('SWT-000601', 'Switch administrable 24p', 'Cisco', 'Catalyst 1000', 'switch', 'LAB01', 5, 'operativo', 75),
    ('SWT-000602', 'Switch capa 3', 'HPE', 'Aruba 2930F', 'switch', 'LAB03', 2, 'baja', 700),
]

# docente, equipo, cantidad, inicio (días desde hoy), duración, estado, retraso devolución
PRESTAMOS = [
    # Devueltos (históricos)
    ('jperez', 'RTR-000501', 2, -60, 7, 'devuelto', 0),
    ('jperez', 'SWT-000601', 1, -45, 5, 'devuelto', 2),
    ('mrojas', 'LAP-000101', 3, -40, 10, 'devuelto', 0),
    ('mrojas', 'PRY-000201', 1, -30, 2, 'devuelto', 1),
    ('lquispe', 'ARD-000401', 5, -28, 14, 'devuelto', 0),
    ('lquispe', 'ARD-000402', 2, -20, 7, 'devuelto', 0),
    ('cflores', 'CAM-000301', 1, -25, 3, 'devuelto', 4),
    ('cflores', 'CAM-000302', 1, -15, 5, 'devuelto', 0),
    # En curso (algunos vencidos)
    ('jperez', 'RTR-000501', 1, -12, 5, 'activo', None),       # vencido
    ('jperez', 'PRY-000202', 1, -2, 7, 'activo', None),
    ('mrojas', 'LAP-000102', 2, -10, 3, 'activo', None),       # vencido
    ('lquispe', 'ARD-000401', 4, -5, 14, 'activo', None),
    ('lquispe', 'ARD-000403', 3, -1, 10, 'activo', None),
    ('cflores', 'CAM-000301', 1, -3, 5, 'activo', None),
    # Solicitudes
    ('mrojas', 'LAP-000101', 2, 0, 5, 'pendiente', None),
    ('cflores', 'PRY-000201', 1, 1, 3, 'pendiente', None),
    ('lquispe', 'SWT-000601', 2, 0, 4, 'rechazado', None),
    ('jperez', 'LAP-000101', 1, -8, 3, 'rechazado', None),
]


class Command(BaseCommand):
    help = 'Carga datos de demostración para el sistema de préstamos.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Elimina datos previos antes de cargar.')

    @transaction.atomic
    def handle(self, *args, **opciones):
        if opciones['reset']:
            Prestamo.objects.all().delete()
            Equipo.objects.all().delete()
            Laboratorio.objects.all().delete()
            RegistroAuditoria.objects.all().delete()
            Usuario.objects.filter(username__in=[u[0] for u in USUARIOS]).delete()
        elif Prestamo.objects.exists():
            self.stdout.write(self.style.WARNING('Ya existen datos. Use --reset para recargar.'))
            return

        sincronizar_grupos()
        hoy = timezone.localdate()

        usuarios = {}
        for username, nombre, apellido, rol, codigo, depto in USUARIOS:
            usuario = Usuario(
                username=username, first_name=nombre, last_name=apellido, rol=rol,
                email=f'{username}@laboratorios.local', codigo_institucional=codigo,
                departamento=depto,
            )
            usuario.set_password(CLAVE_DEMO)  # Hash PBKDF2, nunca texto plano
            usuario.save()
            usuarios[username] = usuario

        labs = {}
        for i, (codigo, nombre, ubicacion) in enumerate(LABORATORIOS):
            labs[codigo] = Laboratorio.objects.create(
                codigo=codigo, nombre=nombre, ubicacion=ubicacion, capacidad=25 + i * 5,
                responsable=usuarios['ahuaman' if i % 2 == 0 else 'rtorres'],
            )

        equipos = {}
        for codigo, nombre, marca, modelo, cat, lab, unidades, estado, dias in EQUIPOS:
            equipos[codigo] = Equipo.objects.create(
                codigo_patrimonial=codigo, nombre=nombre, marca=marca, modelo=modelo,
                categoria=cat, laboratorio=labs[lab], cantidad_total=unidades, estado=estado,
                fecha_ultimo_mantenimiento=(hoy - timedelta(days=dias)) if dias else None,
            )

        asistentes = [usuarios['ahuaman'], usuarios['rtorres']]
        for i, (docente, equipo, cant, inicio, duracion, estado, retraso) in enumerate(PRESTAMOS):
            fecha_inicio = hoy + timedelta(days=inicio)
            prevista = fecha_inicio + timedelta(days=duracion)
            prestamo = Prestamo(
                docente=usuarios[docente], equipo=equipos[equipo], cantidad=cant,
                fecha_prestamo=fecha_inicio, fecha_devolucion_prevista=prevista, estado=estado,
                observaciones='Uso en taller de configuración de equipos.' if i % 3 == 0 else '',
            )
            if estado in ('activo', 'devuelto', 'rechazado'):
                prestamo.registrado_por = asistentes[i % 2]
            if estado == 'devuelto':
                prestamo.fecha_devolucion_real = prevista + timedelta(days=retraso)
                prestamo.estado_equipo_devolucion = Equipo.Estado.OPERATIVO
                prestamo.recibido_por = asistentes[(i + 1) % 2]
                prestamo.observaciones_devolucion = 'Equipo completo y funcionando.'
            if estado == 'rechazado':
                prestamo.motivo_rechazo = 'Unidades reservadas para mantenimiento preventivo.'
            prestamo.save()

        self.stdout.write(self.style.SUCCESS(
            f'Datos cargados: {Laboratorio.objects.count()} laboratorios, '
            f'{Equipo.objects.count()} equipos, {len(usuarios)} usuarios, '
            f'{Prestamo.objects.count()} préstamos.'
        ))
        self.stdout.write(f'Contraseña de todos los usuarios de demostración: {CLAVE_DEMO}')
