import re
from urllib.parse import urljoin, quote_plus
import httpx
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36", "Accept-Language": "es-419,es;q=0.9,en;q=0.6"}
TIMEOUT = 14

async def get_html(url: str):
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return str(response.url), response.text

def normalize(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())

def episode_number(text: str):
    match = re.search(r"(?:episodio|episode|capitulo|capítulo|ep)\s*[-#:]?\s*(\d{1,4})|(?:[-/])(?:episode-?)?(\d{1,4})(?:\b|/)", text or "", re.I)
    if not match:
        return None
    return int(next(group for group in match.groups() if group))

def is_latino(label: str) -> bool:
    text = normalize(label)
    return any(word in text for word in ("latino", "castellano latino", "audio latino", "doblaje latino", "espanol latino"))

def is_spanish_subtitle_only(label: str) -> bool:
    text = normalize(label)
    return any(word in text for word in ("sub esp", "subtitulado", "sub español", "sub espanol")) and not is_latino(text)

def extract_episode_link(soup: BeautifulSoup, base_url: str, title: str, episode: int):
    target = normalize(title)
    candidates = []
    for anchor in soup.select("a[href]"):
        text = anchor.get_text(" ", strip=True)
        href = urljoin(base_url, anchor.get("href", ""))
        combined = f"{text} {href}"
        if episode_number(combined) != episode:
            continue
        score = 0
        norm = normalize(combined)
        if target and any(token in norm for token in target.split() if len(token) > 3):
            score += 2
        if text:
            score += 1
        candidates.append((score, href, text))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][2]

async def find_video_sources(page_url: str, source_name: str):
    final_url, html = await get_html(page_url)
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for node in soup.select("iframe[src], video[src], video source[src], a[href]"):
        url = urljoin(final_url, node.get("src") or node.get("href") or "")
        label = " ".join([node.get_text(" ", strip=True), node.get("title", ""), node.get("aria-label", ""), node.get("class", [""])[0] if isinstance(node.get("class"), list) and node.get("class") else ""])
        if not url.startswith(("http://", "https://")):
            continue
        host = url.split("/")[2].lower()
        if host in {final_url.split('/')[2].lower()} or any(x in host for x in ("facebook.com", "twitter.com", "telegram.me", "whatsapp.com")):
            continue
        # Do not mislabel generic subbed Spanish sources as Latin Spanish.
        if is_spanish_subtitle_only(label):
            continue
        found.append({"name": f"{source_name} · {label[:45]}" if label else source_name, "url": url, "behaviorHints": {"notWebReady": True}})
    return found
