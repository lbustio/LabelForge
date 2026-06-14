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
├── main_label.py          → Entrypoint CLI (llama a scripts/label_persuasion.py)
├── main_webapp.py         → Entrypoint webapp Flask (con --host, --port, --debug)
├── scripts/
│   ├── label_persuasion.py    → Pipeline principal: LLM calls, rate limiter, checkpoints, consenso
│   ├── arbiters_config.json   → Configuración central: árbitros, principios, prompts, parámetros
│   ├── agreement_metrics.py   → Métricas de acuerdo inter-árbitros (compartido CLI + webapp)
│   └── utils.py               → Helpers numéricos compartidos (safe_int, safe_float, etc.)
├── webapp/
│   ├── app.py                 → Backend Flask: endpoints REST + SSE
│   └── templates/
│       ├── base.html          → Layout global con sistema de colores semántico
│       ├── index.html         → Dashboard (árbitros, outputs, estado)
│       ├── arbiters.html      → CRUD de árbitros
│       ├── principles.html    → Edición de principios y preview del prompt
│       ├── processing.html    → Parámetros de procesamiento y rate limits
│       └── run.html           → Ejecución, logs SSE, métricas en vivo, resultados
├── tests/
│   └── test_pipeline.py       → 9 pruebas unitarias (utils, métricas, seguridad)
├── data/                      → ⚠️ PRIVADO — excluido del repo (.gitignore)
├── output/                    → CSVs etiquetados generados (no en repo)
├── checkpoints/               → ⚠️ PRIVADO — estado de ejecuciones (excluido del repo)
├── logs/                      → Logs de ejecuciones (excluidos del repo)
├── secrets.env                → Variables de entorno locales (NUNCA subir a repo)
├── secrets.env.example        → Plantilla pública de variables de entorno
├── requirements.txt           → 7 dependencias directas (incluye openpyxl)
├── environment.yml            → Entorno Conda
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

Los tres árbitros actuales usan **Vertex AI** con autenticación ADC (no API key):

| ID | Nombre | Persona | Modelo | Proveedor |
|---|---|---|---|---|
| `arb1` | Psicólogo Clínico | Dr. Viviana Inés Fuentes | `gemini-2.5-pro` | `vertex_gemini` |
| `arb2` | Especialista Ciberseguridad | MSc. Carlos Doce | `gemini-2.5-flash` | `vertex_gemini` |
| `arb3` | Investigador Forense | Detective Rodrigo Santana | `gemini-2.5-pro` | `vertex_gemini` |

**Proyecto GCP**: `cloud-computing-cybersec-inaoe`  
**Región Vertex**: `us-central1`  
**Temperatura**: `0.1` (baja, para clasificación determinista)  
**max_output_tokens**: `2048` (el JSON binario de salida es muy corto; margen para thinking tokens en gemini-2.5)

