# LabelForge

Plataforma completa para etiquetado de datos con LLMs locales y remotos, con métricas de acuerdo entre anotadores, sistema de árbitros IA y interfaz web segura.

## Características Principales

- **Soporte Multi-Proveedor**: OpenAI, Anthropic, Google Vertex AI, Gemini, Ollama, Saptiva InferNode (Laboratorio de IA IBERO, en línea y por lotes) y modelos locales GGUF via `llama-cpp-python`
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
|-- main_tui.py                  # TUI interactiva estilo menús numerados
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

TUI interactiva (menús numerados, sin flags):

```bash
python main_tui.py
```

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
- `llama_cpp` para GGUF local;
- `saptiva` para los modelos del Laboratorio de IA IBERO (Saptiva InferNode).

La configuracion actual tiene tres arbitros locales GGUF:

| ID | Nombre | Persona | Proveedor | Modelo |
|---|---|---|---|---|
| `arb1` | Psicologo Clinico | Germinio | `llama_cpp` | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` |
| `arb3` | Investigador Forense | Gepetencio | `llama_cpp` | `Mistral-7B-Instruct-v0.3-Q4_K_M.gguf` |
| `arb4` | Psicologo Forense | Claudio | `llama_cpp` | `Llama-3.2-3B-Instruct-Q4_K_M.gguf` |

### Laboratorio de IA IBERO (proveedor `saptiva`)

El proveedor `saptiva` usa la API de modelos de Saptiva Studio del laboratorio
(`studio.laboratorioia.ibero.mx`). Es compatible con el SDK de OpenAI y apunta por
defecto a `https://api.laboratorioia.ibero.mx/infernode/v1`. Documentación:
[API desde código](https://docs.saptiva.com/usuario/modelos/api-desde-codigo) y
[lotes](https://docs.saptiva.com/usuario/modelos/lotes).

1. Pide tu llave `sk-saptiva-...` en Studio (API > API Keys) o al administrador del lab.
2. Guárdala verificada:

   ```bash
   python main_cli.py --set-provider-key saptiva
   ```

3. Usa la configuración de ejemplo, que conserva los tres árbitros y sus prompts y
   asigna a cada uno un modelo distinto del lab:

   | ID | Nombre | Alias | Modelo actual (nov 2026) |
   |---|---|---|---|
   | `arb1` | Psicologo Clinico | `Saptiva Turbo` | Qwen3.6 35B A3B (FP8) |
   | `arb3` | Investigador Forense | `Saptiva Omni` | Nemotron 3 Nano Omni 30B A3B (FP8) |
   | `arb4` | Psicologo Forense | `Saptiva Cortex` | Qwen3.8 27B (FP8) |

   ```bash
   # Una llamada por entrada (rápido para pruebas)
   python main_cli.py --config config/arbiters_config.saptiva.json --limit 20

   # API de lotes: un lote por árbitro, del lado del servidor
   python main_cli.py --config config/arbiters_config.saptiva.json --batch
   ```

Notas:

- Usa siempre los alias (`Saptiva Turbo`, `Saptiva Omni`, `Saptiva Cortex`). El
  nombre exacto del catálogo incluye versión y deja de resolver al actualizarse.
- Los modelos del lab razonan por defecto y el razonamiento consume `max_tokens`.
  LabelForge lo apaga con `chat_template_kwargs.enable_thinking=false`, que es la
  única opción que funcionó en el lab. Para reactivarlo en un árbitro, pon
  `"reasoning": true` en su `generation_config`. Para acotarlo, usa
  `"reasoning_budget": N`, que se envía como `thinking_token_budget`.
  `chat_template_kwargs.reasoning_budget` se ignora en el lab.
- Cada árbitro usa un modelo distinto. `safety.require_distinct_models: true` bloquea
  la corrida si dos árbitros comparten modelo, porque el consenso por mayoría
  necesita votos independientes.
- La config de ejemplo usa `temperature: 0` y `seed: 42` para que las corridas sean
  reproducibles.

#### Por qué la config del lab es así

Probada contra las etiquetas humanas de `pop_dataset_Full(Tiltan).csv`. Primero se
eligió con 200 correos de ajuste y luego se validó con 400 correos distintos.

| Árbitro | Modelo | Razonamiento | Motivo |
|---|---|---|---|
| `arb1` | Saptiva Turbo | apagado | Razonar no mejoró la calidad y con presupuesto devolvió JSON roto en 4% a 10% de las llamadas. |
| `arb3` | Saptiva Omni | presupuesto de 1,024 tokens | Sin razonar marca 0 casi siempre. Con 1,024 iguala la calidad de razonar sin tope, con 11% menos tokens y cero llamadas cortadas; sin tope se cortaron 18 de 400. |
| `arb4` | Saptiva Cortex | apagado | Razonar no mejoró la calidad. Sin razonar es el árbitro individual más acertado. |

En los 400 correos de validación, el consenso logró kappa de Cohen de 0.42 en
autoridad y 0.28 en distracción, contra la referencia humana. En compromiso, prueba
social y afinidad quedó cerca de 0, porque esos principios tienen pocos positivos y
los modelos no los distinguen bien. El techo de calidad lo ponen los prompts y las
definiciones, no la configuración de InferNode.
- `--batch` exige que todos los árbitros sean `saptiva`. Funciona así:
  - Divide en archivos de hasta 50,000 líneas y 95 MiB.
  - Guarda en `checkpoints/saptiva_batch_state.json` los ids y las filas de cada
    lote. Si la corrida se interrumpe, al relanzar retoma esos mismos lotes.
  - La huella de la corrida incluye el contenido del dataset, el prompt y los
    árbitros. Si cambia algo, no retoma lotes viejos. `--fresh` borra el estado.
  - Cada respuesta se guarda al llegar. Al relanzar solo se envían las
    combinaciones de fila y árbitro que faltan.
  - Las líneas que fallan dentro del lote se reintentan en línea hasta
    `processing.batch_max_online_retries` (200 por defecto). Si fallan más, o si
    el reintento da error de llave, cuota o red, la corrida se pausa sin marcar
    filas como fallidas. `--batch-no-retry` pausa sin reintentar.
  - Un lote puede tardar hasta media hora en arrancar. Las líneas del lote no
    gastan rpm, pero cuestan lo mismo que la llamada en línea.
  - `safety.max_total_tokens` no se aplica dentro de un lote.
- Los CSV se leen en UTF-8 y, si no decodifican, en Windows-1252. Así se conservan
  los acentos de archivos exportados desde Excel, como `data_pop.csv`.
- Las peticiones llevan `metadata` con `app=labelforge` y el id del árbitro para
  ver el consumo en `GET /v1/billing/usage`.

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
- **Validación de MIME type real** por magic bytes en Python puro (previene archivos maliciosos disfrazados, sin dependencias de sistema)
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
