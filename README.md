# AnimeLatam – AnimeOnline test

Repositorio temporal e independiente para evaluar el proveedor AnimeOnline.ninja en Stremio.

## Estado

- [x] Esqueleto independiente de addon Stremio y endpoint de diagnóstico.
- [x] Identificación de la cadena AnimeOnline → Saidochesto → FileLions.
- [x] Parser experimental del manifiesto HLS desde el HTML público del embed.
- [ ] Validar selectores reales del catálogo, ficha y episodios.
- [ ] Validar resolución HLS desde el servidor desplegado.
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

El extractor no intenta eludir Cloudflare, obtener cookies/tokens de sesión ni saltarse controles de acceso. Si el proveedor requiere un mecanismo no público, debe solicitarse a sus administradores un endpoint o método autorizado.

## Crédito

Fuente de catálogo y episodios: [AnimeOnline.ninja](https://ww4.animeonline.ninja/), con autorización comunicada por el administrador al responsable del addon. Reproductor externo observado: Saidochesto / FileLions.
