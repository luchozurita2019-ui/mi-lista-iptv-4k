# Configurar proveedores Xtream Codes

Este repositorio puede combinar hasta 10 cuentas autorizadas de Xtream Codes en una lista M3U, filtrar las entradas que tengan indicios de ser argentinas o en español y probar brevemente las URLs de reproducción para preferir las alternativas que entregan datos.

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

Al terminar, descargar el artefacto `lista-clasica-m3u` desde la ejecución. Incluye `lista_clasica.m3u` y `diagnostico_estabilidad.json` (resumen sin URLs ni credenciales). El artefacto se elimina automáticamente después de un día.

## 3. Filtro, categorías y logos

El generador conserva entradas con indicios de español y excluye grupos claramente marcados como VOD, películas individuales o episodios. Después normaliza el atributo `group-title` para que la APK muestre categorías uniformes y la lista quede ordenada alfabéticamente dentro de cada categoría:

1. **Adultos**
2. **Cine y Series**
3. **Noticias**
4. **Deportes**
5. **Infantiles**
6. **Documentales**
7. **Música**
8. **Entretenimiento**
9. **General**
10. **Eventos**

Los canales para adultos reconocidos por su nombre o categoría se agrupan en **Adultos**, separados del resto. Se conservan únicamente si no vienen marcados explícitamente con otro idioma; como con los demás canales, el idioma real no se puede verificar desde el nombre.

Se conserva el atributo `tvg-logo` que entregue cada proveedor. Si un canal aparece repetido y una de las versiones tiene logo mientras la otra no, se prioriza la que sí tiene logo. El generador no inventa direcciones de imágenes: si ningún proveedor ofrece el logo, quedará sin logo hasta agregar una fuente confiable.

**Limitación importante:** la clasificación se basa en nombres y grupos; puede equivocarse si el proveedor etiqueta mal un canal. Tampoco puede confirmar el idioma real del audio. Hay que revisar la primera lista generada y ajustar las reglas con ejemplos reales.

## Privacidad

El M3U generado contiene URLs de reproducción y puede incluir usuario y contraseña dentro de cada URL. No publiques el archivo en este repositorio público. El artefacto de Actions puede ser descargado por personas con permisos de acceso al repositorio.

Esta configuración genera una lista como artefacto temporal; todavía no publica un enlace permanente para que la APK lo consuma. El generador comprueba el formato básico, filtra y combina entradas por nombre, pero no mide latencia ni garantiza que cada canal reproduzca.


## 4. Pruebas de estabilidad de URLs

En cada generación se hace una comprobación HTTP breve de hasta 4 KiB por URL, con un tiempo de espera de 6 segundos y hasta 12 pruebas simultáneas. Se priorizan las alternativas de canales duplicados y se prueban hasta 1.500 URLs por ejecución. Para cada canal duplicado se prefiere una URL que entregue datos; entre las que pasan, se favorece el menor tiempo de respuesta y después la disponibilidad de logo.

El informe `dist/diagnostico_estabilidad.json` registra solo cantidades y resultados agregados, no direcciones de reproducción ni credenciales. Si todas las alternativas de un canal fallan o no llegan a probarse, el generador conserva una alternativa en vez de eliminar el canal automáticamente, porque un fallo puntual puede ser temporal.

**Límite:** esta prueba breve sirve para descartar algunas URLs caídas o que no entregan datos, pero no mide la estabilidad histórica ni garantiza reproducción sostenida, audio correcto o compatibilidad con DRM. Para evaluar cortes reales hace falta acumular resultados durante varios días y probar muestras de reproducción con un reproductor compatible. La ejecución de GitHub Actions no reemplaza las pruebas desde la red y el dispositivo donde se usará la APK.
