# Configurar proveedores Xtream Codes

Este repositorio puede combinar hasta 10 cuentas autorizadas de Xtream Codes en una lista M3U y filtrar las entradas que tengan indicios de ser argentinas o en español.

## 1. Guardar los datos como secretos

En GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

**Recomendado para tus listas:** guardar cada enlace completo en un secreto:
- `XTREAM_1_URL`: URL completa de `get.php`, con sus parámetros `username`, `password`, `type` y `output` tal como los entrega el proveedor.
- Repetir con `XTREAM_2_URL` hasta `XTREAM_10_URL`.

Como alternativa, podés usar los tres secretos por proveedor:
- `XTREAM_1_SERVER`: servidor base, sin `/get.php).
- `XTREAM_1_USERNAME`: usuario Xtream.
- `XTREAM_1_PASSWORD`: contraseña Xtream.

Si para un número se configuran tanto la URL completa como las variables separadas, se usa la URL completa. No escribas credenciales en archivos del repositorio, issues, mensajes de registro ni capturas públicas.

## 2. Ejecutar la generación

Abrir **Actions → Generar Lista clásica M3U desde Xtream → Run workflow**. También se programa cada seis horas.

Al terminar, descargar el artefacto `lista-clasica-m3u` desde la ejecución. El artefacto se elimina automáticamente después de un día.

## 3. Cómo filtra el contenido

El generador prioriza pistas en el nombre del canal, grupo, país e idioma M3U para conservar contenido identificado como argentino o en español, incluidos eventos deportivos/en vivo cuando el nombre o grupo contiene esas pistas. Descarta entradas marcadas claramente como otros países o idiomas.

**Limitación importante:** los proveedores no etiquetan todos los canales de la misma manera. Un canal/evento sin país ni idioma indicado puede quedar afuera; un nombre que diga “ES” o “Latino” tampoco garantiza que el audio realmente esté en español. El script no inspecciona la señal de video/audio, así que hay que revisar la primera lista generada y ajustar los filtros con ejemplos reales si falta algún canal argentino o evento.

## Privacidad

El M3U generado contiene URLs de reproducción y puede incluir usuario y contraseña dentro de cada URL. No publiques el archivo en este repositorio público. El artefacto de Actions puede ser descargado por personas con permisos de acceso al repositorio.

Esta configuración genera una lista como artefacto temporal; todavía no publica un enlace permanente para que la APK lo consuma. El generador comprueba el formato básico, filtra y combina entradas por nombre, pero no mide latencia ni garantiza que cada canal reproduzca.
