import asyncio
import base64
import json
import logging
import os
import re
import time
import unicodedata
from html import escape
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from extractors import AnimeExtractor

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
log = logging.getLogger("animux-stremio")

APP_NAME = "Animux • Anime Latino"
APP_VERSION = "0.2.2"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://animelatam-animeonline-test.vercel.app",
).rstrip("/")

# Solo búsqueda, ficha mínima de episodios y resolución de fuentes.
# Catalog y meta son necesarios para que Stremio pueda seleccionar un episodio.
manifest = {
    "id": "com.animux.stremio.latino",
    "version": APP_VERSION,
    "name": APP_NAME,
    "description": "Buscador de fuentes de anime en español latino.",
    "resources": [
        "catalog",
        {
            "name": "meta",
            "types": ["series"],
            "idPrefixes": ["animux:anime:"],
        },
        {
            "name": "stream",
            "types": ["series"],
        },
    ],
    "types": ["series"],
    "catalogs": [
        {
            "type": "series",
            "id": "animux-search",
            "name": "Buscar anime latino",
            "extra": [{"name": "search", "isRequired": False}],
        }
    ],
    "behaviorHints": {"adult": False, "p2p": False},
}

app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def request_diagnostics(request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        route = request.url.path.split("/")
        route_hint = "/".join(route[:4])
        log.exception(
            "HTTP request failed method=%s route=%s status=500 elapsed_ms=%s ua=%s",
            request.method,
            route_hint,
            elapsed_ms,
            (request.headers.get("user-agent") or "unknown")[:100],
        )
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    route = request.url.path.split("/")
    route_hint = "/".join(route[:4])
    log.warning(
        "HTTP request method=%s route=%s status=%s elapsed_ms=%s ua=%s",
        request.method,
        route_hint,
        response.status_code,
        elapsed_ms,
        (request.headers.get("user-agent") or "unknown")[:100],
    )
    return response


def _encode(data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value):
    try:
        padded = value + "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="Identificador Animux inválido"
        ) from exc


def _normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip().lower()


def _is_non_latin_release(title):
    normalized = _normalize(title)
    excluded = (
        "castellano",
        "españa",
        "espanol de espana",
        "spanish spain",
        "castilian",
        "iberian spanish",
    )
    return any(term in normalized for term in excluded)


def _search(query):
    # AnimeExtractor.search respeta SOURCE_ORDER para doblaje español:
    # AnimeAV1, MonosChinos y AnimeFLV (pista LAT).
    results = AnimeExtractor().search(query, dub=True, lang="es")
    clean = []
    seen = set()

    for item in results or []:
        title = str(item.get("title") or "").strip()
        anime_id = str(item.get("id") or "").strip()
        if not title or not anime_id or _is_non_latin_release(title):
            continue
        if anime_id.lower() in seen:
            continue
        seen.add(anime_id.lower())
        clean.append(
            {
                "id": anime_id,
                "title": title,
                "poster": item.get("poster") or "",
                "extractor": str(item.get("extractor") or "Animux"),
            }
        )
    return clean


def _video_number(value):
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _stream_from_result(result, episode, title):
    formats = result.get("formats") or []
    fmt = formats[0] if formats else {}
    url = result.get("url") or fmt.get("url")

    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None

    headers = {}
    headers.update(fmt.get("http_headers") or {})
    headers.update(result.get("http_headers") or {})
    referer = result.get("referer") or headers.get("Referer")
    if referer:
        headers["Referer"] = referer

    source = (
        result.get("source_label")
        or result.get("source")
        or result.get("extractor")
        or "Animux"
    )
    extension = str(fmt.get("ext") or result.get("ext") or "").lower()
    # Stremio exige notWebReady=true siempre que se envían proxyHeaders.
    # También se marca así para HLS/formatos distintos de MP4.
    not_web_ready = bool(headers) or extension != "mp4" or ".m3u8" in url.lower()

    stream = {
        "name": "Animux • " + str(source),
        "title": f"{title} • Episodio {episode} • Español latino",
        "url": url,
        "behaviorHints": {"notWebReady": not_web_ready},
    }
    if headers:
        stream["behaviorHints"]["proxyHeaders"] = {"request": headers}
    if extension:
        stream["description"] = extension.upper()
    return stream


def _resolve_streams(payload):
    anime_id = str(payload["anime_id"])
    title = str(payload.get("title") or "Anime")
    episode = str(payload["episode"])

    # Una sola resolución automática: evita enumerar los servidores y luego
    # volver a descargar la misma página para resolverlos uno por uno.
    try:
        result = AnimeExtractor().get_episode(
            anime_id,
            episode,
            dub=True,
            title=title,
            lang="es",
            server="auto",
            source="auto",
        )
        stream = _stream_from_result(result, episode, title)
        if stream:
            parsed = urlsplit(stream["url"])
            headers = stream.get("behaviorHints", {}).get("proxyHeaders", {}).get("request", {})
            log.info(
                "Fuente lista: anime=%s episodio=%s host=%s ext=%s headers=%s",
                title,
                episode,
                parsed.hostname or "desconocido",
                result.get("ext") or "desconocida",
                ",".join(sorted(headers.keys())) or "ninguno",
            )
            return [stream]
        log.warning(
            "El extractor devolvió resultado sin URL HTTP válida: anime=%s episodio=%s",
            title,
            episode,
        )
        return []
    except Exception as exc:
        log.warning(
            "No se encontró fuente para %s episodio %s: %s",
            title,
            episode,
            exc,
        )
        return []


