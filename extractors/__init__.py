import concurrent.futures
import difflib
import re
import threading
import unicodedata
import urllib.parse
from dataclasses import dataclass

from .animeav1 import AnimeAV1Extractor
from .animefenix import AnimeFenixExtractor
from .animeflv import AnimeFLVExtractor
from .base import SUPPORTED_SOURCES, _split_source_id
from .gogoanime import GogoanimeExtractor
from .jkanime import JkanimeExtractor
from .monoschino import MonoschinoExtractor
from .tioanime import TioAnimeExtractor
from .streaming import choose_stable_quality, choose_stable_stream


SOURCE_ORDER = {
    ("es", False): (
        "jkanime", "animeflv", "animefenix", "tioanime", "animeav1"
    ),
    ("es", True): ("animeav1", "monoschino", "animeflv"),
    ("en", False): ("gogoanime",),
    ("en", True): ("gogoanime",),
}

SOURCE_LABELS = {
    "jkanime": "JKanime",
    "animeflv": "AnimeFLV",
    "animefenix": "AnimeFenix",
    "tioanime": "TioAnime",
    "animeav1": "AnimeAV1",
    "monoschino": "MonosChinos",
    "gogoanime": "Gogoanime",
}


@dataclass(frozen=True)
class SourceCandidate:
    """Availability of one episode at one site, separate from embeds."""

    source_id: str
    source_label: str
    anime_id: str
    anime_title: str
    episode: str
    available: bool
    episode_url: str = ""
    server_count: int = 0
    status: str = "unknown"
    failure_reason: str = ""
    match_score: float | None = None

    def public_fields(self):
        def clean(value):
            value = re.sub(r"[\t\r\n]+", " ", str(value or "")).strip()
            value = re.sub(r"https?://\S+", "<redacted-url>", value)
            value = re.sub(
                r"(?i)\b(?:token|signature|auth|key)\b(?:\s*=\s*|\s+)\S+",
                "<redacted-secret>",
                value,
            )
            return value

        return (
            clean(self.source_id),
            clean(self.source_label),
            clean(self.anime_title),
            clean(self.episode),
            "1" if self.available else "0",
            clean(self.status),
            str(self.server_count),
            clean(self.failure_reason),
        )


EXTRACTOR_CLASSES = (
    JkanimeExtractor,
    AnimeFLVExtractor,
    AnimeFenixExtractor,
    TioAnimeExtractor,
    AnimeAV1Extractor,
    MonoschinoExtractor,
    GogoanimeExtractor,
)


