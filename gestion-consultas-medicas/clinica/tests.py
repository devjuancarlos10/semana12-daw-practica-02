"""Pruebas: validaciones complejas, permisos granulares, middleware y auditoría."""
from datetime import date, time, timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import CitaForm, PacienteForm
from .horarios import validar_horario_centro
from .models import (
    AccesoHistorial,
    Cita,
    Consulta,
    Especialidad,
    HorarioAtencion,
    Medico,
    Paciente,
    RegistroCambio,
    Usuario,
)
from .roles import sincronizar_grupos

CLAVE = 'Prueba.Segura.2026'


def proximo_dia_habil(desde, weekday=0):
    """Próximo día con el weekday indicado (0 = lunes), estrictamente futuro."""
    dias = (weekday - desde.weekday()) % 7 or 7
    return desde + timedelta(days=dias)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_grupos()
        cls.hoy = timezone.localdate()
        cls.lunes = proximo_dia_habil(cls.hoy, 0)
        cls.recepcion = cls.usuario('recep', Usuario.Rol.RECEPCIONISTA)
        cls.admin = cls.usuario('director', Usuario.Rol.ADMIN)
        cls.esp = Especialidad.objects.create(nombre='Medicina General')
        cls.otra_esp = Especialidad.objects.create(nombre='Odontología')
        cls.medico = Medico.objects.create(usuario=cls.usuario('drhouse', Usuario.Rol.MEDICO),
                                           cmp='12345', especialidad=cls.esp, consultorio='C-1')
        cls.otro_medico = Medico.objects.create(usuario=cls.usuario('drwho', Usuario.Rol.MEDICO),
                                                cmp='54321', especialidad=cls.otra_esp, consultorio='C-2')
        for m in (cls.medico, cls.otro_medico):
            for d in range(6):
                HorarioAtencion.objects.create(medico=m, dia_semana=d, hora_inicio=time(8),
                                               hora_fin=time(13))
        cls.paciente = cls.crear_paciente('70000001', 'pac1')
        cls.otro_paciente = cls.crear_paciente('70000002', 'pac2')

    @staticmethod
    def usuario(username, rol):
        u = Usuario(username=username, rol=rol, first_name=username.title())
        u.set_password(CLAVE)
        u.save()
        return u

    @classmethod
    def crear_paciente(cls, dni, username):
        return Paciente.objects.create(
            usuario=cls.usuario(username, Usuario.Rol.PACIENTE), dni=dni, nombres='Ana',
            apellidos='Pérez', fecha_nacimiento=date(2000, 1, 1), tipo='estudiante',
            codigo_institucional=f'COD{dni}', email=f'{username}@institucion.edu.pe',
        )

    def datos_cita(self, **extra):
        datos = {
            'paciente': self.paciente.pk, 'especialidad': self.esp.pk, 'medico': self.medico.pk,
            'fecha': self.lunes.isoformat(), 'hora': '09:00',
            'motivo': 'Dolor de cabeza intenso desde hace dos días',
        }
        datos.update(extra)
        return datos

    def cita(self, paciente=None, medico=None, fecha=None, hora=time(9), estado=Cita.Estado.PROGRAMADA):
        return Cita.objects.create(
            paciente=paciente or self.paciente, medico=medico or self.medico,
            fecha=fecha or self.lunes, hora=hora, estado=estado,
            motivo='Motivo de prueba suficientemente largo',
        )


class CitaFormTest(Base):
    def test_cita_valida(self):
        form = CitaForm(self.datos_cita(), usuario=self.recepcion)
        self.assertTrue(form.is_valid(), form.errors)

    def test_motivo_minimo_20_caracteres(self):
        form = CitaForm(self.datos_cita(motivo='Dolor'), usuario=self.recepcion)
        self.assertFalse(form.is_valid())
        self.assertIn('motivo', form.errors)

    def test_medico_debe_pertenecer_a_especialidad(self):
        form = CitaForm(self.datos_cita(medico=self.otro_medico.pk), usuario=self.recepcion)
        self.assertFalse(form.is_valid())
        self.assertIn('especialidad', form.errors['medico'][0])

    def test_slot_ocupado(self):
        self.cita(paciente=self.otro_paciente)
        form = CitaForm(self.datos_cita(), usuario=self.recepcion)
        self.assertFalse(form.is_valid())
        self.assertIn('ocupado', form.errors['hora'][0])

    def test_paciente_no_puede_tener_dos_citas_el_mismo_dia(self):
        self.cita(medico=self.otro_medico, hora=time(11))
        form = CitaForm(self.datos_cita(), usuario=self.recepcion)
        self.assertFalse(form.is_valid())
        self.assertIn('ya tiene una cita', form.non_field_errors()[0])

    def test_fuera_de_horario_del_centro(self):
        domingo = proximo_dia_habil(self.hoy, 6)
        form = CitaForm(self.datos_cita(fecha=domingo.isoformat()), usuario=self.recepcion)
        self.assertFalse(form.is_valid())
        self.assertIn('no atiende', form.errors['hora'][0])

    def test_sabado_despues_de_las_13(self):
        self.assertIsNotNone(validar_horario_centro(proximo_dia_habil(self.hoy, 5), time(13)))
        self.assertIsNone(validar_horario_centro(proximo_dia_habil(self.hoy, 5), time(12, 30)))

    def test_fuera_del_horario_del_medico(self):
        form = CitaForm(self.datos_cita(hora='15:00'), usuario=self.recepcion)
        self.assertFalse(form.is_valid())
        self.assertIn('no atiende en ese horario', form.errors['hora'][0])

    def test_paciente_agenda_para_si_mismo(self):
        datos = self.datos_cita()
        datos.pop('paciente')
        form = CitaForm(datos, usuario=self.paciente.usuario)
        self.assertNotIn('paciente', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().paciente, self.paciente)


