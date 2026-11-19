// Filtra la lista de médicos según la especialidad elegida (sin dependencias).
(function () {
  'use strict';
  var especialidad = document.getElementById('id_especialidad');
  var medico = document.getElementById('id_medico');
  if (!especialidad || !medico) return;

  var opciones = Array.prototype.slice.call(medico.options);

  function filtrar() {
    var valor = especialidad.value;
    var seleccionado = medico.value;
    medico.innerHTML = '';
    opciones.forEach(function (opcion) {
      if (!opcion.value || !valor || opcion.dataset.especialidad === valor) {
        medico.appendChild(opcion);
      }
    });
    var sigue = Array.prototype.some.call(medico.options, function (o) { return o.value === seleccionado; });
    medico.value = sigue ? seleccionado : '';
    medico.disabled = !valor;
  }

  especialidad.addEventListener('change', filtrar);
  filtrar();
})();
