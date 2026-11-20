from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.kitsu import parse_kitsu_video_id, get_anime_title
from app.scrapers import scrape_all

APP_ID = "com.animelatam.stremio"
VERSION = "0.1.0"

app = FastAPI(title="AnimeLatam Stremio Addon", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "OPTIONS"], allow_headers=["*"])

@app.get("/")
def root():
    return {"addon": "AnimeLatam", "status": "online", "version": VERSION}

@app.get("/manifest.json")
def manifest():
    return {
        "id": APP_ID,
        "version": VERSION,
        "name": "AnimeLatam",
        "description": "Fuentes de reproducción de anime en español latino. Addon complementario para AIOMetadata.",
        "resources": ["stream"],
        "types": ["series"],
        "idPrefixes": ["kitsu:"],
        "catalogs": [],
        "behaviorHints": {"adult": False, "p2p": False}
    }

@app.get("/stream/{media_type}/{video_id:path}.json")
async def stream(media_type: str, video_id: str, request: Request):
    if media_type != "series":
        return {"streams": []}
    parsed = parse_kitsu_video_id(video_id)
    if not parsed:
        return {"streams": []}
    anime_id, episode = parsed
    try:
        title = await get_anime_title(anime_id)
        streams = await scrape_all(title, episode)
        return {"streams": streams}
    except Exception:
        # Stremio expects a valid response even when a source is temporarily unavailable.
        return {"streams": []}

@app.get("/health")
def health():
    return {"ok": True, "version": VERSION}