class PacienteFormTest(Base):
    def datos(self, **extra):
        d = {'dni': '12345678', 'nombres': '  maría  ', 'apellidos': 'LÓPEZ díaz',
             'fecha_nacimiento': '2001-05-05', 'tipo': 'estudiante', 'codigo_institucional': 'abc12345',
             'email': 'maria@institucion.edu.pe', 'telefono': '', 'alergias': '<b>Polen</b>'}
        d.update(extra)
        return d

    def test_valido_y_normalizado(self):
        form = PacienteForm(self.datos())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['nombres'], 'María')
        self.assertEqual(form.cleaned_data['alergias'], 'Polen')

    def test_dni_ocho_digitos(self):
        self.assertIn('dni', PacienteForm(self.datos(dni='1234A678')).errors)

    def test_fecha_nacimiento_futura(self):
        futura = (self.hoy + timedelta(days=1)).isoformat()
        self.assertIn('fecha_nacimiento', PacienteForm(self.datos(fecha_nacimiento=futura)).errors)

    def test_estudiante_requiere_correo_institucional(self):
        self.assertIn('email', PacienteForm(self.datos(email='maria@gmail.com')).errors)
        self.assertTrue(PacienteForm(self.datos(email='maria@gmail.com', tipo='docente')).is_valid())


