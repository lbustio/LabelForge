# LabelForge - TODO real

Este archivo contiene el backlog vivo del proyecto. No es una lista historica de lo que
ya se implemento; para eso esta `bitacora.md`.

Ultima revision: 2026-06-19.

## Prioridad alta

- [ ] Probar un flujo completo end-to-end desde la webapp con un subconjunto pequeno:
  - configurar dataset desde `/processing`;
  - validar preflight en `/arbiters`;
  - ejecutar `--limit 5` o equivalente desde `/run`;
  - confirmar logs SSE, checkpoint, CSV final y estadisticas en `/api/run/stats`.

- [ ] Ejecutar el pipeline completo sobre el dataset de investigacion y guardar:
  - CSV final en `output/`;
  - log completo en `logs/`;
  - resumen de metricas de acuerdo inter-arbitro;
  - notas metodologicas necesarias para el paper.

- [ ] Revisar las vulnerabilidades pendientes documentadas en `context.md` y `bitacora.md`:
  - `api_key_override` no debe persistir secretos en `config/arbiters_config.json`;
  - `/api/provider-key` deberia limitar escrituras de secretos a localhost o pedir confirmacion secundaria;
  - endpoints `POST/PUT/DELETE` siguen sin CSRF si se expone la app con Basic Auth.

## Prioridad media

- [ ] Limpiar deuda visual/JS en `webapp/templates/arbiters.html`:
  - hay bloques GGUF legacy y v2 conviviendo;
  - conservar solo el flujo actual de gestion de modelos locales;
  - verificar que "Crear nuevo arbitro" y edicion funcionen sin regresiones.

- [ ] Mejorar gestion de modelos GGUF:
  - confirmar en navegador descarga simultanea de varios modelos;
  - validar cancelacion con una descarga real grande;
  - mostrar con mas claridad modelos activos, archivados y externos;
  - decidir si los modelos movidos fuera del cache deben poder restaurarse desde la UI con ruta manual.

- [ ] Homogeneizar la identidad visual de arbitros en toda la UI:
  - elegir formato unico: `nombre (id)`, `persona - modelo`, o similar;
  - aplicarlo en tarjetas, logs coloreados, metricas y export/import.

- [ ] Revisar politica de almacenamiento:
  - `config/` para configuracion versionable;
  - `secrets/` para credenciales;
  - `models/` para GGUF/cache/hardware local;
  - `data/`, `output/`, `checkpoints/`, `logs/`, `archive/` para artefactos locales.
  Confirmar que `.gitignore` refleja esa politica y que no hay datos privados rastreados.

## Prioridad baja

- [ ] Anadir una pagina/panel de resultados historicos mas comoda:
  - comparar outputs;
  - descargar resumen de metricas;
  - ver distribucion por principio y por arbitro.

- [ ] Evaluar modo de justificaciones en produccion:
  - costo adicional en tokens;
  - valor para auditoria cualitativa;
  - impacto en formato CSV y analisis posterior.

- [ ] Validar repos de Hugging Face antes de guardar recomendaciones generadas por LLMs:
  - comprobar `repo_id`;
  - comprobar `filename`;
  - evitar cachear recomendaciones alucinadas.

- [ ] Considerar soporte futuro para nuevas taxonomias:
  - mantener principios configurables;
  - documentar requisitos minimos de prompt;
  - separar ejemplos especificos de Ana Ferreira de la arquitectura general.

## Hecho recientemente

- [x] Configuracion editable movida de `scripts/` a `config/`.
- [x] `scripts/paths.py` como fuente de rutas canonicas y fallback legacy.
- [x] Gestion de modelos GGUF activos, archivados y externos.
- [x] Descargas GGUF simultaneas con progreso por tarjeta.
- [x] Cancelacion de descargas GGUF y limpieza de `.part`.
- [x] Evitar redescarga si el GGUF ya existe.
- [x] Filtrar recomendados que ya estan descargados, archivados o movidos fuera del cache.
- [x] Boton para mover modelos desde `models/` a una carpeta absoluta elegida localmente.
- [x] Hardening inicial para despliegue con Basic Auth, CSP, limite de upload, validacion de filename GGUF y bloqueo SSRF basico.
