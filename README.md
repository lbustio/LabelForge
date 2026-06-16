# LabelForge — Analizador de Principios de Persuasión

Este proyecto es una plataforma interactiva y un pipeline robusto diseñado para etiquetar de manera automática y masiva entradas textuales utilizando taxonomías configurables, incluida la taxonomía de **Principios de Persuasión de Ana Ferreira** (Ingeniería Social). El sistema emplea un enfoque multi-agente por consenso (árbitros con distintos perfiles profesionales/personas) con soporte para múltiples proveedores de Modelos de Lenguaje (LLM).

---

## 🏗️ Arquitectura General del Sistema

El proyecto consta de dos partes principales que interactúan entre sí a través del archivo de configuración central:

1. **Pipeline de Etiquetado (CLI)**: Ejecuta el procesamiento secuencial del dataset, realiza llamadas a las APIs de los LLMs, valida los formatos JSON devueltos, gestiona de forma dinámica los límites de peticiones (Rate Limits), almacena checkpoints para reanudar el proceso en caso de fallo, procesa la cola de entradas fallidas, calcula el consenso mayoritario y genera reportes detallados de **uso de tokens, latencia y métricas por entrada**.
2. **Interfaz de Control (WebApp Flask)**: Proporciona un panel de control interactivo para administrar los árbitros, modificar los principios y las plantillas de prompts, ajustar parámetros de procesamiento en tiempo real, lanzar el etiquetado, visualizar los registros (logs) del sistema en tiempo real y consultar métricas calculadas por el backend. La interfaz no decide resultados ni recalcula métricas de negocio: solo presenta los datos recibidos desde Python.

```mermaid
flowchart TD
    subgraph WebApp [Panel de Control Flask]
        UI[Interfaz Web HTML/JS] <-->|APIs REST / SSE| Server[webapp/app.py]
    end

    subgraph Config [Configuración]
        CfgFile[(scripts/arbiters_config.json)]
    end

    subgraph Pipeline [Pipeline de Etiquetado CLI]
        Runner[scripts/label_persuasion.py]
        Limiter[Dynamic Rate Limiter]
        Checkpoint[Checkpoint Manager]
        FailedQ[Failed Entries Queue]
        Metrics[Token & Latency Tracker]
    end

    subgraph LLM_Providers [Proveedores de LLM]
        Gemini[Google Gemini / Vertex AI]
        Anthropic[Anthropic Claude]
        OpenAI[OpenAI GPT]
        Local[Ollama / OpenAI Compat]
    end

    subgraph Data [Datos]
        InputCSV["data/ (CSV o Excel configurable)"]
        OutputCSV[output/labeled_TIMESTAMP.csv]
        LogFile[logs/run_TIMESTAMP.log]
    end

    Server <-->|Modifica/Lee| CfgFile
    Runner <-->|Lee| CfgFile
    UI -->|Lanza / Monitorea| Runner
    InputCSV --> Runner
    Runner -->|1. LLM requests| Limiter
    Limiter -->|2. Control de Delay| LLM_Providers
    LLM_Providers -->|3. Respuestas JSON| Runner
    Runner -->|4. Mide latencia y tokens| Metrics
    Runner -->|5. Guarda estado| Checkpoint
    Runner -->|5b. Reintenta fallos| FailedQ
    Runner -->|6. Calcula Consenso| Runner
    Runner --> OutputCSV
    Runner --> LogFile
```

---

## 🎯 Principios de Persuasión (Taxonomía de Ana Ferreira)

El sistema analiza y evalúa la presencia (1) o ausencia (0) de cinco tácticas de influencia y persuasión psicológica en cada entrada textual:

*   **`AUTH` (Authority / Autoridad)**: Apela a figuras públicas, expertos, instituciones oficiales o credenciales para otorgar credibilidad o carácter coercitivo al mensaje.
*   **`SP` (Social Proof / Consenso Social)**: Implica que una creencia o comportamiento es compartido por una gran cantidad de personas, explotando la conformidad social y la conducta de rebaño.
*   **`LSD` (Liking, Similarity & Deception / Simpatía, Similitud y Engaño)**: Usa familiaridad, empatía simulada, halagos o encuadres engañosos para rebajar el juicio crítico del lector.
*   **`CRC` (Commitment, Reciprocation & Consistency / Compromiso, Reciprocidad y Coherencia)**: Apela a compromisos pasados, genera deudas morales/obligaciones o presiona al lector a actuar de manera coherente con sus creencias declaradas.
*   **`DIS` (Distraction / Distracción)**: Utiliza urgencia, escasez, miedo a la pérdida, sobrecarga emocional o sensacionalismo para desviar al lector de una evaluación racional y objetiva.

---

## 🌟 Características Clave

