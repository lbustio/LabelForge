# LabelForge

LabelForge es una plataforma local para etiquetar textos con LLMs usando un esquema
multi-arbitro por consenso. Nacio para una investigacion sobre principios de persuasion
en noticias falsas, pero la arquitectura permite usar taxonomias configurables.

El proyecto combina:

- un pipeline CLI reproducible;
- una webapp Flask para configurar, ejecutar y monitorear;
- arbitros LLM con distintos proveedores/personas;
- checkpoints, cola de reintentos, logs y CSV final;
- metricas de acuerdo inter-arbitro;
- soporte para modelos locales GGUF via `llama-cpp-python`.

## Estado actual

Funciona como herramienta de investigacion local. La app tambien tiene hardening inicial
para exponerse en red, pero antes de ponerla en internet hay pendientes de seguridad
descritos en `todos.md` y `context.md`.

Configuracion actual:

- config central en `config/arbiters_config.json`;
- catalogo GGUF en `config/local_models_config.json`;
- secretos en `secrets/secrets.env`;
- modelos y caches locales en `models/`;
- dataset privado y artefactos de ejecucion ignorados por Git.

## Taxonomia incluida

La configuracion por defecto etiqueta cinco principios de persuasion de Ana Ferreira:

| Clave | Principio | Senal buscada |
|---|---|---|
| `AUTH` | Authority | Autoridad, instituciones, expertos, gobierno, bancos o roles oficiales |
| `SP` | Social Proof | Mucha gente cree, actua, cumple o esta de acuerdo |
| `LSD` | Liking, Similarity & Deception | Familiaridad, afinidad, halago, identidad compartida o suplantacion |
| `CRC` | Commitment, Reciprocation & Consistency | Obligacion, reciprocidad, compromiso o coherencia esperada |
| `DIS` | Distraction | Urgencia, miedo, escasez, recompensa, sensacionalismo o sobrecarga emocional |

Regla metodologica central: marcar `1` solo si la senal es explicita en el texto. No se
hace fact-checking, no se usa contexto externo y no se infiere intencion oculta.

## Arquitectura

```text
LabelForge/
|-- setup_env.py                 # Detecta hardware e instala backend local GGUF
|-- main_cli.py                  # Entrypoint del pipeline CLI
|-- main_webapp.py               # Entrypoint de la webapp Flask
|-- config/
|   |-- arbiters_config.json     # Arbitros, principios, prompts, dataset, processing, safety
|   |-- local_models_config.json # Config del catalogo dinamico GGUF
|-- labelforge_core/
|   |-- label_persuasion.py      # Pipeline: LLM calls, rate limiter, checkpoints, consenso, CSV
|   |-- agreement_metrics.py     # Fleiss, Cohen, Krippendorff, acuerdo observado
|   |-- local_models.py          # GGUF: catalogo, descarga, cache, archive/restore/move
|   |-- hw_detection.py          # OS/CPU/RAM/GPU/NPU
|   |-- provider_preflight.py    # Validacion previa de credenciales/modelos
|   |-- rate_limits.py           # Perfiles y delays recomendados
|   |-- arbiters_io.py           # Import/export cifrado `.lfarb`
|   |-- secrets_io.py            # Gestion de `secrets/secrets.env`
|   |-- paths.py                 # Rutas canonicas del proyecto
|   |-- utils.py                 # Utilidades compartidas
|-- webapp/
|   |-- app.py                   # API REST/SSE y paginas
|   |-- templates/               # UI: dashboard, arbitros, principios, processing, run
|-- tests/test_pipeline.py       # Suite unitaria actual
|-- data/                        # Datasets locales/privados
|-- models/                      # GGUF, hw_profile, caches locales
|-- output/                      # CSVs etiquetados
|-- checkpoints/                 # Estado durable de ejecuciones
|-- logs/                        # Logs de setup y runs
|-- archive/                     # Outputs archivados
```

## Instalacion rapida

Requisitos:

- Python 3.10+;
- Windows, Linux o macOS;
- entorno virtual recomendado;
- credenciales de proveedores si se usan LLMs cloud.

Con `venv`:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Con Conda:

```bash
conda env create -f environment.yml
conda activate labelforge
```

Despues, para habilitar modelos locales GGUF:

```bash
python setup_env.py
```

`setup_env.py`:

- detecta OS/CPU/RAM/GPU/NPU;
- instala `llama-cpp-python` con backend apropiado cuando puede: CUDA para NVIDIA, ROCm para AMD Linux, Metal para Apple Silicon, Vulkan para AMD/Intel compatibles y CPU como fallback;
- instala OpenVINO si aplica para NPU Intel;
- escribe `models/hw_profile.json`;
- guarda log en `logs/setup_YYYYMMDD_HHMMSS.log`.

Si solo vas a usar APIs cloud, puedes omitirlo, pero las funciones GGUF avisaran que
falta el perfil de hardware.

## Secretos

Copia la plantilla:

```bash
copy secrets\secrets.env.example secrets\secrets.env
```

Variables habituales:

