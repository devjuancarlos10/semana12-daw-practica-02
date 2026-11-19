"""Pruebas de formularios, autorización, middleware y auditoría.

Ejecutar: python manage.py test
"""
import time
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import DevolucionForm, SolicitudPrestamoForm
from .middleware import CLAVE_ULTIMA_ACTIVIDAD
from .models import Equipo, Laboratorio, Prestamo, RegistroAuditoria, Usuario
from .roles import sincronizar_grupos
from .validators import sanitizar_texto

CLAVE = 'Prueba.Segura.2026'


class BaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_grupos()
        cls.hoy = timezone.localdate()
        cls.admin = cls.crear_usuario('admin1', Usuario.Rol.ADMIN)
        cls.docente = cls.crear_usuario('docente1', Usuario.Rol.DOCENTE)
        cls.otro_docente = cls.crear_usuario('docente2', Usuario.Rol.DOCENTE)
        cls.asistente = cls.crear_usuario('asist1', Usuario.Rol.ASISTENTE)
        cls.lab = Laboratorio.objects.create(codigo='LAB01', nombre='Redes', ubicacion='B-201')
        cls.equipo = Equipo.objects.create(
            codigo_patrimonial='LAP-000001', nombre='Laptop', marca='Lenovo', modelo='E14',
            categoria='laptop', laboratorio=cls.lab, cantidad_total=5,
        )
        cls.equipo_mant = Equipo.objects.create(
            codigo_patrimonial='PRY-000001', nombre='Proyector', marca='Epson', modelo='X49',
            categoria='proyector', laboratorio=cls.lab, estado=Equipo.Estado.MANTENIMIENTO,
        )

    @staticmethod
    def crear_usuario(username, rol):
        usuario = Usuario(username=username, rol=rol, first_name=username.title(),
                          email=f'{username}@test.local')
        usuario.set_password(CLAVE)
        usuario.save()
        return usuario

    def prestamo(self, docente=None, estado=Prestamo.Estado.ACTIVO, inicio=0, dias=5, cantidad=1):
        return Prestamo.objects.create(
            docente=docente or self.docente, equipo=self.equipo, cantidad=cantidad,
            fecha_prestamo=self.hoy + timedelta(days=inicio),
            fecha_devolucion_prevista=self.hoy + timedelta(days=inicio + dias), estado=estado,
        )

    def datos_form(self, **extra):
        datos = {
            'equipo': self.equipo.pk, 'cantidad': 1,
            'fecha_prestamo': self.hoy.isoformat(),
            'fecha_devolucion_prevista': (self.hoy + timedelta(days=3)).isoformat(),
            'observaciones': '',
        }
        datos.update(extra)
        return datos


class SolicitudPrestamoFormTest(BaseTest):
    def test_solicitud_valida(self):
        form = SolicitudPrestamoForm(self.datos_form(), usuario=self.docente)
        self.assertTrue(form.is_valid(), form.errors)
        prestamo = form.save()
        self.assertEqual(prestamo.docente, self.docente)
        self.assertEqual(prestamo.estado, Prestamo.Estado.PENDIENTE)

    def test_asistente_registra_prestamo_activo(self):
        form = SolicitudPrestamoForm(self.datos_form(docente=self.docente.pk), usuario=self.asistente)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().estado, Prestamo.Estado.ACTIVO)

    def test_devolucion_debe_ser_posterior(self):
        form = SolicitudPrestamoForm(
            self.datos_form(fecha_devolucion_prevista=self.hoy.isoformat()), usuario=self.docente,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('fecha_devolucion_prevista', form.errors)

    def test_equipo_debe_estar_operativo(self):
        form = SolicitudPrestamoForm(self.datos_form(equipo=self.equipo_mant.pk), usuario=self.docente)
        self.assertFalse(form.is_valid())
        self.assertIn('operativo', form.errors['equipo'][0])

    def test_maximo_tres_prestamos_vigentes(self):
        for _ in range(3):
            self.prestamo()
        form = SolicitudPrestamoForm(self.datos_form(), usuario=self.docente)
        self.assertFalse(form.is_valid())
        self.assertIn('máximo permitido es 3', form.non_field_errors()[0])

    def test_stock_insuficiente(self):
        self.prestamo(docente=self.otro_docente, cantidad=4)
        form = SolicitudPrestamoForm(self.datos_form(cantidad=2), usuario=self.docente)
        self.assertFalse(form.is_valid())
        self.assertIn('Stock insuficiente', form.errors['cantidad'][0])

    def test_observaciones_sanitizadas(self):
        form = SolicitudPrestamoForm(
            self.datos_form(observaciones='<script>alert(1)</script><b>Clase</b>   de redes'),
            usuario=self.docente,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['observaciones'], 'Clase de redes')


class DevolucionFormTest(BaseTest):
    def test_devolucion_con_dano_actualiza_equipo(self):
        prestamo = self.prestamo(inicio=-3)
        form = DevolucionForm({
            'fecha_devolucion': self.hoy.isoformat(), 'estado_equipo': 'mantenimiento',
            'observaciones': 'Pantalla con píxeles muertos',
        }, prestamo=prestamo)
        self.assertTrue(form.is_valid(), form.errors)
        form.guardar(self.asistente)
        self.equipo.refresh_from_db()
        self.assertEqual(self.equipo.estado, Equipo.Estado.MANTENIMIENTO)
        self.assertEqual(prestamo.estado, Prestamo.Estado.DEVUELTO)

    def test_estado_no_operativo_exige_observaciones(self):
        prestamo = self.prestamo(inicio=-3)
        form = DevolucionForm({
            'fecha_devolucion': self.hoy.isoformat(), 'estado_equipo': 'baja', 'observaciones': '',
        }, prestamo=prestamo)
        self.assertFalse(form.is_valid())
        self.assertIn('observaciones', form.errors)


class AutorizacionTest(BaseTest):
    def test_invitado_ve_catalogo(self):
        self.assertEqual(self.client.get(reverse('prestamos:catalogo')).status_code, 200)

    def test_invitado_redirigido_al_login(self):
        respuesta = self.client.get(reverse('prestamos:prestamo_lista'))
        self.assertRedirects(respuesta, f"{reverse('login')}?next={reverse('prestamos:prestamo_lista')}")
        self.assertTrue(RegistroAuditoria.objects.filter(
            accion=RegistroAuditoria.Accion.ACCESO_ANONIMO).exists())

    def test_solo_admin_elimina_equipos(self):
        url = reverse('prestamos:equipo_eliminar', args=[self.equipo_mant.pk])
        self.client.force_login(self.asistente)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(RegistroAuditoria.objects.filter(
            accion=RegistroAuditoria.Accion.ACCESO_DENEGADO, usuario=self.asistente).exists())
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.post(url), reverse('prestamos:catalogo'))
        self.assertFalse(Equipo.objects.filter(pk=self.equipo_mant.pk).exists())

    def test_solo_asistente_registra_devoluciones(self):
        prestamo = self.prestamo(inicio=-2)
        url = reverse('prestamos:prestamo_devolver', args=[prestamo.pk])
        for usuario in (self.docente, self.admin):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.asistente)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_docente_solo_ve_sus_prestamos(self):
        ajeno = self.prestamo(docente=self.otro_docente)
        self.client.force_login(self.docente)
        self.assertEqual(
            self.client.get(reverse('prestamos:prestamo_detalle', args=[ajeno.pk])).status_code, 403,
        )
        lista = self.client.get(reverse('prestamos:prestamo_lista'))
        self.assertNotContains(lista, f'#{ajeno.pk}<')

    def test_auditoria_solo_admin(self):
        self.client.force_login(self.asistente)
        self.assertEqual(self.client.get(reverse('prestamos:auditoria')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('prestamos:auditoria')).status_code, 200)


