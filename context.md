# LabelForge - contexto tecnico de transferencia

Ultima actualizacion: 2026-06-30.

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

Cambios recientes no pusheados (working tree limpio salvo estos 8 archivos):

- `webapp/app.py`: hardening de seguridad + validación de uploads por magic
  bytes puro Python (sin dependencias de sistema).
- `labelforge_core/secrets_io.py`: permisos restrictivos de archivo
  cross-platform (`icacls` en Windows, `chmod 0o600` en Unix).
- `labelforge_core/cuda_init.py`: rutas de DLLs con pathlib en lugar de
  strings con backslash.
- `labelforge_core/local_models.py`: mensajes de fallo de proveedores al
  consultar catálogo GGUF degradados de WARNING a INFO (Gemini 429,
  Anthropic 401, OpenAI 401 y mensaje "fallback catalog"). Son fallos
  esperados y manejados; no merecen WARNING.
- `setup_env.py`: constantes de rutas Windows con guardia de plataforma,
  `_cuda_source_env()` con retorno anticipado en non-Windows, nuevo paso
  `verify_security()`.
- `requirements.txt`: eliminados `python-magic` y `python-magic-bin`;
  agregado `rich>=12.0.0`.
- `main_tui.py` (nuevo): TUI interactiva estilo menús numerados para el
  CLI. Navegación sin flags. Requiere `rich`. Entrypoint: `python main_tui.py`.
- `README.md`: documentación actualizada (TUI, seguridad, árbitros reales).

Rama remota obsoleta pendiente de eliminar: `revisión-de-proyecto-7044f`
(sus cambios útiles ya están integrados en `main`).

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
main_tui.py
config/
  arbiters_config.json
  local_models_config.json
  README.md
labelforge_core/
  agreement_metrics.py
  arbiters_io.py
  hw_detection.py
  checkpoint_io.py
  dataset_io.py
  label_persuasion.py
  llm_json.py
  local_models.py
  paths.py
  prompting.py
  provider_preflight.py
  rate_limits.py
  run_reporting.py
  secrets_io.py
  usage_metrics.py
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

## 6. Pipeline CLI y TUI

Entrypoints:

```bash
python main_cli.py     # CLI directo con flags
python main_tui.py     # TUI interactiva con menús numerados
```

`main_cli.py`:

1. agrega el root al `sys.path`;
2. carga `secrets/secrets.env`;
3. resuelve credenciales GCP si aplica;
4. crea directorios con `ensure_project_dirs()`;
5. delega a `labelforge_core.label_persuasion.main()`.

Regla actual: `main_cli.py` no debe leer config ni decidir preflight. La
autoridad de preflight es `labelforge_core.provider_preflight`, llamada desde el
pipeline despues de cargar la config real (`--config` incluido). Esto evita que
una config alternativa falle por arbitros locales presentes solo en la config
default.

`labelforge_core/label_persuasion.py` hace el trabajo principal:

- carga config y dataset;
- valida safety;
- ejecuta preflight;
- construye prompts usando `labelforge_core/prompting.py`;
- llama proveedores;
- parsea respuestas JSON/JSON-like usando `labelforge_core/llm_json.py`;
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

Smoke reciente desde webapp:

- fecha: 2026-06-23;
- endpoint: `POST /api/run/start` con `limit=1`;
- Flask lanzo `main_cli.py` como subprocess real;
- dataset: `data/uploads/all_datasets_en_es.csv`;
- columna: `claim`;
- arbitros: `arb1`, `arb3`, `arb4`;
- resultado: exit code 0, 1/1 filas exitosas, 0 fallos;
- CSV: `output/labeled_20260623_162215.csv`;
- `/api/run/stats?limit=5`: 200, con agreement, `paper_summary`,
  resultados por entrada, tokens y latencias;
- `/api/outputs`: 200, detecto 1 fila, 5 principios, 3 arbitros y tokens.

## 7. Checkpoints y salida