```env
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...

GOOGLE_CLOUD_PROJECT=cloud-computing-cybersec-inaoe
VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=secrets\service-account.json

LABELFORGE_HOST=127.0.0.1
LABELFORGE_PORT=7860
LABELFORGE_SECRET_KEY=

# Login local de la webapp. Los usuarios viven en secrets/auth.db.
LABELFORGE_COOKIE_SECURE=1
```

Para Vertex AI se usan Application Default Credentials o service account JSON, no API key.

## Uso

Webapp:

```bash
python main_webapp.py
```

Por defecto abre en:

```text
http://127.0.0.1:7860
```

CLI:

```bash
python main_cli.py --limit 10
python main_cli.py
python main_cli.py --config config/arbiters_config.json --input data/mi_dataset.csv --column claim
```

La webapp incluye:

- `/` dashboard;
- `/arbiters` gestion de arbitros y modelos GGUF;
- `/principles` principios y prompt;
- `/processing` dataset, columna, parametros y seguridad;
- `/run` ejecucion, logs SSE, estado y metricas.

## Flujo del pipeline

1. Lee `config/arbiters_config.json`.
2. Carga dataset CSV/Excel y columna configurada.
3. Ejecuta preflight de proveedores.
4. Para cada entrada, llama a cada arbitro.
5. Valida JSON de salida.
6. Guarda resultados por arbitro y por entrada en checkpoints.
7. Calcula consenso por mayoria.
8. Reintenta fallos desde `failed_queue.jsonl`.
9. Calcula metricas de tokens, latencia y acuerdo.
10. Escribe `output/labeled_YYYYMMDD_HHMMSS.csv`.

Archivos de checkpoint:

| Archivo | Uso |
|---|---|
| `checkpoints/checkpoint.json` | Progreso general |
| `checkpoints/run_state.json` | Estado del run |
| `checkpoints/claim_results.jsonl` | Resultado final por entrada |
| `checkpoints/arbiter_results.jsonl` | Resultado por entrada/arbitro |
| `checkpoints/failed_queue.jsonl` | Cola de reintentos |

## Arbitros y proveedores

Proveedores soportados:

- `vertex_gemini`;
- `gemini`;
- `anthropic`;
- `openai`;
- `openai_compat`;
- `ollama` local compatible con API OpenAI;
- `llama_cpp` para GGUF local.

La configuracion actual tiene tres arbitros cloud:

| ID | Nombre | Persona | Proveedor | Modelo |
|---|---|---|---|---|
| `arb1` | Psicologo Clinico | Germinio | `vertex_gemini` | `gemini-2.5-pro` |
| `arb3` | Investigador Forense | Gepetencio | `openai` | `gpt-4o` |
| `arb4` | Psicologo Forense | Claudio | `anthropic` | `claude-sonnet-4-6` |

## Modelos locales GGUF

La gestion GGUF vive en `/arbiters`, dentro del modal de modelos locales.

Funciones actuales:

- ver perfil de hardware detectado;
- consultar modelos recomendados para el hardware;
- descargar varios modelos al mismo tiempo;
- ver progreso dentro de la tarjeta de cada modelo;
- cancelar descargas en curso;
- no redescargar si el archivo ya existe;
- ocultar de recomendados modelos ya descargados, archivados o movidos fuera del cache;
- listar modelos activos en `models/`;
- ver detalles: tamano, familia, parametros, cuantizacion, capas, creador, repo y ubicacion;
- eliminar modelos activos;
- archivar modelos a `models/archive/`;
- restaurar modelos archivados;
- eliminar archivados;
- mover un modelo activo a una carpeta absoluta elegida localmente.

Rutas importantes:

| Endpoint | Uso |
|---|---|
| `GET /api/local/hardware` | Perfil `models/hw_profile.json` |
| `GET /api/local/recommended` | Recomendados pendientes para descargar |
| `GET /api/local/cached` | Modelos activos en `models/` |
| `GET /api/local/archive` | Modelos en `models/archive/` |
| `POST /api/local/download` | Iniciar descarga GGUF |
| `GET /api/local/download/<job_id>` | Progreso SSE |
| `POST /api/local/download/<job_id>/cancel` | Cancelar descarga |
| `DELETE /api/local/model/<filename>` | Eliminar activo |
| `POST /api/local/model/<filename>/archive` | Archivar activo |
| `POST /api/local/model/<filename>/move` | Mover activo a carpeta absoluta local |
| `POST /api/local/archive/<filename>/restore` | Restaurar archivado |
| `DELETE /api/local/archive/<filename>` | Eliminar archivado |

## CSV de salida

El CSV final conserva columnas originales y agrega:

- `{P}_{arb_id}`: voto del arbitro por principio;
- `{P}_{arb_id}_why`: justificacion si esta activada;
- `{P}_consensus`: consenso por mayoria;
- `prompt_tokens_{arb_id}`, `completion_tokens_{arb_id}`, `total_tokens_{arb_id}`;
- `latency_s_{arb_id}`;
- `entry_prompt_tokens`, `entry_completion_tokens`, `entry_total_tokens`;
- `entry_latency_s`, `entry_latency_avg_s`, `entry_arbiter_calls`;
- metricas de consenso por entrada;
- metricas agregadas de acuerdo globales y por principio.

