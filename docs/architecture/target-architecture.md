# Arquitectura objetivo de LabelForge

Estado: aprobado para migracion incremental
Fecha: 2026-07-21

## Principios obligatorios

1. Dependencias hacia adentro: dominio y aplicacion no importan Flask, SDK de
   proveedores, SQLite ni rutas concretas.
2. Una regla tiene un solo propietario y una sola implementacion autoritativa.
3. Web, CLI y TUI ejecutan los mismos casos de uso.
4. Los efectos externos entran por puertos y se conectan en bootstrap.
5. Todo contrato durable o publico tiene tipo, version y validacion.
6. Los imports son libres de efectos operativos.
7. Compatibilidad legacy es temporal, observable y tiene criterio de retiro.
8. Rendimiento se optimiza con mediciones, no mediante constantes ocultas.
9. Seguridad y autorizacion se validan en el limite del caso de uso, no solo en
   componentes visuales.
10. Cada modulo cambia por un conjunto pequeno y cohesivo de razones.

## Estructura logica

```text
labelforge/
  domain/
    datasets/
    annotation/
    arbiters/
    consensus/
    runs/
    artifacts/
  application/
    commands/
    queries/
    services/
    dto/
    ports/
  infrastructure/
    persistence/
    providers/
    execution/
    datasets/
    models/
    security/
  interfaces/
    web/
    cli/
    tui/
  bootstrap/
    settings.py
    container.py
    webapp_factory.py
```

La estructura fisica puede introducirse incrementalmente. La regla importante
es la direccion de dependencias, no crear carpetas vacias.

## Dominios y contratos minimos

### Dataset y tarea

- `DatasetRef`
- `DatasetRevision`
- `DatasetSchema`
- `AnnotationTask`
- `PrincipleDefinition`

### Arbitros e inferencia

- `ArbiterDefinition`
- `ProviderDefinition`
- `ModelRef`
- `EvaluationRequest`
- `EvaluationResult`
- `Usage`
- `EvaluationFailure`

`RunPlan` validara entre 3 y 15 arbitros y exigira cardinalidad impar. Esta
invariante pertenece al dominio y se aplicara por igual a web, CLI y TUI.

### Ejecucion

- `Run`
- `RunPlan`
- `WorkItem`
- `RunEvent`
- `Checkpoint`
- `Incident`
- `RunSummary`

### Resultados

- `ConsensusResult`
- `AgreementReport`
- `Artifact`
- `ExportSchemaVersion`

## Casos de uso principales

- Configurar tarea.
- Registrar, validar, exportar e importar arbitros.
- Inspeccionar y seleccionar dataset.
- Preparar plan de ejecucion.
- Iniciar, detener y reanudar ejecucion.
- Evaluar un item con un arbitro.
- Consolidar evaluaciones.
- Consultar progreso y resultados.
- Exportar dataset etiquetado.
- Administrar modelos locales.
- Administrar usuarios y permisos.

## Puertos principales

- `RunRepository`
- `CheckpointRepository`
- `EvaluationRepository`
- `ArtifactRepository`
- `IncidentRepository`
- `ConfigurationRepository`
- `DatasetReader`
- `ArtifactWriter`
- `InferenceProvider`
- `RunExecutor`
- `EventPublisher`
- `SecretProvider`
- `Clock`

## Frontera del pipeline

El pipeline objetivo coordina interfaces, no detalles concretos:

```text
RunLabelingUseCase
  -> RunPlanner
  -> ExecutionStrategy
  -> ArbiterEvaluator -> InferenceProvider
  -> CheckpointRepository
  -> ConsensusService
  -> AgreementService
  -> ArtifactExporter
  -> EventPublisher
```

`row_major` y `arbiter_major` implementan `ExecutionStrategy`. Los proveedores
implementan `InferenceProvider`. El consenso no conoce SDK ni filesystem.

## Frontera de interfaces

Los controladores deben limitarse a:

1. autenticar y validar transporte;
2. construir un command/query DTO;
3. invocar el caso de uso;
4. transformar su respuesta al protocolo de salida.

Plantillas y JavaScript no deben recalcular ciclo de vida, permisos, progreso o
compatibilidad de reanudacion.

## Composicion

`bootstrap/container.py` seleccionara adaptadores y construira servicios.
`create_app(container, settings)` construira Flask sin migrar datos, crear
directorios, detectar CUDA ni cargar modelos durante el import.

CLI, TUI y web recibiran el mismo contenedor o subconjuntos de sus puertos.

## Estrategia incremental

Cada corte debe:

- preservar comportamiento mediante pruebas de caracterizacion;
- definir primero contrato y propietario;
- migrar un flujo completo de extremo a extremo;
- eliminar la implementacion anterior cuando ya no tenga consumidores;
- evitar wrappers permanentes que solo oculten globals;
- incluir criterio de retiro para adaptadores legacy.
