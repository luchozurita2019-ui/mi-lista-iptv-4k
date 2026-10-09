# Configuración y selección de señales Xtream

El repositorio combina hasta diez cuentas autorizadas y mantiene la URL que ya consume la aplicación:

`https://raw.githubusercontent.com/luchozurita2019-ui/mi-lista-iptv-4k/main/lista_clasica.m3u`

## Secretos existentes

No cambian los nombres. Usá `XTREAM_1_URL` a `XTREAM_10_URL` para el enlace completo de `get.php`; alternativamente `XTREAM_n_SERVER`, `XTREAM_n_USERNAME` y `XTREAM_n_PASSWORD`. La URL completa tiene prioridad. Los proveedores sin configuración se omiten y un fallo de importación se registra sin imprimir direcciones o excepciones sensibles.

**Los Secrets protegen los datos de entrada, pero no vuelven privados los enlaces del M3U publicado.** Este repositorio es público y las URLs pueden incluir credenciales. Esta mejora conserva la integración existente; resolver esa exposición requiere una decisión separada sobre alojamiento o acceso del cliente. El historial y el diagnóstico nuevos nunca incluyen enlaces, cuentas, cookies ni contraseñas.

## Cómo selecciona ahora

1. Filtra adultos y rutas/catalogos VOD antes de comparar fuentes. Conserva el catálogo y metadatos existentes.
2. Agrupa alternativas por la identidad lógica actual del canal y deduplica URL + headers. Studio Universal y Universal TV tienen identidades separadas.
3. Distribuye hasta 600 comprobaciones: una alternativa por canal antes de segundas alternativas, priorizando las menos recientemente comprobadas. La cobertura rota entre ejecuciones.
4. Solo prueba una señal por cuenta a la vez, incluso cuando la misma cuenta aparece en dos números de proveedor. Hasta cuatro cuentas se comprueban simultáneamente, por turnos. Este límite se refiere a este proceso: no puede conocer otras conexiones abiertas en el televisor.
5. Acumula hasta doce observaciones por URL + headers, con caducidad de treinta días. Favorece resultados recientes y usa suavizado para no tratar una sola observación como una certeza histórica.
6. Elige primero por evidencia actual y después por fiabilidad histórica. La latencia pesa poco y el logo solo desempata. Conserva la fuente anterior cuando la diferencia entre fuentes del mismo nivel es pequeña; un fallo actual no vence a una alternativa comprobada.

### Evidencia y límites

| Estado | Qué se observó |
|---|---|
| `pass` | TS con datos durante aproximadamente dos segundos, o HLS con dos segmentos válidos y un segmento nuevo al volver a consultar; en Actions se exige además que ffprobe identifique video en la muestra |
| `partial` | Medios reconocibles pero muestra corta, avance HLS no observado, cifrado, byte ranges no evaluados o video no identificado en la muestra |
| `fail` | Error de transporte/HTTP, HTML, JSON de error o bytes sin evidencia de medios |
| `unknown` | No se comprobó por el límite de cantidad o tiempo; nunca cuenta como éxito |

La comprobación usa firmas de TS/ISO BMFF, no acepta MIME `text/plain` o `octet-stream` como prueba suficiente. Respeta User-Agent, Referer, Cookie, Authorization y headers de EXTHTTP, EXTVLCOPT, Kodi y URL con sufijo `|headers`.

En HLS se sigue una variante del master (la de menor ancho de banda declarado para limitar carga), se toman muestras de dos segmentos y se vuelve a consultar la media playlist. Una lista con ENDLIST o tipo VOD se excluye. Cifrado y byte ranges se mantienen como evidencia parcial: no se solicita una clave ni se afirma decodificación. La comprobación de una variante no verifica todas las variantes/audio del master.

ffprobe analiza **solo bytes locales mediante stdin**, con protocolos limitados a `pipe`, un timeout de tres segundos y salida silenciosa. No recibe URLs ni hace solicitudes de red. Identificar codec/video no equivale a decodificar y visualizar todos los frames. fMP4 sin su inicialización puede quedar como parcial.

Cada muestra admite hasta 512 KiB para TS/manifiesto y hasta 32 KiB por segmento HLS; hasta dos niveles de master. Socket timeout: cuatro segundos. Presupuesto de muestra nominal: dieciocho segundos (una lectura en curso puede finalizar después). Se observa como máximo cuatro segundos de espera entre consultas HLS; un target duration mayor puede impedir observar progreso y queda parcial. Presupuesto global para programar comprobaciones: quince minutos, dejando terminar las muestras activas. El workflow tiene treinta minutos de límite total.

