# LabelForge

Plataforma completa para etiquetado de datos con LLMs locales y remotos, con métricas de acuerdo entre anotadores, sistema de árbitros IA y interfaz web segura.

## Características Principales

- **Soporte Multi-Proveedor**: OpenAI, Anthropic, Google Vertex AI, Gemini, Ollama, y modelos locales GGUF via `llama-cpp-python`
- **Sistema de Árbitros**: Múltiples LLMs evaluando cada entrada con consenso por mayoría
- **Métricas Avanzadas**: Kappa de Fleiss, Kappa de Cohen, Alfa de Krippendorff, acuerdo observado
- **Interfaz Web Segura**: Flask con autenticación, CSRF protection, y hardening de seguridad
- **CLI Reproducible**: Pipeline completo desde línea de comandos
- **Checkpoints Duraderos**: Reanudación automática tras fallos
- **Validación Segura de Archivos**: Verificación de MIME type y extensión para uploads
- **Gestión de Secretos**: Almacenamiento seguro de API keys y credenciales

## Estado Actual

Proyecto en producción local con hardening de seguridad aplicado para exposición en red controlada.

**Últimas mejoras de seguridad (2024):**
- Validación de MIME type en subida de archivos
- Permisos restringidos (0600) para archivos de secretos
- Ocultamiento de errores internos en respuestas al cliente
- Logging detallado de errores solo en servidor
- Cookies seguras por defecto (`Secure=True`)
- Protección contra SQL injection con queries parametrizadas
- Cabeceras HTTP de seguridad completas (CSP, HSTS, X-Frame-Options, etc.)

## Configuración Actual

- Config central en `config/arbiters_config.json`
- Catálogo GGUF en `config/local_models_config.json`
- Secretos en `secrets/secrets.env` (no versionado, permisos 0600)
- Base de datos de autenticación en `secrets/auth.db` (no versionada)
- Modelos y caches locales en `models/`
- Datasets privados y artefactos de ejecución ignorados por Git

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

## Instalación rápida

Requisitos:

- Python 3.10+
- Windows, Linux o macOS
- Entorno virtual recomendado
- Credenciales de proveedores si se usan LLMs cloud
- **Librería sistema `libmagic1`** (para validación segura de archivos)

### Instalación de dependencias del sistema

**Linux (Debian/Ubuntu):**
```bash
sudo apt-get update && sudo apt-get install -y libmagic1
```

**macOS:**
```bash
brew install libmagic
```

**Windows:**
Las DLLs necesarias se incluyen con `python-magic-bin` en requirements.txt

### Con venv:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### Con Conda:

```bash
conda env create -f environment.yml
conda activate labelforge
```

### Habilitar modelos locales GGUF

Después de instalar las dependencias, para habilitar modelos locales GGUF:

```bash
python setup_env.py
```

`setup_env.py`:
- Detecta OS/CPU/RAM/GPU/NPU
- Instala `llama-cpp-python` con backend apropiado cuando puede: CUDA para NVIDIA, ROCm para AMD Linux, Metal para Apple Silicon, Vulkan para AMD/Intel compatibles y CPU como fallback
- Instala OpenVINO si aplica para NPU Intel
- Escribe `models/hw_profile.json`
- Guarda log en `logs/setup_YYYYMMDD_HHMMSS.log`

Si solo vas a usar APIs cloud, puedes omitirlo, pero las funciones GGUF avisarán que falta el perfil de hardware.

## Secretos

Copia la plantilla:

**Linux/macOS:**
```bash
cp secrets/secrets.env.example secrets/secrets.env
chmod 600 secrets/secrets.env
```

**Windows:**
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
GOOGLE_APPLICATION_CREDENTIALS=secrets/service-account.json

LABELFORGE_HOST=127.0.0.1
LABELFORGE_PORT=7860