Estado durable:

| Archivo | Descripcion |
|---|---|
| `checkpoints/checkpoint.json` | avance general |
| `checkpoints/run_state.json` | estado de ejecucion |
| `checkpoints/claim_results.jsonl` | resultado final por entrada |
| `checkpoints/arbiter_results.jsonl` | resultado por entrada/arbitro |
| `checkpoints/failed_queue.jsonl` | fallos pendientes |

Autoridad actual:

- `labelforge_core/checkpoint_io.py` define rutas y helpers de lectura/escritura
  para checkpoints, JSONL incrementales, cola de fallidos y `run_state.json`;
- `labelforge_core/label_persuasion.py` consume esos helpers;
- `webapp/app.py` usa `clear_runtime_checkpoints()` para limpieza desde la UI.

Salida final:

- `output/labeled_YYYYMMDD_HHMMSS.csv`.

El CSV conserva columnas originales y agrega votos por arbitro, consenso, tokens,
latencias, metricas por entrada y metricas agregadas.

## 7.1 Dataset

Autoridad actual:

- `labelforge_core/dataset_io.py` centraliza carga e inspeccion de CSV/Excel;
- `load_dataset()` es usado por el pipeline CLI y por la webapp para reconstruir
  filas de entrada desde `run_state`;
- `inspect_dataset()` es usado por la webapp para upload/inspect de datasets;
- `resolve_dataset_path()` concentra resolucion de rutas y, cuando la webapp lo
  llama con `allowed_root=DATA_DIR`, evita salir de `data/`.

La webapp conserva lo que si le corresponde como adaptador HTTP: permisos,
uploads, formularios, SQLite/auth y respuestas JSON. No duplicar lectura de
dataset input en `webapp/app.py`; si se agregan formatos, sheets o encoding,
hacerlo en `dataset_io.py`.

## 8. Metricas

`labelforge_core/agreement_metrics.py` calcula:

- observed agreement;
- Fleiss Kappa;
- Cohen Kappa promedio por pares;
- Krippendorff Alpha nominal;
- positive rate.
- Cohen Kappa para pares binarios ya filtrados;
- metricas de solidez de consenso por entrada;
- columnas/flattening de metricas de consenso por entrada.
- consenso binario por mayoria para cada entrada.

Regla arquitectonica:

- los calculos viven en Python;
- `agreement_metrics.py` es la autoridad de estadistica pura;
- `label_persuasion.py` aplica esas metricas al pipeline y escribe CSV;
- `run_reporting.py` organiza reportes a partir de filas CSV/checkpoint;
- la UI renderiza datos y formatea;
- evitar mover reglas de negocio a JavaScript.

Autoridad actual de reporting:

- `labelforge_core/run_reporting.py` interpreta filas de salida
  CSV/checkpoint y produce estructuras de UI/reporte;
- detecta principios y arbitros desde columnas;
- calcula estado de fila, usage, conteos por principio, filas paginadas de
  resultado, metricas de acuerdo y `paper_summary`;
- `webapp/app.py` conserva lectura de archivos, permisos, auth, ownership y
  respuestas HTTP, pero delega el calculo a `run_reporting.py`.

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
- `LABELFORGE_SECRET_KEY` obligatoria en produccion; emite `WARNING` si no
  esta configurada en desarrollo (ya no genera clave silenciosamente);
- cookies `HttpOnly`, `SameSite=Lax`, `Secure=True` por defecto (antes era
  `False`); desactivar explicitamente con `LABELFORGE_COOKIE_SECURE=0` solo
  en desarrollo local HTTP;
- limite upload 300 MB;
- validacion de contenido real de uploads por magic bytes puro Python:
  XLSX (`\x50\x4B\x03\x04`), XLS (OLE2 `\xD0\xCF\x11\xE0`), CSV con
  rechazo de 11 firmas binarias conocidas y umbral de texto imprimible;
  sin dependencias de sistema (`libmagic` eliminado);