class AnimeExtractor:
    """Search language-specific sources and resolve streams with fallback."""

    extractor_classes = EXTRACTOR_CLASSES

    def __init__(self, extractor_classes=None):
        classes = (
            self.extractor_classes
            if extractor_classes is None
            else extractor_classes
        )
        self._extractors = [extractor_class() for extractor_class in classes]
        self._source_match_cache = {}
        self._session_server_scores = {}
        self._preferred_servers = {}

    @staticmethod
    def _normalized_title(value):
        value = unicodedata.normalize("NFKD", str(value or ""))
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = value.lower()
        value = re.sub(
            r"\b(?:sub(?:titulado)?|dub(?:bed)?|doblaje|latino|"
            r"castellano|espanol)\b",
            " ",
            value,
        )
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    @classmethod
    def _title_facts(cls, value):
        normalized = cls._normalized_title(value)
        season = None
        match = re.search(
            r"\b(?:season|temporada|part)\s*([0-9]+)\b", normalized
        )
        if match:
            season = int(match.group(1))
        else:
            match = re.search(r"\b([0-9]+)(?:st|nd|rd|th) season\b", normalized)
            if match:
                season = int(match.group(1))
        media_type = "series"
        if re.search(r"\b(?:movie|film|pelicula)\b", normalized):
            media_type = "movie"
        elif re.search(r"\bova\b", normalized):
            media_type = "ova"
        year_match = re.search(r"\b(?:19|20)[0-9]{2}\b", normalized)
        return {
            "normalized": normalized,
            "season": season,
            "type": media_type,
            "year": int(year_match.group(0)) if year_match else None,
        }

    @classmethod
    def _match_score(cls, requested_title, result):
        requested = cls._title_facts(requested_title)
        titles = [result.get("title", "")]
        alternate_titles = result.get("alternate_titles") or []
        if isinstance(alternate_titles, str):
            alternate_titles = [alternate_titles]
        titles.extend(alternate_titles)
        scores = []
        for title in titles:
            candidate = cls._title_facts(title)
            for key in ("season", "year", "type"):
                if result.get(key) not in (None, ""):
                    candidate[key] = result[key]
            candidate["type"] = str(candidate["type"]).lower()
            if candidate["type"] in {"tv", "ona", "series"}:
                candidate["type"] = "series"
            incompatible = False
            for key in ("season", "year"):
                if (
                    requested[key] is not None
                    and candidate[key] is not None
                    and requested[key] != candidate[key]
                ):
                    incompatible = True
            if requested["type"] != candidate["type"] and (
                requested["type"] != "series"
                or candidate["type"] != "series"
            ):
                incompatible = True
            left = requested["normalized"]
            right = candidate["normalized"]
            if incompatible or not left or not right:
                scores.append(0.0)
                continue
            sequence = difflib.SequenceMatcher(None, left, right).ratio()
            left_tokens = set(left.split())
            right_tokens = set(right.split())
            token_score = (
                len(left_tokens & right_tokens)
                / len(left_tokens | right_tokens)
                if left_tokens | right_tokens
                else 0.0
            )
            scores.append((sequence * 0.6) + (token_score * 0.4))
        return round(max(scores or [0.0]), 4)

    def _source_candidate(
        self, extractor, title_or_id, episode, title, dub, stop_event
    ):
        source, identifier = _split_source_id(title_or_id)
        requested_title = title or (
            self._fallback_title(identifier) if source else str(identifier)
        )
        if stop_event.is_set():
            raise TimeoutError("cancelled")

        if source == extractor.site_name:
            matches = [{
                "id": title_or_id,
                "title": title or self._fallback_title(identifier),
                "extractor": source,
            }]
        else:
            matches = extractor.search(requested_title, dub=dub)
        if stop_event.is_set():
            raise TimeoutError("cancelled")
        if not matches:
            return SourceCandidate(
                extractor.site_name,
                SOURCE_LABELS.get(extractor.site_name, extractor.site_name),
                "",
                "",
                str(episode),
                False,
                status="unavailable",
                failure_reason="no matching title",
            )

        ranked = sorted(
            (
                (self._match_score(requested_title, result), result)
                for result in matches
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        viable = []
        requested_episode = str(episode)
        for score, result in ranked:
            if score < 0.62 and source != extractor.site_name:
                continue
            episodes = extractor.list_episodes(result["id"], dub=dub)
            if requested_episode in {str(number) for number in episodes}:
                viable.append((score, result))
        if not viable:
            best_score, best = ranked[0]
            reason = (
                "title mismatch"
                if best_score < 0.62 and source != extractor.site_name
                else "episode unavailable"
            )
            return SourceCandidate(
                extractor.site_name,
                SOURCE_LABELS.get(extractor.site_name, extractor.site_name),
                "",
                str(best.get("title", "")),
                requested_episode,
                False,
                status="unavailable",
                failure_reason=reason,
                match_score=best_score,
            )
        best_score, best = viable[0]
        if (
            len(viable) > 1
            and abs(best_score - viable[1][0]) <= 0.04
            and self._normalized_title(best.get("title"))
            != self._normalized_title(viable[1][1].get("title"))
        ):
            return SourceCandidate(
                extractor.site_name,
                SOURCE_LABELS.get(extractor.site_name, extractor.site_name),
                "",
                str(best.get("title", "")),
                requested_episode,
                False,
                status="ambiguous",
                failure_reason="multiple close title matches",
                match_score=best_score,
            )

        servers = extractor.list_episode_servers(
            best["id"], requested_episode, dub=dub
        )
        if not servers:
            return SourceCandidate(
                extractor.site_name,
                SOURCE_LABELS.get(extractor.site_name, extractor.site_name),
                str(best["id"]),
                str(best.get("title", "")),
                requested_episode,
                False,
                status="unavailable",
                failure_reason="episode has no servers",
                match_score=best_score,
            )
        return SourceCandidate(
            extractor.site_name,
            SOURCE_LABELS.get(extractor.site_name, extractor.site_name),
            str(best["id"]),
            str(best.get("title", "")),
            requested_episode,
            True,
            server_count=len(servers),
            status="available",
            match_score=best_score,
        )

    def extract(self, url):
        for extractor in self._extractors:
            match = re.match(extractor._VALID_URL, url)
            if match:
                return extractor.extract(url, match)
        raise ValueError(f"Ningún extractor interno soporta la URL: {url}")

    def _ordered_extractors(self, lang="es", dub=False, preferred_source=None):
        if lang not in {"es", "en"}:
            raise ValueError(f"Idioma no soportado: {lang}")
        extractors_by_source = {
            extractor.site_name: extractor
            for extractor in self._extractors
        }
        source_order = list(SOURCE_ORDER[(lang, bool(dub))])
        if preferred_source in extractors_by_source:
            source_order.insert(0, preferred_source)
        seen = set()
        ordered = []
        for source in source_order:
            if source in seen or source not in extractors_by_source:
                continue
            seen.add(source)
            ordered.append(extractors_by_source[source])
        return [
            extractor
            for extractor in ordered
            if not dub or getattr(extractor, "supports_dub", False)
        ]

    @staticmethod
    def _fallback_title(identifier):
        value = urllib.parse.unquote(str(identifier or "").rstrip("/"))
        value = value.rsplit("/", 1)[-1]
        return value.replace("-", " ").replace("_", " ")

    @staticmethod
    def _record_error(extractor, action, exc, errors=None):
        safe_error = re.sub(
            r"https?://\S+", "<redacted-url>", str(exc)
        )
        safe_error = re.sub(
            r"(?i)\b(?:token|signature|auth|key)=\S+",
            "<redacted-secret>",
            safe_error,
        )
        message = f"{extractor.site_name}: {safe_error}"
        if errors is not None:
            errors.append(message)
        log = getattr(extractor, "_log", None)
        if callable(log):
            log(
                f"[{extractor.site_name}][Aviso] "
                f"{action} falló: {safe_error}"
            )

    def list_episode_sources(
        self,
        title_or_id,
        episode,
        lang="es",
        dub=False,
        title=None,
        timeout_per_source=8.0,
        global_timeout=20.0,
    ):
        """Check the requested episode at every compatible source."""
        ordered = self._ordered_extractors(lang=lang, dub=dub)
        if not ordered:
            return []
        cache_key = (
            str(title_or_id),
            str(episode),
            str(lang),
            bool(dub),
            str(title or ""),
        )
        if cache_key in self._source_match_cache:
            return list(self._source_match_cache[cache_key])

        stop_event = threading.Event()
        results_by_source = {}
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(4, len(ordered)),
            thread_name_prefix="animux-source",
        )
        future_map = {
            executor.submit(
                self._source_candidate,
                extractor,
                title_or_id,
                episode,
                title,
                dub,
                stop_event,
            ): extractor
            for extractor in ordered
        }
        try:
            done, not_done = concurrent.futures.wait(
                future_map, timeout=max(0.1, float(global_timeout))
            )
            for future in done:
                extractor = future_map[future]
                try:
                    results_by_source[extractor.site_name] = future.result(
                        timeout=max(0.1, float(timeout_per_source))
                    )
                except Exception as exc:
                    results_by_source[extractor.site_name] = SourceCandidate(
                        extractor.site_name,
                        SOURCE_LABELS.get(
                            extractor.site_name, extractor.site_name
                        ),
                        "",
                        "",
                        str(episode),
                        False,
                        status="error",
                        failure_reason=str(exc),
                    )
            stop_event.set()
            for future in not_done:
                future.cancel()
                extractor = future_map[future]
                results_by_source[extractor.site_name] = SourceCandidate(
                    extractor.site_name,
                    SOURCE_LABELS.get(
                        extractor.site_name, extractor.site_name
                    ),
                    "",
                    "",
                    str(episode),
                    False,
                    status="error",
                    failure_reason="source timeout",
                )
        finally:
            stop_event.set()
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=True)

        results = [
            results_by_source[extractor.site_name] for extractor in ordered
        ]
        self._source_match_cache[cache_key] = tuple(results)
        return results

    def resolve_episode_source(
        self,
        source_id,
        title_or_id,
        episode,
        server="auto",
        dub=False,
        title=None,
        lang="es",
        stream_profile="default",
        quality="best",
        preferred_server="",
    ):
        """Resolve exactly one selected site, with no cross-source fallback."""
        if source_id == "auto":
            return self.get_episode(
                title_or_id,
                episode,
                dub=dub,
                title=title,
                lang=lang,
                server=server,
                stream_profile=stream_profile,
                quality=quality,
                preferred_server=preferred_server,
            )
        extractor = next(
            (
                item
                for item in self._ordered_extractors(lang=lang, dub=dub)
                if item.site_name == source_id
            ),
            None,
        )
        if extractor is None:
            raise ValueError(
                f"Fuente no compatible con idioma/modo: {source_id}"
            )

        original_source, _ = _split_source_id(title_or_id)
        if original_source == source_id:
            request_id = title_or_id
            matched_title = title or self._fallback_title(title_or_id)
        else:
            candidates = self.list_episode_sources(
                title_or_id,
                episode,
                lang=lang,
                dub=dub,
                title=title,
            )
            selected = next(
                (item for item in candidates if item.source_id == source_id),
                None,
            )
            if selected is None or selected.status != "available":
                status = selected.status if selected else "unsupported"
                reason = selected.failure_reason if selected else ""
                raise RuntimeError(
                    f"{source_id}: episodio no disponible ({status})"
                    + (f": {reason}" if reason else "")
                )
            request_id = selected.anime_id
            matched_title = selected.anime_title

        if server == "auto" and (
            stream_profile == "stable" or quality == "stable"
        ):
            result = self._resolve_stable_server(
                extractor,
                request_id,
                episode,
                dub=dub,
                quality=quality,
                preferred_server=preferred_server,
            )
        elif server == "auto":
            result = extractor.get_episode(request_id, episode, dub=dub)
        else:
            result = extractor.resolve_episode_server(
                request_id, episode, server_id=server, dub=dub
            )
        result.setdefault("source", source_id)
        result.setdefault("source_label", SOURCE_LABELS.get(source_id, source_id))
        result.setdefault("matched_title", matched_title)
        return result

    def _resolve_stable_server(
        self,
        extractor,
        request_id,
        episode,
        dub=False,
        quality="best",
        preferred_server="",
    ):
        candidates = extractor.list_episode_servers(
            request_id, episode, dub=dub
        )
        preferred = (
            preferred_server
            or self._preferred_servers.get(extractor.site_name)
        )
        if preferred:
            candidates = sorted(
                candidates,
                key=lambda item: item.candidate_id != preferred,
            )
        candidates = candidates[:3]
        resolved = []
        for candidate in candidates:
            try:
                result = extractor.resolve_episode_server(
                    request_id,
                    episode,
                    server_id=candidate.candidate_id,
                    dub=dub,
                )
            except Exception:
                self._session_server_scores[candidate.candidate_id] = {
                    "success": 0,
                    "failures": 1,
                }
                continue
            resolved.append(result)
        if not resolved:
            raise RuntimeError(
                f"{extractor.site_name}: ningún servidor se pudo resolver."
            )
        winner, measurements = choose_stable_stream(resolved)
        if winner is None:
            raise RuntimeError(
                f"{extractor.site_name}: ningún servidor estable disponible."
            )
        for measurement in measurements:
            self._session_server_scores[measurement.candidate_id] = {
                "success": 1 if measurement.ok else 0,
                "failures": 0 if measurement.ok else 1,
                "latency": measurement.latency,
                "bytes_per_second": measurement.bytes_per_second,
            }
        candidate_id = winner.get("candidate_id", "")
        if candidate_id:
            self._preferred_servers[extractor.site_name] = candidate_id
        if quality == "stable":
            measurement = next(
                (
                    item for item in measurements
                    if item.candidate_id == candidate_id and item.ok
                ),
                None,
            )
            if measurement:
                selected_quality = choose_stable_quality(
                    measurement.bytes_per_second,
                    ("1080p", "720p", "480p", "360p"),
                )
                if selected_quality:
                    winner["stable_quality"] = selected_quality
                    winner["quality_reason"] = (
                        "measured startup throughput with safety margin"
                    )
        return winner

    def search(self, title, dub=False, lang="es"):
        ordered = self._ordered_extractors(lang=lang, dub=dub)

        for extractor in ordered:
            try:
                results = extractor.search(title, dub=dub)
            except Exception as exc:
                self._record_error(extractor, "Búsqueda", exc)
                continue
            if results:
                return results
        return []

    def list_episodes(self, title_or_id, dub=False, lang="es"):
        source, identifier = _split_source_id(title_or_id)
        ordered = self._ordered_extractors(
            lang=lang, dub=dub, preferred_source=source
        )
        errors = []
        fallback_title = (
            self._fallback_title(identifier) if source else str(identifier)
        )

        if source and ordered and ordered[0].site_name == source:
            selected = ordered[0]
            try:
                episodes = selected.list_episodes(title_or_id, dub=dub)
                if episodes:
                    return episodes
            except Exception as exc:
                self._record_error(selected, "Listado de episodios", exc, errors)

        for extractor in ordered:
            if source and extractor.site_name == source:
                continue
            try:
                matches = extractor.search(fallback_title, dub=dub)
                if not matches:
                    raise RuntimeError("sin coincidencias")
                for result in matches:
                    episodes = extractor.list_episodes(result["id"], dub=dub)
                    if episodes:
                        return episodes
                raise RuntimeError("sin episodios")
            except Exception as exc:
                self._record_error(extractor, "Listado de episodios", exc, errors)

        if source and not any(item.site_name == source for item in ordered):
            errors.insert(0, f"{source}: no soporta este idioma o modo")

        raise RuntimeError(
            "No se pudo obtener la lista de episodios. " + "; ".join(errors)
        )

    def get_episode(
        self,
        title_or_id,
        episode,
        dub=False,
        title=None,
        lang="es",
        server="auto",
        source="auto",
        stream_profile="default",
        quality="best",
        preferred_server="",
    ):
        if source != "auto":
            return self.resolve_episode_source(
                source,
                title_or_id,
                episode,
                server=server,
                dub=dub,
                title=title,
                lang=lang,
                stream_profile=stream_profile,
                quality=quality,
                preferred_server=preferred_server,
            )
        if server != "auto":
            return self.resolve_episode_server(
                title_or_id,
                episode,
                server_id=server,
                dub=dub,
                title=title,
                lang=lang,
            )

        source, identifier = _split_source_id(title_or_id)
        ordered = self._ordered_extractors(
            lang=lang, dub=dub, preferred_source=source
        )
        errors = []

        if source:
            selected = next(
                (item for item in ordered if item.site_name == source), None
            )
            if selected:
                try:
                    if (
                        stream_profile == "stable"
                        or quality == "stable"
                    ):
                        return self._resolve_stable_server(
                            selected,
                            title_or_id,
                            episode,
                            dub=dub,
                            quality=quality,
                            preferred_server=preferred_server,
                        )
                    return selected.get_episode(
                        title_or_id, episode, dub=dub
                    )
                except Exception as exc:
                    self._record_error(selected, "Episodio", exc, errors)

        fallback_title = title or (
            self._fallback_title(identifier) if source else identifier
        )

        for extractor in ordered:
            if source and extractor.site_name == source:
                continue
            try:
                matches = extractor.search(fallback_title, dub=dub)
                if not matches:
                    raise RuntimeError("sin coincidencias")
                if stream_profile == "stable" or quality == "stable":
                    return self._resolve_stable_server(
                        extractor,
                        matches[0]["id"],
                        episode,
                        dub=dub,
                        quality=quality,
                        preferred_server=preferred_server,
                    )
                return extractor.get_episode(
                    matches[0]["id"], episode, dub=dub
                )
            except Exception as exc:
                self._record_error(extractor, "Episodio", exc, errors)

        raise RuntimeError(
            "Ninguna fuente devolvió el episodio. " + "; ".join(errors)
        )

    def _episode_requests(
        self, title_or_id, dub=False, title=None, lang="es"
    ):
        """Yield source-specific identifiers in the existing fallback order."""
        source, identifier = _split_source_id(title_or_id)
        ordered = self._ordered_extractors(
            lang=lang, dub=dub, preferred_source=source
        )
        fallback_title = title or (
            self._fallback_title(identifier) if source else identifier
        )
        for extractor in ordered:
            if source and extractor.site_name == source:
                yield extractor, title_or_id
                continue
            try:
                matches = extractor.search(fallback_title, dub=dub)
            except Exception as exc:
                self._record_error(extractor, "Búsqueda", exc)
                continue
            if not matches:
                continue
            yield extractor, matches[0]["id"]

    def list_episode_servers(
        self,
        title_or_id,
        episode,
        dub=False,
        title=None,
        lang="es",
    ):
        errors = []
        requested_source, _ = _split_source_id(title_or_id)
        try:
            requests = self._episode_requests(
                title_or_id, dub=dub, title=title, lang=lang
            )
            for extractor, request_id in requests:
                try:
                    candidates = extractor.list_episode_servers(
                        request_id, episode, dub=dub
                    )
                except Exception as exc:
                    self._record_error(
                        extractor, "Servidores", exc, errors
                    )
                    if requested_source:
                        break
                    continue
                if candidates:
                    return candidates
                if requested_source:
                    break
        except Exception as exc:
            errors.append(str(exc))
        raise RuntimeError(
            "No se pudieron enumerar servidores. " + "; ".join(errors)
        )

    def resolve_episode_server(
        self,
        title_or_id,
        episode,
        server_id,
        dub=False,
        title=None,
        lang="es",
    ):
        if server_id == "auto":
            return self.get_episode(
                title_or_id,
                episode,
                dub=dub,
                title=title,
                lang=lang,
            )
        requested_source = None
        if ":" in str(server_id):
            possible_source = str(server_id).split(":", 1)[0]
            if possible_source in SUPPORTED_SOURCES:
                requested_source = possible_source
        title_source, _ = _split_source_id(title_or_id)
        if requested_source is None and title_source:
            requested_source = title_source
        available = []
        errors = []
        matches = []
        try:
            requests = self._episode_requests(
                title_or_id, dub=dub, title=title, lang=lang
            )
            for extractor, request_id in requests:
                if (
                    requested_source
                    and extractor.site_name != requested_source
                ):
                    continue
                try:
                    candidates = extractor.list_episode_servers(
                        request_id, episode, dub=dub
                    )
                except Exception as exc:
                    self._record_error(
                        extractor, "Servidores", exc, errors
                    )
                    continue
                available.extend(
                    candidate.candidate_id for candidate in candidates
                )
                for candidate in candidates:
                    if extractor._candidate_matches(candidate, server_id):
                        matches.append((extractor, request_id, candidate))
        except RuntimeError as exc:
            errors.append(str(exc))

        if len(matches) > 1 and ":" not in str(server_id):
            choices = ", ".join(
                candidate.candidate_id for _, _, candidate in matches
            )
            raise ValueError(
                f"Servidor ambiguo: {server_id}. "
                f"Usa uno de estos IDs cualificados: {choices}."
            )
        if matches:
            extractor, request_id, candidate = matches[0]
            try:
                return extractor.resolve_episode_server(
                    request_id,
                    episode,
                    server_id=candidate.candidate_id,
                    dub=dub,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"{extractor.site_name}: el servidor seleccionado "
                    f"falló: {exc}"
                ) from exc
        available_text = ", ".join(available) or "ninguno"
        detail = f" Disponibles: {available_text}."
        if errors:
            detail += " " + "; ".join(errors)
        raise ValueError(
            f"Servidor no disponible para este episodio: {server_id}."
            + detail
        )


__all__ = [
    "AnimeExtractor",
    "SourceCandidate",
    "JkanimeExtractor",
    "AnimeFLVExtractor",
    "AnimeAV1Extractor",
    "AnimeFenixExtractor",
    "TioAnimeExtractor",
    "MonoschinoExtractor",
    "GogoanimeExtractor",
    "EXTRACTOR_CLASSES",
    "SOURCE_ORDER",
]