class MiddlewareTest(BaseTest):
    def test_login_registra_auditoria_y_alerta_vencidos(self):
        self.prestamo(inicio=-10, dias=3)  # vencido
        self.client.post(reverse('login'), {'username': 'docente1', 'password': CLAVE})
        respuesta = self.client.get(reverse('prestamos:dashboard'))
        mensajes = [str(m) for m in respuesta.context['messages']]
        self.assertTrue(any('vencido' in m for m in mensajes))
        self.assertTrue(RegistroAuditoria.objects.filter(
            accion=RegistroAuditoria.Accion.LOGIN, usuario=self.docente).exists())

    def test_login_fallido_se_registra(self):
        self.client.post(reverse('login'), {'username': 'docente1', 'password': 'incorrecta'})
        registro = RegistroAuditoria.objects.get(accion=RegistroAuditoria.Accion.LOGIN_FALLIDO)
        self.assertIn('docente1', registro.descripcion)
        self.assertEqual(registro.ip, '127.0.0.1')

    def test_prestamo_queda_auditado(self):
        self.client.force_login(self.docente)
        self.client.post(reverse('prestamos:prestamo_nuevo'), self.datos_form())
        registro = RegistroAuditoria.objects.get(accion=RegistroAuditoria.Accion.PRESTAMO_SOLICITADO)
        self.assertEqual(registro.usuario, self.docente)

    @override_settings(SESSION_TIMEOUT_INACTIVIDAD=60)
    def test_sesion_expira_por_inactividad(self):
        self.client.force_login(self.docente)
        sesion = self.client.session
        sesion[CLAVE_ULTIMA_ACTIVIDAD] = time.time() - 120
        sesion.save()
        respuesta = self.client.get(reverse('prestamos:dashboard'))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse('login'), respuesta['Location'])
        self.assertTrue(RegistroAuditoria.objects.filter(
            accion=RegistroAuditoria.Accion.SESION_EXPIRADA).exists())

    def test_cookie_de_sesion_httponly(self):
        respuesta = self.client.post(reverse('login'), {'username': 'docente1', 'password': CLAVE})
        cookie = respuesta.cookies['equipos_sessionid']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')


class ModeloTest(BaseTest):
    def test_dias_retraso(self):
        prestamo = self.prestamo(inicio=-10, dias=3)
        self.assertTrue(prestamo.esta_vencido)
        self.assertEqual(prestamo.dias_retraso, 7)

    def test_rol_asigna_grupo(self):
        self.assertTrue(self.docente.groups.filter(name='Docentes').exists())
        self.assertTrue(self.asistente.has_perm('prestamos.registrar_devolucion'))
        self.assertFalse(self.docente.has_perm('prestamos.registrar_devolucion'))

    def test_sanitizar_texto(self):
        self.assertEqual(sanitizar_texto('  hola\x00 <i>mundo</i>  '), 'hola mundo')
