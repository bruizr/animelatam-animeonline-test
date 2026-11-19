import json
import os
import re
import sys
import urllib.request
import urllib.parse
from http.cookiejar import CookieJar
import subprocess
import shutil
import concurrent.futures
import hashlib
import html as html_module
import inspect
import threading
from html.parser import HTMLParser
from dataclasses import dataclass, replace


def _clean_text(value):
    """Return readable text from a small HTML fragment."""
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html_module.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


SUPPORTED_SOURCES = (
    "jkanime",
    "animeflv",
    "animeav1",
    "tioanime",
    "animefenix",
    "monoschino",
    "gogoanime",
)


SERVER_LABELS = (
    ("mp4upload", "MP4Upload"),
    ("streamwish", "StreamWish"),
    ("filemoon", "Filemoon"),
    ("streamtape", "Streamtape"),
    ("mixdrop", "Mixdrop"),
    ("pixeldrain", "Pixeldrain"),
    ("megaplay", "Megaplay"),
    ("megacloud", "MegaCloud"),
    ("mega.nz", "Mega"),
    ("voe", "Voe"),
    ("voex", "Voe"),
    ("ok.ru", "Okru"),
    ("okru", "Okru"),
    ("yourupload", "YourUpload"),
    ("ironhentai", "IronHentai"),
    ("jkplayer", "JKPlayer"),
    ("streamlare", "Streamlare"),
    ("dood", "Doodstream"),
)


def _safe_url(value):
    """Drop query strings and fragments before a URL reaches normal logs."""
    try:
        parsed = urllib.parse.urlsplit(str(value or ""))
    except (TypeError, ValueError):
        return "<invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<relative-url>"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def _server_slug(value):
    value = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return value or "server"


def _server_label(url, hint=None):
    """Return a readable, token-free label for an embed URL."""
    if hint:
        label = _clean_text(str(hint))
        if label:
            return label[:48]
    parsed = urllib.parse.urlsplit(str(url or ""))
    hostname = (parsed.hostname or "").lower()
    haystack = f"{hostname}{parsed.path.lower()}"
    for marker, label in SERVER_LABELS:
        if marker in haystack:
            return label
    parts = [part for part in hostname.split(".") if part and part != "www"]
    if len(parts) >= 2:
        return parts[-2].replace("-", " ").title()[:48]
    return (parts[0].replace("-", " ").title() if parts else "Server")[:48]


def _normalized_embed_identity(source, value, referer=""):
    """Return a stable, token-free identity and an absolute embed URL."""
    decoded = html_module.unescape(str(value or "").strip())
    absolute = urllib.parse.urljoin(str(referer or ""), decoded)
    parsed = urllib.parse.urlsplit(absolute)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"
    path = urllib.parse.unquote(parsed.path or "/")
    path = re.sub(r"/+", "/", path)
    if path != "/":
        path = path.rstrip("/")
    return f"{str(source).lower()}|{hostname}|{path}", absolute


def _short_identity_fingerprint(identity):
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]


def infer_stream_metadata(url, metadata=None):
    """Infer media metadata from the URL path, preferring extractor metadata."""
    metadata = dict(metadata or {})
    parsed = urllib.parse.urlsplit(str(url or ""))
    path = parsed.path.lower()
    ext = str(metadata.get("ext") or "").lower()
    protocol = str(metadata.get("protocol") or "").lower()
    if not ext:
        match = re.search(r"\.([a-z0-9]{2,8})$", path)
        if match and match.group(1) in {
            "mp4", "m3u8", "mkv", "webm", "ts", "mpd"
        }:
            ext = match.group(1)
    if not protocol:
        if ext == "m3u8":
            protocol = "m3u8_native"
        elif ext == "mpd":
            protocol = "http_dash_segments"
        elif parsed.scheme in {"http", "https"}:
            protocol = parsed.scheme
    return ext, protocol


