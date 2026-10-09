# Configuración y selección de señales Xtream

El repositorio combina hasta diez cuentas y conserva la integración existente:

`https://raw.githubusercontent.com/luchozurita2019-ui/mi-lista-iptv-4k/main/lista_clasica.m3u`

## Secrets

Se mantienen `XTREAM_1_URL` a `XTREAM_10_URL`, o alternativamente `XTREAM_n_SERVER`, `XTREAM_n_USERNAME` y `XTREAM_n_PASSWORD`. La URL completa de get.php tiene prioridad. Un proveedor sin configuración se omite; los errores no imprimen URLs, usuarios, contraseñas ni excepciones sensibles.

**Los Secrets protegen las entradas, pero las URLs de los M3U públicos pueden contener credenciales.** Se conserva el alojamiento ya usado por el cliente; los nuevos M3U tienen el mismo alcance público. Historial, disponibilidad y diagnóstico contienen identificadores opacos o metadatos, nunca cuentas ni streams.

## Catálogo argentino de TV paga

`data/catalogo_argentina.json` contiene 179 objetivos, aliases, categorías, packs, referencias y 138 logos específicos procedentes de archivos existentes. Incluye HBO Pop, los cinco Universal+, TNT Novelas, AMC Series, USA Network, DreamWorks, Cartoonito, Nick Jr., History 2, Discovery Turbo/World/Theater y otras señales de TV paga comercializadas en Argentina.

Las fuentes oficiales enumeradas en [README.md](README.md) son referencias de nombres y packs. **No se descargan M3U gratuitos ni se generan URLs a partir del catálogo.** Solo se elige lo que llega desde las cuentas configuradas. Los canales nuevos, variantes antiguas y regionales quedan condicionados a disponibilidad y mediciones; una grilla comercial no demuestra que un proveedor Xtream tenga la señal.

La normalización repara caracteres mal decodificados, decoraciones AR, calidades y opciones duplicadas. Usa coincidencias exactas con aliases revisados, no coincidencias parciales del nombre. Mantiene numeraciones y marcas de packs separadas; DSports+ y HBO+ tienen significado propio. Rutas movie/series/vod, episodios, adultos y nombres desconocidos se filtran antes de elegir.

Metadatos explícitos de otro país o idioma bloquean la entrada aunque coincida con una marca conocida. Sin estos metadatos, una marca del bouquet argentino/latinoamericano puede aceptarse: el contenido, país real y audio de la emisión no se verifican por reconocimiento visual ni auditivo.

## Selección de la señal

1. Agrupa alternativas por nombre canónico y deduplica URL + headers.
2. Prioriza una fuente por cada canal, con los packs premium primero dentro de cada ronda. Revalida la fuente anterior si su último resultado no fue fallido; en empates sin historial distribuye canales entre proveedores. Los backups menos recientemente medidos siguen rotando.
3. Programa hasta 600 muestras, una activa por cuenta, incluso cuando la misma cuenta aparece en dos posiciones de Secrets. Hasta cuatro cuentas trabajan en paralelo. El límite solo contempla este proceso y no puede conocer conexiones abiertas en el televisor.
4. Si una muestra falla o es parcial, adelanta un backup pendiente de ese canal. Cada treinta segundos con actividad imprime cantidades y cuentas activas, sin secretos.
5. Compara evidencia actual, fiabilidad histórica ponderada y tiempo de arranque. Un éxito histórico reciente, de hasta doce horas, puede superar una nueva muestra inconclusa cuando la fuente anterior no fue medida; no se etiqueta como éxito actual.
6. Conserva la fuente anterior ante diferencias pequeñas. Una mejora grande de arranque puede cambiar entre fuentes de fiabilidad equivalente. Una URL fallida en la ejecución actual nunca es ganadora.
7. Si todas las muestras fallan, o se pierde más del 20% de identidades elegibles, rechaza la generación y mantiene lo publicado.

La retención aplica el mismo catálogo y filtros a la lista anterior. Así la retirada intencional de señales extranjeras, radios, eventos y aliases duplicados no se confunde con una caída masiva del proveedor. El diagnóstico informa esa limpieza por separado.

## Evidencia y límites

