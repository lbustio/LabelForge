# Envolvente operativa de LabelForge

Estado: aprobado
Fecha: 2026-07-21

El modelo de despliegue ya fija una instancia y un run activo por workspace.
Faltan los siguientes valores para diseñar memoria, almacenamiento, pruebas de
carga y politicas de retencion sin hardcodes arbitrarios.

| Dimension | Valor objetivo |
| --- | --- |
| Filas habituales por dataset | Hasta 10000 |
| Maximo que debe soportarse | 100000 mediante procesamiento incremental |
| Tamano maximo de archivo | Configurable; debe validarse antes de cargarlo completo |
| Longitud habitual/maxima de texto | Sin truncado silencioso; fragmentacion segun ventana efectiva |
| Arbitros habituales por run | 3 a 5, siempre numero impar |
| Maximo de arbitros por run | 15, siempre numero impar |
| Runs simultaneos por workspace | 1 |
| Runs historicos esperados por workspace | Sin limite fijo; sujeto a espacio y politica de limpieza |
| Retencion de artefactos | Hasta eliminacion explicita o politica configurable |
| Retencion de logs | Rotacion configurable; nunca autoridad de negocio |
| Espacio local que puede usar LabelForge | Presupuesto configurable con alerta previa |
| Plataformas soportadas oficialmente | Windows primaria; Linux y macOS compatibles |
| CPU/RAM/GPU minima objetivo | Depende del proveedor/modelo; preflight obligatorio |

## Regla de cardinalidad de arbitros

Un run valido debe utilizar un numero impar de arbitros entre 3 y 15. La regla
se validara en dominio y en los limites de entrada; la interfaz solo la
comunicara. No se corregira silenciosamente una configuracion par agregando o
eliminando arbitros.

La cardinalidad impar evita empates estructurales en decisiones binarias por
mayoria. Tres es el minimo porque un solo evaluador no constituye un panel de
arbitros ni permite interpretar acuerdo entre anotadores.

Los limites configurables de archivo, almacenamiento y logs deberan tener
defaults documentados en Fase 1. No se introducirán limites ocultos.
