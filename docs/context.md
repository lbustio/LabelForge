# LabelForge - contexto portable de continuidad

Documento de relevo para retomar el proyecto en otra sesion, con otro asistente
de codigo o en otra PC. Describe el estado vigente; el historial detallado esta
en `bitacora.md` y el trabajo pendiente exclusivamente en `todos.md`.

Ultima verificacion: 2026-07-18. Rama local: `main`. Remoto: repositorio
`lbustio/LabelForge` en GitHub.

## Antes de continuar

1. Leer este archivo y `todos.md`.
2. Ejecutar `git status --short` antes de editar. El arbol actual contiene muchos
   cambios sin commit y una reorganizacion pendiente de registrar: archivos de
   documentacion pasaron a `docs/` y los de creacion de entornos a `setup/`.
3. No descartar, resetear ni sobrescribir esos cambios. Las eliminaciones de los
   archivos antiguos y los directorios nuevos son las dos mitades de los
   movimientos aun no registrados por Git.
4. Revisar `config/arbiters_config.json`, porque es la autoridad de la corrida,
   y no inferir configuracion a partir de logs historicos.
5. Nunca copiar secretos a JSON, Markdown, logs o commits.

## Objetivo y arquitectura

LabelForge etiqueta texto con varios arbitros LLM y consolida sus votos sobre
los principios de persuasion definidos por el proyecto. Tiene CLI, TUI y webapp
Flask, pero comparten el nucleo de `labelforge_core/`.

- `main_cli.py`: ejecucion batch.
- `main_tui.py`: interfaz de terminal.
- `main_webapp.py` y `webapp/`: aplicacion web y endpoints.
- `manage.py`: operaciones administrativas.
- `labelforge_core/`: dataset, prompts, proveedores, GGUF, checkpoints,
  parsing, metricas, consenso, reportes y rutas canonicas.
- `config/arbiters_config.json`: taxonomia, arbitros, dataset, procesamiento y
  seguridad operacional.
- `config/local_models_config.json`: consulta y cache del catalogo GGUF.
- `config/rate_limits.json`: unica fuente de cuotas y limites conocidos.
- `secrets/secrets.env`: credenciales locales; nunca versionar.
- `models/`, `data/uploads/`, `output/`, `checkpoints/` y `logs/`: artefactos
  locales o de ejecucion.
- `setup/`: definicion del entorno, requisitos y utilidades de instalacion.

No duplicar en `webapp/app.py` logica que ya pertenezca al nucleo. Cuando un
cambio afecte CLI y webapp, el contrato comun debe vivir en
`labelforge_core/`.

## Configuracion de investigacion vigente

Dataset configurado: `data/uploads/data_pop.csv`, columna `txt`, primera hoja si
el formato la requiere. `0` como limite significa procesar todo el dataset.

Arbitros activos:

| ID | Rol / persona | Proveedor | Modelo local |
| --- | --- | --- | --- |
| `arb1` | Psicologo Clinico / Mistral | `llama_cpp` | `Mistral-7B-Instruct-v0.3-Q4_K_M.gguf` |
| `arb3` | Investigador Forense / QWEN | `llama_cpp` | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` |
| `arb4` | Psicologo Forense / Llama | `llama_cpp` | `Llama-3.2-3B-Instruct-Q4_K_M.gguf` |

La taxonomia AUTH, SP, LSD, CRC y DIS en `config/arbiters_config.json` fue
alineada con los principios de `docs/pop.json`. Esas definiciones son
autoritativas: los prompts de cada arbitro especializan la observacion, pero no
pueden ampliar ni sustituir la taxonomia.

## Contrato de contexto y entradas largas

No existe un recorte global tipo `claim_max_chars`. La politica actual es
arquitectonica y se aplica a cualquier proveedor:

- `input_overflow_strategy: chunk_all` procesa el texto completo en segmentos.
- El presupuesto se calcula para cada arbitro usando su ventana efectiva, el
  prompt, la salida reservada y el margen de seguridad.
- Los votos de segmentos se agregan con OR por principio; uso de tokens y
  latencia se acumulan.
- `context_window_fallback: 32768`, margen de 512 tokens y solapamiento de 400
  caracteres son valores configurables, no limites ocultos.
- `n_ctx: 0` significa negociar automaticamente, no usar una ventana minima.

Para GGUF, el runtime lee `context_length` de los metadatos, intenta ventanas
decrecientes si no puede crear el contexto y conserva la ventana negociada por
modelo. En la maquina usada durante esta revision, Llama 3.2 3B fallo con 131072
y cargo con 65536. Este dato es evidencia de esa PC, no una constante portable.

## Runtime GGUF y Hugging Face

El proveedor `llama_cpp` significa exclusivamente GGUF mediante el paquete
Python `llama-cpp-python`; no es un alias de OpenVINO. Se verifico localmente la
carga de DLL de llama/ggml CUDA y offload en una NVIDIA GeForce RTX 5050 Laptop
GPU con 8 GB VRAM. Otra PC debe volver a ejecutar la deteccion de hardware y
validar su backend.

El catalogo GGUF obtiene de Hugging Face los repositorios y archivos reales. La
seleccion conserva el `repo_id` y nombre exacto recibidos; no debe inventar rutas
ni reconstruir nombres por heuristicas. Las descargas exponen estado y progreso,
admiten cancelacion, usan archivos parciales y evitan descargar de nuevo un
archivo ya presente. `config/local_models_config.json` controla busqueda,
cuantizacion, limite y TTL del cache.

## Recuperacion, checkpoints y corrida pendiente

Los resultados parciales se guardan por entrada y arbitro. Al reanudar, el
pipeline omite arbitros ya completados y ejecuta solamente los faltantes. El
interruptor de fallos sistemicos pausa tras tres errores equivalentes del mismo
arbitro para evitar marcar todo el dataset como fallido. Un fallo local de
contexto no debe clasificarse como rate limit o error remoto.

El orden vigente es `processing.execution_order: arbiter_major`: cada arbitro
procesa todas sus entradas pendientes mientras su modelo permanece cargado;
despues se libera y comienza la pasada del siguiente arbitro. Al final se
consolidan las evaluaciones ya persistidas. `row_major` sigue disponible como politica
alternativa, pero con varios GGUF y VRAM para un solo modelo provocaria una carga
y descarga por arbitro en cada texto. Los reintentos dentro de una pasada se
controlan con `arbiter_major_retry_attempts` y no cambian de modelo.

El dashboard usa un contrato dual y no interpreta ambos ordenes como si fueran
iguales. En `row_major`, progreso, velocidad y ETA se expresan por entradas. En
`arbiter_major`, durante las pasadas se expresan por evaluaciones de arbitro;
las entradas consolidadas, el acuerdo y la distribucion de principios solo
avanzan cuando todas las evaluaciones necesarias existen. Una evaluacion es la
respuesta de un arbitro para una entrada analizable; una llamada de modelo es
una invocacion real y puede haber varias por evaluacion debido a fragmentacion.
Tokens y latencias se actualizan por cada `arbiter_result` y no se vuelven a
sumar al consolidar.

La pestaña Ejecutar completa respeta ademas estas reglas:

- el run activo/pausado es siempre la fuente del resumen; nunca se sustituye por
  un CSV historico solo porque tenga mas filas;
- sin consensos, la tabla y el resumen guardado muestran espera explicita y no
  datos de otra corrida;
- tras reiniciar Flask, Estado en vivo se reconstruye desde `run_state.json`,
  evaluaciones parciales y cola persistida;
- Detener convierte el estado en pausa reanudable `user_stopped`;
- dataset, checkpoint, archivo y limpieza no pueden mutarse durante una corrida;
- el presupuesto de tokens se verifica por evaluacion en `arbiter_major`;
- horas de error provienen del timestamp del log y la cola se deduplica por
  entrada;
- tokens por entrada se calculan sobre entradas procesadas, no pendientes;
- filas totales, entradas analizables, evaluaciones, filas cerradas y consensos
  son unidades distintas y nunca deben compartir un contador sin etiqueta;
- los recursos acumulados se restauran desde `arbiter_results.jsonl` al reanudar;
- un fallo provisional encolado no incrementa filas fallidas ni cerradas;
- la ETA usa una mediana movil y espera varias muestras para excluir el costo de
  carga inicial del modelo.

Estado historico de la corrida que origino el diagnostico:

- La corrida `20260718_023137` uso codigo anterior y no produjo filas
  consolidadas.
- Las entradas 0 a 7 llegaron a producir resultados de `arb1` y `arb3`, pero no
  filas consolidadas porque `arb4` fallaba al crear un contexto de 131072.
- `arb4` fue el arbitro que fallo.
- En la comprobacion final, `checkpoints/` solo contenia `.gitkeep`: esos votos
  parciales ya no estaban disponibles y no deben describirse como recuperables.
  La siguiente ejecucion debe comenzar una prueba corta nueva.

Los warnings de backoff mostrados como "Rate limiter: error servidor" durante
esa corrida no demostraban un limite de la RTX ni de GGUF: eran una
clasificacion incorrecta de fallos locales. El codigo actual separa esos casos y
el circuito de fallos sistemicos impide una cascada silenciosa.

Tambien se corrigio la liberacion de recursos nativos al alternar GGUF: quitar
una instancia del diccionario no liberaba necesariamente su contexto/VRAM. El
runtime ahora llama al cierre explicito, fuerza recoleccion antes de cargar el
siguiente modelo y clasifica los fallos de carga como locales. Se verifico con
la secuencia real Mistral -> Qwen -> Llama -> Mistral, que termino correctamente.

Luego se verifico el orden optimizado con una corrida incremental de tres
entradas: una sola carga por pasada, consolidacion sin nuevas inferencias y
resultado 3/3 exitoso en `output/labeled_20260718_030452.csv`.

## Estado de la interfaz web

- En Ejecutar, Historial aparece despues de Ejecutar.
- El panel del dataset es expandible y se comprime tras configurarlo.
- El limite explica que `0` procesa todo el dataset.
- La ultima entrada tiene altura acotada y desplazamiento interno.
- **Detener ejecucion** comunica la cancelacion al proceso; **Limpiar todo**
  borra el estado de ejecuciones tras confirmacion.
- Los logs se colorean por nivel estructurado; palabras del dataset no convierten
  una linea INFO en error.
- Los dialogos de confirmacion usan componentes visuales de la aplicacion, no
  `window.confirm` del navegador.
- Los campos secretos tienen control para mostrar/ocultar su valor.
- Al editar un arbitro, los modelos se consultan para el proveedor activo con
  sus credenciales; cambiar proveedor repite el descubrimiento y Recargar solo
  actualiza ese proveedor.
- Las tarjetas se refrescan despues de guardar y muestran proveedor, modelo y
  categoria calculados a partir de la configuracion vigente.

Estos flujos tienen cobertura automatizada parcial, pero los eventos en tiempo
real y descargas grandes todavia requieren la prueba manual indicada en
`todos.md`.

## Cuotas, seguridad y despliegue

`config/rate_limits.json` es la unica tabla de cuotas. El nucleo falla de forma
explicita si falta o es invalida; no mantener una segunda tabla hardcoded. Las
cifras estaticas son orientativas y no deben bloquear modelos que el proveedor
realmente exponga a las credenciales activas.

La configuracion de seguridad conserva `max_total_tokens: 0`, que significa sin
tope artificial. Se eliminaron switches ambiguos como `free_only_mode`, listas
duplicadas de proveedores y pausas muertas. Los limites reales deben surgir del
proveedor, del presupuesto de contexto y de la politica explicita de ejecucion.

Pendientes de despliegue:

- definir `LABELFORGE_SECRET_KEY` estable;
- usar HTTPS antes de exponer el servicio;
- corregir `labelforge-service.xml`, que todavia apunta a una ruta personal de
  Conda inexistente en la maquina revisada.

## Verificacion y traslado a otra PC

En una maquina nueva:

1. Clonar el repositorio y crear el entorno usando los archivos de `setup/`.
2. Crear `secrets/secrets.env` desde el ejemplo y proporcionar credenciales sin
   versionarlas.
3. Ejecutar la deteccion de hardware; no copiar como verdad el perfil CUDA de
   otra PC.
4. Descargar o registrar los GGUF requeridos y comprobar sus rutas.
5. Validar la configuracion y ejecutar las pruebas antes de reanudar una corrida.

Comandos de comprobacion habituales desde la raiz:

```powershell
git status --short
python -m pytest -q
python main_cli.py --help
python main_webapp.py --help
```

La validacion mas reciente ejecuto 46 pruebas `unittest` correctamente. Tambien
se compilaron las plantillas y el JavaScript renderizado de `run.html` paso
`node --check`. En otra PC deben sincronizarse las dependencias de `setup/` y
repetirse estas verificaciones; no asumir que el resultado de una maquina
garantiza otra.

## Invariantes que no deben regresarse

- `docs/pop.json` prevalece sobre interpretaciones particulares de un arbitro.
- No truncar silenciosamente entradas largas ni fijar ventanas por modelo como
  parche puntual.
- No inventar identificadores o nombres de archivos de Hugging Face.
- No tratar GGUF local como proveedor remoto ni aplicar backoff remoto a errores
  locales deterministas.
- No perder votos parciales al reanudar ni repetir llamadas ya completadas.
- No exponer secretos en configuracion, logs, interfaz o Git.
- No usar una tabla hardcoded paralela a `config/rate_limits.json`.
- No presentar como verificado lo que solo se infirio; dejar evidencia y alcance
  de cada comprobacion.
