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




