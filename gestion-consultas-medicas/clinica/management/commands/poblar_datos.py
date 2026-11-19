"""Datos de demostración: 8 especialidades, 8 médicos, 15 pacientes y 40+ citas.

Uso:
    python manage.py poblar_datos
    python manage.py poblar_datos --reset
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from clinica.models import (
    AccesoHistorial,
    Cita,
    Consulta,
    Especialidad,
    HorarioAtencion,
    Medico,
    NotaConsulta,
    Paciente,
    RegistroCambio,
    Usuario,
)
from clinica.roles import sincronizar_grupos

CLAVE_DEMO = 'Clinica.2026'

ESPECIALIDADES = [
    ('Medicina General', 'Atención primaria y evaluación general'),
    ('Odontología', 'Salud bucal'),
    ('Psicología', 'Salud mental y orientación'),
    ('Nutrición', 'Evaluación y planes nutricionales'),
    ('Oftalmología', 'Salud visual'),
    ('Dermatología', 'Enfermedades de la piel'),
    ('Ginecología', 'Salud de la mujer'),
    ('Traumatología', 'Lesiones musculoesqueléticas'),
]

MEDICOS = [
    # usuario, nombres, apellidos, CMP, consultorio
    ('mgarcia', 'Miguel', 'García Ramos', '45120', 'C-101'),
    ('lcastro', 'Lucía', 'Castro Vela', '51234', 'C-102'),
    ('ppalomino', 'Patricia', 'Palomino Ñaupari', '38877', 'C-103'),
    ('rsalas', 'Ricardo', 'Salas Cóndor', '60231', 'C-104'),
    ('emendoza', 'Elena', 'Mendoza Arias', '42789', 'C-105'),
    ('jvargas', 'Julio', 'Vargas Inga', '57002', 'C-106'),
    ('scardenas', 'Sofía', 'Cárdenas Paz', '49910', 'C-107'),
    ('alopez', 'Andrés', 'López Huaroc', '63345', 'C-108'),
]

PACIENTES = [
    # dni, nombres, apellidos, nacimiento, tipo, código, alergias
    ('70123456', 'Diego', 'Rojas Quispe', '2003-04-12', 'estudiante', '2021100345', 'Penicilina'),
    ('71234567', 'Valeria', 'Huamán Soto', '2002-09-30', 'estudiante', '2020200112', ''),
    ('72345678', 'Kevin', 'Torres Matos', '2004-01-18', 'estudiante', '2022100871', ''),
    ('73456789', 'Camila', 'Paredes León', '2001-11-05', 'estudiante', '2019300456', 'Ibuprofeno'),
    ('74567890', 'Bruno', 'Espinoza Ruiz', '2003-07-22', 'estudiante', '2021300998', ''),
    ('75678901', 'Ariana', 'Chávez Poma', '2005-03-09', 'estudiante', '2023100019', 'Mariscos'),
    ('76789012', 'Mateo', 'Gutiérrez Laura', '2002-12-14', 'estudiante', '2020100777', ''),
    ('77890123', 'Fernanda', 'Ccama Rivera', '2004-06-27', 'estudiante', '2022200540', ''),
    ('78901234', 'Joaquín', 'Ramírez Ticse', '2003-02-03', 'estudiante', '2021200333', 'Látex'),
    ('79012345', 'Daniela', 'Salazar Mucha', '2001-08-16', 'estudiante', '2019100222', ''),
    ('40123456', 'Óscar', 'Meza Bendezú', '1975-05-20', 'docente', 'DOC0451', 'Sulfas'),
    ('41234567', 'Gloria', 'Aquino Pérez', '1980-10-11', 'docente', 'DOC0322', ''),
    ('42345678', 'Hernán', 'Yupanqui Dávila', '1968-01-29', 'docente', 'DOC0188', ''),
    ('43456789', 'Rocío', 'Benites Orihuela', '1985-07-07', 'docente', 'DOC0590', 'Aspirina'),
    ('44567890', 'Walter', 'Inga Carhuamaca', '1972-03-15', 'docente', 'DOC0233', ''),
]

MOTIVOS = [
    'Dolor de cabeza persistente desde hace tres días',
    'Control anual y evaluación general de salud',
    'Molestia dental al masticar en el lado derecho',
    'Ansiedad y dificultad para dormir por exámenes',
    'Orientación sobre alimentación saludable y peso',
    'Visión borrosa al leer por periodos prolongados',
    'Erupción en la piel de los brazos con picazón',
    'Dolor en la rodilla izquierda tras actividad deportiva',
]

DIAGNOSTICOS = [
    ('Cefalea tensional asociada a estrés.', 'Reposo, hidratación y pausas activas.', 'Paracetamol 500 mg c/8 h por 3 días'),
    ('Paciente aparentemente sano.', 'Mantener hábitos saludables y control anual.', ''),
    ('Caries en pieza 46.', 'Restauración con resina en próxima cita.', 'Enjuague con clorhexidina 0.12 % c/12 h'),
    ('Trastorno de ansiedad leve.', 'Terapia cognitivo-conductual, técnicas de respiración.', ''),
    ('Sobrepeso grado I.', 'Plan alimentario de 1800 kcal y actividad física.', ''),
    ('Astigmatismo leve.', 'Uso de lentes correctivos.', 'Lágrimas artificiales c/6 h'),
    ('Dermatitis de contacto.', 'Evitar el agente irritante, hidratar la piel.', 'Hidrocortisona crema 1 % c/12 h por 7 días'),
    ('Esguince de rodilla grado I.', 'Reposo relativo, hielo local y vendaje.', 'Naproxeno 550 mg c/12 h por 5 días'),
]


class Command(BaseCommand):
    help = 'Carga datos de demostración para el centro médico.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true')

    @transaction.atomic
    def handle(self, *args, **opciones):
        if opciones['reset']:
            self._limpiar()
        elif Cita.objects.exists():
            self.stdout.write(self.style.WARNING('Ya existen datos. Use --reset para recargar.'))
            return

        sincronizar_grupos()
        self.hoy = timezone.localdate()
        dominio = settings.DOMINIO_EMAIL_INSTITUCIONAL

        self._usuario('director', 'Carmen', 'Villanueva Rojas', Usuario.Rol.ADMIN)
        self.recepcion = [
            self._usuario('recepcion1', 'Lucero', 'Romero Tapia', Usuario.Rol.RECEPCIONISTA),
            self._usuario('recepcion2', 'Iván', 'Sánchez Mallma', Usuario.Rol.RECEPCIONISTA),
        ]

        self.medicos = []
        for i, ((esp, desc), (user, nom, ape, cmp, consultorio)) in enumerate(zip(ESPECIALIDADES, MEDICOS)):
            especialidad = Especialidad.objects.create(nombre=esp, descripcion=desc)
            usuario = self._usuario(user, nom, ape, Usuario.Rol.MEDICO)
            medico = Medico.objects.create(usuario=usuario, cmp=cmp, especialidad=especialidad,
                                           consultorio=consultorio)
            inicio, fin = (time(8), time(13)) if i % 2 == 0 else (time(13), time(18))
            for dia in range(5):
                HorarioAtencion.objects.create(medico=medico, dia_semana=dia, hora_inicio=inicio, hora_fin=fin)
            if i % 3 == 0:
                HorarioAtencion.objects.create(medico=medico, dia_semana=5, hora_inicio=time(8), hora_fin=time(13))
            self.medicos.append(medico)

        self.pacientes = []
        for dni, nom, ape, nac, tipo, codigo, alergias in PACIENTES:
            usuario = self._usuario(dni, nom, ape, Usuario.Rol.PACIENTE)
            correo_base = f'{nom.lower()}.{ape.split()[0].lower()}'.translate(str.maketrans('áéíóúñ', 'aeioun'))
            self.pacientes.append(Paciente.objects.create(
                usuario=usuario, dni=dni, nombres=nom, apellidos=ape,
                fecha_nacimiento=datetime.strptime(nac, '%Y-%m-%d').date(), tipo=tipo,
                codigo_institucional=codigo, alergias=alergias,
                email=f'{correo_base}@{dominio}' if tipo == 'estudiante' else f'{correo_base}@correo.pe',
                telefono=f'9{dni[:8]}', registrado_por=self.recepcion[0],
            ))

        self.ocupados = set()      # (medico_id, fecha, hora)
        self.paciente_dia = set()  # (paciente_id, fecha)
        citas = self._generar_citas()

        self.stdout.write(self.style.SUCCESS(
            f'Datos cargados: {Especialidad.objects.count()} especialidades, '
            f'{Medico.objects.count()} médicos, {Paciente.objects.count()} pacientes, '
            f'{len(citas)} citas, {Consulta.objects.count()} consultas.'
        ))
        self.stdout.write(f'Contraseña de todos los usuarios de demostración: {CLAVE_DEMO}')

    # ------------------------------------------------------------------
    def _limpiar(self):
        NotaConsulta.objects.all()._raw_delete(NotaConsulta.objects.db)   # registros inmutables
        Consulta.objects.all()._raw_delete(Consulta.objects.db)
        Cita.objects.all().delete()
        HorarioAtencion.objects.all().delete()
        Medico.objects.all().delete()
        Paciente.objects.all().delete()
        Especialidad.objects.all().delete()
        AccesoHistorial.objects.all().delete()
        Usuario.objects.filter(is_superuser=False).delete()
        RegistroCambio.objects.all().delete()

    def _usuario(self, username, nombres, apellidos, rol):
        usuario = Usuario(username=username, first_name=nombres, last_name=apellidos, rol=rol,
                          email=f'{username}@centromedico.local')
        usuario.set_password(CLAVE_DEMO)
        usuario.save()
        return usuario

    def _slot_libre(self, medico, fecha):
        horario = medico.horarios.filter(dia_semana=fecha.weekday()).first()
        if horario is None:
            return None
        actual = datetime.combine(fecha, horario.hora_inicio)
        limite = datetime.combine(fecha, horario.hora_fin) - timedelta(minutes=30)
        while actual <= limite:
            if (medico.pk, fecha, actual.time()) not in self.ocupados:
                return actual.time()
            actual += timedelta(minutes=60)  # deja huecos para agendar en la demo
        return None

    def _crear_cita(self, paciente, medico, fecha, indice, estado=None):
        if (paciente.pk, fecha) in self.paciente_dia:
            return None
        hora = self._slot_libre(medico, fecha)
        if hora is None:
            return None
        if estado is None:
            if fecha < self.hoy:
                estado = (Cita.Estado.NO_ASISTIO if indice % 6 == 0 else
                          Cita.Estado.CANCELADA if indice % 7 == 0 else Cita.Estado.COMPLETADA)
            else:
                estado = Cita.Estado.CANCELADA if indice % 9 == 0 else Cita.Estado.PROGRAMADA
        esp = self.medicos.index(medico)
        cita = Cita.objects.create(
            paciente=paciente, medico=medico, fecha=fecha, hora=hora, estado=estado,
            motivo=MOTIVOS[esp], creada_por=self.recepcion[indice % 2] if indice % 3 else paciente.usuario,
        )
        if estado == Cita.Estado.CANCELADA:
            cita.cancelada_por = paciente.usuario
            cita.motivo_cancelacion = 'Cruce con evaluación académica programada.'
            cita.fecha_cancelacion = timezone.now()
            cita.save()
        else:
            self.ocupados.add((medico.pk, fecha, hora))
            self.paciente_dia.add((paciente.pk, fecha))
        if estado == Cita.Estado.COMPLETADA:
            diag, trat, meds = DIAGNOSTICOS[esp]
            consulta = Consulta.objects.create(
                cita=cita, paciente=paciente, medico=medico, diagnostico=diag, tratamiento=trat,
                medicamentos=meds, adjuntos='Receta médica N.º %04d' % cita.pk if meds else '',
                proxima_cita_control=self.hoy + timedelta(days=30) if indice % 4 == 0 else None,
            )
            if indice % 5 == 0:
                NotaConsulta.objects.create(
                    consulta=consulta, autor=medico.usuario,
                    texto='Paciente reporta mejoría en control telefónico.',
                )
        return cita

    def _generar_citas(self):
        dias = [self.hoy + timedelta(days=d) for d in range(-24, 15)
                if (self.hoy + timedelta(days=d)).weekday() != 6]
        citas = []
        # Citas de hoy para que los médicos vean la alerta al iniciar sesión
        for i, medico in enumerate(self.medicos[:4]):
            for j in range(2):
                cita = self._crear_cita(self.pacientes[i * 2 + j], medico, self.hoy, 100 + i,
                                        estado=Cita.Estado.PROGRAMADA)
                if cita:
                    citas.append(cita)
        indice = 0
        while len(citas) < 44 and indice < 400:
            paciente = self.pacientes[indice % len(self.pacientes)]
            medico = self.medicos[(indice * 3) % len(self.medicos)]
            inicio = (indice * 7) % len(dias)
            for desplazamiento in range(len(dias)):
                fecha = dias[(inicio + desplazamiento) % len(dias)]
                if fecha == self.hoy:
                    continue
                cita = self._crear_cita(paciente, medico, fecha, indice)
                if cita:
                    citas.append(cita)
                    break
            indice += 1
        return citas