> ⚠️ Para que los árbitros Vertex funcionen, se necesita autenticación ADC:
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
    "type": "vertex_gemini | gemini | anthropic | openai | ollama | openai_compat",
    "model": "gemini-2.5-pro",
    "api_key_env": "GEMINI_API_KEY",
    "api_key_override": "",
    "base_url": "",
    "vertex_project": "mi-proyecto-gcp",
    "vertex_location": "us-central1"
  },
  "generation_config": { "temperature": 0.1, "max_output_tokens": 2048 },
  "system_prompt": "..."
}
```

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
| `free_only_mode` | `true` | Bloquea proveedores de pago (Anthropic, OpenAI) |
| `allowed_provider_types` | `["gemini","vertex_gemini","ollama","openai_compat"]` | Lista blanca de proveedores |
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
--host/--port (CLI) > LABELFORGE_HOST/LABELFORGE_PORT (secrets.env) > 127.0.0.1:7860
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

### Frontera backend/frontend
La regla actual del proyecto es: **todo cálculo de procesamiento y métricas vive en
Python; la interfaz solo presenta datos**.

- `scripts/label_persuasion.py` calcula consenso, diagnóstico de consenso por entrada,
  tokens, latencia, llamadas por árbitro y columnas `entry_*`.
- `scripts/agreement_metrics.py` calcula métricas globales y por principio
  (Fleiss/Cohen/Krippendorff/acuerdo observado/tasa positiva), porcentajes e
  interpretación del kappa.
- `webapp/app.py` adapta CSV/eventos a JSON para la interfaz y mantiene compatibilidad
  con CSV anteriores calculando faltantes en backend.
- `webapp/templates/*.html` no debe decidir resultados ni métricas: solo renderiza,
  formatea números/segundos/porcentajes y maneja interacción visual.

---

## 10. Variables de entorno (`secrets.env`)

```env
# API Keys de LLM
GEMINI_API_KEY=...           # Para proveedor "gemini" (AI Studio)
ANTHROPIC_API_KEY=...        # Para proveedor "anthropic"
OPENAI_API_KEY=...           # Para proveedor "openai"

# Vertex AI (no requiere API key — usa ADC)
GOOGLE_CLOUD_PROJECT=cloud-computing-cybersec-inaoe
VERTEX_LOCATION=us-central1

# Servidor web (opcionales)
# LABELFORGE_HOST=127.0.0.1
# LABELFORGE_PORT=7860
```

**El archivo `secrets.env` NUNCA va al repositorio.** Solo existe `secrets.env.example`
(plantilla pública) versionado en el repo.

---

## 11. Instalación desde cero

```bash
# 1. Clonar el repositorio
git clone https://github.com/lbustio/LabelForge.git
cd LabelForge

# 2. Crear entorno (opción A: Conda — recomendado)
conda env create -f environment.yml
conda activate LabelForge

# 2. Crear entorno (opción B: pip)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp secrets.env.example secrets.env
# Editar secrets.env con las API keys correspondientes

# 4. Autenticar Vertex AI (si se usan árbitros vertex_gemini)
gcloud auth application-default login
gcloud auth application-default set-quota-project cloud-computing-cybersec-inaoe

# 5. Verificar instalación
python -m unittest tests/test_pipeline.py

# 6. Arrancar la webapp
python main_webapp.py
# Abrir http://127.0.0.1:7860 en el navegador

# O ejecutar el pipeline CLI directamente
python main_label.py --limit 10
```

---

## 12. Dependencias directas

```
google-genai==2.8.0             # Gemini AI Studio
google-cloud-aiplatform==1.157.0  # Vertex AI
anthropic==0.109.1              # Claude
openai==2.41.1                  # GPT + endpoints compatibles (Ollama, etc.)
flask==3.1.3                    # Webapp
python-dotenv==1.2.2            # Carga de secrets.env
```

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
| `secrets.env` | API keys y credenciales |

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
API key directa. El `free_only_mode` debe tener `vertex_gemini` en la lista blanca
`allowed_provider_types` para no bloquearse a sí mismo.

### Por qué el puerto 7860
El puerto 5000 está ocupado por AirPlay Receiver en macOS 12+ Monterey. El 7860 es
libre en macOS, Windows y Linux, y es reconocido en el ecosistema ML/AI (Gradio,
HuggingFace Spaces). No requiere privilegios de root.

### Por qué dos entrypoints (main_label.py y main_webapp.py)
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
- Webapp Flask con 38 endpoints, 5 páginas y streaming SSE
- 3 árbitros configurados con Vertex AI (gemini-2.5-pro y gemini-2.5-flash)
- JSONDecodeError solucionado: `max_output_tokens` 256→2048 y thinking desactivado
  en gemini-2.5-flash (gemini-2.5-pro no soporta `thinking_budget=0`)
- Controles de ejecución deshabilitados durante proceso activo en `/run`
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

### 🔜 Pendientes / Mejoras futuras identificadas
- Ejecutar el pipeline completo sobre el dataset y obtener resultados finales
- Analizar métricas agregadas de acuerdo inter-árbitros para el paper
- Evaluar si agregar soporte para etiquetado con justificaciones en modo producción
- Posible expansión a otras taxonomías o datasets

---

## 17. Repositorio y contacto

- **GitHub**: https://github.com/lbustio/LabelForge
- **Rama principal**: `main`
- **Historial de decisiones detallado**: ver `bitacora.md` (en el repo)
- **Investigación**: INAOE — proyecto GCP `cloud-computing-cybersec-inaoe`

---

*Última actualización: 2026-06-13 — métricas por entrada, frontera backend/frontend, colores unificados y badges separados para acuerdo observado + Fleiss Kappa.*
