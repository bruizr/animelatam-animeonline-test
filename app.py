import asyncio, base64, json, logging, os, re, unicodedata
from html import escape
from urllib.parse import parse_qs, quote
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from extractors import AnimeExtractor

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
log = logging.getLogger("animux-stremio")
APP_NAME = "Animux • Anime Latino"
APP_VERSION = "0.1.0"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://animelatam-animeonline-test.vercel.app").rstrip("/")
DUB_LATINO_ONLY = True

manifest = {
 "id":"com.animux.stremio.latino","version":APP_VERSION,"name":APP_NAME,
 "description":"Anime en español latino. Motor de extracción basado en Animux.",
 "resources":["catalog","meta","stream"],"types":["series"],
 "catalogs":[{"type":"series","id":"animux-search","name":"Animux • Anime Latino",
              "extra":[{"name":"search","isRequired":True}]}],
 "idPrefixes":["animux:"],"behaviorHints":{"adult":False,"p2p":False}
}
app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None)

def _encode(data):
 raw=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode()
 return base64.urlsafe_b64encode(raw).decode().rstrip("=")

def _decode(value):
 try:
  return json.loads(base64.urlsafe_b64decode((value+"="*(-len(value)%4)).encode()))
 except Exception as exc:
  raise HTTPException(status_code=400,detail="Identificador Animux inválido") from exc

def _normalize(value):
 value=unicodedata.normalize("NFKD",str(value or ""))
 value="".join(c for c in value if not unicodedata.combining(c))
 return re.sub(r"\s+"," ",value).strip().lower()

def _is_non_latin_release(title):
 t=_normalize(title)
 return any(x in t for x in ("castellano","españa","espanol de espana","spanish spain","castilian","iberian spanish"))

def _search(query):
 engine=AnimeExtractor()
 results=engine.search(query,dub=DUB_LATINO_ONLY,lang="es")
 clean=[]; seen=set()
 for item in results or []:
  title=str(item.get("title") or "").strip()
  anime_id=str(item.get("id") or "").strip()
  if not title or not anime_id or _is_non_latin_release(title): continue
  if anime_id.lower() in seen: continue
  seen.add(anime_id.lower())
  clean.append({"id":anime_id,"title":title,"total_episodes":int(item.get("total_episodes") or 0),
                "extractor":str(item.get("extractor") or anime_id.split(":",1)[0]),
                "poster":item.get("poster") or ""})
 return clean

def _video_number(value):
 try:
  n=float(value); return int(n) if n.is_integer() else n
 except (TypeError,ValueError): return None

def _stream_from_result(result,episode,title):
 formats=result.get("formats") or []
 fmt=formats[0] if formats else {}
 url=result.get("url") or fmt.get("url")
 if not url or not str(url).startswith(("http://","https://")): return None
 headers={}
 headers.update(fmt.get("http_headers") or {})
 headers.update(result.get("http_headers") or {})
 referer=result.get("referer") or headers.get("Referer")
 if referer: headers["Referer"]=referer
 source=result.get("source_label") or result.get("source") or result.get("extractor") or "Animux"
 server=result.get("server_label") or result.get("server_id") or "Automático"
 stream={"name":"Animux • "+str(source),
         "title":f"{title} • Ep. {episode}\n{server} • Español latino",
         "url":str(url),"behaviorHints":{"notWebReady":False}}
 if headers: stream["behaviorHints"]["proxyHeaders"]={"request":headers}
 if result.get("ext"): stream["description"]=str(result["ext"]).upper()
 return stream

def _resolve_streams(payload):
 anime_id=str(payload["anime_id"]); title=str(payload.get("title") or "Anime")
 episode=str(payload["episode"]); engine=AnimeExtractor(); streams=[]; seen=set()
 try:
  candidates=engine.list_episode_servers(anime_id,episode,dub=True,title=title,lang="es")
 except Exception as exc:
  log.warning("No se pudieron enumerar servidores: %s",exc); candidates=[]
 for candidate in (candidates or [])[:5]:
  try:
   result=engine.resolve_episode_server(anime_id,episode,candidate.candidate_id,
                                        dub=True,title=title,lang="es")
   stream=_stream_from_result(result,episode,title)
   if stream and stream["url"] not in seen:
    seen.add(stream["url"])
    stream["name"]="Animux • "+str(candidate.source)
    stream["title"]=f"{title} • Ep. {episode}\n{candidate.server_label} • {candidate.source} • Latino"
    streams.append(stream)
  except Exception as exc:
   log.warning("Servidor %s no disponible: %s",candidate.candidate_id,exc)
 if not streams:
  try:
   result=engine.get_episode(anime_id,episode,dub=True,title=title,lang="es",server="auto",source="auto")
   stream=_stream_from_result(result,episode,title)
   if stream: streams.append(stream)
  except Exception as exc: log.warning("Falló fallback Animux: %s",exc)
 return streams

