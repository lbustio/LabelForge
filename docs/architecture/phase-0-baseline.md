# LabelForge: linea base arquitectonica de Fase 0

Estado: aprobado
Fecha: 2026-07-21
Alcance: producto LabelForge completo, no solamente la interfaz Ejecutar
Modelo de despliegue aprobado: instancia local unica por workspace
Persistencia aprobada: SQLite operacional + filesystem para objetos grandes +
JSON validado para configuracion editable

## Proposito del producto

LabelForge prepara datasets de texto, coordina evaluaciones de multiples
arbitros LLM locales o remotos, consolida sus votos, calcula acuerdo, conserva
la trazabilidad de la ejecucion y entrega artefactos etiquetados auditables.

Las interfaces web, CLI y TUI son clientes del mismo producto. Ninguna de ellas
debe definir reglas distintas de etiquetado, reanudacion, consenso o exportacion.

## Capacidades actuales

| Capacidad | Implementacion principal actual | Estado arquitectonico |
| --- | --- | --- |
| Configuracion de tarea y principios | `config/arbiters_config.json`, web y pipeline | Compartida mediante diccionarios; sin esquema versionado |
| Ingreso e inspeccion de datasets | `dataset_io.py`, `webapp/app.py` | Validacion util, pero caso de uso acoplado a rutas y Flask |
| Arbitros y secretos | `arbiters_io.py`, `secrets_io.py`, web | Responsabilidades repartidas; proveedor representado por diccionarios |
| Inferencia local y remota | `label_persuasion.py`, `local_models.py` | Alto acoplamiento entre SDK, configuracion y pipeline |
| Planificacion del etiquetado | `label_persuasion.py` | `row_major` y `arbiter_major` correctos, pero dentro del modulo monolitico |
| Checkpoints y reanudacion | `checkpoint_io.py`, `run_contract.py`, pipeline | Funcional y probado; persistencia fragmentada |
| Consenso y acuerdo | `label_persuasion.py`, `agreement_metrics.py` | Calculo reutilizable parcial; contratos aun no tipados |
| Reportes y exportacion | `run_reporting.py`, pipeline | Funcional; esquema final no es contrato de dominio versionado |
| Ejecucion y control | Flask, subprocess, supervisor local | Vertical parcialmente separada en domain/application/infrastructure |
| Historial y artefactos | manifests JSON, CSV, SQLite legacy | `RunRepository` es autoridad nueva; migracion legacy transitoria |
| Modelos locales | `local_models.py`, hardware y endpoints web | Muchas capacidades concentradas en un solo modulo |
| Usuarios y seguridad web | `webapp/app.py`, SQLite | Funcional, pero unido al resto de la aplicacion y con migraciones al importar |

## Dependencias y flujo actuales

```text
Web Flask ─┬─> archivos de configuracion
           ├─> SQLite de autenticacion
           ├─> filesystem de datasets/modelos/resultados
           ├─> servicios nuevos de runs y metricas
           └─> subprocess main_cli.py

CLI ─────────> label_persuasion.py
TUI ─────────> modulos concretos + CLI/subprocess

label_persuasion.py
  ├─> SDK de proveedores
  ├─> local_models.py / llama_cpp
  ├─> checkpoint_io.py
  ├─> agreement_metrics.py
  ├─> run_reporting.py
  └─> CSV + JSON/JSONL + logs
```

La direccion de dependencias no es uniforme: las interfaces conocen detalles
de infraestructura y el pipeline conoce SDK, archivos y presentacion.

## Concentraciones verificadas

- `webapp/app.py`: 4438 lineas y alrededor de 180 funciones.
- `webapp/templates/run.html`: 3270 lineas.
- `webapp/templates/arbiters.html`: 1817 lineas.
- `labelforge_core/label_persuasion.py`: 1720 lineas.
- `labelforge_core/local_models.py`: 1377 lineas.
- Web, pipeline, modelos y entrypoints realizan efectos durante importacion:
  CUDA, `.env`, directorios, Flask y migraciones locales.
