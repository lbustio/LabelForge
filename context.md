# LabelForge - contexto tecnico de transferencia

Ultima actualizacion: 2026-06-23.

Este documento es para retomar desarrollo rapido. El README explica uso general; esta
nota explica como esta organizado el sistema, que decisiones importan y donde estan
los riesgos.

## 0. Regla operativa para agentes

Antes de modificar codigo:

1. revisar arquitectura afectada;
2. identificar fuente canonica existente;
3. detectar duplicaciones, paths especiales, dead code o rutas subutilizadas;
4. proponer solucion y esperar aprobacion del usuario.

No aplicar parches directos para cambios de arquitectura, persistencia, seguridad
o ejecucion del pipeline sin confirmacion. La prioridad es no romper CLI, webapp,
checkpoints, outputs ni contratos de datos existentes.

## 1. Proposito

LabelForge etiqueta textos con LLMs usando varios arbitros independientes y calcula
consenso por mayoria. La configuracion por defecto usa la taxonomia de Ana Ferreira
para principios de persuasion en noticias falsas, pero los principios viven en JSON
y pueden cambiarse.

Regla metodologica:

- solo marcar una etiqueta si la senal aparece explicitamente en el texto;
- no usar contexto externo;
- no hacer fact-checking;
- no inferir intencion oculta.

## 2. Estado del repo

Rama principal: `main`.

Repo remoto conocido: `https://github.com/lbustio/LabelForge`.

Cambios recientes no necesariamente pusheados:

- configuracion movida a `config/`;
- `labelforge_core/paths.py` agregado;
- gestion GGUF expandida con cancelar descarga, archivo, restauracion y mover fuera del cache;
- prueba CLI con dataset de webapp, columna `claim`, tres arbitros locales y
  `--limit 10`;
- auditoria de arquitectura con duplicaciones detectadas en preflight,
  checkpoints, dataset y reporting;
- docs actualizadas.

Siempre revisar:

```bash
git status -sb
```

No revertir cambios ajenos sin pedir confirmacion.

## 3. Estructura actual

```text
setup_env.py
main_cli.py
main_webapp.py
config/
  arbiters_config.json
  local_models_config.json
  README.md
labelforge_core/
  agreement_metrics.py
  arbiters_io.py
  hw_detection.py
  label_persuasion.py
  local_models.py
  paths.py
  provider_preflight.py
  rate_limits.py
  secrets_io.py
  utils.py
webapp/
  app.py
  templates/
tests/
  test_pipeline.py
```

Directorios locales ignorados:

- `secrets/`;
- `models/`;
- `data/` salvo notas ligeras;
- `output/`;
- `checkpoints/`;
- `logs/`;
- `archive/`.

## 4. Configuracion y rutas

La configuracion canonica esta en:

- `config/arbiters_config.json`;
- `config/local_models_config.json`.

No volver a guardar JSON editables en `labelforge_core/`.

`labelforge_core/paths.py` centraliza rutas canonicas:

- `BASE_DIR`;
- `CONFIG_DIR`;
- `ARBITERS_CONFIG_PATH`;
- `LOCAL_MODELS_CONFIG_PATH`;
- `MODELS_DIR`;
- `HW_PROFILE_PATH`;
- `OUTPUT_DIR`;
- `CHECKPOINTS_DIR`;
- `SCRIPTS_DIR`;
- `resolve_config_file_path()`;
- `resolve_local_models_config_path()`.

No hay redireccion automatica a ubicaciones antiguas: la direccion correcta es
siempre `config/`.

## 5. Config actual de arbitros

`config/arbiters_config.json` contiene:

- `task.principles`;
- `task.user_prompt_template`;
- `arbiters`;
- `dataset`;
- `processing`;
- `safety`.

Arbitros actuales:

