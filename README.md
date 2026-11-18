# AnimeLatam – AnimeOnline test

Repositorio temporal e independiente para evaluar el proveedor AnimeOnline.ninja en Stremio.

## Publicación de prueba

- **Estado del despliegue:** publicado en Vercel (Production / READY).
- **Addon manifest:** https://animelatam-animeonline-test.vercel.app/manifest.json
- **Página de estado:** https://animelatam-animeonline-test.vercel.app/
- **Proyecto Vercel:** https://vercel.com/me-34ee/animelatam-animeonline-test

Para instalar en Stremio, abre el manifest en el navegador y usa la opción de instalación de Stremio, o pega esta dirección en el buscador de addons:
`https://animelatam-animeonline-test.vercel.app/manifest.json`

## Estado técnico

- [x] Esqueleto independiente de addon Stremio y endpoint de diagnóstico.
- [x] Identificación de la cadena AnimeOnline → Saidochesto → FileLions.
- [x] Corrección de exportación del router Express.
- [x] Despliegue publicado y respuesta HTTP 200 del manifest y ruta raíz.
- [ ] Validar selectores reales del catálogo, ficha y episodios.
- [ ] Validar resolución HLS desde el servidor desplegado con una URL real de episodio.
- [ ] Probar reproducción real en Stremio.
- [ ] Integrar metadatos y episodios completos.

## Desarrollo

```bash
npm install
npm start
```

Manifest: `/manifest.json`

Diagnóstico de episodio:

```
/test/resolve?url=https%3A%2F%2Fww4.animeonline.ninja%2Fepisodio%2F...
```

Sustituye el ejemplo por la URL HTTPS real de una página de episodio de AnimeOnline.ninja.

El extractor no intenta eludir Cloudflare, obtener cookies/tokens de sesión ni saltarse controles de acceso. Si el proveedor requiere un mecanismo no público, debe solicitarse a sus administradores un endpoint o método autorizado.

## Crédito

Fuente de catálogo y episodios: [AnimeOnline.ninja](https://ww4.animeonline.ninja/), con autorización comunicada por el administrador al responsable del addon. Reproductor externo observado: Saidochesto / FileLions.