- Existen aproximadamente 90 constantes de modulo y multiples fuentes de
  configuracion o estado global.

El numero de lineas no es por si mismo un defecto. Es evidencia de baja cohesion
cuando el archivo tambien cambia por razones funcionales independientes.

## Persistencia actual y autoridad

| Informacion | Medio actual | Autoridad vigente |
| --- | --- | --- |
| Usuarios y sesiones | SQLite | SQLite |
| Propiedad de runs nuevos | manifests `RunRecord` | `RunRepository` |
| Propiedad antigua | SQLite `run_ownership` | Solo fallback legacy |
| Configuracion | JSON | Archivos de `config/` |
| Secretos | `.env` local | `secrets/secrets.env` |
| Estado reanudable | JSON | `run_state.json` |
| Evaluaciones parciales | JSONL | checkpoint de resultados por arbitro |
| Incidencias y rendimiento | JSONL | archivos de checkpoint correspondientes |
| Resultado final | CSV | artefacto bajo `output/` |
| Logs | texto | `logs/` |
| Catalogo/cache de modelos | JSON + filesystem | `models/` y configuracion local |

Esta pluralidad puede conservarse, pero cada dato necesita version, politica de
atomicidad, retencion, migracion y recuperacion documentadas.

La autoridad objetivo ya fue aprobada en ADR-0002: SQLite concentrara estado
operacional transaccional; datasets, modelos, logs y artefactos permaneceran en
filesystem; JSON se reservara para configuracion editable validada.

## Calidad verificada al abrir la Fase 0

- 126 pruebas `unittest` aprobadas en el entorno Conda `LabelForge`.
- Compilacion Python correcta.
- `git diff --check` sin errores.
- No hay aun medicion automatica de cobertura, lint, type checking, pruebas de
  contrato entre interfaces ni suite de navegador.
- El arbol de trabajo contiene una reconstruccion amplia sin consolidar. Debe
  preservarse y revisarse antes de crear commits.

## Riesgos prioritarios

1. El pipeline y Flask siguen siendo puntos centrales de cambio.
2. Los contratos principales son diccionarios sin version ni validacion comun.
3. Los proveedores no implementan un puerto uniforme.
4. Los efectos de importacion dificultan pruebas, empaquetado y despliegue.
5. La persistencia carece de una politica transversal de transacciones y schema.
6. Web, CLI y TUI pueden divergir al no depender solo de casos de uso.
7. El supervisor en memoria limita el despliegue a una instancia de proceso.
8. El CSV final es un resultado funcional, pero su esquema no esta formalizado
   como contrato versionado de producto.
9. `requirements.txt` y `environment.yml` aplican politicas de versiones
   distintas y no existe `pyproject.toml`.
10. Documentacion historica contiene estados contradictorios; la bitacora debe
    conservarse como historia, no usarse como fuente del estado presente.

## Invariantes que la reconstruccion debe preservar

- No perder ni repetir evaluaciones terminadas al reanudar.
- No truncar silenciosamente entradas largas.
- Mantener separados fila, entrada elegible, evaluacion, llamada y consenso.
- No convertir fallos locales deterministas en rate limit remoto.
- No exponer secretos en archivos publicos, logs, API o Git.
- Mantener `rate_limits.json` como unica fuente estatica de cuotas.
- Conservar `row_major` y `arbiter_major` como estrategias explicitas.
- Cada run usa un numero impar de arbitros entre 3 y 15; una configuracion par
  se rechaza de forma explicita y nunca se corrige silenciosamente.
- Descargar un artefacto final solo con autorizacion y existencia comprobada.
- El dataset exportado debe representar las etiquetas de todos los arbitros
  participantes, ausencias, consenso y trazabilidad sin ambiguedad.

## Fuera de alcance de Fase 0

- Reescribir el pipeline.
- Cambiar formatos persistidos.
- Introducir una cola distribuida.
- Elegir un framework frontend nuevo.
- Eliminar compatibilidad legacy.
- Optimizar sin perfiles de carga reproducibles.

Esas acciones requieren primero aprobar las decisiones registradas en los ADR.
