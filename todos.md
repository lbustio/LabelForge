# LabelForge - TODO real

Este archivo es el backlog vivo del proyecto. No es historico; para decisiones ya
tomadas usar `bitacora.md`. Antes de cambiar codigo, revisar arquitectura,
detectar duplicaciones/riesgos y proponer la solucion para aprobacion.

Ultima revision: 2026-06-23.

## Regla de trabajo

- [ ] Antes de implementar cambios, revisar el flujo completo afectado:
  CLI, webapp, nucleo Python, rutas canonicas, checkpoints, tests y docs.
- [ ] No crear paths especiales para casos puntuales si ya existe una ruta
  canonica o un helper del nucleo.
- [ ] No duplicar logica entre `webapp/app.py` y `labelforge_core/` sin una
  razon explicita.
- [ ] No dejar dead code ni rutas subutilizadas sin documentar.
- [ ] Pedir confirmacion antes de modificar codigo cuando el cambio toque
  arquitectura, persistencia, seguridad o ejecucion del pipeline.

## Prioridad alta

- [ ] Consolidar arquitectura de ejecucion sin romper CLI ni webapp:
  - mantener el nucleo como autoridad de dataset, checkpoints, reporting,
    metricas, prompting, parsing de respuestas LLM y uso/tokens/latencias;
  - antes de nuevas migraciones, disenar contrato y aprobarlo;
  - mantener compatibilidad con CSV final, checkpoints existentes y `/run`;
  - cubrir con tests antes de quitar helpers duplicados.

- [ ] Ejecutar pipeline completo sobre el dataset de investigacion y guardar:
  - CSV final en `output/`;
  - log completo en `logs/`;
  - resumen de metricas de acuerdo inter-arbitro;
  - notas metodologicas necesarias para el paper.

## Prioridad media

- [x] Revisar seguridad pendiente antes de exponer fuera de localhost:
  - `api_key_override` migra a secrets.env y se borra del JSON (arbiters_io.py:39);
  - `/api/provider-key` bloqueado con 403 fuera de localhost (app.py:1502);
  - CSRF activo como before_request para POST/PUT/PATCH/DELETE;
  - `--debug` por defecto False; agregado abort en main_webapp.py si se combina
    con --host distinto de localhost.
  - PENDIENTE: HTTPS obligatorio si se expone fuera de la red local (no es codigo,
    es configuracion de infraestructura).

- [x] Revisar visibilidad/ownership de outputs:
  - DECISION: outputs del CLI son solo para consumo local, no deben aparecer
    en la webapp. La webapp solo muestra outputs generados desde `/api/run/start`.
    No hace falta importar ownership ni agregar logica extra.

- [x] Mejorar gestion de modelos GGUF:
  - descarga simultanea y cancelacion: logica correcta en codigo; pendiente
    validacion manual en navegador con descarga real grande.
  - secciones renombradas: "Activos (models/)", "Archivados (models/archive/)",
    nueva seccion "Externos (movidos fuera del cache)".
  - modelos externos: list_external_models(), restore_external_model(),
    unregister_external_model() en local_models.py; endpoints GET/POST/DELETE
    en /api/local/external; UI con boton restaurar (desactivado si archivo no
    existe en ruta registrada) y boton quitar del registro.

- [x] Homogeneizar identidad visual de arbitros:
  - formato elegido: `Nombre · Persona (id)` (sin persona si el campo esta vacio);
  - `arb_label()` en utils.py para Python; `arbLabel()` en base.html para JS;
  - aplicado en: tarjetas de arbiters.html, tarjetas de index.html, dropdown de
    principles.html, y log de arranque en label_persuasion.py.

- [ ] Revisar politica de almacenamiento:
  - `config/` versionable;
  - `secrets/` privado;
  - `models/` para GGUF/cache/hardware local;
  - `data/`, `output/`, `checkpoints/`, `logs/`, `archive/` locales;
  - confirmar `.gitignore` y que no haya datos privados rastreados.

## Prioridad baja

- [x] Anadir pagina/panel de resultados historicos:
  - nueva pagina /history visible a todos los usuarios autenticados;
  - nuevo endpoint GET /api/outputs/<name>/stats que lee el CSV y
    retorna build_run_stats() completo;
  - panel izquierdo: lista de outputs con fecha, nombre y chips de resumen;
  - panel derecho: metricas globales, acuerdo inter-arbitro (Fleiss/Cohen/Alpha),
    tabla de principios (positivos, negativos, tasa, kappa), tabla de arbitros
    (llamadas, tokens, latencia, tasa de voto), acuerdo por pares, recursos;
  - botones de descarga CSV y resumen JSON (data URI, sin servidor extra);
  - comparacion entre outputs: pendiente para version futura.