@app.get("/",response_class=HTMLResponse)
async def home():
 url=PUBLIC_BASE_URL+"/manifest.json"
 return HTMLResponse("<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Animux Stremio</title><body style='font:16px system-ui;max-width:720px;margin:10vh auto;padding:24px;background:#10131b;color:#f4f5f7'><h1>Animux • Anime Latino</h1><p>Addon de Stremio basado en el motor de extracción de Animux.</p><p><a style='color:#8ec5ff' href='"+escape(url)+"'>Manifest del addon</a></p><p><a style='color:#8ec5ff' href='stremio://"+escape(url.removeprefix("https://"))+"'>Agregar a Stremio</a></p><p>Idioma: español latino. Los enlaces se resuelven al solicitar cada episodio.</p></body></html>")

@app.get("/manifest.json")
async def get_manifest(): return JSONResponse(manifest)

@app.get("/health")
async def health(): return {"ok":True,"name":APP_NAME,"version":APP_VERSION,"engine":"Animux"}

@app.get("/providers")
async def providers():
 return {"language":"Español latino","dub":True,"sources":[
  {"id":"monoschino","name":"MonosChinos","mode":"doblaje"},
  {"id":"animeflv","name":"AnimeFLV","mode":"solo pista LAT"},
  {"id":"animeav1","name":"AnimeAV1","mode":"doblaje"}]}

@app.get("/debug/search")
async def debug_search(q:str=Query(min_length=1,max_length=160)):
 results=await asyncio.to_thread(_search,q.strip())
 return {"query":q,"language":"es-LAT","results":results,"count":len(results)}

@app.get("/catalog/series/animux-search.json")
async def catalog_empty(search:str=""):
 return await _catalog_results(search.strip()) if search.strip() else {"metas":[]}

@app.get("/catalog/series/animux-search/{extra:path}")
async def catalog_search(extra:str,search:str=""):
 params=parse_qs(extra.removesuffix(".json"),keep_blank_values=True)
 query=(params.get("search") or params.get("query") or params.get("q") or [search])[0]
 return await _catalog_results(query.strip()) if query.strip() else {"metas":[]}

async def _catalog_results(query):
 try: results=await asyncio.to_thread(_search,query)
 except Exception as exc:
  log.warning("Búsqueda fallida: %s",exc); return {"metas":[]}
 metas=[]
 for item in results[:80]:
  mid="animux:anime:"+_encode({"anime_id":item["id"],"title":item["title"],"poster":item["poster"]})
  metas.append({"id":mid,"type":"series","name":item["title"],"poster":item["poster"],
                "description":f"Fuente: {item['extractor']} · Doblaje español latino",
                "genres":["Anime","Español latino"],"releaseInfo":"Español latino"})
 return {"metas":metas}

@app.get("/meta/series/{meta_id}.json")
async def get_meta(meta_id:str):
 if not meta_id.startswith("animux:anime:"): raise HTTPException(404,"Anime no encontrado")
 payload=_decode(meta_id.split("animux:anime:",1)[1])
 anime_id=str(payload.get("anime_id") or ""); title=str(payload.get("title") or "Anime")
 if not anime_id: raise HTTPException(400,"Falta el identificador del anime")
 try:
  episodes=await asyncio.to_thread(lambda:AnimeExtractor().list_episodes(anime_id,dub=True,lang="es"))
 except Exception as exc:
  log.warning("No se pudieron listar episodios de %s: %s",title,exc); episodes=[]
 videos=[]
 for number in episodes:
  text=str(number); vid="animux:episode:"+_encode({"anime_id":anime_id,"title":title,"episode":text})
  videos.append({"id":vid,"title":f"Episodio {text}","season":1,"episode":_video_number(text)})
 return {"meta":{"id":meta_id,"type":"series","name":title,"poster":payload.get("poster") or "",
                 "description":"Anime en modo de doblaje español latino. Las fuentes se consultan al reproducir.",
                 "genres":["Anime","Español latino"],"videos":videos}}

@app.get("/stream/series/{video_id}.json")
async def get_stream(video_id:str):
 if not video_id.startswith("animux:episode:"): return {"streams":[]}
 payload=_decode(video_id.split("animux:episode:",1)[1])
 if not payload.get("anime_id") or not payload.get("episode"): raise HTTPException(400,"Episodio inválido")
 title=str(payload.get("title") or "Anime"); episode=str(payload["episode"])
 streams=await asyncio.to_thread(_resolve_streams,payload)
 if streams: return {"streams":streams}
 notice={"name":"Animux • Aviso","title":"No se encontraron fuentes para este episodio",
         "description":f"No fue posible resolver el episodio {episode} de {title} en las fuentes de español latino disponibles.",
         "externalUrl":PUBLIC_BASE_URL+"/no-sources?title="+quote(title)+"&episode="+quote(episode)}
 return {"streams":[notice]}

@app.get("/no-sources",response_class=HTMLResponse)
async def no_sources(title:str="Anime",episode:str=""):
 return HTMLResponse("<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><body style='font:16px system-ui;max-width:680px;margin:10vh auto;padding:24px;background:#10131b;color:#f4f5f7'><h2>No se encontraron fuentes</h2><p>No encontramos una fuente reproducible en español latino para <b>"+escape(title)+"</b> — episodio "+escape(episode)+".</p><p>Los servidores pueden estar temporalmente fuera de servicio o el episodio aún no estar disponible.</p></body></html>")

@app.exception_handler(Exception)
async def unhandled_exception(_request,exc):
 log.exception("Error interno del addon",exc_info=exc)
 return JSONResponse(status_code=500,content={"error":"Error interno de Animux"})