class PermisosTest(Base):
    def test_grupos_y_permisos(self):
        self.assertTrue(self.paciente.usuario.has_perm('clinica.agendar_cita'))
        self.assertFalse(self.medico.usuario.has_perm('clinica.agendar_cita'))
        self.assertTrue(self.medico.usuario.has_perm('clinica.cancelar_cita'))
        self.assertTrue(self.admin.has_perm('clinica.generar_reportes'))
        self.assertFalse(self.recepcion.has_perm('clinica.generar_reportes'))
        self.assertFalse(self.admin.has_perm('clinica.ver_historial_clinico'))

    def test_paciente_no_ve_historial_ajeno(self):
        self.client.force_login(self.paciente.usuario)
        url = reverse('clinica:historial_paciente', args=[self.otro_paciente.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        acceso = AccesoHistorial.objects.get()
        self.assertEqual(acceso.resultado, 'denegado')
        self.assertEqual(acceso.paciente, self.otro_paciente)

    def test_recepcion_no_ve_historial(self):
        self.client.force_login(self.recepcion)
        url = reverse('clinica:historial_paciente', args=[self.paciente.pk])
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_medico_ve_historial_y_queda_auditado(self):
        self.client.force_login(self.medico.usuario)
        url = reverse('clinica:historial_paciente', args=[self.paciente.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        acceso = AccesoHistorial.objects.get()
        self.assertEqual((acceso.username, acceso.resultado), ('drhouse', 'permitido'))

    def test_reportes_solo_admin(self):
        self.client.force_login(self.recepcion)
        self.assertEqual(self.client.get(reverse('clinica:reportes')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('clinica:reportes')).status_code, 200)

    def test_paciente_cancela_solo_su_cita(self):
        ajena = self.cita(paciente=self.otro_paciente)
        self.client.force_login(self.paciente.usuario)
        url = reverse('clinica:cita_cancelar', args=[ajena.pk])
        self.assertEqual(self.client.post(url, {'motivo': 'No podré asistir a la cita'}).status_code, 403)
        propia = self.cita(medico=self.otro_medico, fecha=self.lunes + timedelta(days=1))
        self.client.post(reverse('clinica:cita_cancelar', args=[propia.pk]),
                         {'motivo': 'No podré asistir a la cita'})
        propia.refresh_from_db()
        self.assertEqual(propia.estado, Cita.Estado.CANCELADA)

    def test_login_diferenciado(self):
        r = self.client.post(reverse('login'), {
            'username': 'drhouse', 'password': CLAVE, 'tipo_usuario': 'paciente'})
        self.assertContains(r, 'no corresponden al tipo de usuario')
        r = self.client.post(reverse('login'), {
            'username': 'drhouse', 'password': CLAVE, 'tipo_usuario': 'medico'})
        self.assertRedirects(r, reverse('clinica:dashboard'), fetch_redirect_response=False)


class ConsultaTest(Base):
    def test_consulta_inmutable(self):
        cita = self.cita(fecha=self.hoy - timedelta(days=1), estado=Cita.Estado.COMPLETADA)
        consulta = Consulta.objects.create(cita=cita, paciente=self.paciente, medico=self.medico,
                                           diagnostico='Diagnóstico inicial', tratamiento='Reposo')
        consulta.diagnostico = 'Cambiado'
        with self.assertRaises(PermissionDenied):
            consulta.save()
        with self.assertRaises(PermissionDenied):
            consulta.delete()

    def test_registrar_consulta_completa_la_cita(self):
        cita = Cita.objects.create(paciente=self.paciente, medico=self.medico, fecha=self.hoy,
                                   hora=time(8), motivo='Control general de salud anual')
        self.client.force_login(self.medico.usuario)
        r = self.client.post(reverse('clinica:consulta_registrar', args=[cita.pk]), {
            'diagnostico': 'Paciente sano sin hallazgos', 'tratamiento': 'Mantener hábitos saludables',
            'medicamentos': '', 'adjuntos': '', 'proxima_cita_control': ''})
        self.assertEqual(r.status_code, 302)
        cita.refresh_from_db()
        self.assertEqual(cita.estado, Cita.Estado.COMPLETADA)


class MiddlewareTest(Base):
    def test_middleware_bloquea_fuera_de_horario(self):
        self.client.force_login(self.recepcion)
        domingo = proximo_dia_habil(self.hoy, 6)
        url = reverse('clinica:cita_nueva')
        r = self.client.post(url, self.datos_cita(fecha=domingo.isoformat()))
        self.assertRedirects(r, url, fetch_redirect_response=False)
        self.assertFalse(Cita.objects.exists())
        # El borrador se conserva para el siguiente GET
        r = self.client.get(url)
        self.assertEqual(r.context['form'].initial.get('motivo'), self.datos_cita()['motivo'])

    def test_alerta_citas_pendientes_al_medico(self):
        Cita.objects.create(paciente=self.paciente, medico=self.medico, fecha=self.hoy,
                            hora=time(12, 30), motivo='Control general de salud anual')
        self.client.post(reverse('login'), {'username': 'drhouse', 'password': CLAVE, 'tipo_usuario': 'medico'})
        r = self.client.get(reverse('clinica:dashboard'))
        self.assertTrue(any('pendiente' in str(m) for m in r.context['messages']))

    def test_trazabilidad_de_cambios(self):
        self.client.force_login(self.recepcion)
        self.client.post(reverse('clinica:paciente_editar', args=[self.paciente.pk]), {
            'dni': self.paciente.dni, 'nombres': 'Ana', 'apellidos': 'Pérez Soto',
            'fecha_nacimiento': '2000-01-01', 'tipo': 'estudiante',
            'codigo_institucional': self.paciente.codigo_institucional,
            'email': self.paciente.email, 'telefono': '', 'alergias': 'Polen'})
        registro = RegistroCambio.objects.filter(accion='modificacion', modelo='Paciente').latest('fecha')
        self.assertEqual(registro.usuario, self.recepcion)
        self.assertEqual(registro.cambios['apellidos'], ['Pérez', 'Pérez Soto'])
        self.assertEqual(registro.cambios['alergias'], '(modificado)')  # dato sensible

    @override_settings(SESSION_TIMEOUT_INACTIVIDAD=60)
    def test_timeout_de_sesion(self):
        self.client.force_login(self.medico.usuario)
        with mock.patch('clinica.middleware.time.time', return_value=1_000_000):
            self.client.get(reverse('clinica:mi_sesion'))
        with mock.patch('clinica.middleware.time.time', return_value=1_000_000 + 120):
            r = self.client.get(reverse('clinica:mi_sesion'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse('login'), r['Location'])

    def test_cookie_sesion(self):
        r = self.client.post(reverse('login'), {'username': 'recep', 'password': CLAVE,
                                                'tipo_usuario': 'recepcionista'})
        cookie = r.cookies['clinica_sessionid']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Strict')
        self.assertEqual(int(cookie['max-age']), 20 * 60)
