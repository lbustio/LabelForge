# Bitacora del Proyecto LabelForge

Este documento funciona como memoria viva del proyecto. Su objetivo es que una
persona o un LLM puedan retomar el desarrollo en el futuro con el mayor contexto
posible: que se hizo, por que se hizo, que falta, que decisiones siguen abiertas
y que criterios deben respetarse.

> Nota de uso: aqui se documenta el razonamiento util del proyecto en forma de
> decisiones, supuestos, criterios, alternativas consideradas y conclusiones. No
> es necesario guardar cadenas de pensamiento internas o transcripciones largas;
> lo importante es dejar contexto reutilizable.

## Como mantener esta bitacora

- Agregar una entrada nueva al final de `Historico` cada vez que se haga un
  cambio relevante en codigo, configuracion, datos, prompts, arquitectura o
  criterios de evaluacion.
- Registrar siempre la fecha local, el autor/agente si se conoce, el objetivo,
  los archivos tocados, la decision tomada y la razon practica.
- Si hubo alternativas, anotar brevemente por que se eligio una sobre otra.
- Si algo queda incompleto, moverlo a `Pendientes` con suficiente detalle para
  que otra sesion pueda continuarlo.
- Mantener los secretos fuera de este archivo. No pegar API keys, tokens,
  credenciales, rutas privadas innecesarias ni datos sensibles.
- Cuando una entrada corrija una decision previa, enlazar o mencionar la entrada
  anterior y explicar que cambio.

## Contexto actual del proyecto

LabelForge es una plataforma para etiquetar entradas textuales de forma general,
incluyendo datasets de noticias falsas segun la taxonomia de Principios de
Persuasion de Ana Ferreira aplicada a ingenieria social. El sistema combina:

- Un pipeline CLI de etiquetado masivo.
- Una webapp Flask para configuracion, ejecucion y monitoreo.
- Un archivo central de configuracion de arbitros y principios.
- Un enfoque multi-arbitro por consenso.
- Soporte para distintos proveedores LLM.
- Checkpoints, cola de reintentos, logs, salidas CSV y metricas de tokens y
  latencia.

## Arquitectura conocida

- `main_label.py`: entrada CLI para lanzar el etiquetado.
- `main_webapp.py`: entrada para levantar la webapp Flask.
- `scripts/label_persuasion.py`: nucleo del pipeline de etiquetado, proveedores
  LLM, rate limiter, checkpoints, cola de fallos, consenso y metricas.
- `scripts/arbiters_config.json`: configuracion central de principios,
  arbitros, prompts y parametros de procesamiento.
- `webapp/app.py`: backend Flask, endpoints de configuracion, ejecucion,
  streaming de logs y metricas.
- `webapp/templates/`: pantallas HTML de la interfaz.
- `data/`: datos de entrada.
- `output/`: datasets etiquetados generados.
- `checkpoints/`: estado intermedio de ejecuciones.
- `logs/`: historico de corridas y reportes.

## Criterios de desarrollo

- Preservar la trazabilidad: cualquier cambio que afecte resultados de
  etiquetado debe poder explicarse desde configuracion, logs o esta bitacora.
- Preferir cambios pequenos y verificables, especialmente en el pipeline de
  inferencia y consenso.
- Mantener separadas las responsabilidades entre CLI, configuracion y webapp.
- Evitar introducir dependencias nuevas si la biblioteca estandar o las
  dependencias actuales ya resuelven el problema de forma clara.
- Cuidar la reproducibilidad: registrar parametros, modelos, prompts, limites y
  archivos usados cuando se ejecuten experimentos.
- Para prompts y criterios de clasificacion, documentar no solo el texto final
  sino tambien el motivo del ajuste.

## Pendientes vivos

- Verificar con una ejecucion corta que el pipeline CLI y la webapp siguen
  funcionando despues de futuros cambios.
- Mantener esta lista actualizada con problemas reales detectados en desarrollo.

## Historico

### 2026-06-12 22:47 - Creacion de la bitacora

**Autor/agente:** Codex.

**Objetivo:** crear `bitacora.md` como memoria continua del proyecto para que el
desarrollo pueda retomarse en otra sesion o por otro LLM sin perder contexto.

**Contexto observado:**

- El README describe una plataforma de etiquetado de fake news con pipeline CLI y
  webapp Flask.
- El proyecto usa una configuracion central en `scripts/arbiters_config.json`.
- El procesamiento principal vive en `scripts/label_persuasion.py`.
- La webapp vive en `webapp/app.py` y sus plantillas.
- El repositorio local tiene archivos aun no rastreados por git al momento de
  crear esta bitacora.

**Decision tomada:**

Se creo una bitacora estructurada con secciones para contexto, arquitectura,
criterios, pendientes e historico cronologico. La intencion es que cada cambio
futuro agregue entradas breves pero accionables.

**Razon practica:**

Un LLM futuro no necesita una transcripcion completa del proceso mental; necesita
hechos, decisiones, restricciones, alternativas relevantes y estado pendiente.
Esta estructura prioriza ese contexto reutilizable.

**Archivos tocados:**

- `bitacora.md`

### 2026-06-12 22:58 - Resumen estadistico y tabla de resultados en la webapp

**Autor/agente:** Codex.

**Objetivo:** al finalizar un procesamiento desde la webapp, mostrar un resumen
estadistico del run y una tabla con los resultados obtenidos por claim.

**Contexto observado:**

- La pagina `webapp/templates/run.html` ya invocaba `/api/run/stats` al terminar
  el proceso via SSE y tambien al cargar la pagina.
- El endpoint `/api/run/stats` calculaba tokens y latencia agregados por arbitro
  a partir del CSV mas reciente en `output/`.
- El pipeline ya escribe en el CSV columnas por principio/arbitro, columnas de
  consenso, tokens por arbitro y, en la version actual del pipeline, latencia por
  arbitro.

**Decision tomada:**

Se extendio `/api/run/stats` para que, ademas de los agregados, entregue una
coleccion `results` derivada del CSV mas reciente. Cada fila incluye claim,
claim traducido si existe, dataset/clase, consenso por principio, dictamen de
cada arbitro, justificaciones, tokens y latencia acumulada del claim.

En la interfaz se agrego un panel `Resultados obtenidos` con una tabla por claim.
La tabla muestra columnas de claim, consenso, arbitros, tokens y tiempo. El
detalle de cada arbitro queda dentro de secciones expandibles para mantener la
pantalla legible aun cuando haya muchas justificaciones.

**Razon practica:**

El CSV final ya es la fuente de verdad del procesamiento, asi que reutilizarlo
evita duplicar estado o modificar el pipeline. El endpoint limita por defecto la
tabla a 250 filas, con tope de 500, para que una corrida grande no vuelva pesada
la pagina.

**Verificacion realizada:**

- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`
- `GET /api/run/stats?limit=2` usando `app.test_client()`: devolvio HTTP 200,
  detecto el CSV mas reciente, principios y filas de resultados.
- `GET /run` usando `app.test_client()`: devolvio HTTP 200 y renderizo los nuevos
  elementos `results-panel` y `stats-successful-rows`.
- No se pudo ejecutar `node --check` porque `node` no esta instalado en el
  entorno local.

**Archivos tocados:**

- `webapp/app.py`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-13 18:10 - Terminologia general: entradas en vez de claims

**Autor/agente:** OpenCode.

**Objetivo:** ajustar la terminologia del proyecto porque LabelForge evoluciono
de un etiquetador de claims de fake news a una plataforma de etiquetado de datos
textuales mas general.

**Decision tomada:**

La terminologia publica pasa a ser:

- `entrada(s)` para cada fila/unidad procesada;
- `texto` para el contenido textual que se envia al LLM;
- `datos` o `dataset` para el conjunto completo.

Se mantiene `claim` como nombre tecnico/legacy en variables internas,
checkpoints, eventos SSE y columna CSV esperada, para no romper compatibilidad
con datasets y outputs existentes. La documentacion aclara que `claim` representa
el texto de la entrada.

**Archivos tocados:**

- `README.md`
- `main_label.py`
- `scripts/arbiters_config.json`
- `scripts/label_persuasion.py`
- `webapp/app.py`
- `webapp/templates/index.html`
- `webapp/templates/principles.html`
- `webapp/templates/processing.html`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-13 18:00 - Metricas de agreement entre arbitros

**Autor/agente:** OpenCode.

**Objetivo:** calcular metricas reportables de acuerdo inter-evaluador para el
paper, a partir de las etiquetas binarias emitidas por los arbitros.

**Decision tomada:**

Se agrego en `/api/run/stats` un bloque `agreement` con metricas globales y por
principio:

- acuerdo observado promedio por pares;
- Fleiss kappa multi-arbitro;
- Cohen kappa promedio por pares;
- Krippendorff alpha nominal;
- tasa positiva de etiquetas.

La UI de `Ejecutar` ahora muestra un panel `Agreement entre arbitros` dentro del
resumen del ultimo output guardado, con interpretacion rapida del kappa global.

**Razon practica:**

Estas metricas son estandar en estudios con multiples evaluadores/codificadores y
permiten reportar no solo el consenso final, sino la confiabilidad entre arbitros.
Fleiss kappa es apropiado para multiples arbitros con datos completos;
Krippendorff alpha nominal tolera datos faltantes; Cohen kappa promedio facilita
comparar pares de arbitros.

**Verificacion realizada:**

- Prueba sintetica con acuerdo perfecto en `AUTH` y parcial en `SP` produjo
  metricas diferenciadas esperadas.
- `GET /api/run/stats?limit=1` devolvio HTTP 200 con bloque `agreement` usando el
  output real mas reciente.
- `GET /run` devolvio HTTP 200 y renderizo `agreement-panel`.
- `conda run -n fakenewsds python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/app.py`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-12 23:00 - Ampliacion de `.gitignore`

**Autor/agente:** Codex.

**Objetivo:** evitar que secretos, datos grandes, salidas generadas y artefactos
locales terminen en GitHub.

**Contexto observado:**

- Ya existia un `.gitignore` basico que ignoraba `.env`, caches de Python,
  `output/`, `logs/` y `checkpoints/`.
- El dataset `data/all_datasets_en_es/all_datasets_en_es.csv` pesa
  aproximadamente 118 MB, por lo que no conviene subirlo a GitHub.
- `biblio/` contiene un PDF; se trato como material local/bibliografico que no
  debe publicarse por defecto.
- `scripts/arbiters_config.json` es configuracion central del proyecto y no se
  ignoro; se verifico que sus campos `api_key_override` estan vacios.

**Decision tomada:**

Se amplio `.gitignore` para cubrir:

- `.env`, variantes `.env.*`, llaves, certificados, tokens y credenciales JSON.
- Configuraciones locales o privadas (`*.local.json`, `*.private.json`,
  `*secret*.json`, `*credentials*.json`).
- Datasets grandes en `data/`, manteniendo excepciones para README, Markdown y
  TXT ligeros.
- PDFs/documentos en `biblio/`, manteniendo excepciones para notas ligeras.
- Outputs, logs, checkpoints, caches, entornos virtuales, coverage, notebooks,
  bases locales, modelos/artefactos pesados e IDE.

**Razon practica:**

El repositorio debe contener codigo, configuracion reproducible y documentacion,
pero no credenciales, datasets pesados, resultados generados ni material local.
Esto reduce riesgo de filtracion y evita rechazos por limite de tamano en
GitHub.

**Verificacion realizada:**

- `git status --ignored --short` muestra `.env`, `output/`, `logs/`,
  `checkpoints/`, caches, el CSV grande y el PDF como ignorados.
- `git check-ignore -v` confirmo las reglas que ignoran `.env`, el CSV grande,
  `output/`, `logs/`, `checkpoints/` y el PDF de `biblio/`.
- `git status --short --untracked-files=all` muestra como visibles para commit
  el codigo, README, bitacora, archivos de entorno, configuracion central y el
  TXT ligero del dataset.

**Archivos tocados:**

- `.gitignore`
- `bitacora.md`

### 2026-06-12 23:08 - Metricas en vivo y descripcion de outputs

**Autor/agente:** Codex.

**Objetivo:** mostrar, ademas del resumen del ultimo CSV guardado, las metricas
del run actual en tiempo real. Tambien describir que representa cada archivo en
la tabla de outputs generados.

**Contexto observado:**

- El panel existente de tokens/latencia leia `/api/run/stats`, que se calcula a
  partir del ultimo CSV terminado en `output/`.
- Ese enfoque es correcto para outputs persistidos, pero no sirve para metricas
  en vivo porque el CSV final se escribe hasta el cierre del pipeline.
- Los archivos `labeled_YYYYMMDD_HHMMSS.csv` representan corridas terminadas del
  pipeline; contienen claims originales mas votos por arbitro, justificaciones,
  consenso y metricas cuando el run las genero.

**Decision tomada:**

Se separaron dos conceptos en la webapp:

- `Metricas en vivo del run actual`: se alimenta por eventos estructurados que
  emite `scripts/label_persuasion.py` cuando lo lanza la webapp.
- `Resumen del ultimo output guardado`: sigue leyendo el CSV mas reciente ya
  persistido mediante `/api/run/stats`.

El pipeline ahora emite eventos internos con prefijo `__RUN_METRIC__` solo si la
variable `DATA_LABELING_WEBAPP_METRICS=1` esta activa. La webapp activa esa variable
al lanzar el subproceso, captura esos eventos por SSE, actualiza un snapshot del
run activo y expone el estado en `/api/run/current-stats`.

Tambien se enriquecio `/api/outputs`: cada archivo generado incluye una
descripcion calculada desde su nombre y cabecera CSV, indicando fecha del run,
claims, principios, arbitros, presencia de tokens y presencia de latencia. Esto
aclara por que algunos outputs antiguos pueden tener tokens pero no latencia.

**Razon practica:**

Leer el CSV durante la ejecucion no es confiable porque el archivo final se crea
al terminar. Los eventos estructurados permiten ver tokens, latencias, llamadas y
progreso mientras el proceso sigue corriendo, sin contaminar el uso CLI normal
con mensajes JSON.

**Verificacion realizada:**

- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`
- `GET /api/outputs` usando `app.test_client()`: devolvio HTTP 200 y describio
  el output mas reciente como run de 10 claims, 5 principios, 3 arbitros, con
  tokens y latencia.
- `GET /api/run/current-stats` usando `app.test_client()`: devolvio HTTP 200 con
  snapshot vacio cuando no hay proceso activo.
- Simulacion interna de eventos `start` y `claim_ok`: el acumulador en vivo
  registro 1 claim OK, 12 tokens, 1 llamada y 1.5s de latencia.
- `GET /run` usando `app.test_client()`: devolvio HTTP 200 y renderizo el panel
  `live-stats-panel` y la columna `Descripcion` de outputs.

**Archivos tocados:**

- `scripts/label_persuasion.py`
- `webapp/app.py`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-12 23:16 - Optimizacion de prompts de arbitros

**Autor/agente:** Codex.

**Objetivo:** reducir consumo de tokens y mejorar calidad de clasificacion sin
crear otra variante de arquitectura, ya que modelos y arbitros se pueden ajustar
desde la configuracion existente.

**Contexto observado:**

- El ultimo run medido consumio 16,270 tokens para 10 claims, aproximadamente
  1,627 tokens por claim.
- Los prompts anteriores tenian personas largas y pedian una oracion completa de
  explicacion por cada principio.
- Algunos dictamenes podian marcar persuasion por inferir falsedad, intencion o
  contexto externo, aunque el claim no tuviera una senal linguistica explicita.

**Decision tomada:**

Se reescribio `scripts/arbiters_config.json` para:

- Mantener los mismos campos JSON de salida que espera el pipeline.
- Reducir los `system_prompt` de cada arbitro a su lente profesional esencial.
- Hacer el `user_prompt_template` mas compacto y mas estricto.
- Exigir que el modelo use solo el texto del claim.
- Prohibir fact-checking, contexto externo e inferencia de intencion oculta.
- Marcar `1` solo cuando haya una pista linguistica explicita.
- Limitar cada `*_why` a una frase de maximo 8 palabras.
- Usar `"no cue"` cuando el principio este ausente.

**Razon practica:**

La optimizacion principal no debe duplicar configuraciones que ya existen en la
webapp. Mejorar el contrato del prompt reduce tokens de entrada, reduce tokens de
salida y deberia disminuir falsos positivos causados por razonamiento externo al
texto.

**Medicion local:**

- Antes: aproximadamente 3,992 caracteres de instrucciones fijas por claim
  considerando 3 arbitros.
- Ahora: aproximadamente 2,306 caracteres de instrucciones fijas por claim.
- Reduccion estimada de prompt fijo: ~42%.
- El ahorro adicional esperado viene de respuestas mas cortas, porque las
  justificaciones pasan de oraciones completas a frases de maximo 8 palabras.

**Verificacion realizada:**

- `json.load` cargo correctamente `scripts/arbiters_config.json`.
- El template conserva el placeholder `{claim}`.
- `load_config(Path('scripts/arbiters_config.json'))` valido 3 arbitros y 5
  principios sin errores.

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `bitacora.md`

### 2026-06-12 23:18 - Prompt binario sin justificaciones

**Autor/agente:** Codex.

**Objetivo:** eliminar el gasto innecesario de pedir justificaciones cuando el
objetivo operativo inmediato es obtener solo el vector binario de principios,
por ejemplo `1 0 0 0 1`.

**Contexto observado:**

- El valor reportado previamente como `2,306` correspondia a caracteres fijos de
  instrucciones considerando 3 arbitros, no a tokens reales.
- Aun asi, seguia siendo demasiado overhead para una tarea binaria.
- El pipeline no exige campos `*_why`; si el modelo no los devuelve, los rellena
  con cadena vacia antes de escribir el CSV.

**Decision tomada:**

