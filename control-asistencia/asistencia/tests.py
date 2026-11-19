"""Pruebas: validaciones, flujo de aprobación, permisos jerárquicos y middleware."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import CorreccionAsistenciaForm, EmpleadoForm, MarcacionForm, PermisoForm
from .models import Area, Asistencia, Empleado, Notificacion, Permiso, RegistroAuditoria, Usuario
from .roles import sincronizar_grupos

CLAVE = 'Prueba.Segura.2026'


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_grupos()
        cls.hoy = timezone.localdate()
        cls.sistemas = Area.objects.create(nombre='Sistemas', codigo='SIS')
        cls.ventas = Area.objects.create(nombre='Ventas', codigo='VEN')
        cls.rrhh = cls.empleado('rrhh', 'admin_rrhh', cls.sistemas, '10000001')
        cls.jefe = cls.empleado('jefe', 'jefe_area', cls.sistemas, '10000002')
        cls.emp = cls.empleado('emp', 'empleado', cls.sistemas, '10000003')
        cls.vendedor = cls.empleado('vend', 'empleado', cls.ventas, '10000004')
        cls.contador = cls.empleado('conta', 'contador', cls.ventas, '10000005')
        cls.sistemas.jefe = cls.jefe
        cls.sistemas.save()

    @staticmethod
    def empleado(username, rol, area, dni):
        u = Usuario(username=username, rol=rol, first_name=username.title())
        u.set_password(CLAVE)
        u.save()
        return Empleado.objects.create(
            usuario=u, dni=dni, nombres=username.title(), apellidos='Prueba', cargo='Analista',
            area=area, fecha_ingreso=date(2020, 1, 15), tipo_contrato='planilla',
            email_corporativo=f'{username}@tecnoandes.pe', salario=Decimal('3000'),
        )

    def permiso(self, empleado=None, inicio=5, dias=2, tipo='personal', estado='pendiente'):
        return Permiso.objects.create(
            empleado=empleado or self.emp, tipo=tipo, estado=estado,
            fecha_inicio=self.hoy + timedelta(days=inicio),
            fecha_fin=self.hoy + timedelta(days=inicio + dias - 1),
            motivo='Motivo suficientemente detallado para la prueba unitaria.',
        )

    def datos_permiso(self, **extra):
        d = {'tipo': 'personal', 'fecha_inicio': (self.hoy + timedelta(days=3)).isoformat(),
             'fecha_fin': (self.hoy + timedelta(days=4)).isoformat(),
             'motivo': 'Trámite notarial que requiere mi presencia en horario laboral.',
             'adjunto_descripcion': ''}
        d.update(extra)
        return d


class EmpleadoFormTest(Base):
    def datos(self, **extra):
        d = {'dni': '20000001', 'nombres': 'ana', 'apellidos': 'ruiz', 'cargo': 'Analista',
             'area': self.sistemas.pk, 'fecha_ingreso': '2022-01-10', 'tipo_contrato': 'planilla',
             'email_corporativo': 'aruiz@tecnoandes.pe', 'salario': '3500',
             'hora_entrada': '08:00', 'hora_salida': '17:00', 'rol': 'empleado'}
        d.update(extra)
        return d

    def test_registro_valido_crea_usuario(self):
        form = EmpleadoForm(self.datos())
        self.assertTrue(form.is_valid(), form.errors)
        empleado, clave = form.save()
        self.assertEqual(empleado.usuario.username, 'aruiz')
        self.assertTrue(empleado.usuario.check_password(clave))

    def test_validaciones(self):
        self.assertIn('dni', EmpleadoForm(self.datos(dni=self.emp.dni)).errors)            # DNI único
        self.assertIn('email_corporativo', EmpleadoForm(self.datos(email_corporativo='a@gmail.com')).errors)
        futura = (self.hoy + timedelta(days=1)).isoformat()
        self.assertIn('fecha_ingreso', EmpleadoForm(self.datos(fecha_ingreso=futura)).errors)
        self.assertIn('cargo', EmpleadoForm(self.datos(cargo='   ')).errors)

    def test_antiguedad(self):
        anios, meses = self.emp.antiguedad
        self.assertGreaterEqual(anios, 6)


class MarcacionTest(Base):
    def test_entrada_y_salida_calculan_horas(self):
        form = MarcacionForm({'tipo': 'entrada'}, empleado=self.emp)
        self.assertTrue(form.is_valid())
        registro = form.guardar()
        registro.hora_entrada -= timedelta(hours=9, minutes=30)
        registro.save()
        form = MarcacionForm({'tipo': 'salida'}, empleado=self.emp)
        self.assertTrue(form.is_valid(), form.errors)
        registro = form.guardar()
        self.assertEqual(registro.horas_trabajadas, Decimal('9.50'))
        self.assertEqual(registro.horas_extra, Decimal('1.50'))

    def test_no_entrada_duplicada_sin_salida(self):
        Asistencia.objects.create(empleado=self.emp, fecha=self.hoy, hora_entrada=timezone.now())
        form = MarcacionForm({'tipo': 'entrada'}, empleado=self.emp)
        self.assertFalse(form.is_valid())
        self.assertIn('aún no marca la salida', form.non_field_errors()[0])

    def test_salida_sin_entrada(self):
        form = MarcacionForm({'tipo': 'salida'}, empleado=self.emp)
        self.assertFalse(form.is_valid())

    def test_correccion_requiere_justificacion_50(self):
        a = Asistencia.objects.create(empleado=self.emp, fecha=self.hoy, hora_entrada=timezone.now())
        form = CorreccionAsistenciaForm({'hora_entrada': '08:00', 'hora_salida': '17:00',
                                         'motivo': 'olvido', 'justificacion': 'Muy corta'}, asistencia=a)
        self.assertFalse(form.is_valid())
        self.assertIn('justificacion', form.errors)
        form = CorreccionAsistenciaForm({
            'hora_entrada': '08:00', 'hora_salida': '17:30', 'motivo': 'olvido',
            'justificacion': 'El empleado olvidó marcar su salida; el jefe de área confirmó la hora real.'},
            asistencia=a)
        self.assertTrue(form.is_valid(), form.errors)
        form.guardar(self.rrhh.usuario)
        a.refresh_from_db()
        self.assertTrue(a.corregida)
        self.assertEqual(a.horas_trabajadas, Decimal('9.50'))


class PermisoFormTest(Base):
    def test_valido(self):
        form = PermisoForm(self.datos_permiso(), empleado=self.emp)
        self.assertTrue(form.is_valid(), form.errors)

    def test_fin_antes_de_inicio(self):
        form = PermisoForm(self.datos_permiso(fecha_fin=self.hoy.isoformat()), empleado=self.emp)
        self.assertIn('fecha_fin', form.errors)

    def test_motivo_minimo_30(self):
        self.assertIn('motivo', PermisoForm(self.datos_permiso(motivo='Corto'), empleado=self.emp).errors)

    def test_permisos_superpuestos(self):
        self.permiso(inicio=4, dias=3)
        form = PermisoForm(self.datos_permiso(), empleado=self.emp)
        self.assertFalse(form.is_valid())
        self.assertIn('superpone', form.non_field_errors()[0])

    def test_vacaciones_disponibles(self):
        inicio = date(self.hoy.year, 1, 2)
        Permiso.objects.create(empleado=self.emp, tipo='vacaciones', estado='aprobado',
                               fecha_inicio=inicio, fecha_fin=inicio + timedelta(days=24),
                               motivo='Vacaciones aprobadas al inicio del año calendario.')
        self.assertEqual(self.emp.dias_vacaciones_disponibles, 5)
        form = PermisoForm(self.datos_permiso(
            tipo='vacaciones', fecha_inicio=(self.hoy + timedelta(days=2)).isoformat(),
            fecha_fin=(self.hoy + timedelta(days=9)).isoformat()), empleado=self.emp)
        if (self.hoy + timedelta(days=2)).year == self.hoy.year:
            self.assertFalse(form.is_valid())
            self.assertIn('solo tiene 5 disponibles', form.errors['fecha_fin'][0])


class FlujoAprobacionTest(Base):
    def test_jefe_aprueba_permiso_de_su_area_y_notifica(self):
        p = self.permiso()
        self.client.force_login(self.jefe.usuario)
        r = self.client.post(reverse('asistencia:permiso_detalle', args=[p.pk]),
                             {'decision': 'aprobar', 'comentario': ''})
        self.assertEqual(r.status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.estado, p.revisado_por), ('aprobado', self.jefe.usuario))
        self.assertTrue(Notificacion.objects.filter(usuario=self.emp.usuario, permiso=p).exists())

    def test_jefe_no_aprueba_otra_area(self):
        p = self.permiso(empleado=self.vendedor)
        self.client.force_login(self.jefe.usuario)
        r = self.client.post(reverse('asistencia:permiso_detalle', args=[p.pk]), {'decision': 'aprobar'})
        self.assertEqual(r.status_code, 403)

    def test_jefe_no_aprueba_su_propio_permiso(self):
        p = self.permiso(empleado=self.jefe)
        self.assertFalse(p.puede_ser_revisado_por(self.jefe.usuario))
        self.assertTrue(p.puede_ser_revisado_por(self.rrhh.usuario))

    def test_rrhh_aprueba_cualquiera(self):
        p = self.permiso(empleado=self.vendedor)
        self.assertTrue(p.puede_ser_revisado_por(self.rrhh.usuario))

    def test_rechazo_requiere_comentario(self):
        p = self.permiso()
        self.client.force_login(self.rrhh.usuario)
        self.client.post(reverse('asistencia:permiso_detalle', args=[p.pk]), {'decision': 'rechazar'})
        p.refresh_from_db()
        self.assertEqual(p.estado, 'pendiente')


class AutorizacionTest(Base):
    def test_permisos_por_rol(self):
        self.assertTrue(self.emp.usuario.has_perm('asistencia.registrar_asistencia'))
        self.assertFalse(self.emp.usuario.has_perm('asistencia.aprobar_permiso'))
        self.assertTrue(self.jefe.usuario.has_perm('asistencia.ver_reportes_area'))
        self.assertFalse(self.jefe.usuario.has_perm('asistencia.ver_reportes_globales'))
        self.assertTrue(self.contador.usuario.has_perm('asistencia.ver_reportes_globales'))
        self.assertFalse(self.contador.usuario.has_perm('asistencia.corregir_asistencia'))
        self.assertTrue(self.rrhh.usuario.has_perm('asistencia.corregir_asistencia'))

    def test_empleado_no_accede_a_reportes(self):
        self.client.force_login(self.emp.usuario)
        self.assertEqual(self.client.get(reverse('asistencia:reportes')).status_code, 403)
        self.assertTrue(RegistroAuditoria.objects.filter(accion='seguridad').exists())

    def test_jefe_solo_ve_su_area(self):
        self.client.force_login(self.jefe.usuario)
        r = self.client.get(reverse('asistencia:empleado_lista'))
        self.assertContains(r, self.emp.dni)
        self.assertNotContains(r, self.vendedor.dni)

    def test_salario_solo_rrhh(self):
        self.client.force_login(self.contador.usuario)
        self.assertNotContains(self.client.get(reverse('asistencia:empleado_lista')), 'S/ 3000')
        self.client.force_login(self.rrhh.usuario)
        self.assertContains(self.client.get(reverse('asistencia:empleado_lista')), 'S/ 3000')

    def test_correccion_solo_rrhh(self):
        a = Asistencia.objects.create(empleado=self.emp, fecha=self.hoy, hora_entrada=timezone.now())
        url = reverse('asistencia:asistencia_corregir', args=[a.pk])
        self.client.force_login(self.jefe.usuario)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.rrhh.usuario)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_empleado_no_modifica_registro_despues_de_24h(self):
        a = Asistencia.objects.create(empleado=self.emp, fecha=self.hoy - timedelta(days=2),
                                      hora_entrada=timezone.now() - timedelta(days=2))
        Asistencia.objects.filter(pk=a.pk).update(creado=timezone.now() - timedelta(hours=30))
        self.client.force_login(self.emp.usuario)
        r = self.client.get(reverse('asistencia:asistencia_observacion', args=[a.pk]))
        self.assertEqual(r.status_code, 403)

    def test_exportacion_texto(self):
        self.client.force_login(self.contador.usuario)
        r = self.client.get(reverse('asistencia:exportar_reporte'))
        self.assertEqual(r['Content-Type'], 'text/plain; charset=utf-8')
        self.assertIn('REPORTE DE ASISTENCIA', r.content.decode())


class MiddlewareTest(Base):
    def test_bloqueo_cuenta_desactivada(self):
        self.client.force_login(self.emp.usuario)
        Empleado.objects.filter(pk=self.emp.pk).update(activo=False)
        r = self.client.get(reverse('asistencia:dashboard'))
        self.assertRedirects(r, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_rechaza_cuenta_desactivada(self):
        Empleado.objects.filter(pk=self.emp.pk).update(activo=False)
        r = self.client.post(reverse('login'), {'username': 'emp', 'password': CLAVE})
        self.assertContains(r, 'desactivada')

    def test_recordatorio_de_marcacion(self):
        self.client.post(reverse('login'), {'username': 'emp', 'password': CLAVE})
        r = self.client.get(reverse('asistencia:dashboard'))
        mensajes = [str(m) for m in r.context['messages']]
        if self.hoy.weekday() < 5:
            self.assertTrue(any('Aún no registra su entrada' in m for m in mensajes))

    @override_settings(VALIDAR_HORARIO_MARCACION=True)
    def test_horario_de_marcacion_bloquea_fuera_de_rango(self):
        self.client.force_login(self.emp.usuario)  # entrada programada 08:00
        madrugada = timezone.make_aware(datetime.combine(self.hoy, time(3, 0)))
        with mock.patch('asistencia.middleware.timezone.localtime', return_value=madrugada):
            r = self.client.post(reverse('asistencia:marcar'), {'tipo': 'entrada'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Asistencia.objects.filter(empleado=self.emp).exists())

    @override_settings(VALIDAR_HORARIO_MARCACION=True)
    def test_horario_de_marcacion_permite_dentro_de_rango(self):
        self.client.force_login(self.emp.usuario)
        a_tiempo = timezone.make_aware(datetime.combine(self.hoy, time(9, 30)))
        with mock.patch('asistencia.middleware.timezone.localtime', return_value=a_tiempo):
            self.client.post(reverse('asistencia:marcar'), {'tipo': 'entrada'})
        self.assertTrue(Asistencia.objects.filter(empleado=self.emp).exists())

    def test_auditoria_registra_modificaciones(self):
        p = self.permiso()
        self.client.force_login(self.rrhh.usuario)
        self.client.post(reverse('asistencia:permiso_detalle', args=[p.pk]), {'decision': 'aprobar'})
        registro = RegistroAuditoria.objects.filter(accion='modificacion', modelo='Permiso').latest('fecha')
        self.assertEqual(registro.usuario, self.rrhh.usuario)
        self.assertEqual(registro.cambios['estado'], ['pendiente', 'aprobado'])

    @override_settings(SESSION_TIMEOUT_INACTIVIDAD=60)
    def test_timeout(self):
        self.client.force_login(self.emp.usuario)
        with mock.patch('asistencia.middleware.time.time', return_value=1_000_000):
            self.client.get(reverse('asistencia:mi_sesion'))
        with mock.patch('asistencia.middleware.time.time', return_value=1_000_100):
            r = self.client.get(reverse('asistencia:mi_sesion'))
        self.assertEqual(r.status_code, 302)

    def test_cookie(self):
        r = self.client.post(reverse('login'), {'username': 'emp', 'password': CLAVE})
        cookie = r.cookies['asistencia_sessionid']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(int(cookie['max-age']), 45 * 60)
