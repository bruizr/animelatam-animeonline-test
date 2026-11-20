import httpx

KITSU_API = "https://kitsu.io/api/edge"

def parse_kitsu_video_id(value: str):
    # AIOMetadata episode IDs use kitsu:{anime_id}:{episode_number}.
    parts = value.split(":")
    if len(parts) != 3 or parts[0].lower() != "kitsu":
        return None
    if not parts[1].isdigit() or not parts[2].isdigit():
        return None
    anime_id, episode = int(parts[1]), int(parts[2])
    if anime_id <= 0 or episode <= 0:
        return None
    return anime_id, episode

async def get_anime_title(anime_id: int) -> str:
    url = f"{KITSU_API}/anime/{anime_id}"
    headers = {"Accept": "application/vnd.api+json", "User-Agent": "AnimeLatam/0.1"}
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    attrs = payload.get("data", {}).get("attributes", {})
    titles = attrs.get("titles") or {}
    return (titles.get("en") or attrs.get("canonicalTitle") or next(iter(titles.values()), "")).strip()
