# AnimeLatam Torrents Latino

Addon independiente de Stremio para buscar torrents de anime y mostrar únicamente publicaciones cuyo título declara explícitamente audio español latino.

## Instalación

- Manifest: https://animelatam-animeonline-test.vercel.app/manifest.json
- Estado: https://animelatam-animeonline-test.vercel.app/health
- Proveedores: https://animelatam-animeonline-test.vercel.app/providers

En Stremio: abre el enlace del manifest o pega la URL en el buscador de addons.

## Filtro de idioma

El filtro es intencionalmente estricto. Acepta etiquetas explícitas como `Spanish Latino`, `Español Latino`, `Latam Dub`, `Latino Audio` o `[LAT]`. No considera suficientes etiquetas genéricas como `Spanish`, `MULTI`, `Dual Audio`, `ESP` o la presencia de subtítulos.

**Limitación importante:** el addon filtra por metadatos del nombre del release; no analiza las pistas de audio dentro del archivo torrent/video. Por ello, una etiqueta puede ser incorrecta y un release sin etiqueta latina puede quedar fuera. El resultado debe considerarse una coincidencia declarada, no una verificación técnica del contenido.

## Proveedores

Integrados en esta primera versión: Nyaa RSS, Tokyo Toshokan (HTML público) y The Pirate Bay API.

Los demás proveedores de la lista quedan registrados en `/providers` con estado pendiente o fuera de servicio. No se simulan resultados. AnimeTosho y su feed NZB se marcan fuera de servicio por el anuncio público de cierre de feeds/API. Easynews se omite por decisión del proyecto.

## Desarrollo

```bash
npm install
npm start
```

Node.js 20 o superior.

## Rutas

- `/manifest.json`: manifest Stremio
- `/health`: comprobación de estado
- `/providers`: estado de los proveedores
- `/debug/search?q=...`: resultados y errores para diagnóstico

## Privacidad y seguridad

No se solicitan credenciales ni se almacenan cuentas de proveedores. El addon devuelve enlaces magnet de los indexadores consultados. Utiliza únicamente fuentes y contenido a los que tengas derecho de acceso.
