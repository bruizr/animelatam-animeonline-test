# extractors/gogoanime.py
import concurrent.futures
import os
import re
import urllib.parse
import json
import time
from dataclasses import replace
from urllib.parse import urljoin, urlparse

from .base import BaseAnimeExtractor, _clean_text, _safe_url, _source_id, _split_source_id


class GogoanimeExtractor(BaseAnimeExtractor):
    site_name = "gogoanime"
    supports_dub = True

    _VALID_URL = (
        r"https?://(?:www\.)?gogoanime\.(?:by|or\.at)/"
        r"(?:(?:episode/)?(?P<anime_id>[a-zA-Z0-9_-]+)-episode-(?P<ep_no>\d+)|(?P<slug>[a-zA-Z0-9_-]+)-(?:episodio|episode)-(?P<ep_no2>\d+))/?$"
    )

    base_url = "https://gogoanime.or.at"

    def _is_or_at(self, url_or_id):
        return "or.at" in url_or_id

    def _download_webpage_with_headers(self, url, headers=None):
        import urllib.request
        try:
            req = urllib.request.Request(url)
            ua = self.user_agent if hasattr(self, 'user_agent') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            req.add_header('User-Agent', ua)
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8')
            req.add_header('Accept-Language', 'en-US,en;q=0.5')
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=12) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            if "control characters" not in str(e):
                self._log(f"[gogoanime] Info: Descarga alternativa para endpoint.")
            try:
                return self._download_webpage(url)
            except Exception:
                return None

    # -------------------------------------------------------------------------
    # Resolución de iframes → stream directo
    # -------------------------------------------------------------------------

    def _extract_video_from_iframes(self, iframe_urls, episode_url):
        self._log(f"[gogoanime] Resolviendo {len(iframe_urls)} iframes...")

        for idx, iframe_url in enumerate(iframe_urls):
            self._log(
                f"[gogoanime] Probando iframe {idx+1}/{len(iframe_urls)}: "
                f"{_safe_url(iframe_url)}"
            )

            html = self._download_webpage_with_headers(
                iframe_url,
                headers={"Referer": episode_url}
            )
            if not html:
                continue

            # 1) Intento de extracción estática directa
            video_url, src_context = self._extract_media_from_html(html, iframe_url)
            if video_url:
                self._log(
                    "[gogoanime] Stream directo encontrado en HTML: "
                    f"{_safe_url(video_url)} ({src_context})"
                )
                return video_url, iframe_url

            # 2) Interceptar comportamiento dinámico/AJAX (Megaplay u otros)
            if "megaplay" in iframe_url or "stream" in iframe_url or "newplayer" in iframe_url:
                video_url = self._crack_dynamic_player(html, iframe_url)
                if video_url:
                    self._log(
                        "[gogoanime] Stream encontrado mediante API dinámica: "
                        f"{_safe_url(video_url)}"
                    )
                    return video_url, iframe_url

            # 3) Buscar sub-iframes aninados de forma recursiva controlada
            nested_iframes = self._extract_iframe_srcs(html, iframe_url)
            if nested_iframes:
                self._log(f"[gogoanime] ↪ Encontrados {len(nested_iframes)} iframes anidados.")
                for nested_url in nested_iframes:
                    self._log(
                        "[gogoanime] Descargando iframe anidado: "
                        f"{_safe_url(nested_url)}"
                    )
                    nested_html = self._download_webpage_with_headers(
                        nested_url,
                        headers={"Referer": iframe_url}
                    )
                    if not nested_html:
                        continue

                    video_url, src_context = self._extract_media_from_html(nested_html, nested_url)
                    if video_url:
                        return video_url, nested_url

                    if "megaplay" in nested_url or "stream" in nested_url:
                        video_url = self._crack_dynamic_player(nested_html, nested_url)
                        if video_url:
                            return video_url, nested_url

                    # Fallback yt-dlp sobre el iframe anidado directo
                    ytdl_url = self._get_url_via_ytdlp(nested_url, referer=iframe_url)
                    if ytdl_url and self._is_media_url(ytdl_url):
                        self._log(
                            "[gogoanime] yt-dlp devolvió un flujo útil en "
                            f"sub-iframe: {_safe_url(ytdl_url)}"
                        )
                        return ytdl_url, nested_url

            # 4) Auxilio externo vía yt-dlp sobre la URL exacta del reproductor principal
            self._log(
                "[gogoanime] Fallback yt-dlp en "
                f"{_safe_url(iframe_url)}"
            )
            ytdl_url = self._get_url_via_ytdlp(iframe_url, referer=iframe_url)
            if ytdl_url and self._is_media_url(ytdl_url):
                self._log(
                    "[gogoanime] yt-dlp devolvió un flujo utilizable: "
                    f"{_safe_url(ytdl_url)}"
                )
                return ytdl_url, iframe_url

        return None, None

    # -------------------------------------------------------------------------
    # Ingeniería Inversa de Reproductores Dinámicos e Intercepción de API
    # -------------------------------------------------------------------------

    def _crack_dynamic_player(self, html, player_url):
        """Descubre y emula llamadas de API asíncronas filtrando ruido de librerías de terceros."""
        self._log("[gogoanime] Scaneando estructuras de API asíncronas...")

        # --- 1. BLOQUE OPTIMIZADO PARA MEGAPLAY (Y SIMILARES) ---
        if "megaplay.buzz" in player_url:
            self._log("[gogoanime] Aplicando lógica rápida para Megaplay...")
            real_id = None

            # Megaplay oculta su token real de API en atributos HTML (ej. data-id)
            # en lugar de usar el ID numérico público de la URL.
            match = re.search(r'data-id=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not match:
                match = re.search(r'id=["\']id["\'][^>]+value=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not match:
                match = re.search(r'value=["\']([^"\']+)["\'][^>]+id=["\']id["\']', html, re.IGNORECASE)
            if not match:
                match = re.search(r'["\']?id["\']?\s*:\s*["\']([a-zA-Z0-9_=a-z]+)["\']', html)

            if match:
                real_id = match.group(1)
                self._log(
                    "[gogoanime] ID dinámico interno detectado para Megaplay."
                )

                # Endpoints típicos para este tipo de reproductores
                endpoints_to_try = [
                    f"/ajax/embed/getSources?id={real_id}",
                    f"/ajax/source/{real_id}",
                    f"/api/source/{real_id}"
                ]

                for path in endpoints_to_try:
                    target_url = urljoin(player_url, path)
                    self._log(
                        "[gogoanime] Interceptando endpoint Megaplay: "
                        f"{_safe_url(target_url)}"
                    )
                    res = self._download_webpage_with_headers(
                        target_url,
                        headers={
                            "Referer": player_url,
                            "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json, text/javascript, */*; q=0.01"
                        }
                    )
                    if res and any(x in res for x in ['"file"', '"source"', '"sources"', '"url"', '.m3u8', '.mp4']):
                        video_url, _ = self._extract_media_from_html(res, target_url)
                        if video_url:
                            return video_url
            else:
                self._log("[gogoanime] ⚠️ No se encontró el data-id en el HTML de Megaplay.")

        # --- 2. LÓGICA GENÉRICA / FALLBACK ---
        setup_id = None
        # Priorizar un data-id en el DOM antes que el de la URL para evitar falsos positivos
        dom_id_match = re.search(r'data-id=["\']([^"\']+)["\']', html)
        if dom_id_match:
            setup_id = dom_id_match.group(1)
            self._log("[gogoanime] ID dinámico DOM genérico detectado.")
        else:
            url_id_match = re.search(r'/([0-9]{4,12})(?:/|$|\?|&)', player_url)
            if url_id_match:
                setup_id = url_id_match.group(1)
            else:
                id_match = re.search(r'(?:id|id_ep|episode_id|media_id)\s*[:=]\s*["\']?([a-zA-Z0-9_-]+)["\']?', html)
                if id_match:
                    setup_id = id_match.group(1)

        # 3. Capturar e integrar scripts externos lógicos
        all_scripts_content = [html]
        script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)

        # Reducido a inspeccionar los primeros 3 scripts en lugar de 5
        for src in script_srcs[:3]:
            full_src_url = urljoin(player_url, src)
            # Lista negra extendida: bloquea todo lo relacionado a JWPlayer y frameworks genéricos
            if any(x in full_src_url.lower() for x in ['analytics', 'ads', 'gtag', 'recaptcha', 'jquery', 'bootstrap', 'disqus', 'facebook', 'twitter', 'hls.js', 'hls.min.js', 'jwplayer', 'jw_player', 'client.js']):
                continue

            self._log(f"[gogoanime] 📄 Inspeccionando script externo: {urlparse(full_src_url).path}")
            script_html = self._download_webpage_with_headers(full_src_url, headers={"Referer": player_url})
            if script_html:
                all_scripts_content.append(script_html)

        # 4. Mapear patrones estructurales de endpoints limpios
        api_patterns = [
            r'["\']([^"\']*(?:ajax|api|source|playlist|player|v1|v2)[^"\']*)["\']'
        ]

        endpoints = []

        if setup_id:
            guessed_paths = [
                f"/ajax/embed/getSources?id={setup_id}",
                f"/ajax/source/{setup_id}",
                f"/api/source/{setup_id}",
                f"/playlist/{setup_id}.m3u8",
            ]
            for gp in guessed_paths:
                endpoints.append(urljoin(player_url, gp))

        for script_body in all_scripts_content:
            for p in api_patterns:
                for m in re.finditer(p, script_body):
                    route = m.group(1)

                    if not route or len(route) > 90 or ' ' in route or '\\' in route or ',' in route:
                        continue
                    if not re.match(r'^/?[\w\.\-\d_/]+(?:\?[\w\.\-\d_=&/]*)?$', route):
                        continue
                    if any(x in route.lower() for x in ['.css', '.png', '.jpg', '.jpeg', '.woff', 'schema.org', 'svg', 'playlist request', 'jwplayer']):
                        continue

                    full_route = urljoin(player_url, route)
                    if full_route not in endpoints:
                        endpoints.append(full_route)

        # 5. Interceptación reactiva con emulación XMLHttpRequest
        for endpoint in endpoints:
            if endpoint.split('?')[0] == player_url.split('?')[0]:
                continue

            target_url = endpoint
            if setup_id and "id=" not in target_url and setup_id not in target_url:
                separator = "&" if "?" in target_url else "?"
                target_url += f"{separator}id={setup_id}"

            self._log(
                "[gogoanime] Interceptando endpoint estimado genérico: "
                f"{urlparse(target_url).path}"
            )
            res = self._download_webpage_with_headers(
                target_url,
                headers={
                    "Referer": player_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01"
                }
            )

            if res:
                if any(x in res for x in ['"file"', '"source"', '"sources"', '"url"', '.m3u8', '.mp4']):
                    video_url, _ = self._extract_media_from_html(res, target_url)
                    if video_url:
                        return video_url

        return None

    def _extract_media_from_html(self, html, base_url):
        if not html:
            return None, None

        html = self._decode_html_entities(html)
        html = self._unescape_slashes(html)
        html = self._decode_hex_escapes(html)

        packed_html = self._unpack_dean_edwards(html)
        if packed_html:
            html += "\n" + packed_html

        video_patterns = [
            r'<video[^>]*src=["\']([^"\']+)["\']',
            r'<source[^>]*src=["\']([^"\']+)["\']',
            r'data-video=["\']([^"\']+)["\']',
            r'data-src=["\']([^"\']+)["\']',
            r'data-config=["\']([^"\']+)["\']',
        ]
        for pattern in video_patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                url = urljoin(base_url, match.group(1))
                if self._is_media_url(url):
                    return url, "video/source tag"

        # MODIFICADO: Regex adaptado para llaves string/JSON opcionalmente envueltas en comillas
        js_patterns = [
            r'["\']?(?:file|source|sources|url|video_url|hls|playlist|src|file_url|link)["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        ]
        for pattern in js_patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                url = urljoin(base_url, match.group(1))
                if self._is_media_url(url):
                    return url, "JS/JSON key-value match"

        for b64_match in re.findall(r'["\']([a-zA-Z0-9+/=]{24,})["\']', html):
            try:
                import base64
                padded = b64_match + "=" * ((4 - len(b64_match) % 4) % 4)
                decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
                if any(x in decoded for x in ['http', '.m3u8', '.mp4']):
                    urls = re.findall(r'https?://[^\s<>"\']+', decoded)
                    for u in urls:
                        if self._is_media_url(u):
                            return u, "Base64 payload"
            except Exception:
                pass

        general_pattern = r'(?:https?:)?//[^\s<>"\'()]+\.(?:m3u8|mp4)(?:\?[^\s<>"\'()]*)?'
        for match in re.findall(general_pattern, html, re.IGNORECASE):
            url = self._decode_escaped_url(match)
            url = urljoin(base_url, url)
            if self._is_media_url(url):
                return url, "Global regex scan"

        return None, None

    def _extract_iframe_srcs(self, html, base_url):
        iframe_urls = []
        patterns = [
            r'<iframe[^>]*src=["\']([^"\']+)["\']',
            r'<iframe[^>]*data-src=["\']([^"\']+)["\']',
            r'window\.location\.href\s*=\s*["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                src_url = match.group(1).replace("&amp;", "&")
                full_url = urljoin(base_url, src_url)

                if full_url == base_url or full_url.rstrip('/') == base_url.rstrip('/'):
                    continue
                if any(x in full_url.lower() for x in ["disqus", "facebook", "twitter", "google", "ads", "analytics"]):
                    continue
                if full_url not in iframe_urls:
                    iframe_urls.append(full_url)
        return iframe_urls

    def _unpack_dean_edwards(self, html):
        unpacked_contents = []
        patterns = [
            r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[rd]\s*\)[\s\S]*?\}\s*\(\s*['\"]([\s\S]*?)['\"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['\"]([\s\S]*?)['\"]\s*\.split\s*\(\s*['\"]\|['\"]\s*\)",
            r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[rd]\s*\)[\s\S]*?\}\s*\(\s*([\s\S]*?)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['\"]([\s\S]*?)['\"]\s*\.split\s*\(\s*['\"]\|['\"]\s*\)"
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, html):
                try:
                    p, a, c, k = match.groups()
                    a = int(a)
                    c = int(c)
                    k = k.split('|')

                    def lookup(match_obj):
                        word = match_obj.group(0)
                        try:
                            val = 0
                            for char in word:
                                if '0' <= char <= '9': d = ord(char) - ord('0')
                                elif 'a' <= char <= 'z': d = ord(char) - ord('a') + 10
                                elif 'A' <= char <= 'Z': d = ord(char) - ord('A') + 36
                                else: return word
                                val = val * a + d
                            if val < len(k) and k[val]:
                                return k[val]
                        except Exception:
                            pass
                        return word

                    unpacked_str = re.sub(r'\b[0-9a-zA-Z]+\b', lookup, p)
                    unpacked_contents.append(unpacked_str)
                except Exception:
                    continue
        return "\n".join(unpacked_contents) if unpacked_contents else ""

    def _decode_html_entities(self, text):
        import html
        return html.unescape(text)

    def _unescape_slashes(self, text):
        text = re.sub(r'\\/', '/', text)
        text = re.sub(r'\\u002F', '/', text, flags=re.IGNORECASE)
        text = re.sub(r'%2F', '/', text, flags=re.IGNORECASE)
        return text

    def _decode_hex_escapes(self, text):
        try:
            return re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), text)
        except Exception:
            return text

    def _decode_escaped_url(self, url):
        url = self._unescape_slashes(url)
        url = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), url)
        return url

    def _is_media_url(self, url):
        if not url:
            return False
        url_lower = url.lower()

        if url_lower.rstrip('/').endswith('/manifests') or url_lower.rstrip('/').endswith('/manifest'):
            return False

        if '.m3u8' in url_lower or '.mp4' in url_lower:
            return True
        if any(x in url_lower for x in ['/hls/', '/master/', '/manifest', 'master.m3u8', 'index.m3u8', 'playlist.m3u8']):
            return True
        return False

    def _get_url_via_ytdlp(self, url, referer=None):
        try:
            import yt_dlp
        except ImportError:
            return None

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "format": "best",
            "http_headers": {
                "User-Agent": self.user_agent if hasattr(self, 'user_agent') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
            "skip_download": True,
        }
        if referer:
            ydl_opts["http_headers"]["Referer"] = referer

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None
                formats = info.get("formats", [])
                for f in formats:
                    if f.get("protocol") in ["m3u8", "m3u8_native"]:
                        url_f = f.get("url")
                        if url_f and self._is_media_url(url_f):
                            return url_f
                return info.get("url")
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Flujo principal de enrutamiento para gogoanime.or.at
    # -------------------------------------------------------------------------
    def _debug(self, message):
        if os.getenv("ANIMUX_PLAYER") == "debug" or os.getenv("ANIMUX_DEBUG") == "1":
            self._log(message)

    def _slug_from_title(self, value):
        source, identifier = _split_source_id(value)
        if source == self.site_name:
            value = identifier

        value = urllib.parse.unquote(str(value or "").strip())
        if re.match(r"^https?://", value):
            path = urllib.parse.urlparse(value).path.strip("/")
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] in {"anime", "series", "movies", "tv"}:
                return parts[1]
            if parts:
                episode_slug = re.sub(r"-(?:episode|episodio)-\d+(?:-.+)?$", "", parts[-1], flags=re.IGNORECASE)
                return episode_slug

        value = value.lower()
        value = re.sub(r"['’]", "", value)
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-")

    def _series_url_from_slug(self, slug):
        return f"https://gogoanime.or.at/anime/{slug.strip('/')}/"

    def _episode_url_from_slug(self, slug, episode):
        return f"https://gogoanime.or.at/{slug.strip('/')}-episode-{episode}/"

    def _normalize_series_url(self, value):
        source, identifier = _split_source_id(value)
        if source == self.site_name:
            value = identifier

        value = str(value or "").strip()
        if re.match(r"^https?://", value):
            parsed = urllib.parse.urlparse(value)
            if "gogoanime.or.at" in parsed.netloc:
                path = parsed.path.strip("/")
                if path.startswith(("anime/", "series/", "movies/", "tv/")):
                    return value if value.endswith("/") else value + "/"
                slug = self._slug_from_title(value)
                if slug:
                    return self._series_url_from_slug(slug)
        slug = self._slug_from_title(value)
        if not slug:
            raise ValueError("No se pudo construir el slug de Gogoanime.")
        return self._series_url_from_slug(slug)

    def _normalize_episode_number(self, value):
        text = str(value or "").strip()
        text = re.sub(r"\.0+$", "", text)
        if re.match(r"^0+\d+$", text):
            text = text.lstrip("0") or "0"
        return text

    def _search_tokens(self, value):
        return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))

    def _candidate_score(self, query, series_url, title):
        query_slug = self._slug_from_title(query)
        candidate_slug = self._slug_from_title(series_url)
        if query_slug and query_slug == candidate_slug:
            return 1000

        query_tokens = self._search_tokens(query_slug.replace("-", " "))
        title_tokens = self._search_tokens(title)
        slug_tokens = self._search_tokens(candidate_slug.replace("-", " "))
        score = len(query_tokens & (title_tokens | slug_tokens)) * 10

        if "slime" in query_tokens and "slime" in (title_tokens | slug_tokens):
            score += 25
        if "season" in query_tokens and "season" in (title_tokens | slug_tokens):
            score += 5
        return score

    def _episodes_debug_payload(self, episodes):
        if len(episodes) <= 20:
            return episodes
        return {
            "count": len(episodes),
            "first": episodes[:3],
            "last": episodes[-3:],
        }

    def _result_from_series_url(self, series_url, title, dub=False):
        episodes = self.episodes(series_url)
        self._debug(f"[gogoanime][debug] resultado episodes(): {json.dumps(self._episodes_debug_payload(episodes), ensure_ascii=False)}")
        if not episodes:
            raise RuntimeError("sin episodios")
        return {
            "id": _source_id(self.site_name, series_url),
            "title": title,
            "total_episodes": len(episodes),
            "extractor": self.site_name,
            "url": series_url,
        }

    def _search_candidates_from_html(self, html):
        candidates = []
        seen = set()
        for anchor in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html or "", re.IGNORECASE | re.DOTALL):
            attrs, body = anchor.groups()
            href_match = re.search(r'href=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            if not href_match:
                continue
            series_url = urljoin("https://gogoanime.or.at/", href_match.group(1).replace("&amp;", "&"))
            parsed = urllib.parse.urlparse(series_url)
            if "gogoanime.or.at" not in parsed.netloc:
                continue
            if not re.match(r"^/(?:anime|series|movies|tv)/[^/]+/?$", parsed.path):
                continue
            if series_url in seen:
                continue
            title_match = re.search(r'(?:title|aria-label)=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            title = title_match.group(1) if title_match else _clean_text(body)
            title = _clean_text(title)
            if not title:
                title = self._slug_from_title(series_url).replace("-", " ").title()
            seen.add(series_url)
            candidates.append((series_url if series_url.endswith("/") else series_url + "/", title))
        return candidates

    def search(self, query, lang="en", dub=False, **kwargs):
        """Search Gogoanime .or.at and fall back to the direct slug URL."""
        original_query = str(query or "").strip()
        search_query = original_query
        if dub and "dub" not in search_query.lower():
            search_query += " dub"

        search_url = f"https://gogoanime.or.at/?s={urllib.parse.quote_plus(search_query)}"
        self._debug(f"[gogoanime][debug] search(): query={original_query!r}")
        self._debug(f"[gogoanime][debug] search URL: {search_url}")
        html = self._download_webpage(search_url)

        results = []
        candidates = self._search_candidates_from_html(html)
        candidates.sort(
            key=lambda item: self._candidate_score(original_query, item[0], item[1]),
            reverse=True,
        )
        self._debug(f"[gogoanime][debug] resultados search(): {json.dumps(candidates, ensure_ascii=False)}")

        for series_url, title in candidates:
            try:
                results.append(self._result_from_series_url(series_url, title, dub=dub))
            except Exception as exc:
                self._debug(f"[gogoanime][debug] resultado descartado {series_url}: {exc}")

        if results:
            return results

        slug = self._slug_from_title(original_query)
        series_url = self._series_url_from_slug(slug)
        self._debug(f"[gogoanime][debug] slug fallback: {slug}")
        self._debug(f"[gogoanime][debug] url serie fallback: {series_url}")
        try:
            fallback = self._result_from_series_url(
                series_url,
                slug.replace("-", " ").title(),
                dub=dub,
            )
        except Exception as exc:
            self._debug(f"[gogoanime][debug] fallback directo falló: {exc}")
            return []
        return [fallback]

    def episodes(self, anime_url):
        """Obtiene la lista de episodios desde el contenedor eplister de forma aislada"""
        anime_url = self._normalize_series_url(anime_url)
        self._debug(f"[gogoanime][debug] url serie: {anime_url}")
        html = self._download_webpage(anime_url)
        episode_list = []
        if not html:
            return episode_list

        # Aislar el bloque de la lista de episodios para evitar falsos positivos
        eplister_match = re.search(r'class=["\']eplister["\'](.*?)</ul>', html, re.DOTALL | re.IGNORECASE)
        if not eplister_match:
            return episode_list

        bloque_lista = eplister_match.group(1)

        # Iterar sobre cada elemento <li> de la lista para mantener el contexto interno
        li_pattern = r'<li[^>]*>(.*?)</li>'
        for li_match in re.finditer(li_pattern, bloque_lista, re.DOTALL | re.IGNORECASE):
            li_content = li_match.group(1)

            url_match = re.search(r'href=["\']([^"\']+)["\']', li_content, re.IGNORECASE)
            if not url_match:
                continue
            ep_url = urljoin(anime_url, url_match.group(1).replace("&amp;", "&"))

            ep_num = "0"
            # 1. Intentar extraer el número desde la clase epl-num del item actual
            num_match = re.search(r'class=["\']epl-num["\'][^>]*>(.*?)</div>', li_content, re.IGNORECASE)
            if num_match:
                raw_num = re.sub(r'[^\d.]', '', num_match.group(1))
                if raw_num:
                    ep_num = raw_num

            # 2. Fallback robusto: si dio "0", extraemos el número directamente del slug de la URL
            if ep_num == "0" or not ep_num:
                url_num_match = re.search(r'-(?:episode|episodio)-(\d+)', ep_url, re.IGNORECASE)
                if url_num_match:
                    ep_num = url_num_match.group(1)

            episode_list.append({
                "url": ep_url,
                "episode": ep_num
            })

        # En .or.at vienen del más nuevo al más viejo; invertimos para mantener orden secuencial
        episode_list.reverse()
        return episode_list

    def list_episodes(self, title_or_id, dub=False):
        return [item["episode"] for item in self.episodes(title_or_id)]

    def _stream_result(self, extracted, episode_url="", series_url=""):
        formats = extracted.get("formats", []) if extracted else []
        if not formats or not formats[0].get("url"):
            raise RuntimeError("El extractor no devolvió un stream válido.")
        selected = formats[0]
        headers = selected.get("http_headers", {})
        return {
            "url": selected["url"],
            "referer": headers.get("Referer", ""),
            "extractor": extracted.get("extractor", self.site_name),
            "format_id": selected.get("format_id", ""),
            "ext": selected.get("ext", ""),
            "protocol": selected.get("protocol", ""),
            "episode_url": episode_url,
            "series_url": series_url,
        }

    def get_episode(self, title_or_id, episode, dub=False):
        series_url = self._normalize_series_url(title_or_id)
        slug = self._slug_from_title(series_url)
        self._debug(f"[gogoanime][debug] slug: {slug}")
        self._debug(f"[gogoanime][debug] url serie: {series_url}")

        episodes = self.episodes(series_url)
        self._debug(f"[gogoanime][debug] resultado episodes(): {json.dumps(self._episodes_debug_payload(episodes), ensure_ascii=False)}")
        target = None
        requested = self._normalize_episode_number(episode)
        for item in episodes:
            if self._normalize_episode_number(item.get("episode")) == requested:
                target = item
                break

        if target:
            episode_url = target["url"]
        else:
            episode_url = self._episode_url_from_slug(slug, requested)
            self._debug(f"[gogoanime][debug] url episodio fallback: {episode_url}")

        self._log("[gogoanime] Episodio encontrado.")
        self._debug(f"[gogoanime][debug] url episodio: {episode_url}")
        extracted = self.extract(episode_url)
        self._debug(f"[gogoanime][debug] resultado extract(): {json.dumps(extracted, ensure_ascii=False)}")
        result = self._stream_result(extracted, episode_url=episode_url, series_url=series_url)
        self._log(f"[gogoanime] Formato: {result.get('format_id') or 'best'}")
        self._debug(f"[gogoanime][debug] ext: {result.get('ext')}")
        self._debug(f"[gogoanime][debug] url final: {result.get('url')}")
        return result

    def _episode_server_context(self, title_or_id, episode, dub=False):
        series_url = self._normalize_series_url(title_or_id)
        slug = self._slug_from_title(series_url)
        requested = self._normalize_episode_number(episode)
        target = next(
            (
                item
                for item in self.episodes(series_url)
                if self._normalize_episode_number(item.get("episode"))
                == requested
            ),
            None,
        )
        episode_url = (
            target["url"]
            if target
            else self._episode_url_from_slug(slug, requested)
        )
        html = self._download_webpage(episode_url)
        if not html:
            raise RuntimeError("No se pudo obtener el episodio de Gogoanime.")
        iframe_urls = self._extract_iframe_srcs(html, episode_url)
        discovered = []
        for iframe_url in iframe_urls:
            if "newplayer.php" in iframe_url:
                nested_html = self._download_webpage_with_headers(
                    iframe_url, headers={"Referer": episode_url}
                )
                match = re.search(
                    r'["\'](https://megaplay\.buzz/stream/[^"\']+)',
                    nested_html or "",
                    re.IGNORECASE,
                )
                if match:
                    iframe_url = match.group(1).replace("&amp;", "&")
            if iframe_url not in discovered:
                discovered.append(iframe_url)
        iframe_urls = discovered
        if not iframe_urls:
            raise RuntimeError(
                "Gogoanime no expuso servidores para este episodio."
            )
        return {
            "episode_url": episode_url,
            "iframe_urls": iframe_urls,
        }

    def _resolve_server_candidate(self, candidate, context):
        if "megaplay.buzz/stream/" in candidate.iframe_url:
            return replace(
                candidate,
                direct_url=candidate.iframe_url,
                referer=context["episode_url"],
                format_id="megaplay-direct",
                ext="html",
                protocol="https",
                status="resolved",
            )
        return super()._resolve_server_candidate(candidate, context)

    def _extract_or_at(self, url):
        html = self._download_webpage(url)
        if not html:
            raise RuntimeError("No se pudo obtener el HTML de gogoanime.or.at.")

        iframe_urls = self._extract_iframe_srcs(html, url)

        # Fallback si no hay iframes en el DOM principal
        if not iframe_urls:
            iframe_match = re.search(
                r'src=["\'](https://gogoanime\.me\.uk/newplayer\.php[^"\']+)["\']',
                html,
                re.IGNORECASE,
            )
            if iframe_match:
                iframe_urls.append(iframe_match.group(1).replace("&amp;", "&"))

        if not iframe_urls:
            raise RuntimeError("No se pudo extraer ningún iframe inicial.")

        target_megaplay_url = None

        # Buscamos la URL de Megaplay sin intentar resolverla como video
        for iframe_url in iframe_urls:
            if "megaplay.buzz/stream/" in iframe_url:
                target_megaplay_url = iframe_url
                break

            elif "newplayer.php" in iframe_url:
                nested_html = self._download_webpage_with_headers(iframe_url, headers={"Referer": url})
                if nested_html:
                    match = re.search(r'["\'](https://megaplay\.buzz/stream/[^"\']+)["\']', nested_html, re.IGNORECASE)
                    if match:
                        target_megaplay_url = match.group(1).replace("&amp;", "&")
                        break

        # Si encontramos la URL, la devolvemos inmediatamente
        if target_megaplay_url:
            self._log(
                "[gogoanime] URL de Megaplay capturada: "
                f"{_safe_url(target_megaplay_url)}"
            )
            return {
                "id": url.rstrip("/").split("/")[-1],
                "title": "Gogoanime (or.at) Episodio",
                "extractor": self.site_name,
                "formats": [
                    {
                        "format_id": "megaplay-direct",
                        "url": target_megaplay_url,
                        "ext": "html",
                        "protocol": "https",
                        "http_headers": {
                            "User-Agent": self.user_agent if hasattr(self, 'user_agent') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                            "Referer": url,
                        },
                    }
                ],
            }

        raise RuntimeError("No se pudo encontrar ninguna URL de Megaplay válida.")

    def extract(self, url, match=None):
        if self._is_or_at(url):
            return self._extract_or_at(url)

        match = match or re.match(self._VALID_URL, url)
        if not match:
            raise ValueError("La URL proporcionada no es válida para Gogoanime.")

        anime_id = match.group("anime_id")
        ep_no = match.group("ep_no")
        self._log(f"[gogoanime] Analizando anime: {anime_id} (Episodio {ep_no})")

        html = self._download_webpage(url)
        if not html:
            raise RuntimeError("No se pudo obtener el HTML de la página del episodio.")

        if not re.search(r'class=["\']?servers["\']?', html, re.IGNORECASE):
            raise RuntimeError("No se encontró el bloque de servidores")

        post_id = ""
        script_match = re.search(
            r'(?:defaultPostId|postId)\s*=\s*["\'](\d+)["\']',
            html,
        )
        if script_match:
            post_id = script_match.group(1)

        li_matches = re.findall(
            r"<li\s+([^>]*?player-type-link[^>]*?)>([\s\S]*?)</li>",
            html,
            re.IGNORECASE,
        )

        iframe_urls = []
        seen_iframes = set()

        for attrs_str, _ in li_matches:
            def get_data_attr(attr_name):
                m = re.search(
                    r"data-" + re.escape(attr_name) + r"\s*=\s*[\"']([^\"']*)[\"']",
                    attrs_str,
                    re.IGNORECASE,
                )
                return m.group(1) if m else ""

            server_type = get_data_attr("type")
            if not server_type:
                continue

            iframe_url = ""
            if server_type in ["embed", "kiwi"]:
                plain_url = get_data_attr("plain-url")
                if plain_url:
                    iframe_url = plain_url
            else:
                enc1 = get_data_attr("encrypted-url1")
                enc2 = get_data_attr("encrypted-url2")
                enc3 = get_data_attr("encrypted-url3")
                ref = get_data_attr("ref") or "gogoanime.by"

                params = {
                    server_type: enc1,
                    "feature_image": "",
                    "user_agent": self.user_agent if hasattr(self, 'user_agent') else "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "ref": ref,
                }
                if enc2:
                    params["url2"] = enc2
                if enc3:
                    params["url3"] = enc3
                if post_id:
                    params["postId"] = post_id

                query_string = urllib.parse.urlencode(params)
                iframe_url = (
                    "https://9animetv.be/wp-content/plugins/video-player/"
                    f"includes/player/player.php?{query_string}"
                )

            if iframe_url and iframe_url not in seen_iframes:
                seen_iframes.add(iframe_url)
                iframe_urls.append(iframe_url)

        if not iframe_urls:
            raise RuntimeError(
                "No se encontraron servidores de video utilizables en este "
                "episodio."
            )

        self._log(
            f"[gogoanime] Se encontraron {len(iframe_urls)} servidores "
            "procesables. Iniciando resolución concurrente..."
        )
        video_url, chosen_iframe = self._extract_video_from_iframes(
            iframe_urls, url
        )

        if not video_url:
            self._log("[gogoanime] Intentando yt-dlp en la URL base...")
            video_url = self._get_url_via_ytdlp(
                url, referer=self.base_url
            )
            chosen_iframe = url

        if not video_url and iframe_urls:
            self._log(
                "[gogoanime] Usando el primer iframe como fallback directo."
            )
            video_url = iframe_urls[0]
            chosen_iframe = url

        if not video_url:
            raise RuntimeError(
                "Ninguno de los servidores disponibles devolvió un flujo de "
                "video válido."
            )

        return {
            "id": f"{anime_id}-{ep_no}",
            "title": (
                f"{anime_id.replace('-', ' ').title()} - Episodio {ep_no}"
            ),
            "extractor": "gogoanime",
            "formats": [
                {
                    "format_id": "gogoanime-best",
                    "url": video_url,
                    "ext": "mp4",
                    "protocol": (
                        "m3u8_native"
                        if ".m3u8" in video_url
                        else "https"
                    ),
                    "http_headers": {
                        "User-Agent": self.user_agent,
                        "Referer": chosen_iframe,
                    },
                }
            ],
        }
