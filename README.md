# TV paga y premium para Argentina

El generador busca las señales del catálogo argentino entre los proveedores Xtream configurados en Secrets. Las grillas públicas se utilizan como referencia de nombres, categorías y packs; **no se importan listas gratuitas ni se agregan streams externos**.

El catálogo objetivo contiene **179 señales**. Tener un canal en el catálogo no significa que tus proveedores lo ofrezcan o que haya superado una muestra de estabilidad.

## Listas

Todos los enlaces se regeneran desde los mismos ganadores, con una señal por canal y categorías consistentes.

| Lista | Contenido |
|---|---|
| [Lista principal](lista_clasica.m3u) | Bouquet completo para Argentina; conserva la URL existente |
| [Argentina premium](listas/argentina_premium.m3u) | La selección completa, con TV paga, packs y nacionales/regionales del servicio |
| [Packs premium](listas/packs_premium.m3u) | HBO, Universal+ y fútbol premium |
| [HBO](listas/hbo.m3u) | Las ocho señales del pack, cuando están disponibles |
| [Universal+](listas/universal.m3u) | Premiere, Cinema, Comedy, Crime y Reality |
| [Cine y series](listas/cine_series.m3u) | Señales lineales de ficción |
| [Deportes](listas/deportes.m3u) | ESPN, Fox Sports, TyC, TNT Sports, DSports y demás señales reconocidas |
| [Infantiles](listas/infantiles.m3u) | Canales infantiles del catálogo |
| [Documentales](listas/documentales.m3u) | Discovery, History, National Geographic y afines |
| [Entretenimiento](listas/entretenimiento.m3u) | Variedades, cocina y estilo de vida |
| [Música](listas/musica.m3u) | Canales musicales |
| [Estables](listas/estables.m3u) | Solo ganadores con continuidad TS o avance HLS observado en la ejecución actual |
| [Disponibilidad](listas/disponibilidad.json) | Canales publicados, ausentes del proveedor o sin alternativas útiles; no incluye streams ni cuentas |

Una sublista sin señales disponibles contiene solo la cabecera M3U; no se inventan enlaces para completarla. Las listas completas pueden incluir evidencia parcial o alternativas sin medir, identificadas en el informe. **Una muestra breve no garantiza estabilidad durante horas.**

## Mejoras del selector

- Alias revisados y coincidencias exactas: distingue HBO/HBO Pop, Universal TV/Universal Crime, ESPN 2/3 y DSports/DSports+. Consolida prefijos AR, calidades, opciones y variantes como E$PN, F0X Sports y caracteres mal decodificados.
- Bloquea regiones o idiomas explícitamente ajenos al alcance, incluso si el nombre coincide con una marca conocida. Una ciudad o la palabra “Argentina” dentro de un evento ya no incorporan entradas desconocidas.
- No usa un grupo “Otros” ni “Canales identificados”. Los nacionales y regionales tienen sus categorías; las señales de cine usan TV · Ficción para conservar la compatibilidad con el clasificador actual del cliente.
- Revalida la fuente anterior, distribuye empates entre proveedores y adelanta una alternativa cuando una muestra falla o es inconclusa. Solo mantiene una comprobación activa por cuenta, hasta cuatro cuentas en paralelo.
- Compara continuidad actual, historial y velocidad de arranque. No selecciona una URL que falló en la ejecución actual. La fuente anterior se conserva ante diferencias pequeñas.
- Observa el transporte TS después de llenar el buffer de muestra: hasta 512 KiB retenidos y 4 MiB transferidos. Una fuente rápida ya no queda parcial únicamente por llenar la memoria de muestra antes de dos segundos.
- Usa 138 referencias de logos existentes y específicos de cada señal. Si el catálogo no tiene logo, conserva el del proveedor.
- Protege la publicación ante pérdida de más del 20% de identidades elegibles. La limpieza de señales extranjeras, eventos y radios se calcula aparte.

## Referencias del catálogo

- [Flow: grilla para Argentina](https://www.personal.com.ar/flow/guia-de-canales)
- [Flow: pack HBO](https://www.personal.com.ar/flow/pack-hbo)
- [Flow: Universal+](https://www.personal.com.ar/flow/plataformas-de-streaming/universal-plus)
- [Flow: pack Fútbol](https://www.personal.com.ar/flow/pack-futbol)
- [CGD: grilla de TV paga 2026](https://cgdweb.com.ar/wp-content/uploads/2026/02/GRILLA-CANALES-2026-para-enviar.pdf)
- [DIRECTV Argentina](https://www.directvla.com/ar/servicios/tv-satelital)
- [Archivos de logos](https://github.com/tv-logo/tv-logos)

Los nacionales/regionales y algunas variantes anteriores se conservan como objetivos condicionados a lo que entregue el proveedor. No se afirma que todas las señales de las distintas grillas estén en todos los planes, ni que los nombres antiguos prueben el contenido real de un stream.

Configuración y límites: [XTREAM_SETUP.md](XTREAM_SETUP.md). Metadatos editables: [data/catalogo_argentina.json](data/catalogo_argentina.json).
