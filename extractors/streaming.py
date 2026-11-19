"""Bounded, in-memory stream stability helpers.

This module never persists URLs, responses, cookies, or probe measurements.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import threading
import time
import urllib.request


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    candidate_id: str
    ok: bool
    latency: float = 0.0
    bytes_read: int = 0
    elapsed: float = 0.0
    bytes_per_second: float = 0.0
    status: str = "error"

    @property
    def score(self):
        if not self.ok:
            return float("-inf")
        latency_penalty = min(max(self.latency, 0.0), 10.0) * 150_000
        return self.bytes_per_second - latency_penalty


def probe_stream(
    candidate_id,
    url,
    referer="",
    *,
    max_bytes=262_144,
    timeout=4.0,
    opener=None,
    cancel_event=None,
    clock=time.monotonic,
):
    """Read a small bounded range and measure first-byte latency and speed."""
    max_bytes = int(max_bytes)
    timeout = float(timeout)
    if max_bytes < 1 or max_bytes > 1_048_576:
        raise ValueError("probe max_bytes must be between 1 and 1048576")
    if timeout <= 0 or timeout > 15:
        raise ValueError("probe timeout must be between 0 and 15 seconds")
    cancel_event = cancel_event or threading.Event()
    if cancel_event.is_set():
        return ProbeResult(candidate_id, False, status="cancelled")

    request = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes=0-{max_bytes - 1}",
            **({"Referer": referer} if referer else {}),
        },
    )
    open_request = opener or urllib.request.urlopen
    started = clock()
    first_byte_at = None
    total = 0
    try:
        with open_request(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status >= 400:
                return ProbeResult(
                    candidate_id, False, status=f"http-{status}"
                )
            while total < max_bytes and not cancel_event.is_set():
                chunk = response.read(min(16_384, max_bytes - total))
                if not chunk:
                    break
                if first_byte_at is None:
                    first_byte_at = clock()
                total += len(chunk)
    except Exception:
        return ProbeResult(candidate_id, False, status="error")

    finished = clock()
    if cancel_event.is_set() and total == 0:
        return ProbeResult(candidate_id, False, status="cancelled")
    if first_byte_at is None or total == 0:
        return ProbeResult(candidate_id, False, status="empty")
    latency = max(0.0, first_byte_at - started)
    transfer_elapsed = max(0.001, finished - first_byte_at)
    return ProbeResult(
        candidate_id,
        True,
        latency=latency,
        bytes_read=total,
        elapsed=max(0.0, finished - started),
        bytes_per_second=total / transfer_elapsed,
        status="ok",
    )


def choose_stable_stream(
    candidates,
    *,
    max_candidates=3,
    max_workers=3,
    max_bytes=262_144,
    timeout=4.0,
    global_timeout=8.0,
    probe=probe_stream,
):
    """Return (candidate, measurements), falling back to original order."""
    selected = list(candidates)[: max(1, min(int(max_candidates), 4))]
    if not selected:
        return None, []
    cancel_event = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(int(max_workers), len(selected), 3)),
        thread_name_prefix="animux-probe",
    )
    future_map = {
        executor.submit(
            probe,
            item.get("candidate_id", ""),
            item["url"],
            item.get("referer", ""),
            max_bytes=max_bytes,
            timeout=timeout,
            cancel_event=cancel_event,
        ): item
        for item in selected
    }
    measurements = []
    try:
        done, not_done = concurrent.futures.wait(
            future_map, timeout=max(0.1, float(global_timeout))
        )
        for future in done:
            try:
                measurements.append(future.result())
            except Exception:
                item = future_map[future]
                measurements.append(
                    ProbeResult(
                        item.get("candidate_id", ""),
                        False,
                        status="error",
                    )
                )
        cancel_event.set()
        for future in not_done:
            future.cancel()
    finally:
        cancel_event.set()
        try:
            executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=True)

    successful = [item for item in measurements if item.ok]
    if not successful:
        return selected[0], measurements
    winner = max(successful, key=lambda item: item.score)
    return next(
        item
        for item in selected
        if item.get("candidate_id", "") == winner.candidate_id
    ), measurements


def choose_stable_quality(bytes_per_second, available):
    """Choose a sustainable height with margin; prefer 720p when doubtful."""
    heights = sorted(
        {
            int(str(value).rstrip("p"))
            for value in available
            if str(value).rstrip("p").isdigit()
        },
        reverse=True,
    )
    if not heights or not bytes_per_second or bytes_per_second <= 0:
        return None
    required_bps = {
        1080: 5_000_000 / 8,
        720: 2_500_000 / 8,
        480: 1_200_000 / 8,
        360: 700_000 / 8,
    }
    for height in heights:
        requirement = required_bps.get(height)
        if requirement is None:
            continue
        margin = bytes_per_second / requirement
        if height == 1080 and margin < 1.75 and 720 in heights:
            continue
        if margin >= 1.35:
            return f"{height}p"
    return f"{min(heights)}p"