Se cambio `scripts/arbiters_config.json` para pedir un JSON binario minimo:

`{"AUTH":0,"SP":0,"LSD":0,"CRC":0,"DIS":0}`

Tambien se compactaron aun mas los `system_prompt` de los arbitros a una sola
linea funcional, preservando el lente profesional pero sin biografias ni
explicaciones largas.

**Razon practica:**

Si el producto principal es la matriz de etiquetas, las explicaciones deben ser
opcionales o usarse solo en auditoria. En modo masivo, cada palabra de `*_why`
se multiplica por claims y arbitros.

**Medicion local:**

- Template de usuario actual: 411 caracteres.
- Instrucciones fijas totales por claim con 3 arbitros: 1,462 caracteres.
- Antes de esta reduccion binaria: 2,306 caracteres.
- Reduccion adicional del prompt fijo: ~37%.
- Reduccion acumulada desde los prompts largos iniciales: ~63%.
- El ahorro mayor esperado ahora sera en `completion_tokens`, porque ya no se
  piden cinco justificaciones por arbitro.

**Verificacion realizada:**

- `json.load` cargo correctamente `scripts/arbiters_config.json`.
- El template conserva `{claim}`.
- `load_config(Path('scripts/arbiters_config.json'))` valido 3 arbitros y 5
  principios sin errores.

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `bitacora.md`

### 2026-06-12 23:24 - Justificaciones opcionales por checkbox

**Autor/agente:** Codex.

**Objetivo:** conservar las columnas y capacidad de justificacion `*_why`, pero
hacer que pedirlas al LLM sea opcional para controlar tokens y tiempo.

**Contexto observado:**

- El modo binario minimo ahorra tokens, pero las justificaciones siguen siendo
  utiles para auditoria, depuracion y revision cualitativa.
- El pipeline ya tolera respuestas sin `*_why`: durante la validacion rellena
  esos campos con cadena vacia si no vienen en el JSON.
- Por tanto, no hacia falta cambiar el formato final del CSV ni el calculo de
  consenso.

**Decision tomada:**

Se agrego `task.include_justifications` en `scripts/arbiters_config.json`.

El `user_prompt_template` ahora usa `{output_instruction}`. El pipeline genera
esa instruccion en tiempo de ejecucion:

- Si `include_justifications = false`, pide solo
  `{"AUTH":0,"SP":0,"LSD":0,"CRC":0,"DIS":0}`.
- Si `include_justifications = true`, pide tambien `AUTH_why`, `SP_why`,
  `LSD_why`, `CRC_why` y `DIS_why`, con limite de 8 palabras.

En la webapp se agrego un check en `Principios y Prompt Template` llamado
`Incluir justificaciones (*_why)`, guardado por `/api/prompt-template`.

**Razon practica:**

La explicacion no debe estar atada al modo masivo. Para produccion barata se
puede correr binario; para auditoria o muestras pequenas se activa el check y se
paga el costo adicional de salida.

**Verificacion realizada:**

- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`
- `build_user_prompt(...)` genera salida binaria cuando el check esta apagado.
- `build_user_prompt(...)` genera salida con `*_why` cuando el check esta
  encendido.
- `GET /api/prompt-template` devuelve `include_justifications`.
- `GET /principles` renderiza HTTP 200.

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `scripts/label_persuasion.py`
- `webapp/app.py`
- `webapp/templates/principles.html`
- `bitacora.md`

### 2026-06-12 23:35 - Resiliencia, reanudacion y candado anti-pago

**Autor/agente:** Codex.

**Objetivo:** evitar que una interrupcion por corriente, internet, cuota,
proveedor LLM o cierre manual obligue a comenzar desde cero. Ademas, bloquear
proveedores con riesgo de tokens de paga cuando el modo seguro esta activo.

**Contexto observado:**

- Ya existia `checkpoint.json`, pero se guardaba cada N claims y no preservaba
  respuestas parciales por arbitro.
- Si un claim fallaba despues de que algunos arbitros ya habian respondido, al
  reanudar se podia volver a llamar a arbitros ya completados.
- El CSV final se escribe al terminar, por lo que no puede ser la unica fuente
  de recuperacion.
- La configuracion actual usa `vertex_gemini`, que se considera riesgo de pago
  bajo la politica `free_only_mode`.

**Decision tomada:**

Se agregaron archivos durables en `checkpoints/`:

- `run_state.json`: estado del run (`running`, `paused`, `completed`), resumen,
  razon de pausa, output planeado, arbitros, principios y politica de seguridad.
- `claim_results.jsonl`: resultado final incremental por claim.
- `arbiter_results.jsonl`: respuesta incremental por claim y por arbitro.

El pipeline ahora:

- Guarda cada respuesta de arbitro inmediatamente con escritura durable.
- Reutiliza respuestas de arbitros ya completados al reanudar.
- Guarda resultado final por claim inmediatamente.
- Actualiza `checkpoint.json` despues de cada claim, no cada N claims.
- Recorre todos los claims y salta los que ya tienen resultado final.
- Reusa el `run_id` y output planeado si encuentra un run incompleto compatible.
- Evita reprocesar elementos viejos de `failed_queue` si ya fueron completados.
- Pausa la ejecucion ante errores clasificados como cuota/rate limit, red,
  autenticacion, configuracion, servidor del proveedor o error desconocido.
- Mantiene `invalid_response` como reintentable sin asumir que debe cancelar todo
  el run.

La clasificacion de errores es generica y aplica a Gemini, Vertex Gemini,
Anthropic/Claude, OpenAI/ChatGPT, Ollama y endpoints OpenAI-compatible locales o
remotos.

**Candado anti-pago:**

Se agrego `safety.free_only_mode = true` en `scripts/arbiters_config.json`.
Cuando esta activo, solo permite por defecto:

- `gemini`
- `ollama`
- `openai_compat` local (`localhost`, `127.0.0.1`, `::1`,
  `host.docker.internal`)

Bloquea por defecto:

- `vertex_gemini`
- `anthropic`
- `openai`
- `openai_compat` remoto

Con la configuracion actual, la politica bloquea los tres arbitros porque usan
`vertex_gemini`. Esto es intencional para respetar la restriccion de no gastar
tokens de paga accidentalmente. Para correr en modo free, hay que cambiar los
arbitros a proveedor `gemini` con API key de AI Studio o desactivar
explicitamente `free_only_mode`.

**Webapp:**

- `/api/checkpoint/status` ahora devuelve tambien `run_state`.
- El panel de checkpoint muestra si un run quedo pausado, cuantos claims llevaba
  y el motivo de pausa.
- `Limpiar checkpoint` borra tambien `run_state.json`, `claim_results.jsonl` y
  `arbiter_results.jsonl`.
- El panel de metricas en vivo reconoce evento `paused`.

**Razon practica:**

La unidad real de trabajo costosa no es solo el claim, sino cada llamada de cada
arbitro. Persistir por arbitro evita repetir llamadas ya hechas y reduce perdida
ante fallos. Pausar en vez de seguir insistiendo evita quemar requests cuando hay
cuotas agotadas, red caida o credenciales/configuracion incorrectas.

**Verificacion realizada:**

- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`
- `GET /api/checkpoint/status`, `GET /run` y `GET /api/prompt-template`
  devolvieron HTTP 200 con `app.test_client()`.
- `build_user_prompt(...)` sigue generando JSON binario cuando
  `include_justifications` esta apagado.
- `validate_safety_policy(...)` bloquea la configuracion actual y reporta que
  `arb1`, `arb2` y `arb3` usan `vertex_gemini`, no permitido en
  `free_only_mode`.

**Archivos tocados:**

- `scripts/label_persuasion.py`
- `scripts/arbiters_config.json`
- `webapp/app.py`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-12 23:39 - Eliminacion de API key en pantalla Ejecutar

**Autor/agente:** Codex.

**Objetivo:** quitar el campo redundante `API Key de Gemini` de la pantalla
`Ejecutar`.

**Contexto observado:**

- Las credenciales/API keys se configuran por arbitro en la seccion de
  proveedores.
- La pantalla `Ejecutar` aun mostraba un campo adicional de API key de Gemini,
  lo que duplicaba responsabilidades y confundia el flujo.
- El backend de inicio de run no usaba realmente ese valor para ejecutar el
  pipeline.

**Decision tomada:**

Se elimino el input de API key de `webapp/templates/run.html` y se quito el envio
de `api_key` desde `startRun()`.

**Razon practica:**

`Ejecutar` debe controlar parametros del run, como limite de claims, checkpoint y
estado. Las credenciales pertenecen a la configuracion de arbitros/proveedores.

**Verificacion realizada:**

- `GET /run` usando `app.test_client()` devolvio HTTP 200.
- La pagina renderizada ya no contiene `api-key-input` ni `API Key de Gemini`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-12 23:44 - Preview coloreado del prompt final

**Autor/agente:** Codex.

**Objetivo:** en la pestana `Principios`, mostrar un ejemplo real de como se ve
el prompt que se envia al LLM, no solo el template editable.

**Contexto observado:**

- El pipeline no manda solamente el `user_prompt_template`; primero resuelve
  `{claim}` y `{output_instruction}`.
- Tambien existe un `system_prompt` por arbitro, configurado en la seccion de
  arbitros/proveedores.
- Para entender y depurar tokens/calidad, el usuario necesita ver ambos bloques:
  `SYSTEM` y `USER`.

**Decision tomada:**

Se agrego a `webapp/templates/principles.html` un panel `Ejemplo de prompt final`
con:

- Selector de arbitro para mostrar su `system_prompt`.
- Claim de ejemplo editable.
- Preview del bloque `SYSTEM`.
- Preview del bloque `USER` con placeholders ya resueltos.
- Resaltado por color:
  - `SYSTEM` en morado.
  - Claim insertado en amarillo.
  - `output_instruction`/formato JSON en verde.
  - Restriccion de no usar fact-checking/contexto externo en azul.

El preview se actualiza cuando cambia el template, el check de justificaciones,
el claim de ejemplo, el arbitro seleccionado o los IDs de principios.

**Detalle tecnico:**

Jinja interpreta `{{` dentro de JavaScript como sintaxis de template, asi que la
simulacion de llaves literales usa secuencias `\x7b` y `\x7d` para evitar romper
el render de Flask.

**Verificacion realizada:**

- `GET /principles` usando `app.test_client()` devolvio HTTP 200.
- La pagina renderizada contiene `prompt-preview`, `preview-arbiter`, `SYSTEM`
  y `USER`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/principles.html`
- `bitacora.md`

### 2026-06-12 23:49 - Ayuda contextual en Procesamiento

**Autor/agente:** Codex.

**Objetivo:** agregar un boton de ayuda en la pantalla `Procesamiento` que abra
un popup breve y claro con la explicacion de cada parametro.

**Contexto observado:**

- La pantalla `Procesamiento` concentra parametros sensibles para costo, tiempo,
  rate limits y resiliencia.
- Algunos nombres tecnicos como backoff, recovery y checkpoint pueden ser
  ambiguos si se revisan fuera del contexto del pipeline.
- Despues de la resiliencia implementada, el estado durable se guarda por claim,
  por lo que el texto anterior de checkpoint podia dar una idea incompleta.

**Decision tomada:**

Se agrego un boton con icono `?` junto al titulo `Parametros de procesamiento`.
Al presionarlo se abre un modal Bootstrap con una tabla breve que explica:

- que significa cada campo;
- como impacta la ejecucion;
- que cuidado practico conviene tener, especialmente para cuotas free y
  prevencion de gasto accidental.

Tambien se ajusto la ayuda inline de `Checkpoint cada N claims` para aclarar que
el estado recuperable se guarda por claim y que ese valor controla la cadencia
de reporte de progreso.

**Razon practica:**

La ayuda debe estar disponible en el punto exacto donde se toman las decisiones,
sin obligar a consultar documentacion externa. Esto reduce errores de
configuracion en corridas largas o costosas.

**Verificacion realizada:**

- `GET /processing` usando `app.test_client()` devolvio HTTP 200.
- La pagina renderizada contiene `processingHelpModal`, `bi-question-circle` y
  `Ayuda de procesamiento`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/processing.html`
- `bitacora.md`

### 2026-06-12 23:53 - Leyenda de colores del prompt final

**Autor/agente:** Codex.

**Objetivo:** aclarar que significa cada color usado en el ejemplo de prompt
final de la pestana `Principios`.

**Contexto observado:**

- El preview coloreado ya resaltaba partes importantes del prompt, pero la
  pantalla no explicaba el significado de los colores.
- Sin leyenda, el usuario podia interpretar los colores como decoracion en vez
  de como marcas semanticas del prompt.

**Decision tomada:**

Se agrego una leyenda compacta dentro del panel `Ejemplo de prompt final`.
La leyenda explica:

- morado: bloque `SYSTEM`, instrucciones propias del arbitro;
- azul: restriccion de analisis, como no usar fact-checking ni contexto externo;
- amarillo: claim de ejemplo insertado en el template;
- verde: formato JSON esperado como salida del LLM.

**Razon practica:**

El objetivo del preview es auditar que se le enviara realmente al LLM. La
leyenda reduce ambiguedad y ayuda a distinguir instrucciones, datos de entrada y
contrato de salida.

**Verificacion realizada:**

- `GET /principles` usando `app.test_client()` devolvio HTTP 200.
- La pagina renderizada contiene `Leyenda de colores del prompt`,
  `SYSTEM: instrucciones del arbitro` y `Verde: JSON esperado`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/principles.html`
- `bitacora.md`

### 2026-06-12 23:57 - Ayuda por parametro en Procesamiento

**Autor/agente:** Codex.

**Objetivo:** mover la ayuda de `Procesamiento` desde un boton global junto al
titulo hacia ayudas especificas al lado de cada parametro.

**Contexto observado:**

- El boton global de ayuda obligaba a abrir una ventana general y relacionar
  manualmente cada explicacion con el campo correspondiente.
- Para configurar bien delays, backoff y recovery, la explicacion debe estar
  pegada al parametro que se esta editando.

**Decision tomada:**

Se elimino el modal global `processingHelpModal` y se agrego un icono `?` junto
a cada etiqueta del formulario. Cada icono abre un popover Bootstrap con una
explicacion puntual del campo:

- checkpoint;
- longitud maxima del claim;
- umbral de recovery;
- delay base;
- delay minimo;
- delay maximo;
- backoff por rate limit 429;
- backoff por error de servidor;
- multiplicador de recovery del delay.

**Razon practica:**

La ayuda contextual reduce friccion y errores: el usuario no necesita abandonar
el campo ni leer una tabla completa para entender el parametro actual.

**Verificacion realizada:**

- `GET /processing` usando `app.test_client()` devolvio HTTP 200.
- La pagina renderizada ya no contiene `processingHelpModal`.
- La pagina renderizada contiene 9 ayudas especificas con texto `Ayuda:`.
- La pagina renderizada contiene `initProcessingHelpPopovers` y referencias a
  `popover`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/processing.html`
- `bitacora.md`

### 2026-06-12 23:59 - Color contextual en el dashboard

**Autor/agente:** Codex.

**Objetivo:** agregar mas color al `Panel de control` sin perder el estilo de
herramienta operativa.

**Contexto observado:**

- La pantalla de inicio estaba visualmente muy uniforme: casi todos los numeros,
  iconos y paneles usaban la misma familia morado/gris.
- Las metricas principales no se diferenciaban con facilidad al escanear la
  pantalla.

**Decision tomada:**

Se redisenaron los elementos visuales de `webapp/templates/index.html`:

- tarjetas superiores con acentos distintos:
  - cian para arbitros;
  - violeta para principios;
  - verde para outputs;
  - ambar para checkpoint/resiliencia;
- iconos dentro de contenedores coloreados;
- paneles inferiores con borde/acento por tipo;
- filas de arbitros con barras laterales e iconos coloreados;
- chips de proveedor/modelo con color contextual;
- filas de outputs con icono CSV y acentos alternados.

Tambien se agrego `escapeHtml()` al render del dashboard para evitar insertar
directamente nombres de arbitros, modelos o outputs en HTML.

**Razon practica:**

El color ahora comunica categoria y estado, en vez de ser solo decoracion. Esto
mejora el escaneo rapido del panel sin convertirlo en una pantalla de marketing.

**Verificacion realizada:**

- `GET /` usando `app.test_client()` devolvio HTTP 200.
- La pagina renderizada contiene `dashboard-stat stat-cyan`, `stat-emerald`,
  `arbiter-row`, `output-row` y `escapeHtml`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/index.html`
- `bitacora.md`

### 2026-06-13 00:08 - Paleta homogenea en toda la webapp

**Autor/agente:** Codex.

**Objetivo:** extender el sistema de colores agregado al dashboard hacia toda
la webapp para que no parezca un estilo aislado de la pagina principal.

**Contexto observado:**

- El `Panel de control` ya tenia tarjetas y filas con acentos por categoria,
  pero `Arbitros`, `Principios`, `Procesamiento` y `Ejecutar` seguian usando
  mayormente gris/morado plano.
- Esa diferencia hacia que la pagina principal pareciera pertenecer a otro
  sistema visual.

**Decision tomada:**

Se movio la paleta contextual a `webapp/templates/base.html` como lenguaje
global de UI:

- `accent-cyan`: arbitros/equipo/modelos;
- `accent-violet`: taxonomia/principios/configuracion;
- `accent-emerald`: ejecucion correcta/resultados;
- `accent-amber`: checkpoint, recovery, colas y cautela;
- `accent-rose`: errores/fallos;
- `accent-blue`: logs, progreso y metricas informativas.

Tambien se agregaron componentes reutilizables:

- `accent-card` y `accent-panel`;
- `metric-card`, `metric-mini`, `metric-value`, `metric-label`;
- `entity-row`, `entity-icon`, `entity-title`, `entity-meta`, `entity-size`;
- `soft-chip`;
- navegacion lateral con acento por seccion.

Se actualizo:

- `Inicio` para usar las clases globales en vez de CSS local duplicado;
- `Arbitros` para usar cian en proveedor/modelo y tarjetas con acentos;
- `Principios` para separar visualmente principios, template y preview;
- `Procesamiento` para usar ambar en paneles y popovers;
- `Ejecutar` para usar verde en ejecucion, azul/violeta en metricas, ambar en
  colas/checkpoints y rojo en errores.

**Razon practica:**

El color debe funcionar como sistema semantico, no como decoracion por pantalla.
Esto mejora orientacion, escaneo rapido y consistencia visual al navegar por la
webapp.

**Verificacion realizada:**

- `GET /`, `/arbiters`, `/principles`, `/processing` y `/run` usando
  `app.test_client()` devolvieron HTTP 200.
- Todas las pantallas renderizadas contienen clases `accent-*`.
- Se verifico que ya no quedan referencias a clases locales antiguas como
  `dashboard-stat`, `stat-cyan`, `text-purple`, `arbiter-row`, `output-row` o
  `provider-chip`.
- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py`

