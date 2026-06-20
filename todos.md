# LabelForge — Pendientes

## ✅ Completado (2026-06-19): soporte de modelos locales GGUF + setup de hardware

Implementación completa. Detalles en `bitacora.md` (entrada 2026-06-19 implementación)
y `context.md` (secciones 3, 11, 11b, 12, 16 actualizadas).

---

### Paso 1 — `scripts/hw_detection.py` (módulo nuevo)

Detección multiplataforma de hardware. Usado por `setup_env.py` en tiempo de setup
y por `local_models.py` en runtime para validar lo guardado en `hw_profile.json`.

- [x] Detectar OS: `platform.system()` → `"Windows"` / `"Linux"` / `"Darwin"`
- [x] Detectar CPU: vendor (Intel/AMD/Apple), modelo, núcleos físicos, soporte AVX2/AVX512
- [x] Detectar RAM total: `psutil.virtual_memory().total`
- [x] Detectar NPU:
  - Windows: buscar "Neural" / "AI Boost" en WMI (`wmi` o `subprocess` con `wmic`)
  - Linux: buscar en `/sys/bus/pci/devices/` o `lspci`
- [x] Detectar GPU:
  - NVIDIA: `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader`
  - AMD: `rocm-smi --showmeminfo vram` (Linux) / registry (Windows)
  - Intel Arc: WMI `igfx` (Windows) / `lspci | grep -i intel` (Linux)
  - Apple Silicon: `sysctl hw.optional.arm64`
- [x] Detectar VRAM (discreta vs compartida):
  - Shared memory flag para Intel Arc / Apple Silicon
- [x] Retornar dict estructurado compatible con `hw_profile.json`

---

### Paso 2 — `setup_env.py` (script nuevo en raíz)

Script de setup único. Se ejecuta UNA VEZ por máquina antes de usar la app.

**Logging (DEBE ser idéntico al del pipeline):**
- Formato: `%(asctime)s [%(levelname)s] %(message)s`
- Archivo: `logs/setup_YYYYMMDD_HHMMSS.log`
- Dual: FileHandler + StreamHandler
- Loggers silenciados al WARNING: `pip`, `urllib3`, `httpx`, `httpcore`
- Secciones con `=== NOMBRE ===`
- Salida de `pip install` capturada línea a línea → `[INFO]`; errores → `[ERROR]`

**Flujo:**
- [x] Verificar Python >= 3.10
- [x] Verificar entorno virtual activo (venv o conda); si no → abort con mensaje claro
- [x] Llamar a `hw_detection.py` → inventario completo
- [x] Logear todo el inventario
- [x] Decidir backends en prioridad NPU → GPU → CPU:
  - NPU Intel AI Boost → `openvino`
  - GPU NVIDIA → CUDA (prebuilt wheel)
  - GPU AMD Linux → ROCm (`CMAKE_ARGS="-DGGML_HIPBLAS=on"`)
  - GPU Intel Arc → Vulkan (prebuilt wheel)
  - GPU AMD macOS → Vulkan (MoltenVK, prebuilt wheel)
  - Apple Silicon → Metal (`CMAKE_ARGS="-DGGML_METAL=on"`)
  - Sin GPU → CPU (wheel estándar)
- [x] Instalar `requirements.txt` (base)
- [x] Instalar backend NPU si aplica (`openvino`, `optimum[openvino]`, `nncf`)
- [x] Instalar `llama-cpp-python` con el backend GPU correcto (o CPU si no hay GPU)
- [x] Instalar `huggingface_hub`, `psutil` (ya en requirements.txt, verificar)
- [x] Verificar imports: `import llama_cpp`, `llama_cpp.llama_supports_gpu_offload()`
- [x] Verificar OpenVINO si se instaló: `import openvino`
- [x] Crear `models/` si no existe
- [x] Escribir `models/hw_profile.json` con resultado completo
- [x] Imprimir resumen: backends activos, ruta del log, instrucción de siguiente paso
- [x] Flag `--force` para reinstalar aunque ya esté hecho
- [x] Salir con código 0 si OK, 1 si error

---

### Paso 3 — `scripts/local_models.py` (módulo nuevo)

- [x] Leer `models/hw_profile.json` → parámetros de runtime
- [x] Listar GGUFs en `models/` (desde `models/models.json` o glob `*.gguf`)
- [x] Leer metadata GGUF (número de capas) para cálculo de `n_gpu_layers` parcial:
  - Si `model_size < 0.9 * vram_disponible` → `n_gpu_layers = -1` (todo en GPU)
  - Si `model_size > vram_disponible` → `n_gpu_layers = int(n_layers * vram / model_size)`
  - VRAM compartida (Intel Arc, Apple) → usar factor conservador 0.75 de RAM disponible
  - Sin GPU → `n_gpu_layers = 0`
- [x] Función `get_optimal_params(model_path)` → `{n_gpu_layers, n_threads, n_batch}`
- [x] Cache de modelo cargado: dict global `_LLAMA_MODEL_CACHE[(model_path, n_ctx)] → Llama`
- [x] Catálogo de modelos recomendados por tier de hardware (tabla en bitácora)
- [x] Función `recommended_models(hw_profile)` → lista ordenada de modelos sugeridos
- [x] Descarga de modelo: `huggingface_hub.hf_hub_download(repo_id, filename, local_dir="models/")`
  - Verificar integridad (sha256) después de descargar
  - No dejar archivo corrupto si descarga falla (eliminar parcial)