| ID | Nombre | Persona | Proveedor | Modelo |
|---|---|---|---|---|
| `arb1` | Psicologo Clinico | Germinio | `llama_cpp` | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` |
| `arb3` | Investigador Forense | Gepetencio | `llama_cpp` | `Mistral-7B-Instruct-v0.3-Q4_K_M.gguf` |
| `arb4` | Psicologo Forense | Claudio | `llama_cpp` | `Llama-3.2-3B-Instruct-Q4_K_M.gguf` |

Dataset actual:

- `input_file`: `data/uploads/all_datasets_en_es.csv`;
- `sheet`: vacio;
- `text_column`: `claim`.

Processing actual:

- `checkpoint_every`: 10;
- `claim_max_chars`: 3000;
- `base_delay_seconds`: 1.0;
- `min_delay_seconds`: 0.5;
- `max_delay_seconds`: 120.0;
- backoff 429: 2.0;
- backoff 5xx: 1.5.

Safety actual:

- `free_only_mode`: false;
- `allow_remote_openai_compat`: false;
- `max_total_tokens`: 0;
- `pause_on_quota_error`: true;
- `pause_on_network_error`: true.

## 6. Pipeline CLI

Entrypoint:

```bash
python main_cli.py
```

`main_cli.py`:

1. agrega el root al `sys.path`;
2. carga `secrets/secrets.env`;
3. resuelve credenciales GCP si aplica;
4. crea directorios con `ensure_project_dirs()`;
5. si hay arbitros `llama_cpp`, valida `models/hw_profile.json`;
6. delega a `labelforge_core.label_persuasion.main()`.

`labelforge_core/label_persuasion.py` hace el trabajo principal:

- carga config y dataset;
- valida safety;
- ejecuta preflight;
- llama proveedores;
- parsea JSON;
- guarda checkpoints;
- calcula consenso;
- reintenta fallos;
- escribe CSV final;
- emite eventos metricos para la webapp.

Proveedores en `PROVIDER_CALLERS`:

- `gemini`;
- `vertex_gemini`;
- `anthropic`;
- `openai`;
- `openai_compat`;
- `ollama`;
- `llama_cpp`.

`llama_cpp` usa `_call_local()`, que decide entre llama-cpp y OpenVINO segun
`models/hw_profile.json`.

Validacion reciente del CLI:

- comando usado: `python main_cli.py --limit 10`;
- dataset: `data/uploads/all_datasets_en_es.csv`;
- columna: `claim`;
- arbitros: `arb1`, `arb3`, `arb4`;
- resultado: 10/10 filas, 0 fallos, CSV en `output/`, log en `logs/`,
  metricas globales y por principio generadas.

Nota: existe bandera CLI `--fresh` para limpiar checkpoints reanudables antes
de iniciar una corrida nueva. No extender esta idea a otros casos sin revisar
primero el contrato de checkpoints.

## 7. Checkpoints y salida

Estado durable:

| Archivo | Descripcion |
|---|---|
| `checkpoints/checkpoint.json` | avance general |
| `checkpoints/run_state.json` | estado de ejecucion |
| `checkpoints/claim_results.jsonl` | resultado final por entrada |
| `checkpoints/arbiter_results.jsonl` | resultado por entrada/arbitro |
| `checkpoints/failed_queue.jsonl` | fallos pendientes |

Salida final:

- `output/labeled_YYYYMMDD_HHMMSS.csv`.

El CSV conserva columnas originales y agrega votos por arbitro, consenso, tokens,
latencias, metricas por entrada y metricas agregadas.

## 8. Metricas

`labelforge_core/agreement_metrics.py` calcula:

- observed agreement;
- Fleiss Kappa;
- Cohen Kappa promedio por pares;
- Krippendorff Alpha nominal;
- positive rate.

Regla arquitectonica:

- los calculos viven en Python;
- la UI renderiza datos y formatea;
- evitar mover reglas de negocio a JavaScript.

## 9. Webapp

Entrypoint:

```bash
python main_webapp.py
```

Default:

- host `127.0.0.1`;
- puerto `7860`;
- override por CLI o `LABELFORGE_HOST` / `LABELFORGE_PORT`.

Paginas:

- `/`;
- `/arbiters`;
- `/principles`;
- `/processing`;
- `/run`.

`webapp/app.py` contiene APIs REST y SSE. Hay alrededor de 48 rutas.

Bloques importantes:

- auth y headers de seguridad al inicio;
- listado de modelos de proveedores;
- rate limits;
- modelos locales GGUF;
- dataset config/upload/inspect;
- CRUD arbitros;
- principios/prompt/processing;
- run start/stop/status/logs/stats;
- outputs y checkpoints.

## 10. Seguridad actual

Implementado:

- login local SQLite obligatorio por defecto;
- en primer arranque, `/login` crea el primer usuario en `secrets/auth.db`;
- `LABELFORGE_SECRET_KEY` recomendado para sesiones persistentes;
- cookies `HttpOnly`, `SameSite=Lax`, y `Secure` configurable con `LABELFORGE_COOKIE_SECURE=1`;
- limite upload 50 MB;
- CSP basica, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`;
- validacion de filename GGUF contra traversal;
- bloqueo SSRF basico en `openai_compat` para link-local/metadata.
- CSRF para `POST`/`PUT`/`PATCH`/`DELETE`;
- `/api/provider-key` solo escribe secretos desde localhost;
- `api_key_override` se trata como transitorio y no persiste en `config/arbiters_config.json`.

