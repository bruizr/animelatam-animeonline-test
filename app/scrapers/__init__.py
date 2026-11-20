from app.scrapers.monoschinos import scrape as scrape_monoschinos
from app.scrapers.animeav1 import scrape as scrape_animeav1

async def scrape_all(title: str, episode: int):
    results = []
    # Scraper order agreed for the first release.
    for scraper in (scrape_monoschinos, scrape_animeav1):
        try:
            results.extend(await scraper(title, episode))
        except Exception:
            continue
    # De-duplicate URLs without altering the source URLs.
    unique, seen = [], set()
    for item in results:
        url = item.get("url")
        if url and url not in seen:
            seen.add(url)
            unique.append(item)
    return unique
