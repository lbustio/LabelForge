# LabelForge - trabajo pendiente

Backlog operativo vigente. Las tareas terminadas y las decisiones historicas se
registran en `bitacora.md`; el estado necesario para retomar el proyecto vive en
`context.md`.

Ultima revision: 2026-07-18.

## Bloqueantes inmediatos

- [ ] Ejecutar el dataset de investigacion completo y conservar en `output/`,
  `logs/` y `checkpoints/` el CSV final, el log, las metricas de acuerdo y las
  notas metodologicas necesarias para el paper.
- [ ] Corregir y validar `labelforge-service.xml`. Actualmente referencia
  `C:\Users\lbust\miniconda3\envs\LabelForge\python.exe`, que no existe en la
  maquina verificada; no fijar otra ruta personal sin comprobar el entorno de
  destino.
- [ ] Configurar un `LABELFORGE_SECRET_KEY` estable y secreto para despliegues
  persistentes. La clave temporal generada al arrancar invalida sesiones y no es
  apropiada para produccion.

## Validacion antes de publicar

- [ ] Revisar el arbol de trabajo, separar cambios ajenos si los hubiera y hacer
  commit/push intencional de la reorganizacion y del refactor actual. El arbol
  esta deliberadamente sucio y los movimientos a `docs/` y `setup/` aparecen
  como eliminaciones mas archivos nuevos hasta que Git los registre.
- [ ] Verificar si la rama remota obsoleta `revision-de-proyecto-7044f` todavia
  existe y eliminarla solo si sus cambios ya estan integrados en `main`.
- [ ] Hacer una prueba manual en navegador de los flujos que dependen de tiempo
  real: descarga GGUF grande con progreso/cancelacion/reanudacion, **Detener
  ejecucion**, **Limpiar todo** y descubrimiento de modelos al cambiar de
  proveedor o credencial.
- [ ] Configurar HTTPS mediante proxy inverso o infraestructura equivalente
  antes de exponer la webapp fuera de localhost o de una red controlada.

## Investigacion y evolucion

- [ ] Evaluar el modo de justificaciones con datos reales: costo de tokens,
  utilidad para auditoria cualitativa e impacto sobre CSV y analisis.
- [ ] Implementar comparacion entre salidas historicas cuando exista un caso de
  uso definido (corridas, configuraciones y metricas que deben compararse).
- [ ] Si se incorpora OpenVINO/NPU, modelarlo como proveedor independiente con
  formatos y runtime compatibles. No introducirlo como fallback de GGUF ni bajo
  el identificador `llama_cpp`.