La autenticacion esta encapsulada en `webapp/app.py` y plantillas. `main_cli.py` y
los modulos en `labelforge_core/` siguen agnosticos al login web y pensados para
ejecucion local o para ser llamados por la webapp en servidor.

Pendiente:

- HTTPS obligatorio si se expone fuera de localhost;
- no usar `--debug` en produccion.

## 11. Modelos locales GGUF

Setup:

```bash
python setup_env.py
```

Genera:

- `models/hw_profile.json`;
- logs en `logs/setup_*.log`;
- backend local si pudo instalarlo.

Modulo:

- `labelforge_core/local_models.py`.

Funciones relevantes:

- `load_hw_profile()`;
- `get_optimal_params()`;
- `recommended_models()`;
- `list_cached_models()`;
- `list_archived_models()`;
- `list_known_model_filenames()`;
- `archive_model()`;
- `restore_model()`;
- `delete_archived_model()`;
- `move_model_to_directory()`;
- `download_model()`;
- `get_or_load_llama()`.

Politica actual:

- `models/*.gguf` son modelos activos;
- `models/archive/*.gguf` son modelos guardados para despues;
- modelos movidos fuera del cache se registran en `models/models.json` con
  `location: external`;
- recomendados filtra modelos ya activos, archivados o externos;
- si `download_model()` ve que el destino ya existe, no descarga de nuevo.

Descarga:

- se hace por streaming HTTP desde Hugging Face;
- usa `.part` para descarga parcial;
- soporta reanudacion con Range;
- la UI permite cancelar;
- al cancelar se marca el job como `canceled` y se elimina `.part`.

Endpoints GGUF:

| Endpoint | Descripcion |
|---|---|
| `GET /api/local/hardware` | perfil de hardware |
| `GET /api/local/recommended` | recomendados no descargados |
| `GET /api/local/cached` | activos |
| `GET /api/local/archive` | archivados |
| `POST /api/local/download` | inicia job |
| `GET /api/local/download/<job_id>` | SSE progreso |
| `POST /api/local/download/<job_id>/cancel` | cancelacion |
| `DELETE /api/local/model/<filename>` | elimina activo |
| `POST /api/local/model/<filename>/archive` | mueve a `models/archive/` |
| `POST /api/local/model/<filename>/move` | mueve a ruta absoluta local |
| `POST /api/local/archive/<filename>/restore` | restaura |
| `DELETE /api/local/archive/<filename>` | elimina archivado |

Nota de seguridad: mover a ruta arbitraria solo se permite desde localhost.

## 12. Interfaz de arbitros

Archivo:

- `webapp/templates/arbiters.html`.

Funciones actuales:

- CRUD de arbitros;
- tabs por proveedor;
- seleccion/listado de modelos;
- validacion preflight;
- export/import `.lfarb`;
- gestion de GGUF local.

Estado tecnico:

- el flujo GGUF conserva una sola implementacion activa;
- las tarjetas de modelos locales gestionan descarga, cancelacion, archivado,
  restauracion y movimiento.

## 13. Politica de almacenamiento

Regla vigente:

- `config/`: JSON editables y versionables;
- `labelforge_core/`: solo codigo Python;
- `secrets/`: credenciales privadas;
- `models/`: modelos, hardware profile y caches locales;
- `data/`: datasets locales/privados;
- `output/`, `checkpoints/`, `logs/`, `archive/`: artefactos generados.

`config/*.local.json` y `config/*.private.json` estan ignorados.

No guardar API keys en `config/arbiters_config.json`.

## 14. Tests y verificacion

