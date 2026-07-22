# Requisitos no funcionales y puertas de calidad

Estado: aprobado para la reconstruccion
Fecha: 2026-07-21

## Requisitos no funcionales iniciales

Los valores numericos definitivos requieren decisiones de producto y pruebas de
carga. Mientras tanto rigen estos requisitos cualitativos.

### Correccion y recuperacion

- Reanudar nunca repite una evaluacion durable valida.
- Una escritura parcial no debe presentarse como run completado.
- Todo evento repetible debe ser idempotente por identidad estable.
- Un artefacto final debe poder vincularse con dataset, configuracion, arbitros
  y version del esquema que lo produjeron.
- Los casos de uso rechazan runs con menos de 3, mas de 15 o un numero par de
  arbitros, independientemente de la interfaz que los invoque.

### Seguridad

- Ningun secreto en configuracion versionable, DTO publico, log o artefacto.
- Autorizacion verificada en servidor para usuarios, runs y descargas.
- Uploads limitados por ruta, extension, contenido y tamano.
- Despliegue remoto requiere clave estable, HTTPS y politica de sesion acorde.

### Portabilidad

- Windows, Linux y macOS no dependen de rutas personales.
- La ausencia de CUDA o `llama_cpp` no impide importar dominio/aplicacion ni
  ejecutar pruebas que no usan inferencia local.
- La configuracion especifica de hardware se detecta por equipo y no se copia
  como verdad global.

### Rendimiento

- Datasets grandes no deben duplicarse innecesariamente en memoria.
- La web no mantiene locks mientras realiza IO lento.
- Descargas e inferencia informan progreso y admiten cancelacion coherente.
- Toda optimizacion importante incluye escenario, medicion y regresion.

### Operabilidad

- Logs estructurados con `run_id`, tipo de evento y componente.
- Errores publicos estables; detalles internos solo en observabilidad segura.
- Migraciones y versiones de esquema son explicitas.
- Estado de salud distingue web, persistencia y ejecutor.

## Puertas por cambio

Todo cambio de arquitectura debe pasar:

1. pruebas unitarias y de caracterizacion relevantes;
2. compilacion/importacion sin efectos no autorizados;
3. `git diff --check`;
4. ausencia de secretos y artefactos privados en el diff;
5. contratos o migraciones documentados cuando cambian datos;
6. prueba de interfaz cuando cambia un flujo visible;
7. revision de compatibilidad CLI, TUI y web cuando comparten el caso de uso.

## Toolchain que debe establecer la Fase 1

- `pyproject.toml` como configuracion central.
- Entorno reproducible desde dependencias directas y lock acordado.
- Runner unico de pruebas.
- Cobertura con umbrales diferenciados para dominio y adaptadores.
- Formato y lint automatizados.
- Type checking incremental estricto en codigo nuevo.
- Analisis de dependencias y ciclos.
- Analisis de seguridad de dependencias y secretos.
- Pruebas de navegador para flujos criticos.

No se fija aun una herramienta concreta en este documento; la seleccion se
registrara como decision de Fase 1 y debe funcionar en el entorno Conda usado.

## Criterio de salida de Fase 0

- Arquitectura actual y objetivo documentadas.
- Capacidades e invariantes inventariadas.
- Riesgos priorizados.
- Modo de despliegue decidido: instancia local unica por workspace.
- Autoridad de persistencia objetivo decidida: SQLite operacional, filesystem
  para objetos grandes y JSON validado para configuracion editable.
- Requisitos de escala cuantificados o declarados pendientes por el propietario.
- Envolvente operativa aprobada en `operational-envelope.md`.
- Secuencia de migracion aprobada.
- Arbol actual protegido mediante un checkpoint Git intencional antes de iniciar
  cambios estructurales de Fase 1.
