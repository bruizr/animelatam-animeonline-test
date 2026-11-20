from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from app.scrapers.common import get_html, normalize, episode_number, extract_episode_link, find_video_sources, is_latino

BASE = "https://monoschinos.net"

async def scrape(title: str, episode: int):
    search_url = f"{BASE}/?s={quote_plus(title)}"
    final_url, html = await get_html(search_url)
    soup = BeautifulSoup(html, "html.parser")
    title_norm = normalize(title)
    series_links = []
    for a in soup.select("a[href]"):
        text = normalize(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if href.startswith("/") or href.startswith(BASE):
            if text and any(token in text for token in title_norm.split() if len(token) > 3):
                series_links.append(href if href.startswith("http") else BASE + href)
    for series_url in dict.fromkeys(series_links):
        try:
            _, series_html = await get_html(series_url)
            series_soup = BeautifulSoup(series_html, "html.parser")
            match = extract_episode_link(series_soup, series_url, title, episode)
            if not match:
                continue
            episode_url, label = match
            # Site pages may be subbed; return only sources explicitly marked Latin Spanish.
            sources = await find_video_sources(episode_url, "MonosChinos")
            explicit = [s for s in sources if is_latino(s.get("name", ""))]
            if explicit:
                return explicit
        except Exception:
            continue
    return []