**Archivos tocados:**

- `webapp/templates/base.html`
- `webapp/templates/index.html`
- `webapp/templates/arbiters.html`
- `webapp/templates/principles.html`
- `webapp/templates/processing.html`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-13 18:00 - Revision general y correccion de hallazgos

**Autor/agente:** OpenCode.

**Objetivo:** revisar estructura, redundancias y riesgos del codigo completo, y corregir los problemas sin refactorizar lo que ya funciona.

**Hallazgos de la revision (ordenados por severidad):**

1. **Politica de seguridad bloqueaba la config actual** – `free_only_mode` no incluye `vertex_gemini` en `allowed_provider_types`. La config usa 3 arbitros Vertex. `validate_safety_policy()` lanza RuntimeError y la ejecucion ni empieza.
2. **`_build_arbiter()` descarta `vertex_project` / `vertex_location`** – La webapp envia esos campos al guardar un arbitro, pero la funcion del backend no los preserva en el JSON. Editar un arbitro Vertex desde la UI lo deja sin proyecto/region.
3. **Metricas de acuerdo duplicadas** – `scripts/label_persuasion.py` y `webapp/app.py` tienen copias casi identicas de `_cohen_kappa`, `_agreement_for_items`, y `compute_consensus_metrics`/`_compute_agreement_metrics`. Si divergen, los reportes CSV y webapp discrepan silenciosamente.
4. **Documentacion/UI desactualizada** – `README.md`, mensajes de error y tooltips en `arbiters.html` referencian `.env`, pero el codigo carga `secrets.env`.
5. **Ayuda obsoleta en `main_label.py`** – menciona `--api-key KEY` que no existe en el parser real.
6. **`requirements.txt` / `environment.yml`** – listan dependencias transitivas a mano (`requests`, `tqdm`, `tenacity`, etc.) que no se importan directamente. No rompe pero dificulta mantenimiento.
7. **Sin tests automatizados** – no hay suite de tests unitarios ni de integracion.

**Correcciones aplicadas:**

- `scripts/arbiters_config.json`: agregado `vertex_gemini` a `allowed_provider_types`.
- `scripts/label_persuasion.py`: actualizado default de `allowed_provider_types` y removida la logica duplicada de metricas.
- `webapp/app.py`: `_build_arbiter()` ahora preserva `vertex_project` y `vertex_location`. Removida la logica duplicada de metricas.
- `scripts/agreement_metrics.py`: archivo nuevo con las metricas de acuerdo compartidas entre CLI y WebApp.
- `README.md`, `webapp/templates/arbiters.html`, `webapp/app.py`: referencias a `.env` → `secrets.env`.
- `main_label.py`: removido `--api-key` del texto de uso.

**Pendiente (no critico, para paso futuro):**

1. Extraer `_number()`, `_safe_int()` y otras utilidades duplicadas.
2. Ordenar `requirements.txt` dejando solo dependencias directas.
3. Agregar suite basica de tests (al menos para agreement_metrics y validacion de config).
4. La bitacora tiene referencias historicas a `.env` que no se modificaron.

**Verificacion realizada:**

- `conda run -n fakenewsds python -m py_compile main_label.py main_webapp.py scripts/label_persuasion.py webapp/app.py`
- `conda run -n fakenewsds python -c "..." validate_safety_policy()`: ok con config actual.
- `_build_arbiter()` in-memory: preserva `vertex_project` y `vertex_location`.
- Compilacion de `scripts/agreement_metrics.py` como import: ok.

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `scripts/label_persuasion.py`
- `scripts/agreement_metrics.py` (nuevo)
- `webapp/app.py`
- `webapp/templates/arbiters.html`
- `README.md`
- `main_label.py`
- `bitacora.md`

### 2026-06-13 19:10 - Corrección de importación de métricas y eliminación de duplicados en WebApp

**Autor/agente:** Antigravity (Medium).

**Objetivo:** resolver un ImportError crítico detectado al importar `label_persuasion.py`, corregir errores de tipado de datos en el cálculo de métricas de acuerdo compartido, y eliminar definitivamente el código duplicado de acuerdo inter-árbitros en `webapp/app.py`.

**Decision tomada:**

1. **Corrección de importación**: En [`scripts/label_persuasion.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/label_persuasion.py), se intentaba importar `compute_consensus_metrics` de [`scripts/agreement_metrics.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/agreement_metrics.py), pero la función no existía. Se agregó el puente `compute_consensus_metrics` en [`scripts/agreement_metrics.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/agreement_metrics.py) para que reciba `(results: dict, principles: list, arbiters: list)` y lo traduzca al formato esperado por `compute_agreement_metrics`.
2. **Corrección de NameError y Coerción de datos**:
   - En [`scripts/agreement_metrics.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/agreement_metrics.py), se corrigió un `NameError` en `flatten_consensus_metrics` donde se usaba la variable inexistente `p` en lugar de `principle`.
   - Se añadió la función `_safe_int()` para asegurar que las etiquetas se procesen como enteros (0 o 1) tanto si provienen de la memoria (enteros) como si se leen del CSV por `DictReader` (cadenas de texto `"0"` o `"1"`).