- permisos restrictivos en `secrets/secrets.env`: `icacls /inheritance:r`
  en Windows, `chmod 0o600` en Linux/macOS;
- errores internos de excepcion no se exponen en respuestas HTTP; se
  loguean en servidor con `exc_info=True`;
- CSP completa, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, HSTS en HTTPS;
- validacion de filename GGUF contra traversal;
- bloqueo SSRF basico en `openai_compat` para link-local/metadata;
- CSRF para `POST`/`PUT`/`PATCH`/`DELETE`;
- `/api/provider-key` solo escribe secretos desde localhost;
- `api_key_override` se trata como transitorio y no persiste en
  `config/arbiters_config.json`.

La autenticacion esta encapsulada en `webapp/app.py` y plantillas.
`main_cli.py` y los modulos en `labelforge_core/` siguen agnosticos al
login web y pensados para ejecucion local o para ser llamados por la
webapp en servidor.

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

Nota de nivel de logs: los fallos de proveedor al consultar el catálogo
(Gemini 429, Anthropic 401, OpenAI 401, fallback a catálogo local) se
emiten a nivel INFO, no WARNING. Son fallos esperados y manejados. El
CLI (logging en INFO) los ve para diagnóstico; la TUI (logging en WARNING)
los filtra automáticamente y el terminal queda limpio.

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

### Preflight CLI centralizado

- Hecho: `main_cli.py` es wrapper delgado y no lee config ni `hw_profile.json`.
- `labelforge_core.provider_preflight.require_provider_preflight()` es la unica
  autoridad de preflight para corridas CLI y webapp.
- `provider_preflight.py` usa rutas canonicas de `labelforge_core.paths`.

### Checkpoints centralizados

- Hecho: `labelforge_core/checkpoint_io.py` es la autoridad de rutas y helpers
  durables para checkpoints.
- `label_persuasion.py` ya no mantiene copias locales de JSONL/checkpoint/cola.
- `webapp/app.py` usa el helper compartido para limpiar runtime checkpoints.
- Pendiente futuro: si se requiere mas detalle de checkpoint/status, agregar
  helpers al nucleo sin volver a duplicar interpretacion en Flask.

### Dataset centralizado

- Hecho: `labelforge_core/dataset_io.py` es la autoridad para carga e inspeccion
  de datasets CSV/Excel.
- `label_persuasion.py` ya no mantiene un `load_dataset()` local.
- `webapp/app.py` usa helpers compartidos para upload/inspect y carga de filas
  desde `run_state`.
- Pendiente futuro: si se necesita soporte adicional de formatos o validacion
  de columnas, agregarlo ahi y mantener la webapp como adaptador.

### Reporting centralizado

- Hecho: `labelforge_core/run_reporting.py` concentra interpretacion de outputs
  y checkpoints ya fusionados.
- `webapp/app.py` ya no mantiene copias de `_detect_principles()`,
  `_row_processing_status()`, `_entry_usage_metrics_from_row()`,
  `_paper_reporting_summary()` ni calculo directo de `/api/run/stats`.
- La webapp sigue encargada de permisos, ownership, lectura del ultimo CSV y
  fallback a checkpoints; el calculo de dominio queda en el nucleo.

### Prompting y parsing LLM centralizados

- Hecho: `labelforge_core/prompting.py` construye catalogo de principios,
  instruccion de salida JSON y prompt de usuario.
- Hecho: `labelforge_core/llm_json.py` concentra parsing de respuestas de
  arbitros, incluyendo extraccion desde markdown, reparaciones conservadoras y
  rescate final de campos por principio.
- Hecho: `labelforge_core/usage_metrics.py` concentra calculo de tokens,
  latencias, llamadas por entrada y nombres de columnas `entry_*`.
- `label_persuasion.py` debe seguir orquestando la corrida y no volver a cargar
  funciones locales de prompting/parsing/uso.

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
