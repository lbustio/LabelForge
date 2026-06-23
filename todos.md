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
  - disenar primero contratos para `dataset_io`, `checkpoint_io` y
    `run_reporting` dentro de `labelforge_core/`;
  - migrar por pasos solo despues de aprobar el diseno;
  - mantener compatibilidad con CSV final, checkpoints existentes y `/run`;
  - cubrir con tests antes de quitar helpers duplicados.

- [ ] Probar flujo completo end-to-end desde la webapp con subconjunto pequeno:
  - configurar dataset desde `/processing`;
  - validar preflight en `/arbiters`;
  - ejecutar `--limit 5` o equivalente desde `/run`;
  - confirmar logs SSE, checkpoint, CSV final y estadisticas en `/api/run/stats`.

- [ ] Ejecutar pipeline completo sobre el dataset de investigacion y guardar:
  - CSV final en `output/`;
  - log completo en `logs/`;
  - resumen de metricas de acuerdo inter-arbitro;
  - notas metodologicas necesarias para el paper.

## Prioridad media

- [ ] Revisar seguridad pendiente antes de exponer fuera de localhost:
  - confirmar que `api_key_override` no persiste secretos en
    `config/arbiters_config.json`;
  - mantener `/api/provider-key` limitado a localhost;
  - conservar CSRF activo para `POST`/`PUT`/`PATCH`/`DELETE`;
  - usar HTTPS y no usar `--debug` en produccion.

- [ ] Revisar visibilidad/ownership de outputs:
  - la webapp asigna owner a outputs generados desde `/run`;
  - outputs generados por CLI pueden quedar sin owner;
  - decidir politica para admin, usuarios no-admin y ejecuciones CLI.

- [ ] Mejorar gestion de modelos GGUF:
  - confirmar en navegador descarga simultanea de varios modelos;
  - validar cancelacion con una descarga real grande;
  - mostrar con mas claridad modelos activos, archivados y externos;
  - decidir si modelos movidos fuera del cache deben poder restaurarse desde UI.

- [ ] Homogeneizar identidad visual de arbitros:
  - elegir formato unico: `nombre (id)`, `persona - modelo`, o similar;
  - aplicarlo en tarjetas, logs coloreados, metricas y export/import.

- [ ] Revisar politica de almacenamiento:
  - `config/` versionable;
  - `secrets/` privado;
  - `models/` para GGUF/cache/hardware local;
  - `data/`, `output/`, `checkpoints/`, `logs/`, `archive/` locales;
  - confirmar `.gitignore` y que no haya datos privados rastreados.

## Prioridad baja

- [ ] Anadir pagina/panel de resultados historicos:
  - comparar outputs;
  - descargar resumen de metricas;
  - ver distribucion por principio y por arbitro.

- [ ] Evaluar modo de justificaciones en produccion:
  - costo adicional en tokens;
  - valor para auditoria cualitativa;
  - impacto en CSV y analisis posterior.

- [ ] Validar repos de Hugging Face antes de guardar recomendaciones generadas
  por LLMs:
  - comprobar `repo_id`;
  - comprobar `filename`;
  - evitar cachear recomendaciones alucinadas.

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
- [x] Preflight CLI consolidado: `main_cli.py` ya no lee la config default ni
  valida `hw_profile.json`; el preflight vive en el nucleo y usa la config real.
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