| Estado | Evidencia |
|---|---|
| pass | TS con datos durante aproximadamente dos segundos, o HLS con dos segmentos válidos y un segmento nuevo al volver a consultar; en Actions ffprobe identifica video en la muestra |
| partial | Medios reconocibles, pero muestra corta, avance HLS no observado, cifrado, byte ranges no evaluados o video no identificado |
| fail | Transporte/HTTP fallido, HTML, JSON de error o bytes sin evidencia de medios |
| unknown | Alternativa no comprobada por límite de cantidad o tiempo; no cuenta como éxito actual |

El buffer retenido TS/manifiesto sigue limitado a 512 KiB. La observación TS puede transferir hasta 4 MiB sin aumentar ese buffer, evitando penalizar únicamente a las fuentes rápidas. HLS muestrea hasta 32 KiB por segmento y sigue la variante del master con menor ancho de banda declarado para limitar carga, con hasta dos niveles de master.

Se preservan headers de EXTHTTP, EXTVLCOPT, Kodi y URLs con sufijo. HLS con ENDLIST/tipo VOD se excluye. Cifrado y byte ranges son parciales; no se solicita una clave ni se afirma decodificación. Observar una variante no verifica todas sus variantes o audios.

ffprobe recibe únicamente bytes locales mediante stdin, con protocolos limitados a pipe y timeout de tres segundos. No recibe enlaces, credenciales ni permiso para acceder a medios remotos. Identificar video no equivale a decodificar y mostrar cada frame.

Socket timeout: cuatro segundos. Presupuesto nominal por muestra: dieciocho segundos; una lectura activa puede terminar después. Espera HLS: hasta cuatro segundos. Presupuesto global para programar muestras: quince minutos. El workflow tiene treinta minutos de límite. La comprobación breve no demuestra estabilidad durante horas ni compatibilidad con cualquier reproductor o DRM.

## Listas y publicación

Una selección validada genera `dist/lista_clasica.m3u` y once M3U en `dist/listas/`: Argentina premium, packs premium, HBO, Universal+, cine/series, deportes, infantiles, documentales, entretenimiento, música y estables. Todas reutilizan los mismos ganadores y headers, sin duplicar señales dentro de cada lista.

`estables.m3u` incluye únicamente ganadores pass **de la ejecución actual**. Las completas también pueden incluir partial/unknown; `disponibilidad.json` distingue canales publicados, ausentes en proveedores y sin alternativas útiles. Una sublista sin disponibilidad tiene solo #EXTM3U.

Las señales lineales de cine usan group-title **TV · Ficción**: el clasificador actual del cliente interpreta Cine/Series como VOD. Las categorías nacionales, noticias, deportes, infantiles, documentales, entretenimiento, música, cultura y regionales no usan “Otros”.

Antes de escribir se validan URLs HTTP(S), nombres, categorías permitidas, ausencia de adultos/VOD, recuento y retención. El workflow publica únicamente desde main y confirma lista, sublistas, disponibilidad e historial juntos. Si la generación se rechaza, conserva solo las mediciones nuevas sin sustituir los M3U.

`stability_history.json` conserva hasta doce observaciones por SHA-256 de URL + headers, con caducidad de treinta días; son seudónimos, no cifrado. `dist/diagnostico_estabilidad.json` registra proveedores importados, exclusiones, ganadores por proveedor/evidencia, catálogo objetivo y cantidades por lista. Solo ese diagnóstico se sube como artefacto de un día.

“validada” significa candidato validado; hay que comprobar también publicación, commit y URL final. No se fuerza el push ni se sobrescriben cambios concurrentes.

## Ejecución y pruebas

Actions → **Generar Lista clásica M3U desde Xtream** → Run workflow. Se ejecuta también cada seis horas y al cambiar scripts, catálogo, tests o workflow en main. Las PR ejecutan pruebas sin Secrets ni proveedores reales.

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py
```

En un entorno con credenciales ya configuradas de forma segura y FFmpeg instalado:

```sh
STABILITY_FFPROBE=1 python3 scripts/xtream_to_m3u.py
```

Las pruebas usan servidores locales y fixtures: incluyen TS después del límite de buffer, HLS/VOD/cifrado, headers, ffprobe real, historial, cobertura, distribución, backup adelantado, países/idiomas, aliases engañosos, packs separados, metadatos con comas, sublistas y protección frente a pérdida de canales.

Referencias técnicas: [ffprobe](https://ffmpeg.org/ffprobe.html), [HLS RFC 8216](https://www.rfc-editor.org/rfc/rfc8216.html).