@dataclass(frozen=True)
class ServerCandidate:
    """Normalized server/embed candidate for one episode selection."""

    candidate_id: str
    source: str
    server_id: str
    server_label: str
    iframe_url: str
    direct_url: str = ""
    referer: str = ""
    format_id: str = ""
    ext: str = ""
    protocol: str = ""
    quality: str = ""
    priority: int = 0
    status: str = "available"
    failure_reason: str = ""

    def public_fields(self):
        """Return fields safe for menus and machine-readable stdout."""
        clean_label = str(self.server_label).replace("\t", " ").replace(
            "\n", " "
        )
        clean_source = str(self.source).replace("\t", " ").replace("\n", " ")
        clean_status = str(self.status).replace("\t", " ").replace("\n", " ")
        return (
            self.candidate_id,
            self.server_id,
            clean_label,
            clean_source,
            clean_status,
            str(self.priority),
        )


def _source_id(source, value):
    return f"{source}:{value}"


def _split_source_id(value):
    if not isinstance(value, str) or ":" not in value:
        return None, value
    source, identifier = value.split(":", 1)
    if source not in SUPPORTED_SOURCES:
        return None, value
    return source, identifier


class _ArticleParser(HTMLParser):
    """Extract the first link and heading from every article element."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.article = None
        self.in_heading = False
        self.results = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            if self.depth == 0:
                self.article = {"href": "", "title": []}
            self.depth += 1
        if not self.article:
            return
        if tag == "a" and not self.article["href"]:
            self.article["href"] = attrs.get("href", "")
        if tag in {"h2", "h3"}:
            self.in_heading = True

    def handle_endtag(self, tag):
        if tag in {"h2", "h3"}:
            self.in_heading = False
        if tag == "article" and self.depth:
            self.depth -= 1
            if self.depth == 0 and self.article:
                title = _clean_text("".join(self.article["title"]))
                if self.article["href"] and title:
                    self.results.append((self.article["href"], title))
                self.article = None

    def handle_data(self, data):
        if self.article and self.in_heading:
            self.article["title"].append(data)

class BaseAnimeExtractor:
    site_name = "anime"
    supports_dub = False

    def __init__(self):
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        self.opener.addheaders = [('User-Agent', self.user_agent)]

        self.request_timeout = float(os.getenv("ANI_CLI_JKANIME_EXTRACTOR_TIMEOUT", os.getenv("ANI_CLI_JKANIME_MAX_TIME", "5")))
        self.ytdlp_timeout = float(os.getenv("ANI_CLI_JKANIME_YTDLP_TIMEOUT", "8"))
        self._ytdlp_stream_metadata = {}

    def _log(self, message):
        print(message, file=sys.stderr)

    def _download_webpage(self, url, referer=None):
        req = urllib.request.Request(url)
        if referer:
            req.add_header('Referer', referer)
        try:
            with self.opener.open(req, timeout=self.request_timeout) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            self._log(
                f"[{self.site_name}][Aviso] No se pudo descargar "
                f"{_safe_url(url)}: {e}"
            )
            return ""

    def _request_text(self, url, data=None, headers=None, referer=None):
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", self.user_agent)
        if referer:
            request_headers.setdefault("Referer", referer)
        request = urllib.request.Request(url, data=data, headers=request_headers)
        try:
            with self.opener.open(request, timeout=self.request_timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            self._log(
                f"[{self.site_name}][Aviso] Falló la petición a "
                f"{_safe_url(url)}: {exc}"
            )
            return ""

    def _article_results(self, page, base_url):
        parser = _ArticleParser()
        try:
            parser.feed(page)
        except Exception as exc:
            self._log(f"[{self.site_name}][Aviso] HTML de búsqueda incompleto: {exc}")
        return [
            (urllib.parse.urljoin(base_url, href), title)
            for href, title in parser.results
        ]

    def search(self, title, dub=False):
        raise NotImplementedError

    def list_episodes(self, title_or_id, dub=False):
        raise NotImplementedError

    def get_episode(self, title_or_id, episode, dub=False):
        raise NotImplementedError

    def _episode_server_context(self, title_or_id, episode, dub=False):
        """Discover an episode URL and its embeds without resolving a stream."""
        raise NotImplementedError(
            f"{self.site_name} todavía no enumera servidores por separado."
        )

    def _server_candidates(self, iframe_urls, referer, label_hints=None):
        """Normalize ordered iframe URLs while keeping direct URLs unresolved."""
        prepared = []
        seen_identities = set()
        hints = list(label_hints or [])
        for index, value in enumerate(iframe_urls):
            if isinstance(value, (tuple, list)):
                iframe_url = str(value[0] or "")
                hint = value[1] if len(value) > 1 else None
            else:
                iframe_url = str(value or "")
                hint = hints[index] if index < len(hints) else None
            if not iframe_url:
                continue
            identity, iframe_url = _normalized_embed_identity(
                self.site_name, iframe_url, referer
            )
            if not iframe_url or identity in seen_identities:
                continue
            seen_identities.add(identity)
            label = _server_label(iframe_url, hint=hint)
            base_id = _server_slug(label)
            prepared.append((iframe_url, identity, label, base_id))

        identities_by_label = {}
        for _, identity, _, base_id in prepared:
            identities_by_label.setdefault(base_id, set()).add(identity)

        candidates = []
        for iframe_url, identity, label, base_id in prepared:
            if len(identities_by_label[base_id]) == 1:
                server_id = base_id
            else:
                server_id = (
                    f"{base_id}-{_short_identity_fingerprint(identity)}"
                )
            candidates.append(
                ServerCandidate(
                    candidate_id=f"{self.site_name}:{server_id}",
                    source=self.site_name,
                    server_id=server_id,
                    server_label=label,
                    iframe_url=iframe_url,
                    referer=referer,
                    priority=len(candidates) + 1,
                )
            )
        return candidates

    def list_episode_servers(self, title_or_id, episode, dub=False):
        context = self._episode_server_context(
            title_or_id, episode, dub=dub
        )
        return self._server_candidates(
            context.get("iframe_urls", []),
            context["episode_url"],
            label_hints=context.get("server_labels"),
        )

    @staticmethod
    def _candidate_matches(candidate, server_id):
        return server_id in {
            candidate.server_id,
            candidate.candidate_id,
        }

    def _resolve_server_candidate(self, candidate, context):
        result = self._process_single_iframe(
            candidate.iframe_url,
            context["episode_url"],
            candidate.priority,
        )
        direct_url, chosen_iframe = result[:2]
        metadata = result[2] if len(result) > 2 else {}
        if not direct_url:
            raise RuntimeError(
                f"{candidate.server_label} no devolvió un stream reproducible."
            )
        ext, protocol = infer_stream_metadata(direct_url, metadata)
        return replace(
            candidate,
            direct_url=direct_url,
            referer=chosen_iframe or candidate.iframe_url,
            format_id=f"{self.site_name}-{candidate.server_id}",
            ext=ext,
            protocol=protocol,
            status="resolved",
        )

    def resolve_episode_server(
        self, title_or_id, episode, server_id, dub=False
    ):
        if not server_id or server_id == "auto":
            return self.get_episode(title_or_id, episode, dub=dub)
        context = self._episode_server_context(
            title_or_id, episode, dub=dub
        )
        candidates = self._server_candidates(
            context.get("iframe_urls", []),
            context["episode_url"],
            label_hints=context.get("server_labels"),
        )
        candidate = next(
            (
                item
                for item in candidates
                if self._candidate_matches(item, server_id)
            ),
            None,
        )
        if candidate is None:
            raise ValueError(
                f"Servidor no disponible para este episodio: {server_id}"
            )
        resolved = self._resolve_server_candidate(candidate, context)
        return {
            "url": resolved.direct_url,
            "referer": resolved.referer,
            "extractor": self.site_name,
            "format_id": resolved.format_id,
            "ext": resolved.ext,
            "protocol": resolved.protocol,
            "server_id": resolved.server_id,
            "candidate_id": resolved.candidate_id,
            "server_label": resolved.server_label,
            "source": resolved.source,
        }

    def _stream_result(self, extracted):
        formats = extracted.get("formats", []) if extracted else []
        if not formats or not formats[0].get("url"):
            raise RuntimeError("El extractor no devolvió un stream válido.")
        selected = formats[0]
        headers = selected.get("http_headers", {})
        ext, protocol = infer_stream_metadata(selected["url"], selected)
        return {
            "url": selected["url"],
            "referer": headers.get("Referer", ""),
            "extractor": extracted.get("extractor", self.site_name),
            "format_id": selected.get("format_id", ""),
            "ext": ext,
            "protocol": protocol,
        }

    def _get_url_via_ytdlp(self, url, referer=None):
        if not shutil.which("yt-dlp"):
            return None

        # Cambiamos -g por -J para extraer el diccionario JSON completo
        cmd = ["yt-dlp", "-J", "--referer", referer or url, url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.ytdlp_timeout)
            if result.returncode == 0 and result.stdout.strip():
                import json
                info = json.loads(result.stdout)

                # Prefer actual formats. Generic extraction can echo the embed
                # page in `url`, which is not a playable media stream.
                candidates = []
                candidates.extend(info.get("requested_downloads") or [])
                candidates.extend(reversed(info.get("formats") or []))
                candidates.append(info)
                for entry in info.get("entries") or []:
                    candidates.extend(entry.get("requested_downloads") or [])
                    candidates.extend(reversed(entry.get("formats") or []))
                    candidates.append(entry)
                for candidate in candidates:
                    media_url = candidate.get("url")
                    ext = str(candidate.get("ext") or "").lower()
                    if not media_url or media_url == url or "/embed" in media_url:
                        continue
                    if (
                        re.search(r"\.(?:m3u8|mp4|mkv|webm|ts)(?:[?#]|$)", media_url, re.I)
                        or ext in {"m3u8", "m3u8_native", "mp4", "mkv", "webm", "ts"}
                    ):
                        self._ytdlp_stream_metadata[media_url] = {
                            "ext": candidate.get("ext", ""),
                            "protocol": candidate.get("protocol", ""),
                            "format_id": candidate.get("format_id", ""),
                        }
                        return media_url

        except subprocess.TimeoutExpired:
            self._log(f"[{self.site_name}][Aviso] yt-dlp (básico) tardó demasiado.")
        except Exception as e:
            self._log(f"[{self.site_name}][Aviso] yt-dlp falló: {e}")

        # Integración automática de tus métodos avanzados si existen en la clase hija
        if hasattr(self, '_get_url_via_ytdlp_impersonate'):
            self._log(f"[{self.site_name}] Escalando a yt-dlp con suplantación de Chrome...")
            return self._get_url_via_ytdlp_impersonate(url)

        return None

    def _process_single_iframe(
        self, iframe, base_url, index, cancel_event=None
    ):
        """Tarea individual que ejecutará cada hilo."""
        if cancel_event is not None and cancel_event.is_set():
            return None, None
        self._log(
            f"[{self.site_name}] [Hilo {index}] Analizando: "
            f"{_safe_url(iframe)}"
        )

        # 1. Intentar usar la API directa si la clase hija la tiene (ej. AnimeFLV q8y5z)
        if hasattr(self, '_fetch_stream_from_api') and 'q8y5z' in iframe:
            match = re.search(r'q8y5z\.com/b22/([a-z0-9]+)', iframe)
            if match:
                vid_url = self._fetch_stream_from_api(match.group(1), iframe, base_url)
                if vid_url:
                    self._log(f"[{self.site_name}] [Hilo {index}] ¡Extraído vía API interna!")
                    return vid_url, iframe

        # 2. Descargar el iframe para buscar Regex o flujos directos
        iframe_html = self._download_webpage(iframe, referer=base_url)
        if cancel_event is not None and cancel_event.is_set():
            return None, None
        if not iframe_html:
            return None, None

        if iframe_html.strip().startswith('#EXTM3U'):
            self._log(f"[{self.site_name}] [Hilo {index}] ¡Flujo m3u8 directo encontrado!")
            return iframe, iframe

        patterns = [
            r'(?:file|src|url)\s*:\s*["\']([^"\']+\.(?:m3u8|mp4)[^"\']*)["\']',
            r'<source[^>]+src=["\']([^"\']+)["\']',
            r'https?://[^"\' ]+\.(?:m3u8|mp4)[^"\' ]*'
        ]

        for pattern in patterns:
            m = re.search(pattern, iframe_html)
            if m:
                video_url = m.group(1) if m.groups() else m.group(0)
                self._log(f"[{self.site_name}] [Hilo {index}] ¡Extraído por Regex con éxito!")
                return video_url, iframe

        # 3. Fallback a yt-dlp (que ahora incluye impersonate automáticamente)
        if cancel_event is not None and cancel_event.is_set():
            return None, None
        self._log(f"[{self.site_name}] [Hilo {index}] Regex falló. Probando yt-dlp...")
        res_ytdl = self._get_url_via_ytdlp(iframe, referer=base_url)
        if res_ytdl:
            self._log(f"[{self.site_name}] [Hilo {index}] ¡Extraído mediante yt-dlp!")
            return (
                res_ytdl,
                iframe,
                self._ytdlp_stream_metadata.get(res_ytdl, {}),
            )

        return None, None

    def _run_iframe_task(
        self, iframe, base_url, index, cancel_event
    ):
        """Call old extractor overrides compatibly while enabling cancellation."""
        parameters = inspect.signature(
            self._process_single_iframe
        ).parameters
        if "cancel_event" in parameters:
            return self._process_single_iframe(
                iframe, base_url, index, cancel_event=cancel_event
            )
        if cancel_event.is_set():
            return None, None
        return self._process_single_iframe(iframe, base_url, index)

    def _extract_video_from_iframes(self, iframe_urls, base_url):
        """Bucle concurrente reutilizable por los extractores."""
        video_url = None
        chosen_iframe = None

        # max_workers define cuántos servidores probar a la vez (limitamos a 12 para no saturar la red)
        workers = min(len(iframe_urls), 12)
        if not workers:
            return None, None

        cancel_event = threading.Event()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        future_to_iframe = {
            executor.submit(
                self._run_iframe_task,
                iframe,
                base_url,
                idx,
                cancel_event,
            ): idx
            for idx, iframe in enumerate(iframe_urls, start=1)
        }
        try:
            # as_completed produce los resultados a medida que los hilos
            # terminan; el primer stream válido conserva la estrategia actual.
            for future in concurrent.futures.as_completed(future_to_iframe):
                idx = future_to_iframe[future]
                try:
                    resolved = future.result()
                    res_url, res_iframe = resolved[:2]
                    if res_url:
                        video_url = res_url
                        chosen_iframe = res_iframe
                        self._log(
                            f"[{self.site_name}] ¡Hilo {idx} fue el más "
                            "rápido! Deteniendo los demás..."
                        )
                        cancel_event.set()
                        break
                except Exception as e:
                    self._log(
                        f"[{self.site_name}] Error en el hilo {idx}: {e}"
                    )
        finally:
            cancel_event.set()
            for future in future_to_iframe:
                if not future.done():
                    future.cancel()
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                # Python 3.8 does not expose cancel_futures.
                executor.shutdown(wait=True)

        return video_url, chosen_iframe