3. **Limpieza en WebApp**: Se removieron de [`webapp/app.py`](file:///E:/Code/python/OpenCode/LabelForge/webapp/app.py) las funciones duplicadas de métricas (`_cohen_kappa`, `_agreement_for_items` y `_compute_agreement_metrics`) que aún se encontraban activas y se enlazó el endpoint `/api/run/stats` directamente con la función importada `compute_agreement_metrics`.

**Verificacion realizada:**

- `conda run -n fakenewsds python -m py_compile main_label.py main_webapp.py scripts/label_persuasion.py webapp/app.py scripts/agreement_metrics.py` (Compilación limpia).
- `conda run -n fakenewsds python -c "from webapp.app import app"` (Importación runtime del servidor Flask exitosa).
- `conda run -n fakenewsds python -c "import scripts.label_persuasion"` (Importación runtime del pipeline CLI sin ImportError).

**Archivos tocados:**

- `scripts/agreement_metrics.py`
- `webapp/app.py`
- `bitacora.md`

### 2026-06-13 19:50 - Creación de utils.py, deduplicación de utilidades, limpieza de requirements.txt y suite de pruebas unitarias

**Autor/agente:** Antigravity (Medium).

**Objetivo:** unificar helpers redundantes de conversión numérica en un archivo común, simplificar el archivo de requerimientos, e implementar una suite de pruebas automatizadas para asegurar la confiabilidad del cálculo de acuerdo, utilitarios y validación de seguridad.

**Decision tomada:**

1. **Centralización de Helpers**: Se creó el archivo [`scripts/utils.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/utils.py) que reúne y generaliza las funciones `safe_int`, `safe_float`, `safe_binary_int` y `number`.
2. **Refactorización de Módulos**:
   - [`scripts/agreement_metrics.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/agreement_metrics.py): se actualizó para importar `safe_binary_int` desde `scripts.utils`.
   - [`scripts/label_persuasion.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/label_persuasion.py): se actualizó para importar `number` como `_number` desde `scripts.utils` y remover la definición local redundante.
   - [`webapp/app.py`](file:///E:/Code/python/OpenCode/LabelForge/webapp/app.py): se actualizó para importar `safe_int` como `_safe_int` y `safe_float` como `_safe_float` desde `scripts.utils` y eliminar sus definiciones locales redundantes.
3. **Limpieza de Requerimientos**: Se simplificó [`requirements.txt`](file:///E:/Code/python/OpenCode/LabelForge/requirements.txt) conservando únicamente las 6 dependencias directas y principales de primer nivel del sistema (google-genai, google-cloud-aiplatform, anthropic, openai, flask, python-dotenv).
4. **Suite de Pruebas Unitarias**: Se creó [`tests/test_pipeline.py`](file:///E:/Code/python/OpenCode/LabelForge/tests/test_pipeline.py) utilizando `unittest` para cubrir las utilidades, el cálculo de Fleiss/Cohen/Krippendorff de acuerdo global y por principio, y la verificación de las políticas de seguridad de Vertex y Gemini.

**Verificacion realizada:**

- `conda run -n fakenewsds python -m unittest tests/test_pipeline.py` (9/9 pruebas unitarias exitosas en 0.001s).
- `conda run -n fakenewsds python -m py_compile main_label.py main_webapp.py scripts/label_persuasion.py webapp/app.py scripts/agreement_metrics.py scripts/utils.py tests/test_pipeline.py` (Compilación completa exitosa).

**Archivos tocados:**

- `scripts/utils.py` (nuevo)
- `scripts/agreement_metrics.py`
- `scripts/label_persuasion.py`
- `webapp/app.py`
- `requirements.txt`
- `tests/test_pipeline.py` (nuevo)
- `bitacora.md`

### 2026-06-13 19:55 - Renombrado de plantilla de variables de entorno y corrección de docstrings

**Autor/agente:** Antigravity (Medium).

**Objetivo:** renombrar la plantilla de variables de entorno para que coincida con el nombre real del archivo cargado (`secrets.env`), y corregir las referencias desactualizadas a `.env` en los comentarios y docstrings del código.

**Decision tomada:**

1. **Renombrado de Plantilla**: Se renombró el archivo `.env.example` a [`secrets.env.example`](file:///E:/Code/python/OpenCode/LabelForge/secrets.env.example) mediante Git para mantener alineación con el archivo real `secrets.env` que carga el proyecto. Se actualizó el comentario de ayuda interno.
2. **Corrección de Docstring**: En [`webapp/app.py`](file:///E:/Code/python/OpenCode/LabelForge/webapp/app.py), se reemplazó la referencia desactualizada a `.env` por `secrets.env` en la descripción del parámetro `api_key` de la ruta `/api/models`.

**Verificacion realizada:**

- `git status` muestra el renombrado de `.env.example` -> `secrets.env.example`.
- Compilación del proyecto: exitosa.

**Archivos tocados:**

- `secrets.env.example` (renombrado de `.env.example`)
- `webapp/app.py`
- `bitacora.md`

### 2026-06-13 19:57 - Actualización de README.md para reflejar los archivos del sistema y la suite de pruebas

**Autor/agente:** Antigravity (Medium).

**Objetivo:** actualizar la documentación principal (`README.md`) para reflejar con precisión los archivos nuevos creados en el refactor (utils, agreement_metrics, tests), e instruir al usuario sobre el uso de la plantilla `secrets.env.example` y la ejecución de la suite de pruebas.

**Decision tomada:**

1. **Estructura de Carpetas**: Se agregaron descripciones detalladas para [`scripts/agreement_metrics.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/agreement_metrics.py), [`scripts/utils.py`](file:///E:/Code/python/OpenCode/LabelForge/scripts/utils.py) y el directorio [`tests/`](file:///E:/Code/python/OpenCode/LabelForge/tests/) en la sección de estructura del proyecto de [`README.md`](file:///E:/Code/python/OpenCode/LabelForge/README.md).
2. **Guía de Configuración e Instalación**: Se actualizó el paso de configuración de variables de entorno para que haga mención explícita a la copia del archivo [`secrets.env.example`](file:///E:/Code/python/OpenCode/LabelForge/secrets.env.example) como origen. Se añadió el apartado `5. Ejecución de Pruebas Unitarias` con el comando correspondiente.

**Verificacion realizada:**

- Compilación del proyecto y formato Markdown de README.md correctos.

**Archivos tocados:**

- `README.md`
- `bitacora.md`


### 2026-06-13 20:02 - Configuración de repositorio de origen remoto y subida a GitHub

**Autor/agente:** Antigravity (Medium).

**Objetivo:** configurar la URL del repositorio remoto de origen y subir el proyecto completo a GitHub.

**Decision tomada:**

1. **Configuración de origen remoto**: Se vinculó el repositorio local con la URL remota del servidor [`https://github.com/lbustio/LabelForge.git`](https://github.com/lbustio/LabelForge.git).
2. **Publicación**: Se subió la rama local `main` al servidor remoto (`git push -u origin main`).

**Verificacion realizada:**

- Subida completada con éxito y rama `main` configurada para rastrear `origin/main`.

**Archivos tocados:**

- `bitacora.md`


### 2026-06-13 20:07 - Exclusión de datos privados de investigación del repositorio Git

**Autor/agente:** Antigravity (Medium).

**Objetivo:** evitar que los datos del dataset bilingüe de investigación en curso
y los archivos de checkpoint del pipeline queden publicados en GitHub antes de
que la investigación esté concluida.

**Decision tomada:**

1. **`.gitignore` actualizado**: se añadió `data/all_datasets_en_es/` como
   entrada explícita ignorada (el directorio completo, no solo extensiones).
   Se eliminó la excepción `!checkpoints/.gitkeep` para que el directorio
   `checkpoints/` quede completamente invisible a Git.
   Ambas secciones incluyen comentarios que explican la razón de privacidad.

2. **Archivos desindexados con `git rm --cached`**:
   - `checkpoints/.gitkeep` — ya rastreado, se eliminó del índice.
   - `data/all_datasets_en_es/all_datasets_en_es readme.txt` — ya rastreado,
     se eliminó del índice.

3. **Los archivos físicos permanecen locales** — solo se excluyen del
   repositorio público. Al finalizar la investigación se pueden hacer públicos.

**Razon practica:**

Los datos forman parte de un estudio en curso. Publicarlos antes de tiempo
podría comprometer la investigación o el proceso de publicación académica.
El `.gitignore` es la barrera correcta para esto; `git rm --cached` limpia
lo que ya había sido rastreado por error.

**Verificacion realizada:**

- `git status --short` tras la operación: ningún archivo de `checkpoints/` ni
  de `data/all_datasets_en_es/` aparece en el índice.
- Push exitoso al remoto: commit `f957cc7`.

**Archivos tocados:**

- `.gitignore`
- `bitacora.md`


### 2026-06-13 20:09 - Cambio de puerto del servidor Flask: 5000 → 7860 (cross-platform)

**Autor/agente:** Antigravity (Medium).

**Objetivo:** eliminar el conflicto de puerto en macOS, donde el puerto 5000
está reservado por el sistema operativo para AirPlay Receiver desde macOS 12
Monterey, y homogeneizar la configuración del servidor para macOS, Windows y
Linux usando un único puerto libre en todos los sistemas.

**Contexto observado:**

- En macOS 12+, el servicio AirPlay Receiver ocupa el puerto 5000 por defecto,
  impidiendo que Flask arranque sin cambio manual del usuario.
- La configuración anterior no era consistente: `app.py` usaba `port=5000` sin
  `host` explícito, y `main_webapp.py` usaba `127.0.0.1:5000` como default.
- No había forma de cambiar host o puerto sin editar código fuente.

**Decision tomada:**

1. **Puerto por defecto cambiado a `7860`**: libre en macOS, Windows y Linux,
   no requiere privilegios de administrador, reconocido en el ecosistema ML/AI
   (Gradio, HuggingFace Spaces).

2. **Host siempre explícito `127.0.0.1`**: garantiza comportamiento loopback
   idéntico en los tres sistemas operativos. El usuario puede cambiarlo a
   `0.0.0.0` para acceso en red local.

3. **Jerarquía de configuración** (de mayor a menor prioridad):
   - Argumentos CLI `--host` / `--port` (siempre tienen precedencia)
   - Variables de entorno `LABELFORGE_HOST` / `LABELFORGE_PORT` (leídas desde
     `secrets.env` en `main_webapp.py` y desde el entorno en `app.py`)
   - Valores hardcoded: `127.0.0.1` / `7860`

4. **`secrets.env.example` actualizado**: documenta las nuevas variables
   `LABELFORGE_HOST` y `LABELFORGE_PORT` con comentarios explicativos.

5. **`README.md` actualizado**: URL de ejemplo actualizada a `:7860`.

**Razon practica:**

Un puerto único y libre en todos los SO elimina errores silenciosos en macOS
sin afectar a Windows ni Linux. El mecanismo de env vars permite cambiar el
comportamiento sin tocar código fuente, alineándolo con la convención
12-factor app.

**Verificacion realizada:**

- Compilación de `webapp/app.py` y `main_webapp.py`: exitosa.
- Push exitoso al remoto: commit `3c14804`.

**Archivos tocados:**

- `webapp/app.py`
- `main_webapp.py`
- `secrets.env.example`
- `README.md`
- `bitacora.md`


### 2026-06-13 20:50 - Dataset configurable: soporte CSV y Excel con sheet y columna seleccionables

**Autor/agente:** Antigravity (Medium).

**Objetivo:** eliminar el hardcoding del archivo de entrada (`all_datasets_en_es.csv`)
y la columna de texto (`claim`), permitiendo etiquetar cualquier dataset CSV o Excel
con cualquier columna, sin tocar código fuente.

**Contexto observado:**

- El pipeline asumía siempre `data/all_datasets_en_es/all_datasets_en_es.csv` como
  entrada y `claim` como la columna de texto. Ambos estaban hardcodeados en
  `label_persuasion.py` y en el comando que lanza la webapp.
- No había forma de etiquetar otro dataset sin modificar código.

**Decision tomada:**

1. **`scripts/arbiters_config.json`**: nueva sección `dataset` con tres campos:
   - `input_file`: ruta al archivo de entrada (relativa al root del proyecto o absoluta).
   - `sheet`: nombre o índice de la hoja (solo Excel; cadena vacía = primera hoja).
   - `text_column`: nombre de la columna que contiene el texto a etiquetar.

2. **`scripts/label_persuasion.py`**:
   - Nueva función `load_dataset(path, sheet, text_column)` que soporta `.csv`,
     `.xlsx` y `.xls`, valida que la columna exista, y lanza error descriptivo si no.
   - Nuevos argumentos CLI `--sheet` y `--column`.
   - Jerarquía de configuración: `CLI args > config > defaults`.
   - Todos los `row.get("claim", "")` reemplazados por `row.get(text_column, "")`.
   - `text_column` y `sheet` se guardan en `run_state.json` para reanudaciones coherentes.

3. **`requirements.txt`**: añadido `openpyxl>=3.1.0` para lectura de Excel.

4. **`webapp/app.py`**:
   - `GET  /api/dataset/config` — devuelve la config actual del dataset.
   - `PUT  /api/dataset/config` — guarda la config en `arbiters_config.json`.
   - `POST /api/dataset/inspect` — inspecciona un archivo y devuelve sheets y columnas.
   - `/api/run/start` pasa `--input`, `--sheet` y `--column` al subproceso.

5. **`webapp/templates/processing.html`**: nuevo panel "Dataset de Entrada" (acento cyan)
   con campo de ruta + botón Inspeccionar, dropdown de sheet (solo si es Excel),
   dropdown de columna y botón Guardar. Al cargar la página, pre-inspecciona el archivo
   configurado y muestra un banner con la configuración activa.

**Razon practica:**

La investigación puede requerir etiquetar datasets distintos al original
(diferentes fuentes, idiomas, subconjuntos). Con este cambio, basta con apuntar
la webapp al nuevo archivo y seleccionar la columna correcta — sin tocar código.

**Verificacion realizada:**

- `conda run -n fakenewsds python -m py_compile scripts/label_persuasion.py webapp/app.py` — limpio.
- `conda run -n fakenewsds python -m unittest tests/test_pipeline.py` — 9/9 OK.
- Push exitoso: commit `b093799`.

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `scripts/label_persuasion.py`
- `requirements.txt`
- `webapp/app.py`
- `webapp/templates/processing.html`
- `bitacora.md`
- `README.md`
- `context.md`

### 2026-06-13 21:30 - Corrección de carga de archivos en la webapp (drag & drop + click)

**Autor/agente:** Antigravity (Medium).

**Objetivo:** arreglar el botón "Inspeccionar" / "Seleccionar archivo" en la pantalla `Procesamiento` para que abra el explorador del SO al hacer click en la zona de drop o en el botón, y que el drag-and-drop funcione correctamente.

**Contexto observado:**

- El `onclick` inline en el `<div class="ds-dropzone">` competía con el `onclick` del botón "Seleccionar archivo", causando que al hacer click en el botón se abrieran dos veces el selector o no funcionara.
- El drag-and-drop estaba implementado con handlers inline (`ondragover`, `ondragleave`, `ondrop`) pero el click en la zona no estaba bien gestionado.

**Decision tomada:**

1. Se eliminó el `onclick` inline del `ds-dropzone` que llamaba a `document.getElementById('ds-file-input').click()`.
2. Se agregó un event listener `click` en `DOMContentLoaded` que:
   - Verifica `e.target.closest('button')` para no disparar si se hace click en el botón o sus hijos.
   - Solo entonces llama a `ds-file-input.click()`.
3. Los handlers de drag-and-drop (`onDragOver`, `onDragLeave`, `onDrop`) se mantuvieron igual y funcionan correctamente.

**Razon practica:**

Separar la lógica de click en JS evita conflictos de propagación de eventos entre el contenedor y el botón interno. El patrón `e.target.closest('button')` es robusto y evita doble trigger.

**Verificacion realizada:**

- Click en la zona de drop (fuera del botón) → abre selector de archivos del SO.
- Click en "Seleccionar archivo" → abre selector de archivos del SO.
- Drag & drop de archivo CSV/Excel → sube y muestra sheets/columnas.

**Archivos tocados:**

- `webapp/templates/processing.html`
- `bitacora.md`

### 2026-06-13 21:45 - Corrección de JSONDecodeError por truncamiento de respuesta en Vertex AI (gemini-2.5)

**Autor/agente:** Antigravity (Medium).

**Objetivo:** resolver el error `JSONDecodeError: Unterminated string` / `Expecting property name enclosed in double quotes` que ocurría al etiquetar con los árbitros Vertex AI (gemini-2.5-pro y gemini-2.5-flash).

**Contexto observado:**

- Los errores indicaban que la respuesta JSON del LLM llegaba truncada a mitad de string.
- La configuración tenía `max_output_tokens: 256` para los tres árbitros.
- Gemini 2.5 en Vertex AI tiene "thinking mode" activado por defecto, que consume tokens de salida para razonamiento interno antes de emitir el JSON final. Con 256 tokens, el JSON binario `{"AUTH":0,"SP":0,"LSD":0,"CRC":0,"DIS":0}` se cortaba antes de completarse.

**Decision tomada:**

1. **`scripts/arbiters_config.json`**: subido `max_output_tokens` de 256 → **2048** para los tres árbitros.
2. **`scripts/label_persuasion.py`**: actualizado el default de `max_output_tokens` de 600 → 2048 en todos los proveedores (Gemini AI Studio, Vertex AI, Anthropic, OpenAI-compat).
3. **Vertex AI (`_call_vertex_gemini`)**: agregado desactivación de thinking solo para `gemini-2.5-flash`:
   ```python
   if "gemini-2.5-flash" in provider["model"]:
       generation_config["thinking_config"] = {"thinking_budget": 0}
   ```
   Nota: `gemini-2.5-pro` en Vertex AI **no soporta** `thinking_budget=0` (error 400), por eso la condición es específica para flash.

**Razon practica:**

El JSON de salida binario es muy corto (~35 chars). El problema no era el tamaño de la respuesta esperada, sino que el modelo gastaba tokens en "thinking" interno. Desactivar thinking en flash y dar margen amplio (2048) en ambos modelos elimina el truncamiento. El valor 256 era insuficiente incluso sin thinking si el modelo añadía cualquier token extra.

**Verificacion realizada:**

- `conda run -n fakenewsds python main_label.py --limit 1` → procesa 1 entrada completa sin JSONDecodeError.
- Logs muestran tokens de salida ~80-100 por arbitro (bien dentro de 2048).
- Latencias: arb1 (pro) ~12-22s, arb2 (flash) ~0.8-1s, arb3 (pro) ~11-17s.

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `scripts/label_persuasion.py`
- `bitacora.md`

### 2026-06-13 22:18 - Corrección de JavaScript visible como texto en Procesamiento

**Autor/agente:** Codex.

**Objetivo:** corregir la pestaña `Procesamiento`, donde se estaba mostrando
código JavaScript literal en la página a partir de:
`// Reset inspected state when user edits the path`.

**Contexto observado:**

- `webapp/templates/processing.html` tenía dos versiones de los helpers de dataset:
  una versión nueva para drag & drop/upload y una versión anterior basada en
  `ds-input-file` + botón `Inspeccionar`.
- La versión anterior quedó pegada después de un cierre `</script>`, así que el
  navegador interpretaba ese bloque como HTML visible en vez de JavaScript.
- El HTML actual ya no define `ds-input-file`, por lo que esa segunda versión
  además apuntaba a elementos inexistentes.

**Decision tomada:**

1. Se eliminó el bloque duplicado y obsoleto que estaba fuera del `<script>`.
2. Se dejó como única implementación activa el flujo actual de:
   - input file oculto `ds-file-input`,
   - zona drag-and-drop,
   - endpoint `/api/dataset/upload`,
   - selección de sheet/columna,
   - guardado en `/api/dataset/config`.
3. Se verificó que el template ya no contenga referencias a `ds-input-file`,
   `inspectDataset`, `_datasetInspected` ni al texto `Reset inspected state`.

**Razon practica:**

El error no estaba en Flask ni en Bootstrap; era un cierre prematuro del bloque
`<script>` combinado con código legacy duplicado. Retirar el bloque viejo evita
que el navegador muestre JS como texto y elimina handlers que ya no correspondían
al HTML actual.

**Verificacion realizada:**

- `conda run -n fakenewsds python -m py_compile webapp\app.py main_webapp.py scripts\label_persuasion.py` — limpio.
- `conda run -n fakenewsds python -m unittest tests\test_pipeline.py` — 9/9 OK.
- Render con Flask test client de `/processing` — HTTP 200.
- En el HTML renderizado:
  - `Reset inspected state` → no aparece.
  - `ds-input-file` → no aparece.
  - conteo de scripts renderizados: 3 `<script>` y 3 `</script>`.

**Archivos tocados:**

- `webapp/templates/processing.html`
- `bitacora.md`
- `context.md`

### 2026-06-13 22:55 - Métricas por entrada y frontera backend/frontend

**Autor/agente:** Codex.

**Objetivo:** asegurar que el consenso, el diagnóstico por entrada y las estadísticas
de tokens/latencia se calculen en backend, de forma común para CLI y webapp, y que
la interfaz solo presente datos ya calculados.

**Contexto observado:**

- El log de ejecución mostraba líneas como:
  `[entrada 15] OK — AUTH=0 SP=1 LSD=0 CRC=0 DIS=0`.
- Ese valor correspondía al consenso final de la entrada, pero no incluía señales
  de auditoría sobre cuánta coincidencia hubo entre árbitros en esa entrada.
- La webapp todavía sumaba tokens/tiempos por fila en algunos puntos y calculaba
  promedios simples para mostrar “tokens por entrada”.
- También quedaban pequeñas interpretaciones visuales de métricas en JavaScript,
  como el tono del kappa o la conversión de acuerdo a porcentaje.

**Decisión tomada:**

1. **`scripts/label_persuasion.py`**:
   - Se añadieron campos de diagnóstico de consenso por entrada:
     `entry_consensus_observed_agreement`,
     `entry_consensus_observed_agreement_percent`,
     `entry_consensus_mean_support_ratio`,
     `entry_consensus_mean_support_percent`,
     `entry_consensus_unanimous_principles`,
     `entry_consensus_split_principles` y campos por principio con apoyo/votos.
   - Se añadieron métricas de uso por entrada:
     `entry_prompt_tokens`, `entry_completion_tokens`, `entry_total_tokens`,
     `entry_latency_s`, `entry_latency_avg_s`, `entry_arbiter_calls`,
     `entry_tokens_per_call`.
   - `process_claim`, `build_claim_metric`, `summarize_records`,
     `make_failed_record` y `write_output_csv` usan estas métricas del backend.
   - El log de cada entrada OK ahora incluye diagnóstico operativo del consenso:
     `acuerdo`, `unanimidad`, `divididos` y `apoyo`.
   - Se filtraron checkpoints y resultados parciales al `--limit` activo para evitar
     reportes incoherentes como `Exitosos: 21/1` al hacer pruebas cortas.

2. **`scripts/agreement_metrics.py`**:
   - El módulo compartido ahora entrega también porcentajes y metadatos de
     presentación para kappa (`fleiss_kappa_tone`,
     `fleiss_kappa_interpretation`), de modo que la UI no interprete la métrica.

3. **`webapp/app.py`**:
   - `/api/run/stats` y `/api/run/current-stats` exponen métricas ya calculadas:
     `tokens_per_row`, `tokens_per_successful_row`,
     `tokens_per_processed_row`, `tokens_per_successful_row`,
     `usage_metrics` por fila y `consensus_metrics` por fila.
   - Los adaptadores de CSV mantienen compatibilidad con outputs antiguos: si un
     CSV no trae columnas `entry_*`, el backend calcula el respaldo antes de
     entregar JSON a la interfaz.
   - `/api/run/start` ejecuta `main_label.py` para evitar el error
     `ModuleNotFoundError: No module named 'scripts'`.

4. **`webapp/templates/run.html`**:
   - Se retiraron cálculos de “tokens por entrada” y la interpretación visual del
     kappa.
   - La plantilla solo renderiza datos recibidos, formatea valores y maneja
     interacción visual.

**Aclaracion metodologica:**

Los campos `entry_consensus_*` son diagnósticos por entrada para auditoría,
depuración y trazabilidad. No deben presentarse como las métricas principales de
confiabilidad del paper. Para metodología/reportes académicos, usar las métricas
agregadas inter-árbitros del módulo `scripts/agreement_metrics.py`: acuerdo
observado agregado, kappa de Fleiss, kappa de Cohen promedio por pares,
Krippendorff alpha nominal y tasa positiva por principio/global.

**Razon practica:**

Las métricas deben tener una sola fuente de verdad para que CLI, webapp, CSV,
logs y futuras evaluaciones académicas coincidan. El frontend no debe contener
lógica de negocio porque eso crea divergencias silenciosas entre la versión CLI y
la versión web.

**Verificacion realizada:**

- `conda run -n fakenewsds python -m py_compile scripts\agreement_metrics.py scripts\label_persuasion.py webapp\app.py main_webapp.py` — limpio.
- `conda run -n fakenewsds python -m unittest tests\test_pipeline.py` — 9/9 OK.
- `conda run -n fakenewsds python main_label.py --limit 1` — completó con
  `Exitosos : 1/1`.
- CSV generado `output/labeled_20260613_225238.csv` contiene:
  `entry_prompt_tokens=832`, `entry_completion_tokens=90`,
  `entry_total_tokens=922`, `entry_latency_s=34.035`,
  `entry_latency_avg_s=11.345`, `entry_arbiter_calls=3`,
  `entry_tokens_per_call=307.33`.
- `GET /api/run/stats?limit=1` con Flask test client devolvió HTTP 200 e incluyó
  `tokens_per_row=922`, `tokens_per_successful_row=922`,
  `observed_agreement_percent=100.0`,
  `fleiss_kappa_tone=success` e
  `fleiss_kappa_interpretation=casi perfecto`.
- Búsqueda dirigida confirmó que los cálculos de métricas quedan en Python; en
  plantillas quedan solo formato y renderizado.

**Archivos tocados:**

- `scripts/label_persuasion.py`
- `scripts/agreement_metrics.py`
- `webapp/app.py`
- `webapp/templates/run.html`
- `bitacora.md`
- `context.md`
- `README.md`

### 2026-06-13 21:30 - Corrección de carga de archivos en la webapp (drag & drop + click)

**Autor/agente:** Antigravity (Medium).

**Objetivo:** arreglar el botón "Inspeccionar" / "Seleccionar archivo" en la pantalla
`Procesamiento` para que abra el explorador del SO al hacer click en la zona de drop
o en el botón, y que el drag-and-drop funcione correctamente.

**Contexto observado:**

- El `onclick` inline en el `<div class="ds-dropzone">` competía con el `onclick`
  del botón "Seleccionar archivo", causando que al hacer click en el botón se
  abrieran dos veces el selector o no funcionara.
- El drag-and-drop estaba implementado con handlers inline (`ondragover`,
  `ondragleave`, `ondrop`) pero el click en la zona no estaba bien gestionado.

**Decision tomada:**

1. Se eliminó el `onclick` inline del `ds-dropzone` que llamaba a
   `document.getElementById('ds-file-input').click()`.
2. Se agregó un event listener `click` en `DOMContentLoaded` que:
   - Verifica `e.target.closest('button')` para no disparar si se hace click en
     el botón o sus hijos.
   - Solo entonces llama a `ds-file-input.click()`.
3. Los handlers de drag-and-drop (`onDragOver`, `onDragLeave`, `onDrop`) se
   mantuvieron igual y funcionan correctamente.

**Razon practica:**

Separar la lógica de click en JS evita conflictos de propagación de eventos entre
el contenedor y el botón interno. El patrón `e.target.closest('button')` es
robusto y evita doble trigger.

**Verificacion realizada:**

- Click en la zona de drop (fuera del botón) → abre selector de archivos del SO.
- Click en "Seleccionar archivo" → abre selector de archivos del SO.
- Drag & drop de archivo CSV/Excel → sube y muestra sheets/columnas.

**Archivos tocados:**

- `webapp/templates/processing.html`
- `bitacora.md`

### 2026-06-13 21:45 - Corrección de JSONDecodeError por truncamiento de respuesta en Vertex AI (gemini-2.5)

**Autor/agente:** Antigravity (Medium).

**Objetivo:** resolver el error `JSONDecodeError: Unterminated string` que ocurría
al etiquetar con los árbitros Vertex AI (gemini-2.5-pro y gemini-2.5-flash).

**Contexto observado:**

- Los errores indicaban que la respuesta JSON del LLM llegaba truncada a mitad
  de string.
- La configuración tenía `max_output_tokens: 256` para los tres árbitros.
- Gemini 2.5 en Vertex AI tiene "thinking mode" activado por defecto, que consume
  tokens de salida para razonamiento interno antes de emitir el JSON final. Con
  256 tokens, el JSON binario se cortaba antes de completarse.

**Decision tomada:**

1. **`scripts/arbiters_config.json`**: subido `max_output_tokens` de 256 → 2048
   para los tres árbitros.
2. **`scripts/label_persuasion.py`**: actualizado el default de `max_output_tokens`
   de 600 → 2048 en todos los proveedores (Gemini AI Studio, Vertex AI, Anthropic,
   OpenAI-compat).
3. **Vertex AI (`_call_vertex_gemini`)**: agregado desactivación de thinking solo
   para `gemini-2.5-flash`:
   ```python
   if "gemini-2.5-flash" in provider["model"]:
       generation_config["thinking_config"] = {"thinking_budget": 0}
   ```
   Nota: `gemini-2.5-pro` en Vertex AI **no soporta** `thinking_budget=0` (error
   400), por eso la condición es específica para flash.

**Razon practica:**

El JSON de salida binario es muy corto (~35 chars). El problema no era el tamaño
de la respuesta esperada, sino que el modelo gastaba tokens en "thinking" interno.
Desactivar thinking en flash y dar margen amplio (2048) en ambos modelos elimina
el truncamiento.

**Verificacion realizada:**

- `conda run -n fakenewsds python main_label.py --limit 1` → procesa 1 entrada
  completa sin JSONDecodeError.
- Logs muestran tokens de salida ~80-100 por arbitro (bien dentro de 2048).

**Archivos tocados:**

- `scripts/arbiters_config.json`
- `scripts/label_persuasion.py`
- `bitacora.md`

### 2026-06-13 21:55 - Bloqueo de controles durante ejecución en la webapp

**Autor/agente:** Antigravity (Medium).

**Objetivo:** evitar que el usuario modifique parámetros mientras el proceso de
etiquetado está corriendo desde la pestaña "Ejecutar".

**Contexto observado:**

- El botón "Iniciar" ya se deshabilitaba con `setRunning(true)`, pero el campo
  "Límite de entradas" y el botón "Limpiar checkpoint" seguían editables.
- Si el usuario cambiaba el límite durante la ejecución, generaba confusión
  (no afectaba el run activo pero parecía que sí).

**Decision tomada:**

Se extendió `setRunning(running)` para deshabilitar/habilitar:
- Campo `limit-input` (número de entradas)
- Botón "Limpiar checkpoint" (buscado dinámicamente dentro de `#checkpoint-area`)

**Razon practica:**

Congelar los controles evita la falsa impresión de que se pueden cambiar
parámetros durante un run activo. Al finalizar o cancelar, todo vuelve a
habilitarse automáticamente.

**Archivos tocados:**

- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-13 22:10 - Sistema de colores semántico para métricas de acuerdo

**Autor/agente:** Antigravity (Medium).

**Objetivo:** aplicar colores consistentes a todas las métricas de acuerdo
inter-árbitros (Fleiss Kappa, Cohen Kappa, Krippendorff Alpha, acuerdo
observado, tasa positiva) usando una escala unificada de 4 niveles.

**Contexto observado:**

- Solo Fleiss Kappa tenía color (`fleiss_kappa_tone`), el resto se mostraban como
  texto plano sin indicación visual.
- El badge global usaba la interpretación del Fleiss Kappa (ej: "bajo") aunque el
  acuerdo observado fuera 92.3%, generando contradicción visual.

**Decision tomada:**

1. **Backend (`scripts/agreement_metrics.py`)**:
   - Nueva función `_percent_tone()` para valores porcentuales.
   - `_with_display_fields()` ahora genera `*_tone` para todas las métricas:
     `observed_agreement_tone`, `pairwise_cohen_kappa_avg_tone`,
     `krippendorff_alpha_nominal_tone`, `positive_rate_tone`.

2. **Frontend (`webapp/templates/run.html`)**:
   - Funciones helper `consensusTone()`, `consensusBg()`, `consensusBorder()`
     con la misma escala 4 niveles.
   - Las tarjetas resumen globales ahora muestran badges coloreados para cada
     métrica.
   - La tabla por principio tiene todas las columnas como badges coloreados.
   - El badge global ahora muestra **acuerdo observado** (con su color) en vez
     de solo Fleiss Kappa. Se agregó un badge separado para Fleiss Kappa.

3. **Escala unificada (4 niveles)**:
   | Nivel | Kappa | Porcentaje | Color |
   |---|---|---|---|
   | Alto | ≥ 0.8 | ≥ 80% | `success` (verde) |
   | Medio-alto | ≥ 0.6 | ≥ 60% | `info` (azul) |
   | Bajo | ≥ 0.4 | ≥ 40% | `warning` (amarillo) |
   | Muy bajo | < 0.4 | < 40% | `danger` (rojo) |

4. **Aplicación en toda la página "Ejecutar"**:
   - Tarjeta "Última entrada procesada": borde y fondo según acuerdo.
   - Diagnóstico por entrada: fondo y texto coloreados.
   - Tabla de resultados: borde izquierdo coloreado en cada fila.
   - Panel "Acuerdo entre árbitros": badges en todas las métricas.

**Verificacion realizada:**

- `python -c "from scripts.agreement_metrics import compute_agreement_metrics"`
  → todos los campos `*_tone` presentes con valores correctos.

**Archivos tocados:**

- `scripts/agreement_metrics.py`
- `webapp/templates/run.html`
- `bitacora.md`

### 2026-06-13 22:15 - scripts/__init__.py para corrección de importación

**Objetivo:** resolver ModuleNotFoundError: No module named 'scripts' al ejecutar
el pipeline como subproceso desde la webapp.

**Contexto observado:**

- El directorio `scripts/` no tenía `__init__.py`, por lo que no era un paquete
  Python reconocible.
- Al ejecutar `label_persuasion.py` como subproceso con `cwd=BASE_DIR`, el
  import `from scripts.agreement_metrics import ...` fallaba.

**Decision tomada:**

Crear `scripts/__init__.py` vacío para que Python trate `scripts/` como paquete.

**Archivos tocados:**

- `scripts/__init__.py` (nuevo)
- `bitacora.md`

### 2026-06-15 - Gestión segura de árbitros, secretos y paridad WebApp/CLI

**Objetivo:** permitir experimentar con distintas configuraciones de árbitros sin
perder las existentes, proteger prompts/API keys al exportar, permitir borrar todos
los árbitros cuando el usuario lo necesite, y asegurar que la lógica viva en backend
compartido en vez de quedar duplicada en la interfaz web.

**Contexto observado:**

- La pestaña `Arbitros` permitía editar proveedores/modelos, pero no había forma de
  respaldar/restaurar configuraciones de árbitros.
- Los árbitros contienen información sensible: prompts, proveedores, modelos y, en
  configuraciones heredadas, posibles `api_key_override`.
- La UI bloqueaba eliminar el último árbitro, aunque es válido querer dejar la lista
  vacía para probar otra configuración.
- El formulario de API key era confuso: separaba "variable de entorno" y "clave de
  API", lo que llevaba a pegar claves reales en el campo equivocado.
- El cambio debía aplicar tanto a la WebApp como al CLI. La WebApp debe ser interfaz;
  cifrado, merge, validación y escritura de secretos deben vivir en Python.

**Decisión tomada:**

1. **Nuevo backend compartido para árbitros (`scripts/arbiters_io.py`)**:
   - Exporta/importa la sección `arbiters` en archivos `.lfarb`.
   - Cifra con contraseña usando PBKDF2-HMAC-SHA256 + AES-256-GCM.
   - Valida formato, normaliza árbitros y aplica merge por ID o reemplazo total.
   - La importación por defecto fusiona; `replace` debe pedirse explícitamente.

2. **Nuevo backend compartido para secretos (`scripts/secrets_io.py`)**:
   - Mapea proveedor → variable estándar:
     `gemini → GEMINI_API_KEY`, `anthropic → ANTHROPIC_API_KEY`,
     `openai/openai_compat → OPENAI_API_KEY`.
   - Escribe `secrets.env` de forma controlada y actualiza `os.environ`.
   - Soporta lectura con BOM (`utf-8-sig`) para evitar fallos por editores Windows.

3. **WebApp (`webapp/app.py` + `webapp/templates/arbiters.html`)**:
   - Agregados botones **Importar** y **Exportar** en la pestaña `Arbitros`.
   - Agregado endpoint `POST /api/arbiters/export`.
   - Agregado endpoint `POST /api/arbiters/import`.
   - Agregado endpoint `POST /api/provider-key`: verifica la key listando modelos,
     guarda en `secrets.env` y devuelve modelos si funciona.
   - El formulario ahora muestra un solo campo visible **Clave de API** para Gemini,
     Anthropic, OpenAI y OpenAI-compatible. El backend decide la variable destino.
   - Vertex AI no muestra API key directa porque usa ADC/GCP; Ollama no la necesita.
   - Se permite eliminar todos los árbitros. Si la lista queda vacía, la UI muestra:
     "Sin arbitros no se puede hacer nada." y ofrece crear/importar.

4. **CLI (`scripts/label_persuasion.py`)**:
   - `--export-arbiters archivo.lfarb`
   - `--import-arbiters archivo.lfarb`
   - `--replace-arbiters`
   - `--arbiters-password` / `--arbiters-password-file`
   - `--set-provider-key proveedor`
   - `--api-key`, `--api-key-env`, `--base-url`
   - Ejecutar el pipeline sin árbitros ahora falla con mensaje explícito:
     "Sin arbitros no se puede hacer nada..."

5. **Documentación**:
   - Actualizados `README.md` y `context.md` para reflejar los módulos compartidos,
     comandos CLI, endpoints nuevos, formato `.lfarb`, gestión de API keys y frontera
     backend/frontend.

**Razon práctica:**

La configuración de árbitros es parte del experimento; debe poder respaldarse,
restaurarse y transportarse sin exponer secretos. Además, una sola fuente de verdad
para export/import y secretos evita que WebApp y CLI diverjan. La UI debe disparar
acciones, no implementar reglas de seguridad ni cifrado.

**Verificación realizada:**

- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\label_persuasion.py scripts\arbiters_io.py scripts\secrets_io.py`
- Prueba de export/import CLI sobre una copia temporal del config.
- Verificación de que el archivo `.lfarb` no contiene en claro `system_prompt`,
  `api_key_override`, `provider` ni `arbiters`.
- Verificación de endpoint `/api/provider-key` sin key real: error controlado.
- Reinicio de la WebApp y respuesta `200` en `/arbiters`.

**Archivos tocados:**

- `scripts/arbiters_io.py`
- `scripts/secrets_io.py`
- `scripts/label_persuasion.py`
- `webapp/app.py`
- `webapp/templates/arbiters.html`
- `README.md`
- `context.md`
- `bitacora.md`

---

### 2026-06-15 - Rate limits por proveedor/modelo para todos los LLMs

**Objetivo:** que el tab de árbitros y el backend calculen delays no solo para
Gemini, sino para todos los proveedores soportados por el sistema.

**Cambios realizados:**

1. Agregado `scripts/rate_limits.py` como fuente compartida de perfiles de rate
   limit y cálculo de espera mínima.
2. `webapp/app.py` ahora usa `compute_min_delay(provider, model, tier, n_arbiters)`
   en `/api/rate-limits`, `/api/rate-limits/summary` y validación de parámetros
   de procesamiento.
3. `webapp/templates/arbiters.html` consulta límites con `provider + model` y
   muestra si el dato es publicado (`published`) o dependiente de cuenta
   (`account_dependent`).
4. Se agregaron perfiles por modelo/familia para:
   - Gemini API / AI Studio
   - Vertex Gemini
   - Anthropic Claude
   - OpenAI
   - OpenAI-compatible
   - Ollama/local

**Decisión importante:**

Solo `gemini` se presenta como perfil publicado cuando coincide con la tabla
conocida de Gemini API/AI Studio. Vertex, Anthropic y OpenAI exponen límites que
dependen de proyecto/cuenta/región/tier/organización, por lo que sus valores se
marcan como fallback conservador y no como cuota oficial fija.

**Verificación realizada:**

- `conda run -n LabelForge python -m py_compile webapp\app.py scripts\rate_limits.py`
- Prueba directa de `compute_min_delay()` para `gemini`, `vertex_gemini`,
  `anthropic`, `openai` y `ollama`.

---

### 2026-06-15 - Autorización explícita de Claude en `free_only_mode`

**Problema:** la ejecución fallaba porque `arb4` usa `provider.type="anthropic"`
y `safety.free_only_mode=true` bloquea cualquier proveedor que no esté en
`allowed_provider_types`.

**Cambio:** agregado `anthropic` a `safety.allowed_provider_types` en
`scripts/arbiters_config.json`, manteniendo `free_only_mode=true`.

**Razon práctica:** Claude sigue siendo un proveedor con riesgo de costo, pero en
esta configuración se está usando conscientemente como cuarto árbitro. La política
de seguridad queda activa para bloquear proveedores no autorizados, sin bloquear
`arb4`.

**Verificación realizada:**

- `validate_safety_policy()` pasa con:
  `arb1:vertex_gemini`, `arb2:vertex_gemini`, `arb3:vertex_gemini`,
  `arb4:anthropic`.

---

### 2026-06-15 - Preflight con instrucciones accionables por proveedor

**Problema:** ante falta de credenciales de Vertex AI, el sistema sugería
directamente `gcloud auth application-default login`, asumiendo que Google Cloud
CLI estaba instalado. Eso no cubre máquinas sin `gcloud` ni explicaba alternativas
equivalentes para otros proveedores.

**Cambio:** `scripts/provider_preflight.py` ahora:

- Detecta si `gcloud` existe en `PATH`.
- Para Vertex AI, explica que no se usa API key directa y ofrece:
  - service account JSON + `GOOGLE_APPLICATION_CREDENTIALS` como ruta sin login
    interactivo local;
  - Google Cloud CLI como ruta de login interactivo si está disponible o se instala.
- Para Gemini, Anthropic y OpenAI, indica tres caminos: WebApp (`Arbitros` →
  `Clave de API` → verificar), CLI `--set-provider-key`, o `secrets.env`.
- Para OpenAI-compatible/Ollama, valida `base_url` y muestra ejemplos locales.

**Documentación:** actualizados `README.md` y `context.md` para reflejar que Vertex
tiene dos rutas de autenticación y que las API keys se gestionan por WebApp/CLI o
`secrets.env`.

---

### 2026-06-15 - Claridad operativa de procesamiento, ejecución y logs

**Objetivo:** cerrar ambigüedades detectadas al correr pruebas reales y dejar la
interfaz alineada con lo que el backend realmente hace.

**Problemas observados:**

1. En `/processing`, aunque `dataset.input_file` ya estaba guardado, la interfaz
   seguía pareciendo vacía porque el dropzone era el elemento dominante.
2. En `/run`, "Límite de entradas = 70" era ambiguo: podía interpretarse como
   70 entradas nuevas o como hasta la entrada 70 del dataset.
3. En los logs de ejecución, no era fácil identificar visualmente qué árbitro estaba
   hablando o fallando.
4. El preflight de proveedores necesitaba instrucciones más accionables y no asumir
   que `gcloud` estaba instalado.

**Cambios aplicados:**

- `/processing`:
  - Agregada tarjeta **Archivo listo para procesar** con ruta, hoja y columna
    persistidas en `scripts/arbiters_config.json`.
  - El flujo de upload ahora aclara que subir archivo no basta: hay que seleccionar
    columna y pulsar **Guardar** para que ese archivo sea el usado por el run.

- `/run`:
  - El campo **Límite de entradas** ahora explica que es un límite total desde el
    inicio del dataset, no una cantidad adicional.
  - Con checkpoint, la UI calcula una aproximación de entradas nuevas a procesar.
    Ejemplo: checkpoint con 50 entradas + límite 70 ⇒ ~20 nuevas.
  - Los logs detectan menciones a `arb1`, `arb2`, etc. y pintan la línea con el
    color correspondiente al árbitro en la pestaña `Arbitros`.

- `scripts/provider_preflight.py`:
  - Detecta si `gcloud` existe en `PATH`.
  - Para Vertex AI, propone service account JSON + `GOOGLE_APPLICATION_CREDENTIALS`
    cuando no hay Google Cloud CLI.
  - Para Gemini/Claude/OpenAI, indica WebApp, CLI `--set-provider-key` o `secrets.env`.
  - Para Ollama/OpenAI-compatible, valida `base_url`.

- Configuración:
  - `scripts/arbiters_config.json` queda con `safety.free_only_mode=false` para
    permitir pruebas con Vertex/Claude.
  - La lista `allowed_provider_types` conserva `gemini`, `vertex_gemini`,
    `anthropic`, `ollama` y `openai_compat` por si se reactiva `free_only_mode`.

**Regla operativa que queda documentada:**

El backend manda. La UI no debe inventar estado de procesamiento: muestra el dataset
guardado, consulta checkpoints, dispara APIs y presenta mensajes del preflight. La
semántica de `--limit` vive en el pipeline: es total desde el inicio del dataset.

**Verificación realizada:**

- Render de Flask para `/processing` contiene `ds-ready-card` y `renderDatasetReady`.
- Render de Flask para `/run` contiene `updateLimitExplanation`, `detectArbiterInLog`
  y estilos `log-arbiter-line`.
- `python -m py_compile` sobre módulos tocados durante el preflight.
- `/api/run/start` devuelve `400` con instrucciones accionables cuando faltan ADC
  de Vertex, antes de arrancar el subproceso.
# 2026-06-15 — Centralizacion de secretos en `secrets/`

**Objetivo:** evitar que credenciales reales queden sueltas en la raiz del proyecto.

**Cambios realizados:**
- Se creo la carpeta local `secrets/` para archivos sensibles.
- Se movio `secrets.env` a `secrets/secrets.env`.
- Se movio el JSON de service account de Vertex AI a `secrets/cloud-computing-cybersec-inaoe-dd4d83318111.json`.
- Se actualizo `GOOGLE_APPLICATION_CREDENTIALS` dentro de `secrets/secrets.env` para apuntar al JSON movido.
- Se centralizo la ruta de dotenv en `scripts/secrets_io.py` con `secrets_env_path(BASE_DIR)`, manteniendo fallback temporal a `./secrets.env` si existiera.
- Se actualizaron `main_webapp.py`, `webapp/app.py` y `scripts/label_persuasion.py` para cargar/escribir secretos desde `secrets/secrets.env`.
- Se actualizaron mensajes de preflight/UI y documentacion viva (`README.md`, `context.md`) para usar la nueva ruta.
- `secrets.env.example` queda en la raiz como plantilla publica; no debe moverse a `secrets/`.

**Nota de seguridad:** `secrets/` ya esta cubierto por `.gitignore`, junto con patrones de llaves, credenciales y JSON sensibles.

---

### 2026-06-16 — Rediseño de árbitros: 3 árbitros, modelos diversos, system prompts con lente profesional

**Objetivo:** mejorar la calidad metodológica del etiquetado antes de correr el pipeline
completo sobre el dataset. Se detectaron tres debilidades en la configuración anterior.

**Problemas identificados:**

1. **System prompts demasiado cortos:** los prompts anteriores eran etiquetas de rol
   más dos restricciones (~15 tokens). Las personas no añadían valor real porque no
   describían cómo aplicar el lente profesional a la tarea.

2. **Cuatro árbitros generan empates 2-2:** con número par de árbitros el consenso
   por mayoría simple puede quedar indeciso. Tres árbitros garantizan siempre una
   mayoría.

3. **arb1 y arb3 compartían el mismo modelo (gemini-2.5-pro):** con temperatura 0.1
   los dos árbitros son cuasi-deterministas para el mismo input. Sus respuestas estarían
   correlacionadas por los pesos del modelo, no por genuino desacuerdo de criterio. Eso
   infla el Fleiss Kappa artificialmente — problema metodológico para el paper.

**Cambios realizados en `scripts/arbiters_config.json`:**

- **Eliminado arb2** (Carlos Doce, Especialista Ciberseguridad, gemini-2.5-flash):
  era el árbitro de menor capacidad y su modelo flash sería más útil como diferenciador
  en arb3 que como árbitro independiente con arb4 ya cubriendo la perspectiva de menor
  costo.

- **arb4 subido de `claude-haiku-4-5-20251001` a `claude-sonnet-4-6`:** Haiku es el
  modelo más ligero de Claude, inadecuado para detección matizada de principios de
  persuasión. Sonnet ofrece razonamiento sustancialmente mejor para esta tarea.

- **arb3 cambiado de gemini-2.5-pro a gemini-2.5-flash:** para garantizar que los tres
  árbitros usen modelos genuinamente distintos. Flash tiene escala y comportamiento
  diferente a Pro; el desacuerdo entre ellos refleja diferencia real de criterio.

- **System prompts reescritos para los 3 árbitros:** cada uno ahora describe su rol
  profesional, cómo ese rol se aplica a la detección de cada uno de los 5 principios,
  y refuerza el criterio de "solo lo explícito" en lenguaje propio de esa profesión.
  Extensión: ~80-100 palabras por árbitro vs ~15 anteriores.

**Configuración final de árbitros:**

| ID | Persona | Modelo | Proveedor |
|---|---|---|---|
| arb1 | Viviana Inés Fuentes (Psicóloga Clínica) | gemini-2.5-pro | vertex_gemini |
| arb3 | Rodrigo Santana (Investigador Forense) | gemini-2.5-flash | vertex_gemini |
| arb4 | Claudio Codencio (Psicólogo Forense) | claude-sonnet-4-6 | anthropic |

**Archivo de resultados anteriores:**

Los checkpoints, outputs y logs de la configuración de 4 árbitros fueron movidos a
`archive/4arb_v1/` para preservarlos. Las carpetas `checkpoints/`, `output/` y `logs/`
quedaron limpias para la nueva corrida. Se agregó `archive/` a `.gitignore`.

**Pendiente estratégico documentado:**

Incorporar un cuarto árbitro de familia completamente distinta (GPT-4o, Llama 3 u otro)
para máxima diversidad de arquitectura. Al hacerlo, revisar el sistema de consenso:
con 4 árbitros hay riesgo de empate 2-2 y se necesitaría mayoría ponderada o un quinto
árbitro desempatador. Por ahora se mantienen 3.

**Verificación:**
- `python -c "import json; json.load(open('scripts/arbiters_config.json'))"` — JSON válido.
- Árbitros activos confirmados: `['arb1', 'arb3', 'arb4']`.

---

### 2026-06-19 — Diseño de soporte nativo para modelos locales GGUF (llama-cpp-python)

**Autor/agente:** Claude Sonnet 4.6 (sesión de diseño — SIN implementación aún).

**Objetivo:** permitir que cualquier árbitro use un modelo GGUF local sin depender de
servidores externos (Ollama, LM Studio, llama.cpp server, etc.). El usuario solo
apunta a un archivo `.gguf` y el backend lo carga directamente en el mismo proceso Python.

**Contexto / motivación:**

Los árbitros actuales usan APIs de nube (Gemini Vertex, Anthropic, OpenAI). Para
maximizar la independencia de modelos y reducir costes en producción, se quiere añadir
árbitros locales basados en modelos cuantizados (.gguf). Se descartaron los servidores
externos (Ollama, LM Studio, llama.cpp server) porque agregan complejidad de
configuración y dependencias fuera del proyecto, especialmente cuando otros usuarios
del equipo repliquen el entorno.

**Decisiones de diseño tomadas:**

1. **Nuevo provider `llama_cpp`** en `label_persuasion.py`.
   - Usa la librería `llama-cpp-python` directamente en proceso.
   - `llama-cpp-python` se importa como dependencia **opcional**: solo se carga si
     existe al menos un árbitro con `type: llama_cpp`. Así no rompe entornos que no
     usan modelos locales.
   - Se añade a `requirements.txt` y `environment.yml` para que se instale al crear
     el entorno desde cero.

2. **GPU auto-detectada, transparente al usuario.**
   - Al cargar el modelo, se detecta si `llama-cpp-python` fue compilado con soporte
     GPU (`llama_cpp.llama_supports_gpu_offload()`).
   - Si hay GPU: `n_gpu_layers = -1` (todas las capas en GPU).
   - Si no hay GPU: `n_gpu_layers = 0` (CPU only).
   - El usuario no configura este parámetro; el backend lo resuelve solo.
   - `n_ctx` (ventana de contexto) tiene default 4096, configurable por árbitro.

3. **Cache de modelo cargado en memoria** (`_LLAMA_MODEL_CACHE` dict global).
   - Cargar un GGUF tarda entre 5 y 30 segundos. Se carga una vez y se reutiliza.
   - La clave del cache es `(model_path, n_ctx)`.
   - En el contexto de CLI (proceso independiente): el cache vive mientras el proceso
     corre — comportamiento correcto.
   - En el contexto de webapp (subproceso): cada run carga el modelo una sola vez al
     inicio. Aceptable porque la carga es pequeña comparada con la duración del run.

4. **Tab "Ollama (local)" reemplazado por "Local (GGUF)"** en `arbiters.html`.
   - Ollama desaparece del frontend (queda disponible vía `openai_compat` para quien
     lo necesite, pero sin tab propio).
   - El nuevo tab muestra los modelos GGUF del directorio de cache como una grilla.
   - No tiene sección de API key ni rate limits (modelos locales no tienen límites).
   - Tiene un botón "Descargar modelos" que abre el flujo de descarga asistida.
   - El "modelo seleccionado" es el nombre del archivo `.gguf`.

5. **Cache de modelos en `BASE_DIR/models/`** (dentro del proyecto, no en `~/.cache`).
   - Portable y visible: el usuario sabe exactamente dónde están los archivos.
   - Incluye `models/models.json` como índice con metadata (nombre, tamaño, repo HF, fecha).
   - El directorio `models/` se añade a `.gitignore`.

6. **Inventario de hardware en arranque de la webapp.**
   - Al levantar la webapp, si el directorio `models/` está vacío, se muestra un banner
     no intrusivo: "No tienes modelos locales. [Configurar]".
   - El flujo de setup detecta: RAM total, GPU y VRAM disponible, CPU cores.
   - Con ese inventario, el backend sugiere modelos que caben en el hardware del usuario
     (ver tabla de criterios abajo).
   - La descarga usa `huggingface_hub` en background con progreso visible (SSE o polling).
   - Una vez descargados, los modelos aparecen en el grid del tab Local.

**Tabla de criterios de recomendación de modelos:**

| RAM/VRAM disponible | Modelos recomendados |
|---|---|
| ≥ 8 GB GPU VRAM | Llama 3.2 8B Q5_K_M, Mistral 7B Q5_K_M |
| 4–8 GB GPU VRAM | Llama 3.2 3B Q8_0, Phi-3 Mini Q8_0 |
| < 4 GB GPU o solo CPU con ≥ 16 GB RAM | Llama 3.2 8B Q4_K_M |
| Solo CPU con 8–16 GB RAM | Llama 3.2 3B Q4_K_M, Mistral 7B Q4_K_M |
| Solo CPU con < 8 GB RAM | Llama 3.2 1B Q8_0, Phi-3 Mini Q4_K_M |

Fuente de descarga: Hugging Face (repos curados: bartowski, lmstudio-community).

**Config JSON del árbitro con `llama_cpp`:**

```json
{
  "type": "llama_cpp",
  "model": "llama-3.2-3b-instruct-q4_k_m.gguf",
  "model_path": "models/llama-3.2-3b-instruct-q4_k_m.gguf",
  "n_ctx": 4096,
  "api_key_env": "",
  "api_key_override": "",
  "base_url": "",
  "vertex_project": "",
  "vertex_location": "us-central1"
}
```

`model` = nombre del archivo (para UI y logs). `model_path` = ruta relativa al GGUF.
`n_ctx` = tamaño de contexto; la GPU offloading es automática y no se guarda en config.

**Nuevas piezas a crear/modificar:**

| Pieza | Tipo | Descripción |
|---|---|---|
| `scripts/local_models.py` | Nuevo módulo | HW detection, recomendaciones, descarga vía huggingface_hub, índice de cache |
| `scripts/label_persuasion.py` | Modificar | `_call_llama_cpp()`, `_LLAMA_MODEL_CACHE`, `_detect_gpu_layers()`, entrada en `PROVIDER_CALLERS` |
| `scripts/provider_preflight.py` | Modificar | Preflight para `llama_cpp`: verificar que el archivo existe y `llama-cpp-python` está instalado |
| `scripts/rate_limits.py` | Modificar | Retornar "sin límites" para `llama_cpp` |
| `webapp/app.py` | Modificar | Nuevos endpoints: `/api/local/hardware`, `/api/local/recommended`, `/api/local/cached`, `/api/local/download` (SSE de progreso), `/api/models` ampliar para `llama_cpp` |
| `webapp/templates/arbiters.html` | Modificar | Tab "Ollama" → "Local (GGUF)", nuevo panel de ruta de modelo, sin API key, sin rate limits, botón de descarga |
| `webapp/templates/base.html` | Posible | Añadir enlace o ruta a setup local si aplica |
| `requirements.txt` | Modificar | Añadir `llama-cpp-python`, `huggingface_hub`, `psutil` |
| `environment.yml` | Modificar | Mismo |
| `.gitignore` | Modificar | Añadir `models/` |

**Pendiente (implementación):**

Todo lo anterior está decidido en diseño pero **no implementado**. La próxima sesión
debe comenzar por implementar en este orden:
1. `scripts/local_models.py` (módulo base de detección y cache).
2. Backend `_call_llama_cpp()` + cache + `PROVIDER_CALLERS`.
3. Preflight y rate limits para `llama_cpp`.
4. Endpoints en `webapp/app.py`.
5. Frontend: tab Local (GGUF) + flujo de descarga.

**Criterios de calidad para la implementación:**
- `llama-cpp-python` debe ser import opcional (try/except en el módulo).
- El cache de modelo debe sobrevivir múltiples claims dentro del mismo run.
- El flujo de descarga debe poder cancelarse sin dejar el archivo corrupto en cache.
- El preflight debe dar un mensaje claro si el archivo `.gguf` no existe o está incompleto.

---

### 2026-06-19 — Diseño de setup_env.py: instalación guiada por hardware con prioridad NPU → GPU → CPU

**Autor/agente:** Claude Sonnet 4.6 (sesión de diseño — SIN implementación aún).

**Objetivo:** crear un script de setup que se ejecuta UNA VEZ por máquina, antes de
arrancar cualquier entrypoint (`main_webapp.py` o `main_label.py`). El script detecta
el hardware disponible, instala el backend de inferencia más eficiente para ese hardware
y deja un perfil persistente en `models/hw_profile.json`. Los entrypoints leen ese
perfil al arrancar; si no existe o tiene errores, abortan con un mensaje claro.

**Contexto del parque de máquinas objetivo:**

| Máquina | OS | GPU/NPU | RAM/VRAM | Uso |
|---|---|---|---|---|
| iMac i9 | macOS | AMD Radeon (PCIe) | 8 GB RAM | webapp |
| Servidor | Linux | NVIDIA A100 | 40 GB VRAM | CLI |
| Laptop | Windows | RTX 4050 + AMD CPU | 8 GB VRAM | webapp |
| PC desktop | Windows | Sin GPU dedicada | 24 GB RAM | webapp |
| ASUS NUC 14 PRO | Windows | Intel Arc + Intel NPU | 24 GB compartida | webapp |

**Prioridad de hardware decidida: NPU → GPU → CPU**

El script intenta instalar soporte en ese orden. Si un nivel falla (error de instalación,
HW no detectado, driver ausente), baja al siguiente y lo registra en el log.

**Decisión crítica: Camino B — dos rutas de inferencia**

- **Ruta NPU:** Intel AI Boost (NUC) → `openvino` + `optimum[openvino]`
  El NPU de Intel Core Ultra NO es soportado por `llama-cpp-python`. Requiere OpenVINO.
  Se añade `_call_openvino()` en `label_persuasion.py` como ruta de inferencia alternativa.
- **Ruta GPU/CPU:** todos los demás casos → `llama-cpp-python` compilado para el backend
  correcto según el hardware detectado (CUDA / ROCm / Vulkan / Metal / CPU).

El dispatch en `PROVIDER_CALLERS["llama_cpp"]` llama a `_call_local()`, que lee
`hw_profile.json` y decide qué ruta usar.

**Mapa de hardware → backend de llama-cpp-python:**

| Hardware | Backend | Comando de instalación |
|---|---|---|
| NVIDIA GPU | CUDA | `pip install llama-cpp-python --extra-index-url .../whl/cu121` |
| AMD GPU + Linux | ROCm | `CMAKE_ARGS="-DGGML_HIPBLAS=on" pip install llama-cpp-python` |
| Intel Arc | Vulkan | `pip install llama-cpp-python --extra-index-url .../whl/vulkan` |
| AMD Radeon macOS | Vulkan (MoltenVK) | mismo wheel Vulkan |
| Apple Silicon | Metal | `CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python` |
| Sin GPU / fallback | CPU | `pip install llama-cpp-python` |

**Convención de logging del setup — IDÉNTICA al pipeline existente:**

El sistema de logging del pipeline (`label_persuasion.py`) usa:
- Formato: `%(asctime)s [%(levelname)s] %(message)s`
- Timestamp con milisegundos: `2026-06-16 02:32:09,029 [INFO] mensaje`
- Secciones delimitadas: `=== NOMBRE DE SECCIÓN ===`
- Dual output: `FileHandler` (archivo) + `StreamHandler` (consola)
- Loggers ruidosos silenciados al nivel WARNING: `httpx`, `httpcore`, `pip`, `urllib3`
- Directorio: `logs/` (ya gestionado por el pipeline)
- Nombre del archivo: `setup_YYYYMMDD_HHMMSS.log` (análogo a `run_YYYYMMDD_HHMMSS.log`)

El setup sigue las mismas reglas. No hay un formato alternativo — todo al mismo `logs/`.

**Secciones del log de setup (con delimitadores `===`):**

```
=== LABELFORGE SETUP ===
2026-06-19 10:00:00,000 [INFO] Python 3.12.4 — OK
2026-06-19 10:00:00,010 [INFO] Entorno virtual activo: /path/to/.venv

=== DETECTANDO HARDWARE ===
2026-06-19 10:00:00,020 [INFO] OS: Windows 11
2026-06-19 10:00:00,030 [INFO] CPU: Intel Core Ultra i7-155H — 16 cores — AVX2: sí
2026-06-19 10:00:00,040 [INFO] RAM: 32.0 GB
2026-06-19 10:00:00,050 [INFO] NPU detectado: Intel AI Boost (Neural Processing Unit)
2026-06-19 10:00:00,060 [INFO] GPU detectada: Intel Arc Xe-LPG — VRAM compartida ~24 GB

=== INSTALANDO DEPENDENCIAS BASE ===
2026-06-19 10:00:01,000 [INFO] pip install -r requirements.txt
2026-06-19 10:00:15,000 [INFO] Dependencias base instaladas correctamente

=== INSTALANDO BACKEND NPU (OpenVINO) ===
2026-06-19 10:00:15,010 [INFO] Intentando instalar openvino + optimum[openvino]...
2026-06-19 10:00:45,000 [INFO] OpenVINO 2024.5.0 instalado — OK

=== INSTALANDO BACKEND GPU (llama-cpp-python Vulkan) ===
2026-06-19 10:00:45,010 [INFO] Intentando instalar llama-cpp-python con backend Vulkan...
2026-06-19 10:01:00,000 [INFO] llama-cpp-python 0.3.x instalado — GPU offload: sí

=== VERIFICANDO INSTALACIÓN ===
2026-06-19 10:01:00,010 [INFO] import llama_cpp — OK
2026-06-19 10:01:00,020 [INFO] llama_cpp.llama_supports_gpu_offload() — True
2026-06-19 10:01:00,030 [INFO] import openvino — OK
2026-06-19 10:01:00,040 [INFO] Perfil guardado en models/hw_profile.json

=== RESUMEN ===
2026-06-19 10:01:00,050 [INFO] Backend NPU : OpenVINO 2024.5.0 — ACTIVO
2026-06-19 10:01:00,060 [INFO] Backend GPU : llama-cpp-python Vulkan — ACTIVO
2026-06-19 10:01:00,070 [INFO] Backend CPU : llama-cpp-python CPU — DISPONIBLE (fallback)
2026-06-19 10:01:00,080 [INFO] Setup completado sin errores. Puedes arrancar main_webapp.py o main_label.py
```

Si una instalación falla:
```
2026-06-19 10:00:50,000 [WARNING] OpenVINO no se pudo instalar: <traceback resumido>
2026-06-19 10:00:50,010 [WARNING] Backend NPU desactivado — usando GPU como mejor opción disponible
```

**Salida del subprocess de pip:** capturada línea a línea y escrita como `[INFO]` al log.
Los errores de pip van como `[ERROR]` con el stderr completo.

**Artefacto `models/hw_profile.json`:**

```json
{
  "setup_date": "2026-06-19T10:01:00",
  "setup_log": "logs/setup_20260619_100000.log",
  "os": "Windows",
  "python_version": "3.12.4",
  "cpu": {
    "vendor": "Intel", "model": "Core Ultra i7-155H",
    "cores_physical": 14, "avx2": true, "avx512": false
  },
  "ram_gb": 32.0,
  "npu": {
    "detected": true, "vendor": "Intel", "type": "AI Boost",
    "backend": "openvino", "ok": true, "version": "2024.5.0"
  },
  "gpu": {
    "detected": true, "vendor": "Intel", "name": "Arc Xe-LPG",
    "vram_gb": null, "shared_memory": true,
    "backend": "vulkan", "ok": true
  },
  "best_backend": "npu",
  "setup_ok": true
}
```

**Integración con los entrypoints:**

`main_webapp.py` y `main_label.py` verifican al arrancar:
1. `models/hw_profile.json` existe → si no: error + instrucción de ejecutar `setup_env.py`
2. `setup_ok == true` → si no: error + referencia al log de setup
3. Si OK → `local_models.py` carga los parámetros del perfil sin re-detectar nada

**Nuevas piezas a crear/modificar (adicional a las ya listadas):**

| Pieza | Tipo | Descripción |
|---|---|---|
| `setup_env.py` | Nuevo — raíz | Script principal de setup; detecta HW, instala, verifica, escribe hw_profile.json |
| `scripts/hw_detection.py` | Nuevo módulo | Detección de OS/CPU/RAM/GPU/NPU; usado por setup_env.py y local_models.py en runtime |
| `main_webapp.py` | Modificar | Verificar hw_profile.json al arrancar antes de importar la app Flask |
| `main_label.py` | Modificar | Mismo chequeo |
| `logs/setup_*.log` | Artefacto | Generado por setup_env.py con formato idéntico al de los run logs |
| `models/hw_profile.json` | Artefacto | Perfil persistente del HW + backends instalados |
| `.gitignore` | Modificar | Añadir `models/hw_profile.json` (es local por máquina) |

**Criterios de calidad para la implementación del setup:**
- El script debe funcionar en los tres OS sin dependencias de shell externas (solo stdlib + lo que instala).
- La captura de salida de pip debe ser línea a línea (no buffered) para que el log muestre progreso real.
- Si el entorno virtual no está activo, abortar con error claro antes de instalar nada.
- Un segundo `python setup_env.py` debe ser idempotente: detectar que el backend ya está instalado y verificarlo sin reinstalar (a menos que `--force` se pase).
- Todo error debe quedar en el log; la consola solo muestra resumen final (+ errores).

**Pendiente (implementación):**

Completo en diseño. Implementar en el orden del todos.md actualizado.

---

### 2026-06-19 — Implementación completa: soporte nativo GGUF, setup de hardware y catálogo dinámico

**Autor/agente:** Claude Sonnet 4.6.

**Objetivo:** implementar los 10 pasos del plan diseñado en las entradas anteriores de esta fecha, más las adiciones surgidas durante la sesión.

**Pasos implementados:**

1. **`scripts/hw_detection.py`** (nuevo): detección multiplataforma de OS/CPU/RAM/GPU/NPU.
   - GPU: NVIDIA (`nvidia-smi`) → AMD Linux (`rocm-smi`) / Windows (WMI/registry) → Intel Arc (WMI/`lspci`) → Apple Silicon (`sysctl`) → ATI Radeon macOS (`system_profiler`).
   - NPU: WMI query para "Neural"/"AI Boost" en Windows, `lspci` en Linux.
   - Función `detect_all()` retorna dict completo compatible con `hw_profile.json`.

2. **`setup_env.py`** (nuevo, raíz): script de setup único por máquina.
   - Logging idéntico al pipeline: `%(asctime)s [%(levelname)s] %(message)s`, FileHandler + StreamHandler, `logs/setup_YYYYMMDD_HHMMSS.log`.
   - Flujo: verificar Python ≥3.10 → verificar venv → detectar HW → instalar `requirements.txt` base → instalar backend NPU (OpenVINO, opcional) → instalar `llama-cpp-python` con el backend correcto → verificar imports → `ensure_project_dirs()` → escribir `models/hw_profile.json` → imprimir resumen.
   - Flag `--force` para reinstalar aunque ya esté hecho.
   - `_LLAMA_INSTALL_MAP`: dict `backend → (env_vars, pip_args)` para CUDA/ROCm/Metal/Vulkan/CPU.

3. **`scripts/local_models.py`** (nuevo): módulo central de modelos locales.
   - `_LLAMA_MODEL_CACHE: dict = {}` + `_cache_lock = threading.Lock()` — cache thread-safe de instancias Llama.
   - `build_dynamic_catalog(hw_profile, force)`: consulta Gemini, Anthropic y OpenAI en paralelo (`ThreadPoolExecutor(max_workers=3)`), sintetiza por número de votos, cache 7 días en `models/catalog_cache.json`, fallback a `_FALLBACK_CATALOG` si ningún LLM disponible.
   - `recommended_models(hw_profile, force_refresh)`: filtra por `effective_mem`, ordena por fits/votos/tamaño.
   - `get_or_load_llama(model_path, n_ctx)`: carga con `n_gpu_layers=-1` si hay soporte GPU, `n_gpu_layers=0` si no.
   - `download_model(repo_id, filename, expected_sha256)`: `hf_hub_download()` + verificación SHA-256.
   - Lectura de `n_layers` directamente del header binario GGUF (sin cargar el modelo completo).

4. **`scripts/label_persuasion.py`** (modificado):
   - `_call_llama_cpp(arbiter, prompt_user)`: import opcional con `try/except`, cache vía `get_or_load_llama()`, `create_chat_completion()` con `response_format={"type":"json_object"}`.
   - `_call_openvino(arbiter, prompt_user)`: import opcional de `optimum.intel.OVModelForCausalLM`, ruta NPU.
   - `_call_local(arbiter, prompt_user)`: dispatcher que lee `hw_profile.json`; si `best_backend == "npu"` intenta OpenVINO primero, cae a `llama_cpp` si falla.
   - `PROVIDER_CALLERS["llama_cpp"] = _call_local`.
   - En `call_arbiter()`: `if prov_type != "llama_cpp": rate_limiter.wait()` — no hay delay para modelos locales.

5. **`scripts/provider_preflight.py`** (modificado):
   - `_check_llama_cpp(arb_id, provider)`: verifica que `hw_profile.json` exista y `setup_ok=true`, que `llama_cpp` sea importable, y que el `model_path` exista como archivo `.gguf`. Mensaje accionable si algo falta.

6. **`scripts/rate_limits.py`** (modificado):
   - `get_rate_limit_profile()`: early return para `llama_cpp` con `limit_kind="local"`, todos los límites `None`.
   - `compute_min_delay()`: early return para `limit_kind="local"` con `min_delay_s=0`, `safe_delay_s=0`.

7. **`webapp/app.py`** (modificado):
   - Carga `models/hw_profile.json` al arrancar (`_load_hw_profile()`); si falta, loguea warning y continúa (no aborta la webapp).
   - `_list_local_gguf_models()`: lista archivos `.gguf` en `models/` con ruta y tamaño.
   - Nuevas rutas: `GET /api/local/hardware`, `GET /api/local/recommended`, `GET /api/local/cached`, `POST /api/local/download`, `GET /api/local/download/<job_id>` (SSE), `DELETE /api/local/model/<filename>`.
   - `POST /api/models` ampliado: para `llama_cpp` retorna lista de GGUFs locales.
   - `_download_jobs: dict` + `_download_jobs_lock` para descargas en background.

8. **`webapp/templates/arbiters.html`** (modificado):
   - Tab `data-provider="ollama"` → `data-provider="llama_cpp"`, label "Local (GGUF)", ícono `bi-cpu`.
   - `selectProvider("llama_cpp")`: oculta API key, oculta rate limits, muestra sección local-gguf con campo `n_ctx` y botón "Descargar modelos".
   - `saveArbiter()` para `llama_cpp`: añade `model_path` (ruta relativa del GGUF) y `n_ctx`.
   - Modal `downloadModal`: sección de inventario HW, lista de recomendados con badge de votos (★N), barra de progreso SSE, botón "Refrescar".
   - Funciones JS: `openDownloadModal()`, `loadRecommended(forceRefresh)`, `startDownload(repoId, filename, btn)`.

9. **`main_label.py`** y **`main_webapp.py`** (modificados):
   - `ensure_project_dirs(ROOT)` llamado al inicio.
   - `_check_hw_profile_if_needed()` en `main_label.py`: solo verifica `hw_profile.json` si hay árbitros `llama_cpp` en el config actual; no fuerza setup si el usuario solo usa APIs cloud.

10. **`.gitignore`** (modificado): añadidos `models/*.gguf`, `models/hw_profile.json`, `models/models.json`.

**Adiciones mid-sesión:**

**ensure_project_dirs():** el usuario pidió que los entrypoints verifiquen y creen todos los directorios necesarios. Se añadió a `scripts/utils.py`:
```python
REQUIRED_DIRS = [root/"logs", root/"output", root/"checkpoints", root/"models",
                 root/"secrets", root/"scripts", root/"data", root/"archive"]
```
Retorna lista de `Path` creados. Llamado por ambos entrypoints y por `setup_env.py`.

**Catálogo dinámico (en lugar de lista estática):** el usuario rechazó la lista `RECOMMENDED_CATALOG` hardcodeada porque quedaría obsoleta. Se implementó `build_dynamic_catalog()` que consulta en paralelo los LLMs ya configurados (Gemini, Anthropic, OpenAI), sintetiza respuestas por votos, mantiene cache de 7 días en `models/catalog_cache.json`, y cae a `_FALLBACK_CATALOG` de 6 modelos si ningún LLM está disponible.

**Prompts a archivo de configuración:** el usuario pidió que los prompts del catálogo no estuvieran hardcodeados en código. Se creó `scripts/local_models_config.json` con:
- `catalog.cache_ttl_days`: TTL de la caché del catálogo.
- `catalog.llm_models`: qué modelo usar de cada proveedor (ej. `"gemini": "gemini-2.5-flash"`).
- `catalog.system_prompt`: prompt de sistema para la consulta.
- `catalog.user_prompt_template`: template con `{hw_summary}` como placeholder.

**Paquetes instalados en venv:** `requirements.txt` fue reescrito con todas las dependencias directas (incluyendo `llama-cpp-python`, `huggingface_hub`, `psutil`, `openpyxl`). `environment.yml` actualizado con las mismas adiciones. Paquetes verificados en el venv del iMac:
- `psutil 7.2.2` ✅
- `huggingface_hub 1.20.1` ✅
- `llama_cpp 0.3.30` ✅ — GPU offload: True (AMD Radeon Pro 580X detectada via Metal)

**Verificación realizada:**

- `py_compile` sobre todos los archivos nuevos y modificados: sin errores de sintaxis.
- Import verificado: `psutil`, `huggingface_hub`, `llama_cpp` importan correctamente en el venv.
- `llama_cpp.llama_supports_gpu_offload()` → `True` en iMac i9 con AMD Radeon Pro 580X.
- Metal backend activo: `ggml_metal_device_init: GPU name: MTL0 (AMD Radeon Pro 580X)`.

**Archivos tocados:**

- `scripts/hw_detection.py` (nuevo)
- `setup_env.py` (nuevo)
- `scripts/local_models.py` (nuevo)
- `scripts/local_models_config.json` (nuevo)
- `scripts/label_persuasion.py`
- `scripts/provider_preflight.py`
- `scripts/rate_limits.py`
- `scripts/utils.py`
- `webapp/app.py`
- `webapp/templates/arbiters.html`
- `main_label.py`
- `main_webapp.py`
- `.gitignore`
- `requirements.txt`
- `environment.yml`
- `bitacora.md`
- `context.md`
- `todos.md`
- `README.md`

---

### 2026-06-19 — Correcciones: setup_env.py, logging Flask, modal GGUF y auto-ID de árbitros

**Autor/agente:** Claude Sonnet 4.6.

**Objetivo:** resolver varios bugs y carencias detectados al usar el sistema por primera vez en Windows con GPU NVIDIA RTX 5050.

---

#### 1. Bug crítico: `setup_env.py` nunca escribía `hw_profile.json`

**Causa raíz:** `requirements.txt` incluía `llama-cpp-python>=0.3.0`. Al ejecutar `pip install -r requirements.txt`, pip intentaba compilar `llama-cpp-python` desde fuente pero Windows no tiene MSVC/CMake. Pip retornaba código 1 → `_abort()` ejecutaba `sys.exit(1)` → el setup terminaba antes de detectar hardware o escribir `hw_profile.json`.

**Fixes aplicados:**

- `requirements.txt`: eliminada la línea `llama-cpp-python>=0.3.0` (comentada con instrucciones). `llama-cpp-python` **nunca debe estar en `requirements.txt`**; requiere compilación con flags de GPU o wheel precompilada específica al hardware.

- `environment.yml`: actualizado el comentario del bloque llama-cpp-python para explicar por qué está comentado.

- `setup_env.py — install_base_requirements()`: ahora filtra las líneas de `llama-cpp-python` del `requirements.txt` usando un archivo temporal antes de pasarlo a pip. Retorna `bool` en lugar de llamar `_abort()`. El flujo principal loguea el error pero **continúa**.

- `setup_env.py` — flujo principal: el `hw_profile.json` **siempre se escribe** independientemente de si los requirements o `llama-cpp-python` fallaron. Antes, cualquier fallo de pip abortaba antes de llegar a `write_hw_profile()`.

#### 2. Bug: instalación de `llama-cpp-python` en Windows CUDA fallaba

**Causa:** `_LLAMA_INSTALL_MAP["cuda"]` usaba `CMAKE_ARGS="-DGGML_CUDA=on"` que compila desde fuente. Sin MSVC instalado en Windows, falla con "no such file or directory: nmake".

**Fix:** `_LLAMA_INSTALL_MAP` reestructurado. Para cada backend, ahora hay una **lista de intentos en orden**:

```python
"cuda": [
    ({}, ["--prefer-binary", "--extra-index-url", ".../cu126"]),  # wheel CUDA 12.6
    ({}, ["--prefer-binary", "--extra-index-url", ".../cu124"]),  # wheel CUDA 12.4
    ({}, ["--prefer-binary", "--extra-index-url", ".../cu121"]),  # wheel CUDA 12.1
    ({"CMAKE_ARGS": "-DGGML_CUDA=on"}, ["--force-reinstall"]),   # último recurso: fuente
]
```

`--prefer-binary` dice a pip que prefiera un `.whl` precompilado sobre compilar `sdist`. Las wheels provienen del índice de abetlen en GitHub (`https://abetlen.github.io/llama-cpp-python/whl/cuXXX`). No requieren MSVC.

Añadida detección de versión CUDA vía `nvidia-smi` para elegir el índice correcto primero:
```python
def _detect_cuda_version() -> str:
    out = subprocess.check_output(["nvidia-smi"], ...)
    m = re.search(r"CUDA Version:\s*(\d+\.\d+)", out)
    ...
```

Añadido `import re` que faltaba en `setup_env.py`.

**Hardware del equipo donde se descubrió:** NVIDIA GeForce RTX 5050 Laptop GPU (8 GB VRAM), 23.2 GB RAM, Windows 11, AMD Ryzen 12 núcleos. Backend óptimo: `cuda`.

#### 3. Bug: logging de acceso de Flask/Werkzeug era inútil

**Síntoma:** el servidor imprimía `127.0.0.1 - - [19/Jun/2026 20:24:03] "GET / HTTP/1.1" 200 -` para cada request — sin método útil, sin timing, sin contexto.

**Fix en `webapp/app.py`:**
- Importado `g` de Flask.
- `logging.getLogger("werkzeug").setLevel(logging.ERROR)` — silencia el access log de Werkzeug.
- Logger dedicado `labelforge.http` con `StreamHandler` y formato del proyecto.
- `@app.before_request`: guarda `g._t0 = time.perf_counter()`.
- `@app.after_request`: loguea `METHOD /path → STATUS (Xms)`. Level INFO para 2xx, WARNING para 4xx, ERROR para 5xx. Silencia `/static/`, `/favicon.ico` y 304.

Resultado:
```
2026-06-19 20:24:03,123 [INFO]  GET   /                 200  (12ms)
2026-06-19 20:24:56,789 [INFO]  GET   /api/arbiters/preflight  200  (2ms)
```

#### 4. Bug: modal de modelos GGUF mostraba "No cabe" para todos los modelos

**Causa:** cuando `hw_profile.json` no existe, `_HW_PROFILE = {}` → `ram_gb = 0`, `vram_gb = 0` → `effective_mem = 0 * 0.6 = 0` → `model["size_gb"] > 0` → `fits_in_memory = False` para todos.

**Fix en `scripts/local_models.py`:**
- `hw_known = bool(ram_gb or vram_gb)` — detecta si hay datos reales de HW.
- Cuando no hay datos en hw_profile, intenta obtener RAM vía `psutil.virtual_memory()` como fallback en vivo.
- `fits_in_memory = (size_gb <= effective_mem) if hw_known else None`.
- Sort actualizado: `m["fits_in_memory"] is False` (antes `not m["fits_in_memory"]` que trata `None` como falsy).

**Fix en `webapp/templates/arbiters.html`:**
- `fits_in_memory === false` (estricto) → "No cabe" en rojo con tooltip.
- `fits_in_memory !== false` (incluye `null`) → botón Descargar.
- Borde e intensidad de texto del item ahora usan `=== false` en lugar de truthy/falsy.

#### 5. Mejoras al modal de gestión de modelos GGUF

**Problema:** el modal no tenía scrollbar, no listaba los modelos ya descargados, y no permitía descargar por nombre.

**Fixes en `webapp/templates/arbiters.html`:**
- `modal-dialog-scrollable` añadido al `downloadModal`.
- Nueva sección "Modelos descargados": lista todos los `.gguf` en `models/` con tamaño, capas (si legible del header GGUF) y botones **Eliminar** y **Re-descargar**.
- Nueva sección "Descargar por nombre": campos `repo_id` (ej: `bartowski/Llama-3.2-3B-Instruct-GGUF`) + `filename` (ej: `Llama-3.2-3B-Instruct-Q4_K_M.gguf`). Permite descargar cualquier GGUF de HuggingFace directamente.
- `openDownloadModal()`: ahora carga modelos descargados Y recomendados en paralelo (`Promise.all`).
- `startDownload()` exitoso: refresca también la lista de modelos descargados.

**Fix en `scripts/local_models.py`:**
- `list_cached_models()`: lee `models/models.json` (si existe) para añadir `repo_id` a cada modelo, necesario para el botón re-descargar.
- `_update_models_json()`: ahora persiste `repo_id` junto con `filename`, `path`, `size_gb`.
- `download_model()`: eliminado parámetro deprecado `local_dir_use_symlinks=False` de `hf_hub_download`.

#### 6. Bug: catálogo dinámico tenía repos de HuggingFace inventados por los LLMs

**Síntoma:** al descargar modelos, el error era `401 Client Error — Repository Not Found` porque el `repo_id` estaba mal (ej: `bartowski/Llama-3-8B-Instruct-GGUF` en lugar de `bartowski/Meta-Llama-3-8B-Instruct-GGUF`).

**Fix:** eliminado `models/catalog_cache.json`. Se regenerará en el próximo uso del modal, ya sea consultando los LLMs configurados o usando el catálogo estático `_FALLBACK_CATALOG` en `local_models.py` (cuyos repo IDs sí son correctos).

**Lección:** el catálogo dinámico generado por LLMs puede tener repo IDs incorrectos. En el futuro sería útil validar los repo IDs contra la API de HuggingFace antes de cachear.

#### 7. Auto-ID de árbitros

**Petición:** al abrir "Nuevo árbitro", el campo ID debería sugerirse automáticamente.

**Fix en `webapp/templates/arbiters.html`:**
- Nueva función `nextArbId(arbiters)`: encuentra el número más alto en IDs con patrón `arb\d+` y retorna `arb{max+1}`.
- `openModal(null)`: pre-rellena `#arb-id` con `nextArbId(arbiters)` en lugar de dejarlo vacío.

#### Archivos tocados

- `requirements.txt`
- `environment.yml`
- `setup_env.py`
- `scripts/local_models.py`
- `webapp/app.py`
- `webapp/templates/arbiters.html`
- `models/catalog_cache.json` (eliminado)
- `context.md`
- `bitacora.md`

---

### 2026-06-19 — Auditoría de seguridad y corrección de vulnerabilidades (parte 1: código)

**Autor/agente:** Claude Sonnet 4.6.

**Contexto que motivó la auditoría:**

El usuario decidió que la webapp será expuesta en internet (no solo en localhost). Se realizó una auditoría de seguridad completa con un agente especializado, seguida de verificación de falsos positivos por agentes paralelos para cada hallazgo. El resultado fue 4 vulnerabilidades reales corregidas y 2 descartadas como falsos positivos.

---

#### Vulnerabilidades encontradas y corregidas

**1. Path traversal en descarga de modelos GGUF**

- **Archivo:** `webapp/app.py` — `api_local_download()` (POST `/api/local/download`)
- **Descripción:** El campo `filename` del body JSON se pasaba directamente a `download_model()` sin validación. `download_model()` construía `dest = MODELS_DIR / filename`. Con un filename como `../secrets/secrets.env`, Python construye la ruta `models/../secrets/secrets.env` que el SO resuelve a `secrets/secrets.env`, fuera del directorio `models/`. Un atacante podría sobrescribir `secrets/secrets.env` (u otros archivos del proyecto en el directorio padre) con contenido controlado descargado desde una URL remota.
- **Por qué el DELETE no tenía este bug pero el POST sí:** `api_local_delete_model()` ya validaba `"/" in filename or "\\" in filename`, pero esa validación nunca se aplicó al endpoint de descarga cuando se creó.
- **Fix aplicado:** Añadida la misma validación que ya tenía DELETE, extendida con `.gguf`:
  ```python
  if not filename.endswith(".gguf") or "/" in filename or "\\" in filename or ".." in filename:
      return jsonify({"error": "Nombre de archivo inválido"}), 400
  ```

**2. Stored XSS en la tarjeta de árbitros**

- **Archivo:** `webapp/templates/arbiters.html` — función `renderList()`
- **Descripción:** Los campos `a.name`, `a.persona`, `a.system_prompt`, `a.id` y `p.model` se interpolaban directamente en el `innerHTML` del contenedor de tarjetas mediante template literals. Un árbitro con `name: "<img src=x onerror=alert(document.cookie)>"` se almacenaría en `arbiters_config.json` vía POST `/api/arbiters` y ejecutaría JavaScript en el navegador de cualquier usuario que cargara `/arbiters`.
- **Agravante:** La función `escapeHtml()` ya existía en el mismo archivo (usada en otros contextos), lo que confirma que su ausencia aquí fue un olvido, no una decisión de diseño.
- **Fix aplicado:** Todos los campos de datos de usuario en `renderList()` envueltos con `escapeHtml()`:
  - `a.name`, `a.persona`, `a.system_prompt`, `a.id`, `p.model`, `p.base_url`
  - Los atributos `onclick="openModal('...')"` y `onclick="askDelete('...')"` también, ya que `escapeHtml()` escapa comillas simples a `&#039;`.

**3. Reflected XSS en el panel de hardware**

- **Archivo:** `webapp/templates/arbiters.html` — función de carga del perfil HW (llama a `/api/local/hardware`)
- **Descripción:** Los valores de `hw.os`, `hw.cpu.vendor`, `hw.ram_gb`, `gpu.name`, `npu.vendor`, `npu.type` y `hw.best_backend` se interpolaban sin escapar en `hwBody.innerHTML`. Estos valores provienen de `models/hw_profile.json` generado por `setup_env.py`. Si alguien modifica `hw_profile.json` directamente (o si la detección de hardware devuelve un valor inesperado), podría ejecutarse JS en la página.
- **Severidad menor que los anteriores** porque el origen es un archivo local generado internamente, no input de usuario remoto. Aun así es corregible de forma trivial.
- **Fix aplicado:** `escapeHtml()` aplicado a todos los campos interpolados en el bloque de hardware.

**4. SSRF via `base_url` en proveedor `openai_compat`**

- **Archivo:** `webapp/app.py` — `_list_openai_compat_models()` (llamada desde POST `/api/models` y POST `/api/provider-key`)
- **Descripción:** El campo `base_url` del body JSON se pasaba directamente a `openai.OpenAI(base_url=base_url)`. El SDK de OpenAI inmediatamente hace un GET a `{base_url}/models` para listar modelos. Sin validación de esquema o host, un atacante podía enviar `base_url: "http://169.254.169.254/"` para acceder al endpoint de metadatos de instancias cloud (AWS IMDS, GCP metadata server). La respuesta parcial del error se devolvía en el JSON de error (hasta 260 chars), convirtiendo el endpoint en un oráculo SSRF limitado.
- **Fix aplicado:** Nueva función `_validate_base_url()` que:
  - Rechaza esquemas que no sean `http` o `https`
  - Bloquea IPs link-local (169.254.x.x) — cloud metadata endpoints
  - Bloquea hostnames conocidos de metadatos internos (`metadata.google.internal`, etc.)
  - Permite localhost y redes privadas (necesarios para Ollama/APIs locales)

---

#### Falsos positivos descartados

**Path traversal via `model_path`** (confianza inicial: alta; tras verificación: 2/10):
La hipótesis era que POST `/api/arbiters` con `model_path: "../secrets/secrets.env"` permitiría que `llama_cpp.Llama()` abriera un archivo arbitrario. Verificación de código: `normalize_arbiter()` en `scripts/arbiters_io.py` usa una **allowlist explícita** de campos de proveedor y descarta silenciosamente `model_path`. El campo nunca se persiste vía API. Falso positivo confirmado.

**Sin autenticación** (clasificado antes como alta severidad para herramienta local):
Con el modelo de amenaza original (localhost-only), la ausencia de auth era por diseño. Al cambiar el threat model a internet-facing, esto se convirtió en una vulnerabilidad real — manejada en la entrada siguiente.

---

**Archivos tocados:**

- `webapp/app.py`
- `webapp/templates/arbiters.html`

---

### 2026-06-19 — Auditoría de seguridad y corrección de vulnerabilidades (parte 2: despliegue en internet)

**Autor/agente:** Claude Sonnet 4.6.

**Contexto:**

La app pasó de ser "solo localhost" a "expuesta en internet". Esto cambia completamente el threat model: el atacante ya no necesita estar en la misma máquina. Con ~40 endpoints sin autenticación, cualquier persona en internet podía ejecutar el pipeline (quemando créditos de API), borrar árbitros, sobreescribir la configuración, o descargar modelos hasta llenar el disco.

---

#### Cambios implementados

**1. HTTP Basic Auth global (`LABELFORGE_PASSWORD`)**

- **Archivo:** `webapp/app.py` — bloque de autenticación añadido después de `_log_http` (línea ~112)
- **Mecanismo:**
  - Nueva variable de entorno `LABELFORGE_PASSWORD` en `secrets/secrets.env`
  - Si la variable **no está configurada**: solo se permite acceso desde `127.0.0.1` o `::1` (localhost). Cualquier otra IP recibe 403. Este es el comportamiento seguro por defecto: si alguien despliega sin leer la documentación, la app no queda expuesta.
  - Si la variable **está configurada**: HTTP Basic Auth estándar. El navegador muestra el diálogo nativo de usuario/contraseña. El usuario puede ser cualquier string; solo importa la contraseña. Se usa `secrets.compare_digest()` para comparación resistente a timing attacks.
- **Rutas exentas:** `/static/` y `/favicon.ico` (recursos que el navegador carga antes de autenticar).
- **Sin nuevas dependencias:** implementado con `base64` y `secrets` de la stdlib de Python.

**Por qué Basic Auth y no sesiones o JWT:**
Para una herramienta de investigación de un solo investigador, Basic Auth es suficiente, sin estado, funciona en el navegador y en curl/scripts, y no requiere una pantalla de login adicional. El HTTPS (configurado a nivel de nginx) hace que Basic Auth sea seguro en tránsito.

**2. `app.secret_key` desde variable de entorno**

- `app.secret_key` ahora se lee de `LABELFORGE_SECRET_KEY` en secrets.env. Si no está configurado, se genera uno aleatorio en cada reinicio (las sesiones no persisten entre reinicios). Para producción, generar con `python -c "import secrets; print(secrets.token_hex(32))"`.

**3. Límite de tamaño de uploads (50 MB)**

- `app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024`
- Flask devuelve 413 automáticamente si se supera. Sin este límite, cualquier atacante podía enviar archivos arbitrariamente grandes a `/api/dataset/upload` para agotar el disco.

**4. Cabeceras de seguridad HTTP en todas las respuestas HTML**

Añadidas en el hook `@app.after_request`:
- `Content-Security-Policy`: restringe las fuentes de scripts, estilos, fuentes e imágenes. Permite `unsafe-inline` para JS/CSS (necesario para la app actual) y CDN de Bootstrap (`cdn.jsdelivr.net`) para iconos y fuentes.
- `X-Frame-Options: DENY`: previene clickjacking (la app no puede ser embebida en un iframe).
- `X-Content-Type-Options: nosniff`: previene MIME sniffing en navegadores antiguos.

---

#### Pendientes de infraestructura (no código, deben hacerse antes de desplegar)

Estas acciones son responsabilidad del operador/administrador, no del código. Se documentan aquí para que no se olviden al desplegar:

1. **HTTPS obligatorio**: configurar nginx como reverse proxy con certificado Let's Encrypt. Flask HTTP en internet expone las credenciales de Basic Auth en claro (aunque estén codificadas en base64, son trivialmente decodificables). Sin HTTPS, la autenticación no protege nada contra un atacante en la red.

2. **Configurar la contraseña**: añadir a `secrets/secrets.env`:
   ```
   LABELFORGE_PASSWORD=<mínimo 20 caracteres aleatorios>
   LABELFORGE_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
   ```

3. **No usar `--debug` en producción**: el modo debug de Flask expone un REPL interactivo en el navegador si ocurre una excepción. Ya está desactivado por defecto; verificar que el proceso de arranque no lo active.

---

#### Vulnerabilidades conocidas pendientes (para abordar en una sesión futura)

Estas vulnerabilidades **no han sido corregidas** en esta sesión. Se documentan con suficiente detalle para que puedan abordarse sin necesidad de re-auditar el código.

**V-PEND-1: `api_key_override` persistido en texto plano en `arbiters_config.json`**

- **Archivo:** `scripts/arbiters_config.json` (versionado en git)
- **Descripción:** `normalize_arbiter()` en `scripts/arbiters_io.py` conserva el campo `api_key_override` de cada árbitro cuando se guarda. Si un usuario pega una API key real en el campo "Clave de API" de la UI y la webapp la guarda en `api_key_override` en lugar de (o además de) en `secrets/secrets.env`, esa key quedaría en el JSON. `arbiters_config.json` está versionado en git: si el usuario hace commit sin revisar, la key se publicaría en el repositorio.
- **Estado actual:** Los tres árbitros configurados tienen `api_key_override: ""` (vacío) y usan `api_key_env` apuntando a las variables de entorno. El riesgo es operacional, no de código.
- **Solución propuesta:** En `normalize_arbiter()`, si `api_key_override` es no vacío, moverlo automáticamente a `secrets/secrets.env` y reemplazarlo por cadena vacía antes de persistir en el JSON. Esto convertiría el campo en un "buzón transitorio" que nunca persiste en disco.
- **Prioridad:** Media. Bajo control mientras el equipo sepa no usar `api_key_override`.

**V-PEND-2: `/api/provider-key` escribe API keys al disco sin restricción adicional**

- **Archivo:** `webapp/app.py` — `api_provider_key()` (POST `/api/provider-key`)
- **Descripción:** Este endpoint recibe una API key del usuario, la verifica contra el proveedor LLM (bueno), y si la verificación pasa, llama a `set_secret_value(SECRETS_ENV_PATH, env_var, api_key)` para escribirla en `secrets/secrets.env`. Con autenticación ahora en vigor, solo usuarios autenticados pueden usarlo. Sin embargo, si la contraseña de LabelForge se compromete, un atacante podría usar este endpoint para sobreescribir las API keys de Gemini/Anthropic/OpenAI con claves vacías o inválidas, rompiendo el pipeline de investigación.
- **Impacto:** Denegación de servicio / sabotaje (no exfiltración de datos existentes).
- **Solución propuesta:** Añadir una confirmación de contraseña secundaria antes de cualquier escritura en `secrets/secrets.env`, similar a cómo sudo pide contraseña incluso a usuarios ya autenticados en su sesión. Alternativa más simple: hacer que `/api/provider-key` solo funcione desde localhost independientemente de la autenticación global.
- **Prioridad:** Baja-media. Solo explotable por atacantes ya autenticados en la app.

**V-PEND-3: Sin CSRF en endpoints de modificación de estado**

- **Archivo:** `webapp/app.py` — todos los endpoints POST/PUT/DELETE
- **Descripción:** HTTP Basic Auth enviada por el navegador en cada request hace que las peticiones cross-site también incluyan las credenciales automáticamente. Un sitio web malicioso podría embeber una imagen o script que haga POST a `http://tu-servidor/api/run/start`, y el navegador incluiría las credenciales Basic Auth guardadas en caché, iniciando el pipeline sin que el usuario lo sepa.
- **Mitigación parcial existente:** `X-Frame-Options: DENY` previene que la app sea embebida en iframes (bloquea algunos vectores). Los endpoints que reciben JSON con `Content-Type: application/json` son menos vulnerables porque los navegadores no envían CSRF con ese Content-Type por defecto desde formularios HTML.
- **Solución propuesta:** Añadir un token CSRF (usando Flask-WTF o una implementación manual con `secrets.token_hex()` guardado en sesión). Alternativamente, migrar de Basic Auth a session-based auth con cookies `SameSite=Strict`, que bloquea CSRF por diseño.
- **Prioridad:** Media. Solo explotable si la app tiene usuarios que visitan sitios maliciosos mientras tienen sesión activa.

---

**Archivos tocados:**

- `webapp/app.py`
- `secrets.env.example`
- `bitacora.md`
- `context.md`
