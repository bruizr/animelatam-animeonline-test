# Animux Stremio • Buscador de fuentes

Addon de Stremio basado en el motor de extracción de [Animux](https://github.com/MarcoAnotino/animux).

## Funciones activas
- Una única búsqueda de anime.
- Selección mínima de episodios.
- Resolución automática de la primera fuente reproducible.
- Solo doblaje español latino; AnimeFLV se limita a la pista LAT.
- Aviso externo cuando no se encuentra una fuente.

No hay catálogos de populares, últimos agregados, tendencias ni páginas de descubrimiento. El catálogo de búsqueda y la ficha mínima de episodios se conservan porque Stremio necesita un anime y un identificador de episodio antes de solicitar sus fuentes.

## Resolución
La extracción intenta primero el servidor directo MP4Upload cuando está disponible, después servidores secundarios y deja los servidores HLS más lentos para el final. El addon devuelve la primera fuente válida para evitar que Stremio espere a múltiples intentos redundantes.

## Desarrollo local
Requiere Python 3.10+.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 3000
```

Rutas principales: `/manifest.json`, `/catalog/series/animux-search`, `/meta/series/{id}.json` y `/stream/series/{id}.json`.

## Despliegue
FastAPI sobre runtime Python de Vercel. Los pushes a `main` activan el despliegue conectado.

## Créditos y licencia
El motor se basa en [Animux](https://github.com/MarcoAnotino/animux), de MarcoAnotino, derivado a su vez de ani-cli. Se conservan los avisos originales en `LICENSE` y `NOTICE`.

Esta adaptación se distribuye bajo GNU GPL v3.0. Consulta `LICENSE` para el texto completo.

El addon no aloja archivos multimedia; los proveedores externos determinan la disponibilidad y las condiciones de acceso.