Metricas agregadas:

- observed agreement;
- Fleiss Kappa;
- Cohen Kappa promedio por pares;
- Krippendorff Alpha nominal;
- positive rate.

## API principal

Paginas:

| Metodo | Endpoint |
|---|---|
| `GET` | `/` |
| `GET` | `/arbiters` |
| `GET` | `/principles` |
| `GET` | `/processing` |
| `GET` | `/run` |

Configuracion y arbitros:

| Metodo | Endpoint |
|---|---|
| `GET` | `/api/config` |
| `GET/POST` | `/api/arbiters` |
| `GET/PUT/DELETE` | `/api/arbiters/<arb_id>` |
| `POST` | `/api/arbiters/export` |
| `POST` | `/api/arbiters/import` |
| `GET` | `/api/arbiters/preflight` |
| `GET/PUT` | `/api/principles` |
| `GET/PUT` | `/api/prompt-template` |
| `GET/PUT` | `/api/processing` |

Dataset:

| Metodo | Endpoint |
|---|---|
| `GET/PUT` | `/api/dataset/config` |
| `POST` | `/api/dataset/inspect` |
| `POST` | `/api/dataset/upload` |

Ejecucion:

| Metodo | Endpoint |
|---|---|
| `POST` | `/api/run/start` |
| `POST` | `/api/run/stop` |
| `GET` | `/api/run/status` |
| `GET` | `/api/run/current-stats` |
| `GET` | `/api/run/logs` |
| `GET` | `/api/run/stats` |
| `GET` | `/api/run/principle-counts` |

Outputs y checkpoints:

| Metodo | Endpoint |
|---|---|
| `GET` | `/api/outputs` |
| `GET` | `/outputs/<name>` |
| `DELETE` | `/api/outputs/<name>` |
| `POST` | `/api/outputs/archive-all` |
| `GET` | `/api/checkpoint/status` |
| `POST` | `/api/checkpoint/clear` |
| `GET` | `/api/failed-queue` |

Modelos/proveedores:

| Metodo | Endpoint |
|---|---|
| `POST` | `/api/models` |
| `POST` | `/api/provider-key` |
| `GET` | `/api/rate-limits` |
| `GET` | `/api/rate-limits/summary` |

## Politica de almacenamiento

| Directorio | Versionable | Uso |
|---|---:|---|
| `config/` | Si | Configuracion editable base |
| `labelforge_core/` | Si | Nucleo Python importable: pipeline, proveedores y utilidades |
| `webapp/` | Si | Interfaz web, API HTTP/SSE y plantillas |
| `tests/` | Si | Pruebas |
| `secrets/` | No | Credenciales locales |
| `models/` | No | GGUF, hardware profile, caches |
| `data/` | No por defecto | Datasets privados/locales |
| `output/` | No | CSVs generados |
| `checkpoints/` | No | Estado de ejecuciones |
| `logs/` | No | Logs |
| `archive/` | No | Salidas archivadas |

Reglas:

- No guardar API keys ni secretos en `config/*.json`.
- Las claves reales deben vivir en `secrets/secrets.env`.
- `labelforge_core/` es el nucleo Python importable; no guardar JSON editables ahi.
- Para overrides personales, usar nombres como `*.local.json` o `*.private.json`.
- Los overrides locales/privados estan ignorados por Git.

## Seguridad

La webapp tiene:

- login obligatorio al abrir la webapp;
- login local SQLite obligatorio por defecto;
- primer arranque crea el primer usuario en `secrets/auth.db`;
- CSRF para metodos `POST`/`PUT`/`PATCH`/`DELETE`;
- `MAX_CONTENT_LENGTH = 50 MB`;
- cabeceras CSP, `X-Frame-Options` y `X-Content-Type-Options`;
- validacion de filenames GGUF;
- validacion basica contra SSRF en `openai_compat`;
- `/api/provider-key` solo escribe secretos desde localhost;
- `api_key_override` no persiste en `config/arbiters_config.json`.

Para despliegue publico:

- usar HTTPS obligatorio con reverse proxy;
- activar `LABELFORGE_COOKIE_SECURE=1` cuando ya sirvas por HTTPS;
- no usar `--debug`;
- usar `LABELFORGE_SECRET_KEY` persistente y aleatoria.

La autenticacion vive solo en la webapp. El CLI y los modulos en `labelforge_core/`
siguen pensados para ejecucion local y no dependen de sesiones web.

## Tests

```bash
python -m py_compile webapp/app.py labelforge_core/local_models.py
python -m unittest tests.test_pipeline
```

En el entorno local del proyecto tambien se ha usado:

```bash
C:\Users\lbust\miniconda3\Scripts\conda.exe run -n labelforge python -m unittest tests.test_pipeline
```

## Documentos vivos

- `README.md`: manual actual de uso y arquitectura.
- `context.md`: transferencia tecnica para continuar desarrollo.
- `todos.md`: backlog real.
- `bitacora.md`: decisiones historicas y cambios relevantes.
