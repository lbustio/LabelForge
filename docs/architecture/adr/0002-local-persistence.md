# ADR-0002: persistencia local y fuentes de autoridad

Estado: aceptado
Fecha: 2026-07-21
Depende de: ADR-0001

## Contexto

LabelForge usa actualmente SQLite, JSON, JSONL, CSV, logs de texto y estado en
memoria. Los formatos son utiles para distintos fines, pero algunas entidades
se duplican y varias operaciones relacionadas no son atomicas.

El modelo aprobado es una instancia local por workspace. No se necesita una
base remota, pero si recuperacion consistente, migraciones y backups claros.

## Decision

Usar una arquitectura hibrida local:

### SQLite: estado operacional transaccional

- usuarios y tokens de autenticacion;
- runs y sus propietarios;
- estado y transiciones de lifecycle;
- identidad y revision del dataset usado;
- snapshot de configuracion de cada run;
- arbitros participantes;
- evaluaciones o indice durable de evaluaciones;
- incidencias;
- muestras o agregados de rendimiento necesarios para recuperar el panel;
- referencias y metadatos de artefactos;
- versiones de esquema y migraciones.

### Filesystem: objetos grandes y archivos interoperables

- datasets de entrada;
- modelos GGUF;
- CSV u otros artefactos exportados;
- logs operativos;
- archivos temporales y descargas parciales;
- archivos de backup/exportacion.

### JSON versionable: configuracion base editable

- taxonomia y configuracion inicial;
- catalogo/configuracion de modelos;
- perfiles orientativos de rate limits.

Al iniciar un run, la configuracion efectiva se valida y se guarda como snapshot
inmutable asociado al run. Modificar el JSON despues no cambia el significado
historico del resultado.

### Secretos

Los secretos permanecen fuera de SQLite operativo, manifests, configuracion
versionable y artefactos. Se resuelven mediante un `SecretProvider` local.

## Fuentes de autoridad propuestas

| Informacion | Autoridad |
| --- | --- |
| Configuracion editable vigente | JSON validado |
| Configuracion usada por un run | Snapshot SQLite inmutable |
| Estado y eventos de run | SQLite |
| Evaluaciones recuperables | SQLite o tabla/index durable definido por schema |
| Dataset y revision | Archivo + fingerprint registrado en SQLite |
| Artefacto final | Archivo + metadata/fingerprint en SQLite |
| Modelo local | Archivo + inventario reconstruible |
| Log diagnostico | Archivo de texto; no autoridad de negocio |
| Proyeccion del dashboard | Derivada; cacheable y reconstruible |

## Atomicidad

- Registrar evaluacion y avance de checkpoint debe pertenecer a una misma
  transaccion logica.
- Finalizar un run requiere validar artefacto y registrar su metadata antes de
  exponer estado `completed`.
- La escritura de archivos usa temporal, `fsync` cuando corresponda y rename
  atomico antes de confirmar metadata.
- Incidencias y efectos no deben perderse despues de avanzar la proyeccion.
- Las operaciones destructivas eliminan por identidad de run y aplican una
  politica explicita ante archivos ausentes.

## Migracion

1. Introducir schema y repositorios sin eliminar lectores actuales.
2. Migrar manifests y propiedad legacy de forma explicita e idempotente.
3. Importar estado/checkpoints activos con version de origen.
4. Comparar lecturas antiguas y nuevas durante una ventana controlada.
5. Cambiar autoridad por entidad, nunca todas a la vez.
6. Retirar JSON/JSONL legacy cuando no existan consumidores ni datos pendientes.

## Alternativas consideradas

### Mantener todos los JSON/JSONL

Es simple e inspeccionable, pero obliga a implementar locking, indices,
transacciones, migraciones y consultas de forma artesanal.

### Guardar tambien datasets y modelos en SQLite

Descartado: reduce interoperabilidad, complica streaming y backups y no aporta
valor para archivos grandes locales.

### Base externa

Descartada para el alcance local por costo operacional innecesario.

## Consecuencias

- Habra una migracion gradual de persistencia.
- SQLite se convierte en componente operativo critico y necesita backup,
  integridad, WAL/configuracion y pruebas de recuperacion.
- Los archivos siguen requiriendo fingerprints y reconciliacion con metadata.
- JSONL puede conservarse como export/debug, no como autoridad duplicada.

## Restricciones para la implementacion

- No crear un segundo origen de verdad mientras se migra una entidad. Durante
  compatibilidad deben quedar declarados lector primario, fallback y criterio
  de retiro.
- No almacenar paths sin resolver como identidad unica de datasets o
  artefactos; registrar identidad, path relativo controlado y fingerprint.
- No guardar secretos ni copias de credenciales en snapshots de configuracion.
- No marcar un run como completado antes de confirmar el artefacto final.
- No usar logs de texto para reconstruir decisiones de negocio.
- Toda tabla durable tendra version/migracion y pruebas de actualizacion.
- La base local y los artefactos deben poder respaldarse con la aplicacion
  detenida mediante un procedimiento documentado.

El schema concreto se diseñara al preparar Fase 5, despues de estabilizar los
modelos de dominio y eventos de Fases 2 y 3.