*   **Consenso por Mayoría**: Para cada entrada, múltiples árbitros independientes (por ejemplo, 3) evalúan de forma paralela. Se calcula la predicción de consenso mediante votación mayoritaria simple.
*   **Árbitros Expertos con Personas**: Cada árbitro se configura con un rol/persona particular (p. ej., Psicólogo Clínico, Especialista en Ciberseguridad, Investigador Forense) y una instrucción de sistema (`system_prompt`) adaptada para que evalúe el texto desde su perspectiva profesional particular.
*   **Dynamic Rate Limiter (Limitador Dinámico)**: Adapta el delay de peticiones automáticamente basándose en las respuestas del servidor. Si recibe un código `429` (Rate Limit) o un error `5xx`, aplica un multiplicador de *backoff* exponencial; si las llamadas tienen éxito de forma consecutiva, reduce el delay de manera progresiva hasta el límite inferior seguro.
*   **Resiliencia (Checkpoints y Cola de Reintentos)**:
    *   **Puntos de control**: Guarda el progreso cada $N$ entradas. Si la ejecución se detiene, se reanuda desde la última entrada procesada sin perder el trabajo completado.
    *   **Manejo de errores**: Si una entrada falla con un árbitro, se encola en `failed_queue.jsonl`. Al final de la corrida, el sistema reduce la velocidad del rate limiter e intenta procesar de nuevo únicamente las entradas fallidas antes de generar el reporte final.
*   **Métricas de Acuerdo Inter-Árbitros**: Al finalizar cada run, el módulo [`scripts/agreement_metrics.py`](scripts/agreement_metrics.py) calcula automáticamente estadísticas de fiabilidad entre evaluadores: acuerdo observado por pares, **Fleiss Kappa** (multi-árbitro), **Cohen's Kappa** (promedio por pares) y **Krippendorff's Alpha** nominal, tanto de forma global como desglosada por principio. También prepara porcentajes e interpretación del kappa para que la interfaz solo los muestre.
*   **Diagnóstico de Consenso por Entrada**: Para cada entrada se calculan coincidencia entre árbitros, apoyo promedio al consenso, principios unánimes, principios divididos y apoyo por principio. Estas métricas nacen en el pipeline/backend y se guardan en el CSV para auditoría y depuración; no sustituyen las métricas agregadas de acuerdo inter-árbitros que se reportan metodológicamente.
*   **Contabilidad Exhaustiva de Tokens**: Cada llamada al LLM registra los tokens de entrada (prompt), salida (completion) y total. Además, cada entrada guarda agregados `entry_*` con suma de tokens, llamadas contabilizadas y tokens por llamada.
*   **Contabilidad Exhaustiva de Latencia**: El tiempo exacto de cada petición al LLM se mide con `time.perf_counter()` y se almacena en el CSV. Cada entrada incluye tiempo total y promedio; el reporte final incluye latencia promedio, mínima, máxima y total acumulada por árbitro.
*   **Política de Seguridad (`free_only_mode`)**: Bloquea proveedores con riesgo de cargo (Vertex AI, Anthropic, OpenAI) cuando el modo seguro está activo, evitando consumo accidental de tokens de pago durante pruebas.
*   **Dataset Configurable (CSV y Excel)**: El archivo de entrada, la hoja de cálculo (solo Excel) y la columna de texto son configurables desde la webapp o la CLI sin tocar código. Soporta `.csv`, `.xlsx` y `.xls`.
*   **Soporte Multimodelo y Multiproveedor**: Integra los SDKs oficiales de Google GenAI (`google-genai`), Vertex AI (`google-cloud-aiplatform`), Anthropic (`anthropic`) y OpenAI (`openai`), además de APIs locales u open-source compatibles con el protocolo de OpenAI (Ollama, LM Studio, vLLM).
*   **Gestión Segura de Árbitros y Secretos**: Los árbitros pueden exportarse/importarse en archivos `.lfarb` cifrados con contraseña. Las API keys se verifican antes de guardarse en `secrets/secrets.env`, y la lógica de cifrado/secretos está centralizada en módulos Python compartidos por WebApp y CLI.
*   **Preflight de Proveedores**: Antes de iniciar un run, el backend valida credenciales/configuración de cada proveedor y devuelve instrucciones accionables (WebApp, CLI, `secrets/secrets.env`, service account o `gcloud`) sin esperar a que falle una entrada a mitad del procesamiento.
*   **Logs Interactivos en la WebApp**: Utiliza Server-Sent Events (SSE) para transmitir la salida de consola de Python directamente a la pantalla de ejecución web en tiempo real (incluyendo el reporte de tokens y latencia al final).

---

## 📂 Estructura del Proyecto

