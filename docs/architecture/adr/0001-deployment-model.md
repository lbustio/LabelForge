# ADR-0001: modelo de despliegue y supervision

Estado: aceptado
Fecha: 2026-07-21

## Contexto

LabelForge combina una web con trabajos largos de inferencia, modelos GGUF que
consumen recursos exclusivos, proveedores remotos y estado reanudable. El
supervisor actual es thread-safe dentro de un proceso, pero no coordina varios
procesos o nodos.

## Alternativa A: producto local de una sola instancia

- Una instancia web por workspace.
- Un run activo por workspace.
- Executor local mediante subprocess.
- Filesystem local como parte valida de la infraestructura.
- Autenticacion util para control local o red controlada, no tenancy fuerte.

Ventajas: menor complejidad, control directo de GPU y despliegue sencillo.

Limitaciones: no escala horizontalmente y la alta disponibilidad no es un
objetivo.

## Alternativa B: servicio multiusuario con workers

- API separada de uno o mas workers.
- Cola durable y leases de ejecucion.
- Supervisión y cancelacion persistentes.
- Persistencia compartida con transacciones.
- Aislamiento por usuario/proyecto.
- Artefactos en almacenamiento compartido.

Ventajas: concurrencia, escalado y operacion centralizada.

Costos: complejidad considerable en scheduling de GPU, seguridad, despliegue y
recuperacion distribuida.

## Decision

Se adopta la alternativa A: LabelForge es un producto local de una sola
instancia por workspace.

La arquitectura debe asumir:

- una instancia de aplicacion por workspace;
- un run activo a la vez por workspace;
- ejecucion local mediante un proceso separado;
- acceso exclusivo coordinado a modelos/GPU durante un run;
- filesystem local como infraestructura valida para datasets, modelos, logs y
  artefactos;
- usuarios locales para autorizacion y trazabilidad, no tenancy distribuida;
- reinicio y recuperacion sobre estado durable local.

Se conservaran puertos para executor, repositorios y artefactos. Esto permite
cambiar adaptadores en el futuro sin introducir ahora una cola distribuida.

## Consecuencias

### Positivas

- Menor superficie operacional y de fallo.
- Control determinista de GPU, modelos y subprocess.
- Instalacion portable sin servicios externos obligatorios.
- Persistencia y backups comprensibles para el propietario del workspace.
- Las prioridades se concentran en correccion, recuperacion y usabilidad.

### Restricciones aceptadas

- No se soporta escalado horizontal.
- No se soportan varios runs simultaneos en el mismo workspace.
- Un proxy con varios procesos web no puede iniciar supervisores independientes.
- La alta disponibilidad no es un objetivo del producto local.
- Exponer la aplicacion fuera de una maquina o red controlada requiere medidas
  adicionales y no cambia por si mismo el modelo a servicio multiusuario.

## Requisitos derivados

- El arranque debe impedir dos instancias activas sobre el mismo workspace o,
  como minimo, detectar y rechazar una segunda instancia de forma segura.
- El run activo debe recuperar estado despues de reiniciar la web.
- La identidad del proceso y las solicitudes de detencion deben tener una
  representacion durable suficiente para diagnostico y recuperacion.
- Operaciones destructivas deben usar paths resueltos dentro del workspace.
- El backup debe poder cubrir configuracion, base local, manifests y artefactos
  sin incluir secretos accidentalmente.
- CUDA y modelos locales solo se inicializan cuando un caso de uso los requiere.

## Alternativa descartada por ahora

El servicio central con workers no forma parte del roadmap actual. Reabrir este
ADR requeriria nuevos requisitos de concurrencia, tenancy, cola durable,
almacenamiento compartido y scheduling de GPU.
