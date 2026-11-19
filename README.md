# Animux Stremio • Anime Latino

Addon de Stremio que adapta el motor de extracción de [Animux](https://github.com/MarcoAnotino/animux) a una API HTTP.

## Funciones
- Catálogo Populares: selección curada y validada mediante búsqueda real en los proveedores.\n- Catálogo Últimos agregados: lectura de listados públicos de los proveedores compatibles con doblaje.\n- Búsqueda de anime y fichas de series con episodios.\n- No se publica un ranking de Tendencias porque los proveedores consultados no exponen métricas fiables de popularidad.
- Resolución de enlaces al solicitar cada episodio.
- Varios servidores cuando la fuente permite enumerarlos.
- Fallback entre proveedores.
- Modo de doblaje español; AnimeFLV se limita a la pista `LAT`, excluyendo `ESP` y `CAST`.
- Aviso cuando no se encuentran fuentes reproducibles.

## Fuentes
El orden del motor Animux para español doblado es MonosChinos → AnimeFLV → AnimeAV1. La disponibilidad cambia según los proveedores. Las etiquetas de idioma de terceros no siempre permiten verificar de forma independiente la región del audio.

## Desarrollo local
Requiere Python 3.10+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 3000
```

Rutas: `/manifest.json`, `/health`, `/providers`, `/debug/search?q=naruto`, catálogo, meta y stream de Stremio.

## Despliegue
FastAPI sobre runtime Python de Vercel. Los pushes a `main` activan el despliegue conectado.

## Créditos y licencia
El motor se basa en [Animux](https://github.com/MarcoAnotino/animux), de MarcoAnotino, derivado a su vez de ani-cli. Se conservan los avisos originales en `LICENSE` y `NOTICE`.

Esta adaptación se distribuye bajo GNU GPL v3.0. Se modificó para integrarla con Stremio y limitar AnimeFLV a la pista LAT. Consulta `LICENSE` para el texto completo.

El addon no aloja archivos multimedia; los proveedores externos determinan la disponibilidad y las condiciones de acceso.