*   **[`main_label.py`](file:///e:/Code/python/OpenCode/LabelForge/main_label.py)**: Punto de entrada principal para ejecutar el etiquetado a través de la terminal.
*   **[`main_webapp.py`](file:///e:/Code/python/OpenCode/LabelForge/main_webapp.py)**: Punto de entrada para inicializar la aplicación web Flask en modo local.
*   **`scripts/`**:
    *   **[`label_persuasion.py`](file:///e:/Code/python/OpenCode/LabelForge/scripts/label_persuasion.py)**: Contiene la lógica central del pipeline: controladores de API por proveedor, limitador dinámico de peticiones, procesamiento de checkpoints, consenso, **medición de tokens y latencia por llamada y por entrada**, diagnóstico de consenso por entrada y generación del reporte final.
    *   **[`arbiters_config.json`](file:///e:/Code/python/OpenCode/LabelForge/scripts/arbiters_config.json)**: Archivo JSON central que almacena la configuración de los principios, los árbitros, los prompts del sistema y los parámetros del limitador de velocidad.
    *   **[`agreement_metrics.py`](file:///e:/Code/python/OpenCode/LabelForge/scripts/agreement_metrics.py)**: Módulo compartido de cálculo de métricas de acuerdo inter-árbitros (Fleiss Kappa, Cohen's Kappa, Krippendorff's Alpha).
    *   **[`arbiters_io.py`](file:///e:/Code/python/OpenCode/LabelForge/scripts/arbiters_io.py)**: Módulo compartido para exportar/importar árbitros en archivos `.lfarb` cifrados, validar el formato y fusionar/reemplazar configuraciones.
    *   **[`secrets_io.py`](file:///e:/Code/python/OpenCode/LabelForge/scripts/secrets_io.py)**: Módulo compartido para mapear proveedores a variables de entorno y escribir `secrets/secrets.env` de forma controlada.
    *   **[`provider_preflight.py`](file:///e:/Code/python/OpenCode/LabelForge/scripts/provider_preflight.py)**: Módulo compartido para validar credenciales/configuración de proveedores antes de ejecutar el pipeline.
    *   **[`utils.py`](file:///e:/Code/python/OpenCode/LabelForge/scripts/utils.py)**: Helper utilitario para parseo numérico y tipos de datos seguros.
*   **`webapp/`**:
    *   **[`app.py`](file:///e:/Code/python/OpenCode/LabelForge/webapp/app.py)**: Backend Flask de la aplicación web que gestiona los servicios API, el renderizado de plantillas, la ejecución del subproceso CLI de etiquetado y los endpoints de métricas. Lee datos ya calculados por el pipeline y solo calcula respaldos para CSV antiguos.
    *   **`templates/`**:
        *   [`base.html`](file:///e:/Code/python/OpenCode/LabelForge/webapp/templates/base.html): Estructura HTML común y hoja de estilos globales (diseño web oscuro y premium con Bootstrap).
        *   [`index.html`](file:///e:/Code/python/OpenCode/LabelForge/webapp/templates/index.html): Panel de control con estadísticas rápidas (árbitros, principios, salidas generadas, entradas en checkpoint).
        *   [`arbiters.html`](file:///e:/Code/python/OpenCode/LabelForge/webapp/templates/arbiters.html): Panel de gestión de árbitros (CRUD completo, asignación de modelos, tokens y claves API).
        *   [`principles.html`](file:///e:/Code/python/OpenCode/LabelForge/webapp/templates/principles.html): Visualización y edición de los principios de persuasión y la plantilla del prompt del usuario.
        *   [`processing.html`](file:///e:/Code/python/OpenCode/LabelForge/webapp/templates/processing.html): Panel de configuración de parámetros de red, checkpoints y cálculos de tiempos/delays recomendados.
        *   [`run.html`](file:///e:/Code/python/OpenCode/LabelForge/webapp/templates/run.html): Interfaz interactiva de lanzamiento y monitorización del proceso de etiquetado con streaming de logs por SSE, métricas en vivo y resultados. Su JavaScript se limita a interacción visual y formato de valores.
*   **`data/`**: Directorio para los archivos de datos de entrada. El dataset principal (`data/all_datasets_en_es/all_datasets_en_es.csv`) es **privado** (investigación en curso) y está excluido del repositorio mediante `.gitignore`. Se publicará al concluir el estudio.
*   **`output/`**: Carpeta de destino para los datasets etiquetados resultantes (`labeled_YYYYMMDD_HHMMSS.csv`).
*   **`checkpoints/`**: Almacena el estado intermedio de ejecuciones: `checkpoint.json`, `failed_queue.jsonl`, `run_state.json`, `claim_results.jsonl` y `arbiter_results.jsonl`. **Excluido del repositorio** (datos de estado local privados).
*   **`logs/`**: Almacena los registros detallados de cada ejecución del pipeline (`run_YYYYMMDD_HHMMSS.log`), incluyendo el reporte completo de tokens y latencia al final.
*   **`tests/`**: Contiene la suite de pruebas unitarias automáticas ([`test_pipeline.py`](file:///e:/Code/python/OpenCode/LabelForge/tests/test_pipeline.py)) para auditar el correcto funcionamiento de utilidades, métricas y seguridad.

---

## 🛠️ Requisitos e Instalación

### 1. Requisitos de Software
*   Python 3.10 o superior (Recomendado 3.11)
*   Conda / Miniconda (opcional, recomendado para entornos aislados)

### 2. Configuración del Entorno de Conda
Puedes recrear el entorno de desarrollo usando el archivo [`environment.yml`](file:///e:/Code/python/OpenCode/LabelForge/environment.yml):

```bash
conda env create -f environment.yml
conda activate LabelForge
```

Alternativamente, si utilizas un entorno de Python estándar (`venv`), instala las dependencias fijadas en [`requirements.txt`](file:///e:/Code/python/OpenCode/LabelForge/requirements.txt):

```bash
pip install -r requirements.txt
```

### 3. Configuración de Variables de Entorno (`secrets/secrets.env`)
Crea el archivo [`secrets/secrets.env`](file:///e:/Code/python/OpenCode/LabelForge/secrets/secrets.env) dentro de la carpeta local `secrets/`, copiando la plantilla [`secrets.env.example`](file:///e:/Code/python/OpenCode/LabelForge/secrets.env.example), y edítalo para incluir tus credenciales y claves API de los diferentes proveedores que desees utilizar:

```env
# Google Gemini (API Key estándar)
GEMINI_API_KEY=tu_clave_de_gemini_aqui

# Anthropic Claude
ANTHROPIC_API_KEY=tu_clave_de_anthropic_aqui

# OpenAI ChatGPT
OPENAI_API_KEY=tu_clave_de_openai_aqui

# Google Cloud / Vertex AI
GOOGLE_CLOUD_PROJECT=id_del_proyecto_gcp
VERTEX_LOCATION=us-central1

# Servidor web (opcionales — descomenta para cambiar los valores por defecto)
# LABELFORGE_HOST=127.0.0.1   # 0.0.0.0 para acceso desde la red local
# LABELFORGE_PORT=7860         # Puerto libre en macOS, Windows y Linux
```

> **Nota sobre macOS**: el puerto `5000` está ocupado por el servicio AirPlay
> Receiver desde macOS 12 Monterey. LabelForge usa `7860` por defecto para
> evitar ese conflicto. Si necesitas otro puerto, define `LABELFORGE_PORT` en
> `secrets/secrets.env` o usa el argumento `--port` en la línea de comandos.

### 4. Autenticación para Vertex AI (Google Cloud)

Si usas `vertex_gemini` como proveedor, debes autenticarte con Application Default Credentials (ADC). Vertex AI no usa API key directa como Gemini AI Studio, Claude u OpenAI.

Opción sin login interactivo local: usa una service account JSON creada en Google Cloud Console y define la ruta en `secrets/secrets.env`:

```env
GOOGLE_APPLICATION_CREDENTIALS=secrets\service-account.json
GOOGLE_CLOUD_PROJECT=TU_PROJECT_ID
VERTEX_LOCATION=us-central1
```

Opción con Google Cloud CLI: ejecuta **una sola vez** en la máquina donde corre el pipeline:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project TU_PROJECT_ID
```

Esto guarda las credenciales localmente para que el SDK de Vertex AI las use de forma automática sin necesidad de una API Key en `secrets/secrets.env`.

Para proveedores con API key (`gemini`, `anthropic`, `openai`, `openai_compat`), la ruta recomendada es la WebApp: pestaña **Arbitros**, pega la clave en **Clave de API** y pulsa verificar. Si prefieres CLI: `python main_label.py --set-provider-key anthropic` (cambia el proveedor según corresponda). Ollama no usa key, pero debe tener `base_url`, por ejemplo `http://localhost:11434/v1`.

### 5. Ejecución de Pruebas Unitarias

El proyecto cuenta con pruebas automáticas para garantizar la estabilidad y consistencia de las métricas matemáticas y helpers de conversión. Ejecútalas usando:

```bash
python -m unittest tests/test_pipeline.py
```

---

## 🚀 Instrucciones de Uso

El proyecto se puede controlar completamente tanto de forma visual (WebApp) como directa (CLI).

### Opción A: Interfaz Web (Recomendado)

Inicia el servidor Flask ejecutando:

```bash
python main_webapp.py
```

Por defecto, la webapp arrancará en `http://127.0.0.1:7860`.
Puedes especificar argumentos adicionales en consola:
*   `--port`: Cambiar el puerto del servidor (ej. `--port 8080`).
*   `--host`: Configurar el host (ej. `--host 0.0.0.0` para acceso en red local).
*   `--debug`: Ejecutar en modo desarrollo con recarga automática (*hot reloader*).


#### Flujo de trabajo en la Webapp:
1.  **Panel de Inicio**: Observa el estado actual del dataset, los árbitros activos y el último checkpoint.
2.  **Gestionar Árbitros**: Ve a la pestaña **Arbitros** para agregar nuevos perfiles o modificar los modelos de lenguaje utilizados por cada uno (p. ej., cambiar de `gemini-2.5-pro` a `gemini-2.5-flash`). Para proveedores con API key (Gemini AI Studio, Anthropic, OpenAI y OpenAI-compatible), pega la clave en **Clave de API** y pulsa verificar: si la clave lista modelos correctamente, se guarda en `secrets/secrets.env` bajo la variable correspondiente y se refresca la grilla de modelos. La pestaña también permite **Importar** y **Exportar** árbitros en formato `.lfarb` cifrado. Se puede dejar la lista de árbitros vacía; en ese caso la UI muestra una advertencia y no se puede ejecutar etiquetado hasta crear o importar al menos uno.
3.  **Configurar Dataset**: En **Procesamiento**, usa el panel "Dataset de Entrada" para seleccionar el archivo de datos (CSV o Excel) mediante **drag & drop** o haciendo click en la zona o el botón "Seleccionar archivo". Al guardar, `dataset.input_file`, `dataset.sheet` y `dataset.text_column` quedan persistidos en `scripts/arbiters_config.json`. La pestaña muestra una tarjeta **Archivo listo para procesar** con la ruta/hoja/columna que se usarán en la próxima ejecución.
4.  **Configurar Parámetros**: En la misma pestaña **Procesamiento**, selecciona tu nivel de facturación (Free o Paid) para recibir recomendaciones automáticas de delay seguro.
5.  **Ejecutar**: En la pestaña **Ejecutar**, define un **límite total desde el inicio del dataset** (ej. `70` = considerar las primeras 70 filas, no 70 nuevas). Si hay checkpoint con 50 entradas ya persistidas y pones `70`, se procesarán aproximadamente 20 entradas nuevas. La UI muestra esta explicación dinámicamente. Antes de arrancar, el backend ejecuta un preflight de credenciales/proveedores; si algo falta, el run no empieza y el log muestra instrucciones claras.
6.  **Panel de Métricas**: Al finalizar la ejecución, el resumen del resultado guardado se actualiza automáticamente mostrando las estadísticas completas de consumo calculadas por el backend.

---

### Opción B: Ejecución por Terminal (CLI)

Para ejecutar el pipeline directamente desde consola sin usar el servidor web, utiliza el script [`main_label.py`](file:///e:/Code/python/OpenCode/LabelForge/main_label.py):

```bash
# Procesar únicamente las primeras 10 entradas para prueba
python main_label.py --limit 10

# Procesar todo el dataset completo
python main_label.py

# Utilizar un archivo de configuración JSON personalizado y procesar 50 entradas
python main_label.py --config scripts/mi_configuracion.json --limit 50

# Especificar un archivo CSV diferente y la columna de texto
python main_label.py --input data/otro_dataset.csv --column texto

# Archivo Excel: indicar sheet y columna
python main_label.py --input data/dataset.xlsx --sheet "Datos" --column "Noticia"

# Verificar y guardar una API key en secrets/secrets.env
python main_label.py --set-provider-key anthropic
python main_label.py --set-provider-key openai --api-key "sk-..."

# Exportar/importar árbitros cifrados
python main_label.py --export-arbiters backup.lfarb
python main_label.py --import-arbiters backup.lfarb
python main_label.py --import-arbiters backup.lfarb --replace-arbiters
```

#### Argumentos soportados en CLI:
*   `--config`: Ruta al JSON de configuración (default: `scripts/arbiters_config.json`).
*   `--input`: Ruta al archivo de entrada — CSV (`.csv`) o Excel (`.xlsx`/`.xls`). Si se omite, se usa el valor de `dataset.input_file` en el config.
*   `--sheet`: Nombre o índice de la hoja de cálculo (solo para Excel). Si se omite, se usa la primera hoja.
*   `--column`: Nombre de la columna que contiene el texto a etiquetar. Si se omite, se usa `dataset.text_column` del config.
*   `--limit`: Límite total de filas consideradas desde el inicio del dataset. Con checkpoint, las filas ya finalizadas dentro de ese rango se saltan; no es una cantidad adicional.
*   `--set-provider-key`: Verifica y guarda una API key para `gemini`, `anthropic`, `openai` u `openai_compat`.
*   `--api-key`: API key para `--set-provider-key`. Si se omite, se solicita de forma interactiva.
*   `--api-key-env`: Variable destino en `secrets/secrets.env`; si se omite, se usa la variable estándar del proveedor.
*   `--base-url`: Dirección base para verificar proveedores `openai_compat`.
*   `--export-arbiters`: Exporta la lista de árbitros a un archivo `.lfarb` cifrado y sale.
*   `--import-arbiters`: Importa árbitros desde un archivo `.lfarb` cifrado y sale.
*   `--replace-arbiters`: Al importar, reemplaza toda la lista actual en vez de fusionar por ID.
*   `--arbiters-password` / `--arbiters-password-file`: Contraseña para import/export cifrado. Si se omite, se solicita de forma interactiva.

---

## 📊 Contabilidad de Tokens y Latencia

El sistema registra de forma exhaustiva el consumo de tokens y el tiempo de respuesta de cada llamada a los LLMs. Los cálculos se hacen en backend y se almacenan en **tres lugares simultáneamente**:

### 1. En el CSV de salida (por entrada y por árbitro)

Cada fila del CSV etiquetado incluye las siguientes columnas adicionales para cada árbitro (`[arb_id]` es el identificador del árbitro, p. ej. `arb1`, `arb2`, `arb3`):

| Columna | Descripción |
|---|---|
| `prompt_tokens_[arb_id]` | Tokens de entrada enviados en el prompt a ese árbitro |
| `completion_tokens_[arb_id]` | Tokens generados en la respuesta de ese árbitro |
| `total_tokens_[arb_id]` | Suma de prompt + completion tokens para ese árbitro |
| `latency_s_[arb_id]` | Tiempo de respuesta de la llamada al LLM en segundos |
| `entry_prompt_tokens` | Suma de tokens de entrada de todos los árbitros en esa entrada |
| `entry_completion_tokens` | Suma de tokens de salida de todos los árbitros en esa entrada |
| `entry_total_tokens` | Total de tokens de la entrada completa |
| `entry_latency_s` | Tiempo total medido de la entrada completa |
| `entry_latency_avg_s` | Tiempo promedio por llamada de árbitro en esa entrada |
| `entry_arbiter_calls` | Número de llamadas de árbitro contabilizadas |
| `entry_tokens_per_call` | Promedio de tokens por llamada de árbitro |

> Los campos se marcan con `-1` si la entrada falló definitivamente en ese árbitro.

### 2. En el archivo de log (reporte final al terminar el run)

Al finalizar cada ejecución, el reporte aparece en consola **y** se guarda automáticamente en `logs/run_YYYYMMDD_HHMMSS.log`:

```
=== REPORTE DE CONSUMO DE TOKENS Y LATENCIA ===
  Arbitro arb1 (Psicologo Clinico):
    Tokens Entrada (Prompt)     : 2859
    Tokens Salida (Completion)  : 1977
    Tokens Totales              : 4836
    Latencia Promedio           : 47.823s
    Latencia Min / Max          : 38.102s / 61.445s
    Latencia Total Acumulada    : 478.230s (10 llamadas)
  Arbitro arb2 (Especialista Ciberseguridad):
    ...
  CONSUMO GLOBAL TOTAL DEL RUN  : 14685 tokens
```

### 3. En la WebApp (panel visual)

La pestaña **Ejecutar** incluye paneles de métricas en vivo y de resumen del resultado guardado. Ambos muestran datos calculados por Python. El panel muestra:

*   **KPIs globales**: Tokens totales del run, número de entradas etiquetadas y promedio de tokens por entrada, entregados ya calculados por el backend.
*   **Tabla por árbitro**: Tokens de entrada, salida y total; número de llamadas; latencia promedio, mínima/máxima y latencia total acumulada.

El panel también se carga al abrir la página si ya existe un CSV previo, gracias al endpoint `GET /api/run/stats`.

El visor de logs colorea las líneas asociadas a árbitros con la misma secuencia visual usada en la pestaña **Arbitros** (`arb1`, `arb2`, etc.). Esto ayuda a identificar rápidamente qué árbitro produjo una respuesta, warning o error.

Antes de lanzar el subproceso, `/api/run/start` ejecuta `scripts/provider_preflight.py`. Este preflight bloquea el inicio si detecta credenciales o configuración incompletas:

*   `vertex_gemini`: valida proyecto y credenciales ADC. Si falta ADC, explica si `gcloud` está instalado o propone service account JSON + `GOOGLE_APPLICATION_CREDENTIALS`.
*   `gemini`, `anthropic`, `openai`: valida API key por variable/env y sugiere WebApp, CLI `--set-provider-key` o edición de `secrets/secrets.env`.
*   `openai_compat` y `ollama`: valida `base_url` y muestra ejemplos locales como `http://localhost:11434/v1`.

---

## 📈 Resultados y Reportes

Cuando el proceso finaliza de forma exitosa (o es detenido y concluido), los resultados se guardan en la carpeta `output/` con el nombre `labeled_YYYYMMDD_HHMMSS.csv`.

El CSV resultante conservará todas las columnas originales del dataset e incorporará las siguientes columnas adicionales para cada principio de persuasión (`P = AUTH, SP, LSD, CRC, DIS`) y cada árbitro:

| Columna | Descripción |
|---|---|
| `[P]_[arbiter_id]` | Evaluación individual de ese árbitro (1 = presente, 0 = ausente) |
| `[P]_[arbiter_id]_why` | Justificación breve en lenguaje natural aportada por el árbitro |
| `[P]_consensus` | Evaluación por mayoría consensuada de todos los árbitros (1 ó 0) |
| `prompt_tokens_[arbiter_id]` | Tokens de entrada consumidos por ese árbitro en esta entrada |
| `completion_tokens_[arbiter_id]` | Tokens de salida generados por ese árbitro en esta entrada |
| `total_tokens_[arbiter_id]` | Total de tokens consumidos por ese árbitro en esta entrada |
| `latency_s_[arbiter_id]` | Latencia de la llamada al LLM de ese árbitro en segundos |
| `entry_total_tokens` | Tokens totales de la entrada, sumando todos los árbitros |
| `entry_latency_s` | Tiempo total de la entrada, sumando llamadas medidas |
| `entry_consensus_observed_agreement` | Coincidencia por pares para esa entrada; útil para auditoría, no como métrica principal del paper |
| `entry_consensus_observed_agreement_percent` | Coincidencia por pares expresada como porcentaje |
| `entry_consensus_mean_support_ratio` | Apoyo promedio al consenso por principio |
| `entry_consensus_unanimous_principles` | Número de principios con voto unánime |
| `entry_consensus_split_principles` | Número de principios con desacuerdo entre árbitros |

Si una entrada falla permanentemente después de la cola de reintentos, el consenso se marcará con `-1` y la justificación se registrará como `FAILED`.

---

## 🔌 API Endpoints de la WebApp

### Páginas

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/` | Panel de inicio (dashboard) |
| `GET` | `/arbiters` | Gestión de árbitros |
| `GET` | `/principles` | Principios y plantilla del prompt |
| `GET` | `/processing` | Parámetros de procesamiento |
| `GET` | `/run` | Interfaz de ejecución y monitoreo |

### Ejecución y monitoreo

| Método | Endpoint | Descripción |
|---|---|---|
| `POST` | `/api/run/start` | Inicia el pipeline de etiquetado |
| `POST` | `/api/run/stop` | Detiene el proceso en curso |
| `GET` | `/api/run/status` | Estado actual del proceso (running / idle) |
| `GET` | `/api/run/current-stats` | Métricas en vivo del run activo (tokens, latencia, progreso) |
| `GET` | `/api/run/logs` | Stream SSE de logs en tiempo real |
| `GET` | `/api/run/stats` | Estadísticas de tokens, latencia, diagnóstico de consenso por entrada y métricas agregadas de acuerdo del último CSV |

### Árbitros

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/arbiters` | Lista todos los árbitros configurados |
| `POST` | `/api/arbiters` | Crea un nuevo árbitro |
| `GET` | `/api/arbiters/<arb_id>` | Obtiene un árbitro por ID |
| `PUT` | `/api/arbiters/<arb_id>` | Actualiza un árbitro existente |
| `DELETE` | `/api/arbiters/<arb_id>` | Elimina un árbitro; puede dejar la lista vacía |
| `POST` | `/api/arbiters/export` | Exporta los árbitros a un archivo `.lfarb` cifrado |
| `POST` | `/api/arbiters/import` | Importa árbitros desde un `.lfarb` cifrado (merge o replace) |

### Principios y configuración

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/principles` | Lista los principios configurados |
| `PUT` | `/api/principles` | Actualiza la lista de principios |
| `GET` | `/api/prompt-template` | Obtiene la plantilla de prompt y configuración de justificaciones |
| `PUT` | `/api/prompt-template` | Actualiza la plantilla de prompt |
| `GET` | `/api/processing` | Obtiene los parámetros de procesamiento |
| `PUT` | `/api/processing` | Actualiza los parámetros de procesamiento |
| `GET` | `/api/config` | Configuración completa (árbitros + principios + parámetros) |

### Modelos y rate limits

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/models` | Lista modelos disponibles para un proveedor (`?provider=gemini\|anthropic\|openai\|ollama`) |
| `POST` | `/api/provider-key` | Verifica una API key, la guarda en `secrets/secrets.env` y devuelve modelos disponibles |
| `GET` | `/api/rate-limits` | Límites de velocidad y delay mínimo recomendado para un proveedor/modelo (`provider`, `model`, `tier`, `arbiters`) |
| `GET` | `/api/rate-limits/summary` | Resumen de rate limits y delay global para todos los árbitros configurados |

Los perfiles de rate limit viven en `scripts/rate_limits.py` y se consumen desde el backend. Para `gemini` se usan límites publicados de Gemini API/AI Studio cuando el modelo coincide con la tabla conocida. Para `vertex_gemini`, `anthropic`, `openai`, `openai_compat` y `ollama`, la UI muestra perfiles por modelo o familia como fallback conservador y los marca como `account_dependent`, porque las cuotas reales dependen de cuenta, proyecto, región, tier, organización o hardware local.

### Dataset de entrada

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/dataset/config` | Devuelve la configuración actual del dataset (archivo, sheet, columna) |
| `PUT` | `/api/dataset/config` | Guarda la configuración del dataset en `arbiters_config.json` |
| `POST` | `/api/dataset/inspect` | Inspecciona un CSV o Excel y devuelve sheets y columnas disponibles |
| `POST` | `/api/dataset/upload` | Sube un CSV o Excel a `data/uploads/` e inspecciona sus columnas |

### Outputs y checkpoint

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/outputs` | Lista los CSVs generados en `output/` con metadata |
| `GET` | `/outputs/<name>` | Descarga un CSV de salida por nombre |
| `DELETE` | `/api/outputs/<name>` | Elimina un CSV de salida |
| `GET` | `/api/checkpoint/status` | Estado del checkpoint actual y del run pausado |
| `POST` | `/api/checkpoint/clear` | Limpia el checkpoint y todos los archivos de estado |
| `GET` | `/api/failed-queue` | Cola de entradas fallidas pendientes de reintento |

---

## ⚙️ Detalles Técnicos Relevantes

### Vertex AI — Configuración de `max_output_tokens`

Los modelos de razonamiento como `gemini-2.5-pro` generan tokens de "pensamiento" (thinking tokens) que **se descuentan del límite de `max_output_tokens`**. Por esta razón, la configuración usa un valor de `2048` tokens (default anterior `600`). Si se reduce demasiado, las respuestas JSON se truncan y causan `JSONDecodeError`.

La configuración de generación se pasa como **diccionario plano** (no `GenerationConfig`) para evitar un bug del SDK de Vertex AI que causaba truncamiento silencioso. En `gemini-2.5-flash` se desactiva explícitamente el thinking (`thinking_budget: 0`); en `gemini-2.5-pro` no se fuerza porque Vertex AI devuelve error 400.

### Sistema de Colores para Métricas de Acuerdo

Todas las métricas de acuerdo inter-árbitros se muestran en la webapp con una escala de colores unificada de 4 niveles, aplicada tanto en el backend (campos `*_tone`) como en el frontend:

| Nivel | Kappa | Porcentaje | Color Bootstrap |
|---|---|---|---|
| Alto | ≥ 0.8 | ≥ 80% | `success` (verde) |
| Medio-alto | ≥ 0.6 | ≥ 60% | `info` (azul) |
| Bajo | ≥ 0.4 | ≥ 40% | `warning` (amarillo) |
| Muy bajo | < 0.4 | < 40% | `danger` (rojo) |

Cada métrica expone su propio campo `*_tone` desde `compute_agreement_metrics()`: `observed_agreement_tone`, `fleiss_kappa_tone`, `pairwise_cohen_kappa_avg_tone`, `krippendorff_alpha_nominal_tone`, `positive_rate_tone`. En el panel global de `/run` se muestran dos badges separados: **acuerdo observado** (con su color) y **Fleiss Kappa** (con su interpretación y color), resolviendo visualmente la paradoja de prevalencia (kappa bajo con acuerdo observado alto).

### Frontera backend/frontend

La regla actual es que **todo cálculo de procesamiento y métricas vive en Python**:

- `scripts/label_persuasion.py` calcula consenso, diagnóstico de consenso por entrada, tokens, latencia y columnas `entry_*`.
- `scripts/agreement_metrics.py` calcula métricas globales y por principio, porcentajes e interpretación del kappa.
- `scripts/arbiters_io.py` centraliza el formato `.lfarb`, cifrado AES-GCM, validación y merge/reemplazo de árbitros.
- `scripts/secrets_io.py` centraliza el mapeo proveedor→variable (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) y la escritura de `secrets/secrets.env`.
- `webapp/app.py` expone esos datos por API y solo calcula respaldos para CSV antiguos.
- Las plantillas en `webapp/templates/` solo renderizan datos, formatean valores y manejan interacción visual. No deben implementar cifrado, escritura de secretos ni reglas de negocio.

### Gestión de secretos y exportación de árbitros

Las API keys reales viven en `secrets/secrets.env`, no en `scripts/arbiters_config.json`.
El archivo de configuración de árbitros solo debe guardar `provider.api_key_env`.
Para configuraciones nuevas, `provider.api_key_override` debe permanecer vacío.

| Proveedor | Variable usada |
|---|---|
| `gemini` | `GEMINI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `openai_compat` | `OPENAI_API_KEY` |
| `vertex_gemini` | ADC/Google Cloud, sin API key directa |
| `ollama` | Sin API key por defecto |

Los árbitros se pueden exportar como `.lfarb`, un JSON técnico que contiene un
`ciphertext` cifrado con contraseña. El contenido sensible (prompts, modelos y
posibles secretos heredados) no queda visible en claro.

### Política de Seguridad (`free_only_mode`)

Estado actual de `scripts/arbiters_config.json`: `safety.free_only_mode = false` para permitir pruebas con Vertex/Claude sin bloqueos de política. Si se vuelve a poner en `true`, el pipeline bloquea en el arranque cualquier árbitro cuyo proveedor no esté en `allowed_provider_types`. Esto evita costos accidentales, pero permite declarar excepciones explícitas cuando quieres usar un proveedor pagado como Claude.

| Permitidos en la configuración actual | Bloqueados salvo autorización explícita |
|---|---|
| `gemini` (AI Studio) | `openai` |
| `vertex_gemini` | `openai_compat` remoto |
| `anthropic` | otros proveedores no listados |
| `ollama` | |
| `openai_compat` local (`127.0.0.1`, `localhost`) | |

Para modo seguro, cambia `free_only_mode` a `true` y revisa `allowed_provider_types`. Para pruebas amplias, puede mantenerse en `false`; el preflight de credenciales seguirá activo.

### Módulo de Utilidades Compartidas (`scripts/utils.py`)

Contiene las funciones de conversión numérica segura usadas por todos los módulos del proyecto:
- `safe_int(v)`: convierte a entero, devuelve `0` si no es posible.
- `safe_float(v)`: convierte a float, devuelve `0.0` si no es posible.
- `safe_binary_int(v)`: convierte a `0` o `1` (útil para etiquetas binarias leídas de CSV como strings).
- `number(v, default)`: conversión numérica genérica con fallback configurable.

### Módulo de Acuerdo Inter-Árbitros (`scripts/agreement_metrics.py`)

Módulo compartido entre el pipeline CLI y la WebApp que centraliza el cálculo de métricas de fiabilidad:
- **Fleiss Kappa**: acuerdo multi-árbitro con múltiples categorías.
- **Cohen's Kappa (promedio por pares)**: comparación entre cada par de árbitros.
- **Krippendorff's Alpha nominal**: tolera datos faltantes.
- **Acuerdo observado promedio** y **tasa positiva** por principio.

Las métricas se calculan tanto de forma global como desglosadas por principio y se exponen en `/api/run/stats`. El módulo también entrega porcentajes, interpretación textual del kappa y campos `*_tone` (success/info/warning/danger) para que la interfaz pueda colorear cada métrica sin lógica propia.

### Configuración del Servidor Web

Jerarquía de precedencia para host y puerto del servidor Flask:

```
--host / --port (CLI)  >  LABELFORGE_HOST / LABELFORGE_PORT (env)  >  127.0.0.1:7860 (default)
```

- **Puerto por defecto `7860`**: libre en macOS (donde el `5000` está ocupado por AirPlay Receiver desde Monterey), Windows y Linux.
- **Las variables de entorno** pueden definirse en `secrets/secrets.env` sin necesidad de tocar el código fuente.

### Configuración del Dataset de Entrada

El archivo de entrada y la columna de texto son configurables desde el config o la webapp sin necesidad de tocar código:

```json
"dataset": {
  "input_file": "data/mi_dataset.csv",
  "sheet": "Hoja1",
  "text_column": "texto"
}
```

| Campo | Descripción |
|---|---|
| `input_file` | Ruta relativa al root del proyecto o absoluta. Soporta `.csv`, `.xlsx`, `.xls`. |
| `sheet` | Nombre o índice (0-based) de la hoja Excel. Vacío = primera hoja. Ignorado en CSV. |
| `text_column` | Nombre de la columna que contiene el texto a etiquetar. Debe existir en el archivo. |

Jerarquía de configuración: `CLI args > config > defaults`.

### Logging Dual (Consola + Fichero)

El logger de Python usa `logging.basicConfig` con dos handlers simultáneos:
- `FileHandler` → `logs/run_YYYYMMDD_HHMMSS.log`
- `StreamHandler` → stdout (capturado por el SSE de la webapp)

Esto garantiza que **toda la salida visible en consola**, incluyendo el reporte final de tokens y latencia, quede también persistida en el archivo de log.