# CRÍTICO: Genera una clave aleatoria persistente para sesiones
# En Linux/macOS: python -c "import secrets; print(secrets.token_hex(32))"
LABELFORGE_SECRET_KEY=tu_clave_secreta_muy_larga_y_aleatoria

# Login local de la webapp. Los usuarios viven en secrets/auth.db.
# Producción (HTTPS): LABELFORGE_COOKIE_SECURE=1
# Desarrollo local (HTTP): LABELFORGE_COOKIE_SECURE=0
LABELFORGE_COOKIE_SECURE=1
```

Para Vertex AI se usan Application Default Credentials o service account JSON, no API key.

**Nota de seguridad:** El archivo `secrets/secrets.env` debe tener permisos `600` (solo lectura/escritura para el propietario). La base de datos `secrets/auth.db` nunca debe versionarse en Git.
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
python main_cli.py --limit 10 --fresh
python main_cli.py
python main_cli.py --config config/arbiters_config.json --input data/mi_dataset.csv --column claim
```

Usa `--fresh` cuando quieras ignorar checkpoints reanudables de una corrida anterior y arrancar una salida nueva sin mezclar resultados parciales.

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
| `GET` | `/api/run/snapshot` |
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

La webapp incluye las siguientes protecciones implementadas:

### Autenticación y Sesiones
- Login obligatorio con hash de contraseñas (werkzeug.security)
- Base de datos local SQLite en `secrets/auth.db` (no versionada)
- Primer usuario creado automáticamente al primer arranque
- Cookies de sesión con flags: `HttpOnly`, `SameSite=Lax`, `Secure` (configurable)
- Timeout de sesión configurable (idle y absoluto)
- Invalidación de sesión al cambiar contraseña o rol

### Protección de Datos
- CSRF protection para todos los métodos `POST`, `PUT`, `PATCH`, `DELETE`
- Tokens CSRF generados por sesión con `secrets.token_urlsafe(32)`
- Queries SQL parametrizadas para prevenir SQL injection
- Ocultamiento de errores internos en respuestas al cliente (logging detallado solo en servidor)

### Subida de Archivos Segura
- Validación de extensión (.csv, .xlsx, .xls)
- **Validación de MIME type real** usando librería `magic` (previene archivos maliciosos disfrazados)
- Límite de tamaño: 300MB
- Uso de `secure_filename()` de Werkzeug
- Prevención de path traversal
- Archivos guardados en directorio controlado (`data/uploads/`)

### Cabeceras HTTP de Seguridad
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: same-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Content-Security-Policy` (con script-src 'self')
- `Strict-Transport-Security` (en HTTPS)
- `Cache-Control: no-store` (para páginas autenticadas)

### Control de Acceso
- Sistema de roles (admin/user)
- Protección de rutas admin con decoradores
- Rate limiting configurado por proveedor de LLM
- `/api/provider-key` solo escribe secretos desde localhost

### Gestión de Secretos
- API keys almacenadas en `secrets/secrets.env` (no versionado)
- **Permisos de archivo restringidos (0600)** aplicados automáticamente
- Plantilla `secrets.env.example` sin valores reales
- Función `resolve_gcp_credentials()` para manejo seguro de credenciales GCP
- `api_key_override` no persiste en config

### Para despliegue público (producción)

**CRÍTICO:**
- Usar HTTPS obligatorio con reverse proxy (nginx, traefik, etc.)
- Configurar `LABELFORGE_COOKIE_SECURE=1` en `secrets/secrets.env`
- Establecer `LABELFORGE_SECRET_KEY` persistente y aleatoria (ej. 64 caracteres hex)
- **Nunca** usar `--debug` en producción

**Recomendado:**
- Implementar rate limiting general con Flask-Limiter
- Configurar HSTS preload
- Revisar logs regularmente
- Mantener dependencias actualizadas

La autenticación vive solo en la webapp. El CLI y los módulos en `labelforge_core/` siguen pensados para ejecución local y no dependen de sesiones web.
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
