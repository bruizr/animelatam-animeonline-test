# AnimeLatam

Addon complementario de Stremio para aportar fuentes de reproducción de anime en español latino a AIOMetadata.

## Alcance inicial
- Recurso Stremio: únicamente `stream`.
- Tipo: `series`.
- Identificadores: episodios Kitsu con formato `kitsu:{anime_id}:{episode_number}`.
- Scrapers iniciales: MonosChinos y AnimeAV1, en ese orden.
- AnimeFLV queda fuera de la primera versión hasta verificar dominio y funcionamiento.
- No incluye catálogos, búsqueda ni metadatos propios.

## Desarrollo local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Pruebas rápidas: `/manifest.json`, `/health` y `/stream/series/kitsu:1:1.json`.

## Nota sobre idioma
Los scrapers solo deben devolver reproductores que indiquen explícitamente audio/doblaje latino. Una página marcada como "sub español" no se considera español latino. Si el sitio no expone una etiqueta verificable del idioma del audio, no se atribuye esa condición a la fuente.

## Estado
Base inicial del protocolo y de los scrapers. La extracción real de los reproductores debe validarse contra páginas de episodios concretas antes de considerar lista la versión pública.
