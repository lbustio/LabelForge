# LabelForge — Contexto Completo del Proyecto

> **Propósito de este archivo**: transferencia de contexto total. Cualquier desarrollador,
> asistente de código (Copilot, Cursor, Gemini, Claude, GPT, etc.) o programador nuevo
> debe poder leer este documento y continuar el trabajo como si siempre hubiera estado aquí.
> Mantenerlo actualizado es tan importante como el código mismo.

---

## 1. Qué es este proyecto

**LabelForge** es una plataforma de etiquetado automático y masivo de textos con LLMs,
construida para una investigación académica en curso sobre **principios de persuasión
en noticias falsas** (fake news).

El sistema usa un enfoque de **consenso multi-árbitro**: múltiples LLMs con distintos
"roles profesionales" (árbitros) etiquetan cada texto de forma independiente, y se
calcula un consenso por mayoría. Luego se calculan métricas estadísticas de acuerdo
inter-evaluador para validar la fiabilidad del etiquetado (Fleiss Kappa, Cohen's Kappa,
Krippendorff's Alpha).

**Contexto académico:**
- La taxonomía usada es la de **Ana Ferreira** sobre ingeniería social y persuasión.
- El dataset es bilingüe (inglés/español).
- El objetivo es publicar los resultados en un paper académico.
- El dataset y los checkpoints son **privados hasta que se publique el paper**.

---

## 2. Los cinco principios de persuasión (taxonomía de Ana Ferreira)

Estas son las etiquetas binarias (0/1) que se calculan para cada texto:

| Clave | Nombre completo | Qué detecta |
|---|---|---|
| `AUTH` | Authority / Autoridad | Invoca expertos, instituciones, roles oficiales, gobierno, bancos o figuras de autoridad para aumentar credibilidad o forzar cumplimiento |
| `SP` | Social Proof / Prueba Social | Afirma o sugiere que muchas personas ya actúan, creen, cumplen o están de acuerdo |
| `LSD` | Liking, Similarity & Deception | Crea confianza mediante familiaridad, afinidad, halagos, identidad compartida o suplantación |
| `CRC` | Commitment, Reciprocation & Consistency | Presiona mediante obligación, reciprocidad, acuerdos previos, relaciones o expectativas de coherencia |
| `DIS` | Distraction / Distracción | Usa urgencia, escasez, miedo, pánico, recompensa, plazos o sobrecarga emocional para apresurar el juicio |

**Regla fundamental del etiquetado**: marcar `1` **solo** cuando la señal sea explícita
en el texto. No se infiere intención oculta, no se hace fact-checking, no se usa
contexto externo.

---

## 3. Arquitectura del sistema

```
LabelForge/
├── setup_env.py           → ⚠️ EJECUTAR PRIMERO — detecta HW, instala backends, escribe hw_profile.json
├── main_cli.py          → Entrypoint CLI (verifica hw_profile, llama a scripts/label_persuasion.py)
├── main_webapp.py         → Entrypoint webapp Flask (verifica hw_profile, con --host, --port, --debug)
├── scripts/
│   ├── label_persuasion.py    → Pipeline principal: LLM calls, rate limiter, checkpoints, consenso
│   ├── arbiters_config.json   → Configuración central: árbitros, principios, prompts, parámetros
│   ├── agreement_metrics.py   → Métricas de acuerdo inter-árbitros (compartido CLI + webapp)
│   ├── arbiters_io.py         → Export/import cifrado de árbitros (compartido CLI + webapp)
│   ├── secrets_io.py          → Escritura/lectura controlada de secrets/secrets.env
│   ├── provider_preflight.py  → Validación previa de credenciales/proveedores
│   ├── hw_detection.py        → Detección multiplataforma de OS/CPU/RAM/GPU/NPU
│   ├── local_models.py        → Catálogo dinámico GGUF (consulta LLMs en paralelo), cache, descarga HF
│   ├── local_models_config.json → Prompts y selección de modelos LLM para el catálogo (editable sin tocar código)
│   └── utils.py               → Helpers numéricos + ensure_project_dirs() para crear dirs al arrancar
├── webapp/
│   ├── app.py                 → Backend Flask: endpoints REST + SSE
│   └── templates/
│       ├── base.html          → Layout global con sistema de colores semántico
│       ├── index.html         → Dashboard (árbitros, outputs, estado)
│       ├── arbiters.html      → CRUD de árbitros (tab "Local GGUF" reemplaza "Ollama")
│       ├── principles.html    → Edición de principios y preview del prompt
│       ├── processing.html    → Parámetros de procesamiento y rate limits
│       └── run.html           → Ejecución, logs SSE, métricas en vivo, resultados
├── tests/
│   └── test_pipeline.py       → 9 pruebas unitarias (utils, métricas, seguridad)
├── models/                    → Cache de modelos GGUF locales (excluido del repo)
│   ├── hw_profile.json        → Perfil de hardware + backends instalados (generado por setup_env.py)
│   └── *.gguf                 → Modelos descargados
├── data/                      → ⚠️ PRIVADO — excluido del repo (.gitignore)
├── output/                    → CSVs etiquetados generados (no en repo)
├── checkpoints/               → ⚠️ PRIVADO — estado de ejecuciones (excluido del repo)
├── logs/                      → Logs de ejecuciones Y de setup (excluidos del repo)
│   └── setup_YYYYMMDD_HHMMSS.log  → Log del último setup_env.py
├── secrets/                   → Credenciales locales (NUNCA subir a repo)
├── secrets.env.example        → Plantilla pública de variables de entorno
├── requirements.txt           → Dependencias directas (huggingface_hub, psutil, flask, etc. — llama-cpp-python excluido: lo instala setup_env.py)
├── README.md                  → Documentación pública del proyecto
├── bitacora.md                → Historial detallado de decisiones de desarrollo
└── context.md                 → Este archivo — contexto de transferencia
```

---

## 4. Cómo funciona el pipeline de etiquetado

### Flujo completo de una ejecución

```
Archivo de entrada (CSV o Excel) + columna configurable
    ↓
load_dataset(path, sheet, text_column) → lista de dicts + fieldnames
    ↓
load_config() → lee arbiters_config.json (sección dataset: input_file, sheet, text_column)
    ↓
validate_safety_policy() → bloquea si free_only_mode viola restricciones
    ↓
Para cada claim:
    ├── ¿Ya está en checkpoint? → saltar
    ├── Para cada árbitro:
    │   ├── Construir prompt (system_prompt + user_prompt_template resuelto)
    │   ├── Llamar al LLM del proveedor configurado
    │   ├── Parsear JSON de respuesta → {AUTH:0, SP:1, LSD:0, CRC:0, DIS:1}
    │   ├── Registrar tokens y latencia
    │   └── Guardar en arbiter_results.jsonl (durable)
    ├── Calcular consenso por mayoría
    ├── Calcular diagnóstico por entrada: coincidencia de árbitros, tokens y latencia
    ├── Guardar en claim_results.jsonl (durable)
    └── Actualizar checkpoint.json
    ↓
Al finalizar:
    ├── Procesar failed_queue.jsonl (reintentar fallos)
    ├── Calcular métricas de acuerdo (Fleiss/Cohen/Krippendorff)
    ├── Escribir CSV final en output/labeled_YYYYMMDD_HHMMSS.csv
    └── Escribir log en logs/run_YYYYMMDD_HHMMSS.log
```

### Sistema de resiliencia (checkpoints)

El pipeline guarda estado de forma granular — **por árbitro y por claim**:

| Archivo | Contenido | Propósito |
|---|---|---|
| `checkpoints/checkpoint.json` | Índice de claims completados | Saber desde dónde reanudar |
| `checkpoints/run_state.json` | Estado del run (running/paused/completed) | Detectar runs pausados |
| `checkpoints/claim_results.jsonl` | Resultado final por claim | Reconstruir CSV sin re-procesar |
| `checkpoints/arbiter_results.jsonl` | Respuesta por claim+árbitro | Evitar repetir llamadas ya hechas |
| `checkpoints/failed_queue.jsonl` | Claims que fallaron para reintento | Cola de reintentos |

### Rate Limiter dinámico

- Comienza con `base_delay_seconds` (default: 1.5s)
- Si recibe error 429 o 5xx → aplica `rate_limit_backoff_multiplier` (x2.0)
- Si tiene N éxitos consecutivos (`recovery_threshold_successes`: 5) → reduce delay × `recovery_delay_multiplier` (0.8)
- Nunca baja de `min_delay_seconds` (0.5s) ni sube de `max_delay_seconds` (120s)

---

## 5. Árbitros configurados actualmente

La configuración actual tiene **tres árbitros** (número impar para evitar empates en consenso):
uno usa **Vertex AI** con autenticación ADC (no API key), uno usa **OpenAI** con `OPENAI_API_KEY`
y uno usa **Anthropic Claude** con `ANTHROPIC_API_KEY`, ambas desde `secrets/secrets.env`.

| ID | Nombre | Persona | Modelo | Proveedor |
|---|---|---|---|---|
| `arb1` | Psicólogo Clínico | Germinio | `gemini-2.5-pro` | `vertex_gemini` |
| `arb3` | Investigador Forense | Gepetencio | `gpt-4o` | `openai` |
| `arb4` | Psicólogo Forense | Claudio | `claude-sonnet-4-6` | `anthropic` |

**Decisión de diseño — diversidad de modelos y familias:** los tres árbitros usan modelos
de **tres proveedores distintos** (Vertex AI/Gemini, OpenAI/GPT, Anthropic/Claude) para
garantizar independencia real de criterio. Dos árbitros del mismo proveedor con temperatura 0.1
producirían acuerdo correlacionado artificialmente, inflando el Fleiss Kappa sin reflejar
consenso genuino.

**Proyecto GCP para árbitros Vertex**: `cloud-computing-cybersec-inaoe`  
**Región Vertex**: `us-central1`  
**Temperatura**: `0.1` (baja, para clasificación determinista)  
**max_output_tokens**: `2048` (el JSON binario de salida es muy corto; margen para thinking tokens en gemini-2.5)

> ⚠️ Para que los árbitros Vertex funcionen, se necesita autenticación ADC.
> Vertex AI no usa API key directa. Hay dos rutas:
>
> - Sin login interactivo local: usar una service account JSON y definir
>   `GOOGLE_APPLICATION_CREDENTIALS=secrets\service-account.json`.
> - Con Google Cloud CLI:
> ```bash
> gcloud auth application-default login
> gcloud auth application-default set-quota-project cloud-computing-cybersec-inaoe
> ```

---

## 6. Formato de prompt y respuesta del LLM

### Template del prompt de usuario

```
Task: label explicit persuasion cues in TEXT only, using the definitions below.
Do not fact-check, use outside context, or infer hidden intent.
Return 1 only when the cue is explicit; otherwise 0.

{principles_catalog}

TEXT: {claim}

{output_instruction}
```

- `{principles_catalog}` se construye en tiempo de ejecución desde los principios del config.
- `{claim}` es el texto a etiquetar.
- `{output_instruction}` depende del flag `include_justifications`:
  - **`false`** (default): `Return ONLY valid JSON: {"AUTH":0,"SP":0,"LSD":0,"CRC":0,"DIS":0}`
  - **`true`**: también pide `AUTH_why`, `SP_why`, etc. (máx 8 palabras cada uno)

### Respuesta esperada del LLM

```json
{"AUTH": 0, "SP": 1, "LSD": 0, "CRC": 0, "DIS": 1}
```

Con justificaciones activadas:
```json
{"AUTH": 0, "AUTH_why": "no cue", "SP": 1, "SP_why": "many users comply", ...}
```

### Consenso

- Votación mayoritaria simple: si ≥ 2 de 3 árbitros votan 1, el consenso es 1.
- Si un árbitro falla permanentemente, su voto se marca `-1` y no cuenta para el consenso.

---

## 7. Estructura del CSV de salida

El CSV de salida `output/labeled_YYYYMMDD_HHMMSS.csv` preserva todas las columnas
originales del dataset y agrega:

| Patrón de columna | Ejemplo | Descripción |
|---|---|---|
| `{P}_{arb_id}` | `AUTH_arb1` | Voto binario del árbitro (0 o 1, -1 si falló) |
| `{P}_{arb_id}_why` | `AUTH_arb1_why` | Justificación (vacío si no se pidió) |
| `{P}_consensus` | `AUTH_consensus` | Consenso por mayoría (0 o 1, -1 si todos fallaron) |
| `prompt_tokens_{arb_id}` | `prompt_tokens_arb1` | Tokens de entrada enviados al árbitro |
| `completion_tokens_{arb_id}` | `completion_tokens_arb1` | Tokens de respuesta del árbitro |
| `total_tokens_{arb_id}` | `total_tokens_arb1` | Total de tokens del árbitro |
| `latency_s_{arb_id}` | `latency_s_arb1` | Latencia en segundos de esa llamada |
| `entry_prompt_tokens` | `832` | Suma de tokens de entrada de todos los árbitros para esa entrada |
| `entry_completion_tokens` | `90` | Suma de tokens de salida de todos los árbitros para esa entrada |
| `entry_total_tokens` | `922` | Tokens totales de la entrada completa |
| `entry_latency_s` | `34.035` | Tiempo total medido de la entrada completa |
| `entry_latency_avg_s` | `11.345` | Tiempo promedio por llamada de árbitro en esa entrada |
| `entry_arbiter_calls` | `3` | Número de llamadas de árbitro contabilizadas en la entrada |
| `entry_tokens_per_call` | `307.33` | Promedio de tokens por llamada de árbitro |
| `entry_consensus_observed_agreement` | `1.0` | Coincidencia por pares para esa entrada; diagnóstico/auditoría, no métrica principal del paper |
| `entry_consensus_observed_agreement_percent` | `100.0` | Coincidencia por pares expresada como porcentaje |
| `entry_consensus_mean_support_ratio` | `1.0` | Apoyo promedio al consenso por principio |
| `entry_consensus_mean_support_percent` | `100.0` | Apoyo promedio expresado como porcentaje |
| `entry_consensus_unanimous_principles` | `5` | Principios con voto unánime |
| `entry_consensus_split_principles` | `0` | Principios con desacuerdo entre árbitros |
| `consensus_global_fleiss_kappa` | — | Fleiss Kappa global del run |
| `{P}_consensus_fleiss_kappa` | `AUTH_consensus_fleiss_kappa` | Fleiss Kappa por principio |
| _(más métricas de acuerdo)_ | — | Cohen, Krippendorff, observed_agreement, positive_rate |

**Columna de texto configurable**: la columna que se envía al LLM se define en
`dataset.text_column` del config o con `--column` en CLI. Por defecto es `claim`
(nombre legacy del dataset original). Se puede usar cualquier nombre de columna.

---

## 8. Configuración central (`scripts/arbiters_config.json`)

### Sección `task`
```json
{
  "description": "...",
  "principles": { "AUTH": "...", "SP": "...", "LSD": "...", "CRC": "...", "DIS": "..." },
  "include_justifications": false,
  "user_prompt_template": "..."
}
```

### Sección `arbiters` (array)
```json
{
  "id": "arb1",
  "name": "Nombre público",
  "persona": "Nombre del personaje",
  "provider": {
    "type": "vertex_gemini | gemini | anthropic | openai | openai_compat | llama_cpp",
    "model": "gemini-2.5-pro",
    "api_key_env": "GEMINI_API_KEY",
    "api_key_override": "",
    "base_url": "",
    "vertex_project": "mi-proyecto-gcp",
    "vertex_location": "us-central1",
    "model_path": "",
    "n_ctx": 4096
  },
  "generation_config": { "temperature": 0.1, "max_output_tokens": 2048 },
  "system_prompt": "..."
}
```

**Campos específicos de `llama_cpp`:**
- `model_path`: ruta relativa al `.gguf` dentro del proyecto (ej: `models/llama-3.2-3b-instruct-q4_k_m.gguf`).
- `n_ctx`: tamaño de la ventana de contexto (default 4096).
- GPU offloading es **automático**: si `llama-cpp-python` detecta GPU, se usan todas las capas (`n_gpu_layers=-1`); si no, CPU (`n_gpu_layers=0`). No se persiste en config.
- No requiere `api_key_env`, `base_url` ni `vertex_project`.

**Nota sobre Ollama:** el provider `ollama` fue eliminado del frontend. Quien necesite
Ollama puede usar `openai_compat` con `base_url=http://localhost:11434/v1`. El tab
"Ollama (local)" fue reemplazado por "Local (GGUF)" que usa `llama_cpp` nativo.

**Regla de secretos:** `api_key_override` existe por compatibilidad histórica pero
no debe usarse para configuraciones nuevas. Las claves reales viven en
`secrets/secrets.env`; el árbitro solo guarda `api_key_env` apuntando a la variable
correspondiente (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.).
La webapp y el CLI usan `scripts/secrets_io.py` para verificar y guardar claves.

### Export/import cifrado de árbitros

La sección `arbiters` puede exportarse a un archivo `.lfarb` cifrado con contraseña.
El contenido real (prompts, modelos y posibles secretos heredados) va cifrado con
AES-256-GCM; el archivo visible solo contiene metadatos técnicos (`salt`, `nonce`,
`ciphertext`). La lógica vive en `scripts/arbiters_io.py` y se usa desde webapp y CLI.

- Webapp: pestaña `/arbiters`, botones **Importar** y **Exportar**.
- CLI:
  ```bash
  python main_cli.py --export-arbiters backup.lfarb
  python main_cli.py --import-arbiters backup.lfarb
  python main_cli.py --import-arbiters backup.lfarb --replace-arbiters
  ```

La importación por defecto fusiona por ID y conserva los demás árbitros. Con
`--replace-arbiters` o el modo "Reemplazar" de la webapp se reemplaza toda la lista.
Se permite eliminar todos los árbitros; en ese estado la UI muestra una advertencia
y el pipeline CLI aborta con mensaje explícito hasta que se cree o importe al menos uno.

### Sección `dataset`
```json
{
  "input_file": "data/all_datasets_en_es/all_datasets_en_es.csv",
  "sheet": "",
  "text_column": "claim"
}
```

Jerarquía: `CLI args (--input/--sheet/--column) > config > defaults`.
Formatos soportados: `.csv`, `.xlsx`, `.xls`.
Si `sheet` está vacío, se usa la primera hoja del Excel.
Si `text_column` no existe en el archivo, el pipeline termina con error descriptivo.

### Sección `processing`
| Parámetro | Default | Descripción |
|---|---|---|
| `checkpoint_every` | 10 | Cada cuántos claims actualizar el progreso en la webapp |
| `claim_max_chars` | 3000 | Truncar textos más largos |
| `base_delay_seconds` | 1.5 | Delay inicial entre peticiones |
| `min_delay_seconds` | 0.5 | Delay mínimo (nunca bajar de aquí) |
| `max_delay_seconds` | 120.0 | Delay máximo (nunca superar) |
| `rate_limit_backoff_multiplier` | 2.0 | Factor de backoff ante error 429 |
| `server_error_backoff_multiplier` | 1.5 | Factor de backoff ante error 5xx |
| `recovery_threshold_successes` | 5 | Éxitos consecutivos para reducir delay |
| `recovery_delay_multiplier` | 0.8 | Factor de reducción al recuperar |

### Sección `safety`
| Parámetro | Default | Descripción |
|---|---|---|
| `free_only_mode` | `false` | Estado actual para pruebas; si se activa, bloquea proveedores fuera de lista blanca |
| `allowed_provider_types` | `["gemini","vertex_gemini","anthropic","openai","openai_compat","llama_cpp"]` | Lista blanca de proveedores autorizados |
| `allow_remote_openai_compat` | `false` | Bloquea endpoints OpenAI-compat remotos |
| `max_total_tokens` | `0` | Límite global de tokens (0 = sin límite) |
| `pause_on_quota_error` | `true` | Pausar ante error de cuota |
| `pause_on_network_error` | `true` | Pausar ante error de red |

---

## 9. Webapp Flask

### Cómo arrancar
```bash
# Forma recomendada (acepta argumentos CLI y env vars)
python main_webapp.py

# Cambiar puerto (ej: si 7860 está ocupado)
python main_webapp.py --port 8080

# Modo debug (hot-reload)
python main_webapp.py --debug

# Accesible desde la red local
python main_webapp.py --host 0.0.0.0
```

### Configuración del servidor
La jerarquía de precedencia es:
```
--host/--port (CLI) > LABELFORGE_HOST/LABELFORGE_PORT (secrets/secrets.env) > 127.0.0.1:7860
```

**Puerto 7860** — libre en macOS (el 5000 está ocupado por AirPlay Receiver desde
macOS 12 Monterey), Windows y Linux. No requiere privilegios de administrador.

### Páginas de la webapp
| URL | Descripción |
|---|---|
| `/` | Dashboard: árbitros activos, outputs recientes, estado del checkpoint |
| `/arbiters` | CRUD completo de árbitros (crear, editar, eliminar) |
| `/principles` | Edición de principios, template del prompt y preview coloreado del prompt final |
| `/processing` | Parámetros de rate limiter, delays, backoff, recovery, seguridad y **carga de dataset (drag & drop + click)** |
| `/run` | Lanzar etiquetado, ver logs en tiempo real (SSE), métricas en vivo, resultados |

### Comunicación en tiempo real (SSE)
El pipeline emite dos tipos de mensajes al arrancarse desde la webapp:
1. **Logs de texto plano** → se muestran en el visor de logs en `/run`
2. **Eventos estructurados** con prefijo `__RUN_METRIC__` → se procesan como métricas
   en vivo y se acumulan en `/api/run/current-stats`

Estos eventos estructurados solo se emiten cuando la variable
`DATA_LABELING_WEBAPP_METRICS=1` está activa (la webapp la activa automáticamente
al lanzar el subproceso).

### Semántica de ejecución y UI actual

- **Dataset listo:** `/processing` muestra una tarjeta "Archivo listo para procesar"
  cuando `dataset.input_file` está guardado en `scripts/arbiters_config.json`.
  Esa tarjeta enseña ruta, hoja y columna. Subir un archivo nuevo solo lo deja
  preparado; hay que seleccionar columna y pulsar **Guardar** para que se use.
- **`--limit` / "Límite de entradas":** es un límite total desde el inicio del
  dataset, no una cantidad adicional. Ejemplo: con 50 entradas ya persistidas en
  checkpoint y límite `70`, se consideran las primeras 70 filas y se procesan
  aproximadamente 20 nuevas. La UI de `/run` explica esto dinámicamente.
- **Checkpoint:** las entradas con resultado final en `checkpoint/claim_results.jsonl`
  o `checkpoint.json` se saltan; `arbiter_results.jsonl` evita repetir llamadas de
  árbitros ya completados para una entrada parcial.
- **Logs por árbitro:** `/run` colorea líneas que mencionan `arb1`, `arb2`, etc.,
  usando la misma secuencia de colores de `/arbiters`, para identificar qué árbitro
  habló o falló.
- **Preflight:** antes de lanzar el subproceso, WebApp y CLI llaman a
  `scripts/provider_preflight.py`. Si faltan credenciales/proyecto/base_url/API key,
  el run no empieza y se muestran instrucciones accionables.

### Frontera backend/frontend
La regla actual del proyecto es: **todo cálculo de procesamiento y métricas vive en
Python; la interfaz solo presenta datos**.

- `scripts/label_persuasion.py` calcula consenso, diagnóstico de consenso por entrada,
  tokens, latencia, llamadas por árbitro y columnas `entry_*`.
- `scripts/agreement_metrics.py` calcula métricas globales y por principio
  (Fleiss/Cohen/Krippendorff/acuerdo observado/tasa positiva), porcentajes e
  interpretación del kappa.
- `scripts/arbiters_io.py` centraliza export/import cifrado de árbitros, validación
  de formato y merge/reemplazo.
- `scripts/secrets_io.py` centraliza el mapeo proveedor→variable y la escritura
  controlada de `secrets/secrets.env`.
- `scripts/provider_preflight.py` centraliza validación previa de proveedores:
  ADC de Vertex, API keys de Gemini/Anthropic/OpenAI y `base_url` para
  OpenAI-compatible/Ollama.
- `webapp/app.py` adapta CSV/eventos a JSON para la interfaz y mantiene compatibilidad
  con CSV anteriores calculando faltantes en backend.
- `webapp/templates/*.html` no debe decidir resultados, métricas, cifrado ni escritura
  de secretos: solo renderiza, formatea números/segundos/porcentajes y dispara
  acciones contra APIs backend.

---

## 10. Variables de entorno (`secrets/secrets.env`)

```env
# API Keys de LLM
GEMINI_API_KEY=...           # Para proveedor "gemini" (AI Studio)
ANTHROPIC_API_KEY=...        # Para proveedor "anthropic"
OPENAI_API_KEY=...           # Para proveedor "openai"

# Vertex AI (no requiere API key — usa ADC)
GOOGLE_CLOUD_PROJECT=cloud-computing-cybersec-inaoe
VERTEX_LOCATION=us-central1
# GOOGLE_APPLICATION_CREDENTIALS se detecta automáticamente desde secrets/*.json

# HuggingFace (para descarga rápida de modelos GGUF)
HF_TOKEN=hf_xxx...           # Sin token: ~200-300 KB/s. Con token: varios MB/s

# Seguridad — OBLIGATORIO si la app está expuesta en internet
LABELFORGE_PASSWORD=...      # Sin esto: solo localhost. Con esto: Basic Auth en red.
LABELFORGE_SECRET_KEY=...    # Clave Flask. Generar: python -c "import secrets; print(secrets.token_hex(32))"

# Servidor web (opcionales)
# LABELFORGE_HOST=127.0.0.1
# LABELFORGE_PORT=7860
```

**El archivo `secrets/secrets.env` NUNCA va al repositorio.** Solo existe `secrets.env.example`
(plantilla pública) versionado en el repo.

Las claves pueden editarse manualmente o guardarse desde las interfaces:

- Webapp `/arbiters`: seleccionar proveedor, pegar la clave en **Clave de API**,
  pulsar verificar. Si la clave lista modelos correctamente, se guarda en
  `secrets/secrets.env` y se refresca la grilla de modelos.
- CLI:
  ```bash
  python main_cli.py --set-provider-key gemini
  python main_cli.py --set-provider-key anthropic
  python main_cli.py --set-provider-key openai
  python main_cli.py --set-provider-key openai_compat --base-url http://localhost:11434/v1
  ```

Mapa de variables por proveedor:

| Proveedor | Variable en `secrets/secrets.env` |
|---|---|
| `gemini` | `GEMINI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `openai_compat` | `OPENAI_API_KEY` |
| `vertex_gemini` | No usa API key directa; usa ADC/GCP |
| `llama_cpp` | No usa API key; el modelo es un archivo `.gguf` local |

### Rate limits por proveedor/modelo

La fuente compartida para recomendaciones de espera es `scripts/rate_limits.py`.
La WebApp consulta `/api/rate-limits` y `/api/rate-limits/summary`; los frontends
no deben duplicar tablas ni reglas de throttling.

- `gemini`: usa perfiles publicados de Gemini API/AI Studio cuando hay match por
  modelo o familia (`limit_kind="published"`).
- `vertex_gemini`, `anthropic`, `openai`, `openai_compat`: usan perfiles
  por modelo/familia como fallback conservador (`limit_kind="account_dependent"`).
  Sus cuotas reales dependen de cuenta, proyecto, región, tier, organización o
  proveedor compatible, por lo que la UI debe mostrarlas como orientativas.
- `llama_cpp`: sin rate limits. El backend retorna `limit_kind="local"` y la UI
  oculta la sección de rate limits para árbitros de este tipo.

---

## 11. Instalación desde cero

```bash
# 1. Clonar el repositorio
git clone https://github.com/lbustio/LabelForge.git
cd LabelForge

# 2. Crear entorno virtual (pip — recomendado para control de backends locales)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# 3. Ejecutar el script de setup de hardware (OBLIGATORIO antes de arrancar)
#    Detecta CPU/GPU/NPU, instala las dependencias base y el backend de
#    inferencia local óptimo para este equipo. Loguea todo en logs/setup_*.log
python setup_env.py

# 4. Verificar que el setup terminó sin errores (el propio script lo indica)
#    Si hubo errores → revisar logs/setup_YYYYMMDD_HHMMSS.log antes de continuar

# 5. Configurar variables de entorno
cp secrets.env.example secrets/secrets.env
# Editar secrets/secrets.env con las API keys correspondientes

# 6. Autenticar Vertex AI (si se usan árbitros vertex_gemini)
# Opción A: service account JSON
set GOOGLE_APPLICATION_CREDENTIALS=secrets\service-account.json   # Windows
export GOOGLE_APPLICATION_CREDENTIALS=secrets/service-account.json  # Linux/macOS

# Opción B: Google Cloud CLI
gcloud auth application-default login
gcloud auth application-default set-quota-project cloud-computing-cybersec-inaoe

# 7. Verificar instalación
python -m unittest tests/test_pipeline.py

# 8. Arrancar la webapp
python main_webapp.py
# Abrir http://127.0.0.1:7860 en el navegador

# O ejecutar el pipeline CLI directamente
python main_cli.py --limit 10
```

**Nota:** `main_webapp.py` y `main_cli.py` verifican al arrancar que `models/hw_profile.json`
existe y tiene `setup_ok = true`. Si no, abortan con instrucción de ejecutar `setup_env.py`.

---

## 11b. Convenciones de logging

Todo el proyecto usa **un único formato** de logging para que los logs del pipeline,
del setup y de cualquier módulo futuro sean coherentes y filtrables del mismo modo.

### Formato estándar

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
```

Ejemplo de línea real:
```
2026-06-16 02:32:09,029 [INFO] Config cargado: 3 arbitros, 5 principios
```

### Reglas que aplican a todos los módulos

| Regla | Detalle |
|---|---|
| Formato único | `%(asctime)s [%(levelname)s] %(message)s` — sin variaciones |
| Directorio único | `logs/` — tanto runs como setup |
| Nomenclatura | `run_YYYYMMDD_HHMMSS.log` / `setup_YYYYMMDD_HHMMSS.log` |
| Dual output | `FileHandler` (archivo) + `StreamHandler` (consola) siempre |
| Secciones | `=== NOMBRE DE SECCIÓN ===` para marcar fases |
| Loggers ruidosos | Silenciar al nivel WARNING: `httpx`, `httpcore`, `anthropic`, `openai`, `google`, `pip`, `urllib3` |
| Subprocesos | Salida de `pip install` capturada línea a línea → escrita como `[INFO]`; errores como `[ERROR]` con stderr completo |
| Niveles | `DEBUG` para trazas internas, `INFO` para flujo normal, `WARNING` para degradaciones recuperables, `ERROR` para fallos |

### Logging del servidor Flask (`webapp/app.py`)

El servidor Flask usa un logger dedicado `labelforge.http` (con su propio `StreamHandler`)
que **reemplaza** el access log ruidoso de Werkzeug:

```
# Antes (Werkzeug por defecto — inútil):
127.0.0.1 - - [19/Jun/2026 20:24:03] "GET / HTTP/1.1" 200 -

# Ahora (formato del proyecto):
2026-06-19 20:24:03,123 [INFO]  GET   /                          200  (12ms)
2026-06-19 20:24:05,456 [INFO]  POST  /api/arbiters              201  (8ms)
2026-06-19 20:24:56,789 [WARNING] GET /api/nonexistent           404  (1ms)
```

Implementación en `webapp/app.py`:
- `logging.getLogger("werkzeug").setLevel(logging.ERROR)` — silencia el access log de Werkzeug
- `@app.before_request` → guarda `g._t0 = time.perf_counter()`
- `@app.after_request` → loguea `METHOD /path → STATUS (Xms)` con nivel INFO/WARNING/ERROR según código
- Filtra automáticamente `/static/` y `/favicon.ico`; silencia 304 (caché)

### Secciones estándar del log de setup (`setup_env.py`)

```
=== LABELFORGE SETUP ===
=== DETECTANDO HARDWARE ===
=== INSTALANDO REQUIREMENTS BASE ===
=== INSTALANDO BACKEND NPU (OpenVINO) ===       # si aplica
=== INSTALANDO llama-cpp-python ===
=== VERIFICANDO IMPORTS CRITICOS ===
=== VERIFICANDO DIRECTORIOS DEL PROYECTO ===
=== ESCRIBIENDO hw_profile.json ===
=== RESUMEN FINAL ===
```

**Flujo de `setup_env.py` (post-2026-06-19 fix):**
- `install_base_requirements()` filtra `llama-cpp-python` del requirements.txt con archivo temporal antes de pasárselo a pip — evita intentar compilarlo sin MSVC
- El flujo **nunca aborta** por fallos de pip: loguea error y continúa. `hw_profile.json` **siempre** se escribe aunque `llama-cpp-python` falle
- Para CUDA (Windows): prueba wheels precompiladas en orden `cu126 → cu124 → cu121 → compilación desde fuente`. Detecta versión exacta de CUDA con `nvidia-smi` para elegir el índice correcto (`https://abetlen.github.io/llama-cpp-python/whl/cuXXX`). No requiere MSVC ni CMake si el wheel existe.

### Parque de máquinas y su backend de inferencia local

| Máquina | OS | GPU/NPU | Backend llama.cpp | Backend NPU |
|---|---|---|---|---|
| iMac i9 | macOS | AMD Radeon (PCIe) | Vulkan (MoltenVK) | — |
| Servidor | Linux | NVIDIA A100 40 GB | CUDA | — |
| Laptop | Windows | NVIDIA RTX 5050 8 GB VRAM, 23 GB RAM | CUDA (prebuilt wheel) | — |
| PC desktop | Windows | Sin GPU dedicada | CPU (AVX2) | — |
| ASUS NUC 14 PRO | Windows | Intel Arc + AI Boost | Vulkan | OpenVINO |

**ASUS NUC — aclaración importante:** el Intel AI Boost NPU NO es soportado por
`llama-cpp-python`. Se usa OpenVINO como ruta de inferencia independiente para el NPU.
El Intel Arc GPU se usa vía Vulkan (o SYCL si oneAPI está disponible).
La prioridad de selección en runtime: NPU (OpenVINO) → GPU (llama-cpp-python) → CPU.

## 12. Dependencias directas

```
google-genai==2.8.0             # Gemini AI Studio
google-cloud-aiplatform==1.157.0  # Vertex AI
anthropic==0.109.1              # Claude
openai==2.41.1                  # GPT + endpoints compatibles (openai_compat)
flask==3.1.3                    # Webapp
python-dotenv==1.2.2            # Carga de secrets/secrets.env
openpyxl                        # Lectura de archivos Excel (.xlsx)
llama-cpp-python                # Modelos locales GGUF (provider llama_cpp)
huggingface_hub                 # Descarga de modelos desde Hugging Face
psutil                          # Detección de RAM y hardware del sistema
```

**Nota sobre `llama-cpp-python`:** **no está en `requirements.txt`**. Lo instala `setup_env.py`
según el hardware detectado. Para CUDA en Windows usa wheels precompiladas del índice
`https://abetlen.github.io/llama-cpp-python/whl/cuXXX` (no requiere MSVC ni CMake).
Para Metal (macOS) y ROCm (Linux AMD) compila desde fuente. La aplicación detecta
automáticamente qué soporte GPU está disponible en tiempo de ejecución.

No hay dependencias de análisis de datos (numpy, pandas, etc.) — las métricas de
acuerdo están implementadas directamente en `scripts/agreement_metrics.py` con Python
puro para mantener el entorno mínimo.

---

## 13. Datos privados — qué no está en el repo

| Directorio/Archivo | Por qué está excluido |
|---|---|
| `data/all_datasets_en_es/` | Dataset bilingüe de investigación, **privado hasta publicación** |
| `checkpoints/` | Estado de ejecuciones locales (no reproducible en otro equipo) |
| `output/` | CSVs etiquetados (resultados de ejecuciones locales) |
| `logs/` | Logs de corridas locales |
| `secrets/secrets.env` | API keys y credenciales |

Todos están cubiertos por `.gitignore`. Los datos se harán públicos al concluir
el estudio y publicar el paper.

**Columna clave del dataset**: `claim` — texto a etiquetar. El dataset también puede
tener columnas `claim_en` (traducción al inglés), `dataset`, `class`, etc.

---

## 14. Módulos internos — referencia rápida

### `scripts/utils.py`
```python
safe_int(value, default=None)       # str/float → int, None si falla
safe_float(value, default=None)     # cualquier → float, None si falla
safe_binary_int(value)              # → 0, 1, o None
number(value, default=0)            # → type(default)(value), o default si falla
ensure_project_dirs(root: Path)     # crea logs/, output/, checkpoints/, models/, secrets/, scripts/, data/, archive/
                                    # si no existen; retorna lista de Path creados; llamado al arrancar por ambos entrypoints
```

### `scripts/label_persuasion.py` — funciones clave
```python
load_dataset(input_path, sheet="", text_column="claim") -> (rows, fieldnames)
# Carga un CSV o Excel. Valida que text_column exista.
# Excel: usa openpyxl; sheet puede ser nombre o índice entero.
# Raises SystemExit con mensaje descriptivo si el archivo o columna no existen.
```

### `scripts/agreement_metrics.py`
```python
compute_agreement_metrics(rows, principles, arbiter_ids) → dict
# rows: lista de dicts {AUTH_arb1: 0, SP_arb1: 1, ...}
# Retorna: {"global": {...métricas...}, "per_principle": {"AUTH": {...}, ...}}

compute_consensus_metrics(results, principles, arbiters) → dict
# Versión para el pipeline CLI (formato de entrada diferente)

flatten_consensus_metrics(metrics, principles) → dict
# Aplana las métricas a columnas CSV planas

build_consensus_metric_fieldnames(principles) → list
# Genera la lista de nombres de columnas de métricas para el CSV header
```

### Claves de métricas retornadas
```
items                      → número de entradas evaluadas
raters                     → número de árbitros
observed_agreement         → acuerdo observado promedio por pares
fleiss_kappa               → Fleiss Kappa multi-árbitro
pairwise_cohen_kappa_avg   → Cohen Kappa promedio por pares
krippendorff_alpha_nominal → Krippendorff Alpha nominal
positive_rate              → tasa de etiquetas positivas (1)
```
Cada métrica retorna además un campo `*_tone` (success/info/warning/danger) para
coloreado consistente en la UI. La función `_percent_tone()` aplica la escala de
4 niveles para valores porcentuales; `_kappa_tone()` para valores de kappa.

**Interpretación del Kappa** (regla general):
| Valor | Interpretación |
|---|---|
| < 0.00 | Sin acuerdo |
| 0.00–0.20 | Leve |
| 0.21–0.40 | Regular |
| 0.41–0.60 | Moderado |
| 0.61–0.80 | Sustancial |
| 0.81–1.00 | Casi perfecto |

---

## 15. Decisiones de diseño importantes

### Por qué no se usa numpy/pandas
Las métricas de acuerdo están implementadas en Python puro para mantener el entorno
de dependencias mínimo. El único dato de entrada es el CSV final ya escrito en disco.

### El dataset y la columna de texto son configurables
Desde la version actual, el archivo de entrada (CSV o Excel), la hoja de calculo
y la columna de texto a etiquetar son configurables desde la webapp o la CLI
sin tocar codigo. La columna `claim` es el valor por defecto (dataset original),
pero puede usarse cualquier nombre de columna existente en el archivo.

### Por qué `vertex_gemini` en vez de `gemini`
Los árbitros actuales usan Vertex AI (proyecto GCP `cloud-computing-cybersec-inaoe`)
porque ofrece mayor cuota de requests para producción. Requiere ADC en lugar de
API key directa. Si `free_only_mode` se reactiva, debe tener `vertex_gemini` y
`anthropic` en `allowed_provider_types` para no bloquear la configuración actual.
En el estado actual de pruebas, `free_only_mode=false`, pero el preflight de
credenciales sigue bloqueando runs imposibles.

### Por qué el puerto 7860
El puerto 5000 está ocupado por AirPlay Receiver en macOS 12+ Monterey. El 7860 es
libre en macOS, Windows y Linux, y es reconocido en el ecosistema ML/AI (Gradio,
HuggingFace Spaces). No requiere privilegios de root.

### Por qué dos entrypoints (main_cli.py y main_webapp.py)
Separación de responsabilidades: el CLI no debe depender de Flask, y la webapp no
debe ejecutar directamente el pipeline en el mismo proceso (usa un subproceso).

### Por qué SSE en vez de WebSockets
SSE es unidireccional (servidor → cliente), más simple de implementar con Flask, y
suficiente para el caso de uso (streaming de logs). Los WebSockets requerirían una
librería adicional (Flask-SocketIO) o cambiar el servidor.

### Por qué `max_output_tokens: 2048`
El JSON de respuesta binaria es muy corto (~35 chars). El valor anterior (256) era
insuficiente porque **Gemini 2.5 en Vertex AI tiene "thinking mode" activado por defecto**,
que consume tokens de salida para razonamiento interno antes de emitir el JSON.
Con 256 tokens, la respuesta se truncaba a mitad del JSON (`JSONDecodeError`).

**Solución aplicada:**
- `max_output_tokens: 2048` en config para todos los árbitros (margen amplio).
- En `_call_vertex_gemini`: desactivar thinking **solo para `gemini-2.5-flash`**:
  ```python
  if "gemini-2.5-flash" in provider["model"]:
      generation_config["thinking_config"] = {"thinking_budget": 0}
  ```
- `gemini-2.5-pro` **no soporta** `thinking_budget=0` en Vertex AI (error 400), por
  eso la condición es específica para flash.

### Configuración de Vertex AI como diccionario plano
La configuración de generación se pasa como `dict` (no como objeto `GenerationConfig`)
para evitar un bug del SDK de Vertex AI que causaba truncamiento silencioso de
respuestas en versiones recientes de Gemini.

---

## 16. Estado actual del proyecto

### ✅ Completado y funcionando
- Pipeline CLI completo con rate limiter, checkpoints, cola de fallos y métricas
- Soporte de entrada CSV y Excel con sheet y columna configurables (webapp + CLI)
- Pestaña `/processing` corregida tras mezcla de JS legacy: ya no muestra código
  como texto y usa solo el flujo actual de drag & drop/upload
- Webapp Flask con 48 endpoints, 5 páginas y streaming SSE
- 3 árbitros configurados: 1 Vertex AI (`arb1/gemini-2.5-pro`), 1 OpenAI (`arb3/gpt-4o`)
  y 1 Anthropic Claude (`arb4/claude-sonnet-4-6`)
- JSONDecodeError solucionado: `max_output_tokens` 256→2048 y thinking desactivado
  en gemini-2.5-flash (gemini-2.5-pro no soporta `thinking_budget=0`)
- Controles de ejecución deshabilitados durante proceso activo en `/run`
- Preflight de proveedores antes de iniciar runs, con instrucciones por proveedor
- `/processing` muestra el archivo realmente listo para procesar
- `/run` aclara que el límite de entradas es total desde el inicio del dataset
- Logs de `/run` coloreados por árbitro
- Métricas de acuerdo inter-árbitros (Fleiss/Cohen/Krippendorff) en CLI y webapp
- Diagnóstico de consenso por entrada y estadísticas de tokens/latencia por entrada
  calculados en backend y persistidos en CSV
- Separación backend/frontend reforzada: la UI no calcula métricas de negocio
- Sistema de colores semántico unificado (4 niveles) aplicado a todas las métricas
  de acuerdo: observado, Fleiss, Cohen, Krippendorff y tasa positiva
- Badge global muestra acuerdo observado + Fleiss Kappa por separado para evitar
  paradoja visual de prevalencia
- Suite de 9 pruebas unitarias
- README completo, bitácora actualizada y context.md de transferencia
- `scripts/__init__.py` creado para resolver ModuleNotFoundError en subprocesos
- Repositorio público en GitHub: https://github.com/lbustio/LabelForge

### ⚠️ Datos privados (investigación en curso)
- Dataset bilingüe `data/all_datasets_en_es/` — excluido del repo
- Checkpoints y outputs de ejecuciones — excluidos del repo
- Se harán públicos al concluir el estudio y publicar el paper

### ✅ Completado también (2026-06-19) — soporte nativo de modelos locales GGUF

Implementación completa de 10 pasos + 4 adiciones mid-sesión:

1. `scripts/hw_detection.py` — detecta OS/CPU/RAM/GPU/NPU multiplataforma ✅
2. `setup_env.py` — instala backends según HW, escribe `models/hw_profile.json`, loguea en `logs/setup_*.log` ✅
3. `scripts/local_models.py` — catálogo dinámico (consulta Gemini+Anthropic+OpenAI en paralelo), cache, descarga HF ✅
4. `scripts/label_persuasion.py` — `_call_local()`, `_call_llama_cpp()`, `_call_openvino()`, `PROVIDER_CALLERS["llama_cpp"]` ✅
5. `scripts/provider_preflight.py` — `_check_llama_cpp()`: hw_profile + import + archivo .gguf ✅
6. `scripts/rate_limits.py` — `limit_kind="local"` para `llama_cpp` (delay 0, sin límites) ✅
7. `webapp/app.py` — `/api/local/hardware|recommended|cached|download|model`, carga hw_profile al arrancar ✅
8. `webapp/templates/arbiters.html` — tab "Local (GGUF)" reemplaza "Ollama", modal de descarga con HW y recomendados ✅
9. `main_cli.py` + `main_webapp.py` — `ensure_project_dirs()` + `_check_hw_profile_if_needed()` al arrancar ✅
10. `.gitignore`, `requirements.txt`, `environment.yml` actualizados; paquetes instalados en venv ✅

**Adiciones mid-sesión:**
- `ensure_project_dirs(root)` en `scripts/utils.py` — crea los 8 directorios requeridos al arrancar ambos entrypoints
- Catálogo GGUF dinámico: consulta 3 LLMs en paralelo (ThreadPoolExecutor), sintetiza por votos, TTL 7 días en `models/catalog_cache.json`; fallback a `_FALLBACK_CATALOG` si ningún LLM disponible
- `scripts/local_models_config.json` — prompts del catálogo y selección de modelos LLM, editables sin tocar código
- Versiones instaladas (conda env `LabelForge` en Windows): `psutil 7.2.2`, `huggingface_hub 1.20.1`. `llama-cpp-python` se instala por máquina vía `setup_env.py` (no se incluye en el entorno base). En iMac i9: `llama-cpp-python 0.3.30` con GPU offload activo (AMD Radeon Pro 580X via Metal).

### ✅ Completado también (2026-06-19, sesión posterior) — fixes de setup, logging y modal GGUF

**Bugs de `setup_env.py` corregidos:**
- `requirements.txt` incluía `llama-cpp-python>=0.3.0` → pip intentaba compilarlo desde fuente sin MSVC → abortaba → `hw_profile.json` nunca se escribía. **Fix:** eliminado de requirements.txt; `install_base_requirements()` filtra la línea con archivo temporal.
- El flujo llamaba `_abort()` ante fallos de pip, impidiendo que `hw_profile.json` se escribiera. **Fix:** `install_base_requirements()` retorna `bool`; el flujo principal siempre escribe `hw_profile.json` independientemente de errores parciales.
- Para CUDA en Windows: antes intentaba compilar con `CMAKE_ARGS="-DGGML_CUDA=on"` sin MSVC. **Fix:** ahora prueba en orden wheels precompiladas `cu126 → cu124 → cu121` con `--prefer-binary`, detectando la versión de CUDA desde `nvidia-smi`. Compilación desde fuente solo como último recurso.

**Logging del servidor Flask:**
- Werkzeug access log silenciado; reemplazado por logger `labelforge.http` con formato del proyecto.
- `before_request` + `after_request` hooks: loguean `METHOD /path → STATUS (Xms)` con nivel según código HTTP.
- Filtro de ruido: `/static/`, `/favicon.ico`, 304 Not Modified no se loguean.

**Modal de gestión de modelos GGUF (`arbiters.html`):**
- `modal-dialog-scrollable` añadido: el modal tiene scrollbar interno.
- Nueva sección "Modelos descargados": lista GGUFs en `models/` con botones eliminar y re-descargar.
- Nueva sección "Descargar por nombre": campos repo_id + filename para descargar cualquier GGUF directamente desde HuggingFace.
- `models/models.json` ahora persiste `repo_id` de cada modelo descargado (para el botón re-descargar).
- `fits_in_memory` corregido: antes retornaba `False` para todos cuando `hw_profile.json` no existía (porque `effective_mem = 0 * 0.6 = 0`). Ahora:
  - Si hw_profile existe: calcula fit real contra VRAM/RAM.
  - Si hw_profile no existe: intenta obtener RAM de `psutil` (sistema en vivo) como fallback.
  - Solo `None` (botón mostrado sin garantía) si absolutamente no hay datos de HW.
  - En frontend: `fits_in_memory === false` → "No cabe" en rojo; `null` → botón Descargar (sin datos HW).
- `catalog_cache.json` invalidado (tenía repos inventados por LLMs, e.g. `bartowski/Llama-3-8B-Instruct-GGUF` que no existe).
- `hf_hub_download`: eliminado parámetro deprecado `local_dir_use_symlinks=False`.

**Auto-ID de árbitros:**
- Al abrir "Nuevo árbitro", el campo ID se pre-rellena con `arbX` donde X es el consecutivo del ID más alto existente (`arb1`, `arb3`, `arb4` → sugiere `arb5`).

### ✅ Completado también (2026-06-19, sesión de seguridad) — hardening para despliegue en internet

La app pasó de ser localhost-only a ser expuesta en internet. Se realizó una auditoría completa; los hallazgos con confianza ≥ 8/10 fueron corregidos.

**Vulnerabilidades corregidas:**
- **Path traversal en download:** `filename` del POST `/api/local/download` no validado → podía sobrescribir `secrets/secrets.env`. Fix: misma validación que el DELETE (`/`, `\`, `..`, extensión `.gguf`).
- **Stored XSS en árbitros:** `a.name`, `a.persona`, `a.system_prompt`, `a.id`, `p.model`, `p.base_url` interpolados en `innerHTML` sin `escapeHtml()`. Fix: `escapeHtml()` aplicado a todos.
- **Reflected XSS en panel HW:** campos del hardware profile interpolados en `innerHTML`. Fix: `escapeHtml()` en todos los campos del bloque de HW.
- **SSRF via `base_url`:** campo del POST pasado directamente al SDK de OpenAI → GET a cualquier URL del servidor. Fix: `_validate_base_url()` bloquea esquemas no-http(s) e IPs link-local (169.254.x.x).
- **Sin autenticación:** HTTP Basic Auth añadido globalmente. `LABELFORGE_PASSWORD` en secrets.env habilita acceso en red. Sin contraseña: solo localhost.
- **Sin límite de upload:** `MAX_CONTENT_LENGTH = 50 MB`.
- **Sin cabeceras de seguridad:** CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` en todas las respuestas HTML.

**Falsos positivos descartados:**
- Path traversal via `model_path`: `normalize_arbiter()` usa allowlist explícita — `model_path` se descarta en ingesta.
- Sin auth para herramienta local: era por diseño (localhost-only). Ya no aplica.

### 🔴 Vulnerabilidades pendientes — NO corregidas aún (ver bitácora para detalles)

> **Importante:** estas vulnerabilidades están documentadas pero no corregidas. Leer la entrada de la bitácora del 2026-06-19 (parte 2) antes de abordarlas para no repetir el análisis.

| ID | Descripción corta | Severidad | Archivo(s) |
|---|---|---|---|
| V-PEND-1 | `api_key_override` persiste en `arbiters_config.json` (git). Si se usa, la key queda en el repo. | Media | `scripts/arbiters_io.py` → `normalize_arbiter()` |
| V-PEND-2 | `/api/provider-key` escribe API keys al disco. Sabotaje posible con auth comprometida. | Baja-media | `webapp/app.py` → `api_provider_key()` |
| V-PEND-3 | Sin protección CSRF. Basic Auth en caché del navegador puede ser abusado cross-site. | Media | `webapp/app.py` — todos los endpoints POST/PUT/DELETE |

**V-PEND-1 — solución propuesta:** en `normalize_arbiter()`, si `api_key_override` es no vacío, moverlo a `secrets/secrets.env` y vaciar el campo antes de persistir en JSON.

**V-PEND-2 — solución propuesta:** que `/api/provider-key` solo funcione desde localhost, independientemente de la autenticación global.

**V-PEND-3 — solución propuesta:** migrar de Basic Auth a session-based auth con cookies `SameSite=Strict` (bloquea CSRF por diseño), o añadir Flask-WTF con tokens CSRF.

### 🔜 Pendientes / Mejoras futuras identificadas

- Ejecutar el pipeline completo sobre el dataset y obtener resultados finales para el paper
- Analizar métricas agregadas de acuerdo inter-árbitros (Fleiss/Cohen/Krippendorff)
- Evaluar si agregar soporte para etiquetado con justificaciones en modo producción
- Posible expansión a otras taxonomías o datasets

---

## 17. Repositorio y contacto

- **GitHub**: https://github.com/lbustio/LabelForge
- **Rama principal**: `main`
- **Historial de decisiones detallado**: ver `bitacora.md` (en el repo)
- **Investigación**: INAOE — proyecto GCP `cloud-computing-cybersec-inaoe`

---

*Última actualización: 2026-06-19 — corrección documental: árbitros actualizados (arb3 cambió a openai/gpt-4o), endpoint count corregido a 48, personas sincronizadas con arbiters_config.json, nota de versiones de paquetes por entorno aclarada.*

