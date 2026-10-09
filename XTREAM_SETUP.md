# Configurar proveedores Xtream Codes

Este repositorio puede combinar hasta 10 cuentas autorizadas de Xtream Codes en una lista M3U.

## 1. Guardar los datos como secretos

En GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

Para cada proveedor, crear los tres secretos correspondientes:
- `XTREAM_1_SERVER`: servidor base, por ejemplo `https://servidor.example:8080` (sin `/get.php`).
- `XTREAM_1_USERNAME`: usuario Xtream.
- `XTREAM_1_PASSWORD`: contraseña Xtream.

Repetir con el número 2 hasta el 10 según la cantidad de cuentas. No escribir credenciales en archivos del repositorio, issues, mensajes de registro ni capturas públicas.

## 2. Ejecutar la generación

Abrir **Actions → Generar Lista clásica M3U desde Xtream → Run workflow**. También se programa cada seis horas.

Al terminar, descargar el artefacto `lista-clasica-m3u` desde la ejecución. El artefacto se elimina automáticamente después de un día.

## Importante sobre la privacidad

El archivo M3U contiene URLs de reproducción y puede incluir usuario y contraseña dentro de cada URL. Por eso, aunque los secretos de GitHub estén protegidos, **el M3U generado no debe publicarse en este repositorio público**. El artefacto de Actions también puede ser descargado por personas con permisos de acceso al repositorio.

Esta configuración genera la lista de forma segura para pruebas, pero todavía no publica un enlace permanente para que la APK lo consuma. Para conectar la APK, hay que elegir un destino privado adecuado o un servicio intermedio autenticado. No activar la publicación pública de la lista con credenciales reales.

El generador valida el formato básico de cada lista y combina canales por nombre. Esto no garantiza que un canal reproduzca ni mide estabilidad real de reproducción; esa comprobación se debe hacer después, con pruebas de señal y reproducción.