Comandos usados:

```bash
python -m py_compile webapp/app.py labelforge_core/local_models.py
python -m unittest tests.test_pipeline
```

En Windows con Conda local:

```bash
C:\Users\lbust\miniconda3\Scripts\conda.exe run -n labelforge python -m py_compile webapp\app.py labelforge_core\\local_models.py
C:\Users\lbust\miniconda3\Scripts\conda.exe run -n labelforge python -m unittest tests.test_pipeline
```

Smoke test util:

```python
from webapp.app import app
c = app.test_client()
print(c.get("/api/local/recommended").status_code)
print(c.get("/api/local/cached").status_code)
print(c.post("/api/local/download/nope/cancel").status_code)
print(c.post("/api/local/model/nope.gguf/move", json={"destination_dir": "C:\\Temp"}).status_code)
```

## 15. Auditoria arquitectonica reciente

Hallazgos detectados el 2026-06-23. No corregir automaticamente; proponer
diseno y pedir aprobacion antes de tocar codigo.

### Preflight CLI duplicado

- `main_cli.py` revisa `models/hw_profile.json` antes de delegar al nucleo.
- `labelforge_core.provider_preflight.require_provider_preflight()` vuelve a
  validar proveedores dentro del pipeline.
- Riesgo: `main_cli.py` lee la config canonica para decidir si hay `llama_cpp`,
  no necesariamente el `--config` pasado al CLI.
- Direccion recomendada: una sola autoridad de preflight en el nucleo, con CLI
  como wrapper delgado.

### Checkpoints duplicados

- Nucleo: constantes y funciones en `labelforge_core/label_persuasion.py`.
- Webapp: `_clear_runtime_checkpoint_files()`, `/api/checkpoint/status`,
  `/api/checkpoint/clear`, y lectura manual de `run_state.json` /
  `claim_results.jsonl`.
- Riesgo: CLI y webapp pueden divergir sobre que significa limpiar, reanudar o
  completar un run.
- Direccion recomendada: modulo `labelforge_core/checkpoint_io.py` con contrato
  compartido para limpiar, cargar, resumir y describir estado.

### Dataset duplicado

- Nucleo: `load_dataset()` en `label_persuasion.py`.
- Webapp: upload/inspect/config y carga de input para reconstruir checkpoints.
- Riesgo: diferencias de encoding, Excel sheets, columnas y validacion de rutas.
- Direccion recomendada: modulo `labelforge_core/dataset_io.py` con helpers
  para inspeccion, carga y validacion reusable.

### Reporting duplicado

- Nucleo: consenso, usage, resumen y CSV final.
- Webapp: `_detect_principles()`, `_row_processing_status()`,
  `_entry_usage_metrics_from_row()`, `_paper_reporting_summary()` y
  `/api/run/stats`.
- Riesgo: cambios de nombres o esquemas pueden producir resultados distintos
  entre CSV, UI y paper summary.
- Direccion recomendada: modulo `labelforge_core/run_reporting.py` para leer
  CSV/checkpoint y producir stats consumibles por UI.

### Outputs generados por CLI

- Webapp registra ownership solo cuando el run nace desde `/api/run/start`.
- CSVs creados manualmente por CLI pueden quedar sin owner para usuarios no-admin.
- Direccion recomendada: decidir politica antes de cambiar: admin-only,
  importacion de ownership, o visibilidad controlada.

### OpenVINO/NPU

- Existe ruta `_call_openvino()` activada desde `_call_local()` si
  `best_backend == "npu"`.
- Por decision actual, no atender ahora. Mantener como ruta futura.
- Cuando se reactive, validar explicitamente que no trate un GGUF como modelo
  Transformers/OpenVINO.

## 16. Pendientes vivos

Ver `todos.md` para backlog actualizado.

Los puntos mas importantes ahora:

- consolidar arquitectura sin romper CLI/webapp;
- prueba end-to-end desde webapp con dataset pequeno;
- ejecutar pipeline completo para resultados del paper;
- validar descarga/cancelacion GGUF con archivos reales;
- dejar OpenVINO/NPU para una fase futura.

## 17. Documentos

- `README.md`: manual de uso y arquitectura.
- `context.md`: este contexto tecnico.
- `todos.md`: backlog real.
- `bitacora.md`: historico de decisiones.