def _catalog_item(item, query):
    source_title = str(item["title"])
    display_title = source_title
    if (
        _normalize(query) == "overgeared"
        and _normalize(source_title) == "tempal item no chikara"
    ):
        display_title = "Overgeared"

    meta_id = "animux:anime:" + _encode(
        {
            "anime_id": item["id"],
            "title": display_title,
            "poster": item.get("poster") or "",
        }
    )
    return {
        "id": meta_id,
        "type": "series",
        "name": display_title,
        "poster": item.get("poster") or "",
        "description": (
            f"Fuente: {item.get('extractor', 'Animux')} · "
            "Búsqueda de fuentes en español latino"
        ),
        "genres": ["Anime", "Español latino"],
        "releaseInfo": "Español latino",
    }


@app.get("/", response_class=HTMLResponse)
async def home():
    manifest_url = PUBLIC_BASE_URL + "/manifest.json"
    return HTMLResponse(
        "<!doctype html><html lang='es'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Animux • Buscador de fuentes</title>"
        "<body style='font:16px system-ui;max-width:680px;margin:10vh auto;"
        "padding:24px;background:#10131b;color:#f4f5f7'>"
        "<h1>Animux • Buscador de fuentes</h1>"
        "<p>Addon de Stremio limitado a búsqueda y resolución de fuentes "
        "en español latino.</p><p><a style='color:#8ec5ff' href='"
        + escape(manifest_url)
        + "'>Manifest del addon</a></p></body></html>"
    )


@app.get("/manifest.json")
async def get_manifest():
    return JSONResponse(manifest)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "name": APP_NAME,
        "version": APP_VERSION,
        "mode": "search-and-stream-only",
    }


@app.get("/catalog/series/animux-search.json")
async def catalog_search_empty(search: str = ""):
    query = search.strip()
    if not query:
        return {"metas": []}
    return await _catalog_results(query)


@app.get("/catalog/series/animux-search/{extra:path}")
async def catalog_search(extra: str):
    params = parse_qs(extra.removesuffix(".json"), keep_blank_values=True)
    query = (params.get("search") or params.get("query") or params.get("q") or [""])[0]
    query = query.strip()
    if not query:
        return {"metas": []}
    return await _catalog_results(query)


async def _catalog_results(query):
    try:
        results = await asyncio.to_thread(_search, query)
    except Exception as exc:
        log.warning("Búsqueda fallida para %s: %s", query, exc)
        return {"metas": []}

    return {
        "metas": [_catalog_item(item, query) for item in results[:40]]
    }


@app.get("/meta/series/{meta_id}.json")
async def get_meta(meta_id: str):
    if not meta_id.startswith("animux:anime:"):
        raise HTTPException(status_code=404, detail="Anime no encontrado")

    payload = _decode(meta_id.split("animux:anime:", 1)[1])
    anime_id = str(payload.get("anime_id") or "")
    title = str(payload.get("title") or "Anime")
    if not anime_id:
        raise HTTPException(status_code=400, detail="Falta el identificador del anime")

    try:
        episodes = await asyncio.to_thread(
            lambda: AnimeExtractor().list_episodes(
                anime_id, dub=True, lang="es"
            )
        )
    except Exception as exc:
        log.warning("No se pudieron listar episodios de %s: %s", title, exc)
        episodes = []

    videos = []
    for number in episodes:
        episode = str(number)
        video_id = "animux:episode:" + _encode(
            {"anime_id": anime_id, "title": title, "episode": episode}
        )
        videos.append(
            {
                "id": video_id,
                "title": f"Episodio {episode}",
                "season": 1,
                "episode": _video_number(episode),
            }
        )

    return {
        "meta": {
            "id": meta_id,
            "type": "series",
            "name": title,
            "poster": payload.get("poster") or "",
            "description": "Fuentes en español latino disponibles al reproducir.",
            "genres": ["Anime", "Español latino"],
            "videos": videos,
        }
    }


@app.get("/stream/series/{video_id}.json")
async def get_stream(video_id: str):
    if not video_id.startswith("animux:episode:"):
        return {"streams": []}

    payload = _decode(video_id.split("animux:episode:", 1)[1])
    if not payload.get("anime_id") or not payload.get("episode"):
        raise HTTPException(status_code=400, detail="Episodio inválido")

    streams = await asyncio.to_thread(_resolve_streams, payload)
    if streams:
        return {"streams": streams}

    title = str(payload.get("title") or "Anime")
    episode = str(payload["episode"])
    notice_url = (
        PUBLIC_BASE_URL
        + "/no-sources?title="
        + quote(title)
        + "&episode="
        + quote(episode)
    )
    return {
        "streams": [
            {
                "name": "Animux • Aviso",
                "title": "No se encontraron fuentes para este episodio",
                "description": (
                    f"No se encontró una fuente reproducible en español latino "
                    f"para {title}, episodio {episode}."
                ),
                "externalUrl": notice_url,
            }
        ]
    }


@app.get("/no-sources", response_class=HTMLResponse)
async def no_sources(title: str = "Anime", episode: str = ""):
    return HTMLResponse(
        "<!doctype html><html lang='es'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<body style='font:16px system-ui;max-width:680px;margin:10vh auto;"
        "padding:24px;background:#10131b;color:#f4f5f7'>"
        "<h2>No se encontraron fuentes</h2><p>No encontramos una fuente "
        "reproducible en español latino para <b>"
        + escape(title)
        + "</b> — episodio "
        + escape(episode)
        + ".</p><p>El servidor puede estar temporalmente fuera de servicio "
        "o el episodio aún no estar disponible.</p></body></html>"
    )


@app.exception_handler(Exception)
async def unhandled_exception(_request, exc):
    log.exception("Error interno del addon", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Error interno de Animux"},
    )