- [ ] Evaluar modo de justificaciones en produccion:
  - costo adicional en tokens;
  - valor para auditoria cualitativa;
  - impacto en CSV y analisis posterior.

- [x] Validar repos de Hugging Face antes de guardar recomendaciones generadas
  por LLMs:
  - `_is_valid_hf_repo_id()`: regex owner/repo, sin espacios ni rutas;
  - `_is_valid_gguf_filename()`: extension .gguf, sin path traversal;
  - size_gb y ram_min_gb deben ser positivos;
  - items invalidos se descartan con warning antes de llegar al cache;
  - validacion de existencia real en HF ya ocurre en _resolve_gguf_filename()
    al momento de descargar.

- [ ] Revisar OpenVINO/NPU en el futuro:
  - por ahora se deja como esta;
  - no bloquear trabajo actual de CLI/webapp/GGUF;
  - cuando se reactive, validar que no trate GGUF como modelo Transformers.

## Hecho recientemente

- [x] CLI probado con dataset de webapp, columna `claim`, tres arbitros locales
  y `--limit 10`; genero CSV, log, metricas y estado completed.
- [x] Checkpoints centralizados en `labelforge_core/checkpoint_io.py`; el
  pipeline y la webapp usan el helper compartido para limpieza/carga basica.
- [x] Dataset centralizado en `labelforge_core/dataset_io.py`; CLI y webapp
  comparten carga CSV/Excel, inspeccion de columnas/sheets y validacion de ruta
  permitida para datasets de la UI.
- [x] Reporting centralizado en `labelforge_core/run_reporting.py`; la webapp
  delega metricas, filas de resultados, conteos por principio y resumen paper
  al contrato del nucleo.
- [x] Metricas estadisticas centralizadas en `labelforge_core/agreement_metrics.py`:
  acuerdo inter-arbitro, Cohen por pares, consenso por mayoria y consenso por
  entrada.
- [x] Prompting centralizado en `labelforge_core/prompting.py`; el runner usa el
  helper compartido para catalogo de principios, schema JSON y prompt de usuario.
- [x] Parsing de respuestas LLM centralizado en `labelforge_core/llm_json.py`;
  el runner delega extraccion JSON, reparaciones conservadoras y rescate por
  campos de principio.
- [x] Uso/tokens/latencias por entrada centralizados en
  `labelforge_core/usage_metrics.py`; runner y reporting comparten los mismos
  campos `entry_*`.
- [x] Preflight CLI consolidado: `main_cli.py` ya no lee la config default ni
  valida `hw_profile.json`; el preflight vive en el nucleo y usa la config real.
- [x] Smoke end-to-end desde webapp con `limit=1`: Flask lanzo `main_cli.py`,
  procesaron los 3 arbitros locales, se genero CSV, snapshot runtime,
  `/api/run/stats` y `/api/outputs`.
- [x] Auditoria inicial de arquitectura: detectadas duplicaciones en preflight,
  checkpoints, dataset y reporting.
- [x] Configuracion editable movida de `scripts/` a `config/`.
- [x] `labelforge_core/paths.py` como fuente de rutas canonicas.
- [x] Nucleo Python renombrado de `scripts/` a `labelforge_core/`.
- [x] Gestion de modelos GGUF activos, archivados y externos.
- [x] Descargas GGUF simultaneas con progreso por tarjeta.
- [x] Cancelacion de descargas GGUF y limpieza de `.part`.
- [x] Evitar redescarga si el GGUF ya existe.
- [x] Filtrar recomendados que ya estan descargados, archivados o movidos fuera
  del cache.
- [x] Boton para mover modelos desde `models/` a una carpeta absoluta local.
- [x] Hardening inicial: login local, CSP, limite de upload, validacion GGUF,
  bloqueo SSRF basico, CSRF y `/api/provider-key` solo localhost.
- [x] `api_key_override` tratado como transitorio hacia `secrets/secrets.env`.
- [x] Autenticacion reducida a login local SQLite.
- [x] Plantilla de entorno movida a `secrets/secrets.env.example`.