- [x] Actualizar `models/models.json` tras cada descarga

---

### Paso 4 — `scripts/label_persuasion.py` (modificar)

- [x] `_call_local(arbiter, prompt_user)` — dispatcher que lee `hw_profile.json`:
  - Si `best_backend == "npu"` → `_call_openvino()`
  - Else → `_call_llama_cpp()`
- [x] `_call_llama_cpp(arbiter, prompt_user)`:
  - Import opcional: `try: from llama_cpp import Llama except ImportError: raise RuntimeError(...)`
  - Obtener `optimal_params` desde `local_models.get_optimal_params(model_path)`
  - Cache vía `_LLAMA_MODEL_CACHE`
  - `llama.create_chat_completion(messages, response_format={"type":"json_object"}, ...)`
  - Retornar `(text, usage_dict)`
- [x] `_call_openvino(arbiter, prompt_user)`:
  - Import opcional: `try: from optimum.intel import OVModelForCausalLM ...`
  - Cargar modelo en formato OpenVINO IR desde cache
  - Retornar `(text, usage_dict)`
- [x] Añadir `"llama_cpp": _call_local` a `PROVIDER_CALLERS`
- [x] El provider `llama_cpp` no aplica rate limiter delay (modelo local, sin límites de API)

---

### Paso 5 — `scripts/provider_preflight.py` (modificar)

- [x] Caso `llama_cpp`: verificar que `llama-cpp-python` importa sin error
- [x] Verificar que `provider["model_path"]` existe como archivo y tiene extensión `.gguf`
- [x] Verificar que `models/hw_profile.json` existe y `setup_ok == true`
- [x] Mensaje accionable si falta: indicar `python setup_env.py` y directorio `models/`

---

### Paso 6 — `scripts/rate_limits.py` (modificar)

- [x] Retornar `{"limit_kind": "local", "rpm": None, "rpd": None, "tpm": None, "alert_level": "info", "alert_msg": "Modelo local — sin límites de API", "min_delay_s": 0}` para `llama_cpp`
- [x] La webapp oculta la sección de rate limits cuando `limit_kind == "local"`

---

### Paso 7 — `webapp/app.py` (modificar)

- [x] Al arrancar: leer `models/hw_profile.json`; si no existe o `setup_ok=false` → loguear warning (no abortar la webapp, solo indicarlo)
- [x] `GET /api/local/hardware` → retorna `hw_profile.json`
- [x] `GET /api/local/recommended` → retorna modelos recomendados según HW
- [x] `GET /api/local/cached` → lista GGUFs en `models/` con metadata
- [x] `POST /api/local/download` → inicia descarga en background thread, retorna `{job_id}`
- [x] `GET /api/local/download/<job_id>` → SSE de progreso (bytes descargados / total)
- [x] `DELETE /api/local/model/<filename>` → elimina GGUF del cache
- [x] Ampliar `POST /api/models`: para `llama_cpp` → retorna lista de GGUFs del cache

---

### Paso 8 — `webapp/templates/arbiters.html` (modificar)

- [x] Renombrar tab `ollama` → `llama_cpp`, label "Local (GGUF)", icono `bi-cpu`
- [x] Actualizar `DEFAULT_ENV`, `API_KEY_HELP`, `PROVIDER_ICONS`, `PROVIDER_LABELS` para `llama_cpp`
- [x] Cuando provider es `llama_cpp`:
  - Ocultar sección API key
  - Ocultar sección rate limits
  - El grid de modelos se carga desde `/api/local/cached` (GGUFs del cache)
  - Mostrar botón "Descargar modelos" → abre modal de descarga
- [x] Modal de descarga:
  - Sección de inventario HW (desde `/api/local/hardware`)
  - Lista de modelos recomendados con checkboxes (desde `/api/local/recommended`)
  - Barra de progreso SSE durante descarga
- [x] Al guardar árbitro `llama_cpp`: `model` = nombre del archivo, `model_path` = ruta relativa

---

### Paso 9 — `main_webapp.py` y `main_cli.py` (modificar)

- [x] Al arrancar, antes de cualquier otra cosa, verificar `models/hw_profile.json`:
  - No existe → `print("Error: ejecuta 'python setup_env.py' primero")` + `sys.exit(1)`
  - `setup_ok == false` → `print("El setup tiene errores. Revisa logs/setup_*.log")` + `sys.exit(1)`
  - OK → continuar normalmente

---

### Paso 10 — Infraestructura

- [x] `.gitignore`: añadir `models/`, `models/*.gguf`, `models/hw_profile.json`, `models/models.json`
- [x] `requirements.txt`: ya actualizado ✅
- [x] `environment.yml`: añadir `llama-cpp-python`, `huggingface_hub`, `psutil`, `openvino`, `optimum`

---

## Pendientes previos (backlog)

- Ejecutar el pipeline completo sobre el dataset y obtener resultados finales para el paper
- Analizar métricas agregadas de acuerdo inter-árbitros (Fleiss/Cohen/Krippendorff)
- Crear pestaña en la webapp para configurar `processing` y `safety` desde la UI
  (actualmente solo se puede editar a mano el JSON de config)
- Homogeneizar cómo se identifica a los árbitros en la UI:
  actualmente mezcla ID, nombre y proveedor+modelo según el contexto.
  Decidir: ¿mostrar siempre `nombre (id)` o `persona — modelo`?