Un fallo no introduce una nueva URL fallida. La URL anterior que todavía está en el catálogo puede mantenerse ante un fallo puntual, con estado `fail` explícito. Si no existe alternativa comprobada, una no medida puede conservarse como `unknown`. No se inventan streams.

## Publicación y recuperación

Antes de escribir `dist/lista_clasica.m3u` se valida que haya entradas, URLs HTTP(S), nombres, ausencia de adultos/VOD y correspondencia entre entradas generadas y parseadas. También se exige conservar al menos el **80% de las identidades anteriores**. Es un umbral relativo de revisión, no una cantidad fija de canales; perder más de una quinta parte obliga a revisar el proveedor o el cambio de catálogo antes de publicar.

Si todo lo medido falla o hay pérdida masiva, el proceso sale con error y no reemplaza la lista anterior. Los resultados parciales permiten continuidad, pero no se cuentan como muestras completas. La escritura del candidato es temporal y se reemplaza al finalizar validación.

El workflow publica únicamente desde `main`. Confirma lista e historial juntos cuando la generación es válida. Ante rechazo, conserva únicamente las mediciones nuevas, si existen, y sigue mostrando una ejecución fallida. No fuerza un push ni sobrescribe trabajo concurrente; la concurrencia existente cancela generaciones anteriores y el push falla si la rama cambió.

`stability_history.json` guarda SHA-256 opacos, estado, latencia y fecha. Son seudónimos de transporte, no cifrado; no debe añadirse información de las cuentas. Se guardan mediciones de ejecuciones rechazadas para que también influyan en la siguiente selección. `dist/diagnostico_estabilidad.json` contiene cantidades, estados y motivo de rechazo/validación. Solo este diagnóstico se sube como artefacto por un día; ya no se sube otra copia del M3U.

“validada” en el diagnóstico significa validación del candidato, no confirmación del push ni reproducción en el televisor. Hay que verificar el paso de publicación y la URL final.

## Categorías y compatibilidad

Se mantienen noticias, deportes, cultura, entretenimiento, regionales, infantiles, documentales, música y eventos, además de los nombres canónicos del catálogo. Las señales lineales de cine se etiquetan **TV · Ficción**, porque el clasificador actual de tvplayer interpreta palabras como “Cine” y “Series” en group-title como VOD/episodios. La app no requiere reconstrucción para leer este cambio de metadatos.

Los filtros de país/idioma son heurísticos; el audio y la región no se verifican por reproducción. Persisten posibles coincidencias ambiguas del catálogo. No se modificó masivamente ese catálogo para corregir estabilidad.

## Ejecución y pruebas

Actions → **Generar Lista clásica M3U desde Xtream** → Run workflow. También se ejecuta cada seis horas. En una PR corre **Validar selector de estabilidad**, sin secretos ni proveedores reales.

Pruebas locales:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/xtream_to_m3u.py scripts/stream_stability.py
```

Generación local, solo en un entorno donde las credenciales ya estén configuradas de forma segura:

```sh
STABILITY_FFPROBE=1 python3 scripts/xtream_to_m3u.py
```

Instalá FFmpeg/ffprobe previamente. Sin `STABILITY_FFPROBE=1` se evalúa transporte y el diagnóstico indica que ffprobe está deshabilitado.

Las pruebas automatizadas utilizan servidores locales y fixtures sintéticos, sin cuentas reales. Incluyen HTML/HTTP, MIME incorrecto, avance HLS, cifrado/VOD, headers, historial, cambios de fuente, pérdidas masivas, metadatos con comas y serialización de una misma cuenta.

Referencia técnica: [ffprobe](https://ffmpeg.org/ffprobe.html) y [RFC 8216 HLS](https://www.rfc-editor.org/rfc/rfc8216.html).

## Alcance de esta revisión

Se revisaron los cuatro archivos que componían main en el commit `a0cf9f6ebcb611ca4769bc0c73065cde5f08d757`: generador, workflow, documentación y lista publicada. El workflow anterior medía una sola respuesta de 4 KiB con hasta 24 solicitudes simultáneas, podía conservar fallos como ganadores, no tenía historial ni control relativo de pérdida de canales. La documentación anterior describía parámetros y privacidad distintos del código real.

La última falla examinada, run `37909404093`, rechazó una lista final vacía. Los commits posteriores corrigieron el separador usado en la validación; esta mejora agrega pruebas para que ese flujo no vuelva a pasar inadvertido.

Pendientes: validación contra proveedores reales en Actions tras aprobar los cambios; pruebas desde la red/dispositivo del usuario; revisión de exposición pública de los enlaces; evolución de identidades/región e idioma. Ninguna muestra breve garantiza estabilidad durante horas ni compatibilidad con todo DRM o reproductor.
