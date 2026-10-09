"""Bounded transport evidence, not a guarantee of decoding or long-term uptime.

Only opaque digests and measurements are persisted; never stream URLs/headers.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = 1
TTL = 30 * 86400
MAX_SAMPLE = 512 * 1024
MAX_TRANSFER = 4 * 1024 * 1024


def identity(url, headers=None):
    payload = json.dumps([url, sorted((headers or {}).items())], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def request_target(entry):
    url, _, inline = entry[-1].partition("|")
    headers = {}
    options = {"http-user-agent": "User-Agent", "http-referrer": "Referer",
               "http-referer": "Referer", "http-origin": "Origin",
               "http-cookie": "Cookie", "http-authorization": "Authorization"}
    for line in entry[1:-1]:
        if line.startswith("#EXTHTTP:"):
            try:
                values = json.loads(line.split(":", 1)[1])
                if isinstance(values, dict):
                    headers.update({str(k): str(v) for k, v in values.items()})
            except (ValueError, TypeError):
                pass
        elif line.startswith("#EXTVLCOPT:"):
            key, _, value = line.split(":", 1)[1].partition("=")
            if key.lower() in options:
                headers[options[key.lower()]] = value
            elif key.lower() == "http-header":
                name, sep, value = value.partition(":")
                if sep:
                    headers[name.strip()] = value.strip()
        elif line.startswith("#KODIPROP:inputstream.adaptive.") and "headers=" in line:
            headers.update(dict(urllib.parse.parse_qsl(line.split("=", 1)[1])))
    headers.update(dict(urllib.parse.parse_qsl(inline)))
    # Canonicalize duplicate names, reject control characters.
    clean = {}
    for key, value in headers.items():
        if re.fullmatch(r"[A-Za-z0-9-]+", key) and not re.search(r"[\r\n]", value):
            clean[key.lower()] = value
    return url, clean


def media_signature(data):
    # TS may start mid-packet. Require four consecutive sync bytes.
    for offset in range(min(188, max(0, len(data) - 564))):
        if all(data[offset + n * 188] == 0x47 for n in range(4)):
            return "ts"
    # ISO BMFF: validate first box size and type, rather than MIME alone.
    if len(data) >= 12 and data[4:8] in (b"ftyp", b"styp", b"moof", b"sidx"):
        size = int.from_bytes(data[:4], "big")
        if 8 <= size <= MAX_SAMPLE:
            return "mp4"
    return None


def error_body(data):
    sample = data[:2048].lstrip().lower()
    return sample.startswith((b"<!doctype", b"<html", b"<head", b"<body", b"{", b"["))


class Probe:
    def __init__(self, timeout=4, window=2, hls_wait=4, deadline=18, verify_media=False):
        self.timeout = timeout
        self.window = window
        self.hls_wait = hls_wait
        self.deadline = deadline
        self.verify_media = verify_media

    def _verify_sample(self, data):
        # Demux only the bounded bytes already downloaded. ffprobe never receives
        # a private URL, credentials, cookies or permission to fetch remote media.
        try:
            completed = subprocess.run([
                "ffprobe", "-v", "quiet", "-protocol_whitelist", "pipe",
                "-probesize", str(MAX_SAMPLE), "-analyzeduration", "1000000",
                "-show_entries", "stream=codec_type,codec_name", "-of", "json",
                "-i", "pipe:0"], input=data, capture_output=True, timeout=3, check=False)
            payload = json.loads(completed.stdout)
            return any(row.get("codec_type") == "video" and row.get("codec_name") not in (None, "unknown")
                       for row in payload.get("streams", []))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def _open(self, url, headers, end):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("invalid_target")
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("budget")
        values = {"user-agent": "TVFULL-Stability/2.0", "accept": "*/*",
                  "connection": "close", **headers}
        # A partial Range can make a live TS stop after a few KiB. Do not use it.
        values.pop("range", None)
        return urllib.request.urlopen(urllib.request.Request(url, headers=values),
                                      timeout=min(self.timeout, remaining))

    def _read(self, response, limit, end):
        data = bytearray()
        while len(data) < limit and time.monotonic() < end:
            chunk = response.read1(min(4096, limit - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def run(self, url, headers=None):
        started = time.monotonic()
        end = started + self.deadline
        result = {"state": "fail", "reason": "transport", "latency": None,
                  "bytes": 0, "duration": 0.0, "kind": "unknown"}
        try:
            with self._open(url, headers or {}, end) as response:
                result["status"] = response.status
                first = response.read1(4096)
                while first and len(first) < 1024 and not first.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"#EXTM3U"):
                    chunk = response.read1(1024 - len(first))
                    if not chunk:
                        break
                    first += chunk
                result["latency"] = time.monotonic() - started
                result["bytes"] = len(first)
                if response.status not in (200, 206) or not first or error_body(first):
                    result["reason"] = "non_media"
                    return result
                if first.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"#EXTM3U"):
                    manifest = first + self._read(response, MAX_SAMPLE - len(first), end)
                    if len(manifest) >= MAX_SAMPLE:
                        result.update(state="partial", reason="manifest_limit", kind="hls")
                        return result
                    return self._hls(response.geturl(), manifest, headers or {}, end, result)
                sample_start = time.monotonic()
                sample = bytearray(first[:MAX_SAMPLE])
                reads = 0
                # Bound retained bytes separately from transport observation.
                # Fast HD feeds otherwise fill 512 KiB before two seconds and
                # are systematically mislabeled as short/inconclusive samples.
                while time.monotonic() - sample_start < self.window and result["bytes"] < MAX_TRANSFER:
                    chunk = response.read1(min(8192, MAX_TRANSFER - result["bytes"]))
                    if not chunk:
                        break
                    if len(sample) < MAX_SAMPLE:
                        sample.extend(chunk[:MAX_SAMPLE - len(sample)])
                    result["bytes"] += len(chunk)
                    reads += 1
                result["duration"] = time.monotonic() - sample_start
                kind = media_signature(sample)
                result["kind"] = kind or "unknown"
                if kind:
                    sustained = reads >= 2 and result["duration"] >= self.window * .8
                    result.update(state="pass" if sustained and kind == "ts" else "partial",
                                  reason="continuous_ts" if sustained and kind == "ts" else "short_media")
                    if result["state"] == "pass" and self.verify_media:
                        result["video_identified"] = self._verify_sample(bytes(sample))
                        if not result["video_identified"]:
                            result.update(state="partial", reason="video_not_identified")
                else:
                    result["reason"] = "unknown_payload"
        except urllib.error.HTTPError as exc:
            result.update(status=exc.code, reason="http")
        except Exception:
            # Never retain exception messages: they often contain credentials.
            result["reason"] = "transport"
        return result

    def _hls(self, url, manifest, headers, end, result, depth=0):
        result["kind"] = "hls"
        text = manifest.decode("utf-8-sig", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0] != "#EXTM3U":
            result["reason"] = "invalid_manifest"
            return result
        if "#EXT-X-ENDLIST" in lines or "#EXT-X-PLAYLIST-TYPE:VOD" in lines:
            result["reason"] = "vod"
            return result
        if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
            if depth >= 2:
                result.update(state="partial", reason="nested_master")
                return result
            variants = []
            for i, line in enumerate(lines[:-1]):
                if line.startswith("#EXT-X-STREAM-INF:") and not lines[i+1].startswith("#"):
                    match = re.search(r"(?:^|,)BANDWIDTH=(\d+)", line.split(":", 1)[1])
                    variants.append((int(match[1]) if match else 0, lines[i+1]))
            if not variants:
                result["reason"] = "invalid_master"
                return result
            target = urllib.parse.urljoin(url, min(variants)[1])
            with self._open(target, headers, end) as response:
                body = self._read(response, MAX_SAMPLE, end)
                return self._hls(response.geturl(), body, headers, end, result, depth+1)
        segments = [urllib.parse.urljoin(url, lines[i+1])
                    for i, line in enumerate(lines[:-1])
                    if line.startswith("#EXTINF:") and not lines[i+1].startswith("#")]
        target = next((line.split(":", 1)[1] for line in lines
                       if line.startswith("#EXT-X-TARGETDURATION:")), None)
        if not segments or target is None:
            result["reason"] = "invalid_media_playlist"
            return result
        protected = any(line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line for line in lines)
        byterange = any(line.startswith("#EXT-X-BYTERANGE:") for line in lines)
        if byterange:
            result.update(state="partial", reason="byterange_not_verified")
            return result
        checked = 0
        samples = []
        for segment in segments[-2:]:
            with self._open(segment, headers, end) as response:
                data = self._read(response, 32768, end)
            result["bytes"] += len(data)
            if not data or error_body(data) or (not protected and not media_signature(data)):
                result["reason"] = "invalid_segment"
                return result
            checked += 1
            samples.append(data)
        wait = min(max(float(target), .1), self.hls_wait)
        if time.monotonic() + wait >= end:
            result.update(state="partial", reason="probe_budget")
            return result
        time.sleep(wait)
        with self._open(url, headers, end) as response:
            newer = self._read(response, MAX_SAMPLE, end).decode("utf-8-sig", errors="replace")
        # URI change catches live playlists without MEDIA-SEQUENCE as well.
        new_lines = [line.strip() for line in newer.splitlines() if line.strip()]
        if "#EXT-X-ENDLIST" in new_lines or "#EXT-X-PLAYLIST-TYPE:VOD" in new_lines:
            result.update(state="fail", reason="vod")
            return result
        new_segments = [urllib.parse.urljoin(url, new_lines[i+1])
                        for i, line in enumerate(new_lines[:-1])
                        if line.startswith("#EXTINF:") and not new_lines[i+1].startswith("#")]
        progress = bool(new_lines and new_lines[0] == "#EXTM3U" and new_segments and
                        set(new_segments) - set(segments))
        result.update(state="pass" if checked >= 2 and progress and not protected else "partial",
                      reason="hls_progress" if progress and not protected else
                             "encrypted_not_decoded" if protected else "hls_no_progress_observed")
        if result["state"] == "pass" and self.verify_media:
            result["video_identified"] = self._verify_sample(b"".join(samples))
            if not result["video_identified"]:
                result.update(state="partial", reason="video_not_identified")
        return result


def load_history(path):
    try:
        value = json.loads(Path(path).read_text())
        if value.get("version") != VERSION:
            return {"version": VERSION, "streams": {}}
        streams = {}
        for key, row in value.get("streams", {}).items():
            if not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(row, dict):
                continue
            updated = float(row.get("updated", 0))
            if not math.isfinite(updated) or time.time() - updated > TTL:
                continue
            observations = []
            for obs in row.get("observations", [])[-12:]:
                if isinstance(obs, dict) and obs.get("state") in ("pass", "partial", "fail"):
                    latency = obs.get("latency")
                    if latency is not None and (not isinstance(latency, (int, float)) or not math.isfinite(latency)):
                        latency = None
                    observations.append({"state": obs["state"], "latency": latency})
            streams[key] = {"updated": updated, "observations": observations}
        return {"version": VERSION, "streams": streams}
    except (ValueError, TypeError, AttributeError, OSError):
        return {"version": VERSION, "streams": {}}


def observe(history, key, result):
    row = history["streams"].setdefault(key, {"observations": []})
    row["updated"] = time.time()
    row["observations"] = (row["observations"] + [
        {"state": result["state"], "latency": result.get("latency")}])[-12:]


def reliability(history, key):
    observations = history["streams"].get(key, {}).get("observations", [])
    # Smoothed reliability and recency weighting. Unknown is never a success.
    weight = sum(.85 ** i for i in range(len(observations)))
    good = sum((1 if row["state"] == "pass" else .35 if row["state"] == "partial" else 0)
               * .85 ** i for i, row in enumerate(reversed(observations)))
    return (good + 1) / (weight + 2)


def choose(options, results, history, previous_key=None):
    def rank(candidate):
        result = results.get(candidate["key"])
        state = result["state"] if result else "unknown"
        tier = {"pass": 3, "partial": 2, "unknown": 1, "fail": 0}[state]
        latency = result.get("latency") if result else None
        # A recently passing incumbent that was not remeasured is stronger
        # evidence than an inconclusive new sample, but not a current pass.
        cached = history["streams"].get(candidate["key"], {})
        observations = cached.get("observations", [])
        if state == "unknown" and observations and observations[-1]["state"] == "pass" \
                and time.time() - cached.get("updated", 0) <= 12 * 3600:
            tier = 2.5
        speed = 0 if latency is None else .12 / (1 + max(0, latency))
        return tier, reliability(history, candidate["key"]) + speed, int(candidate["has_logo"])
    winner = max(options, key=rank)
    previous = next((c for c in options if c["key"] == previous_key), None)
    # Hysteresis only within the same current evidence tier, never over a failure.
    if previous and rank(previous)[0] == rank(winner)[0] and rank(winner)[1] - rank(previous)[1] < .06:
        return previous
    return winner


def probe_order(grouped, history, previous):
    # Cover every channel before second alternatives. Recheck the incumbent
    # first; if it last failed, prefer a healthier alternative. Cold ties are
    # distributed across accounts so one large provider cannot starve all others.
    rounds = []
    load = {}
    for name in sorted(grouped):
        options = grouped[name]
        def first_rank(candidate):
            row = history["streams"].get(candidate["key"], {})
            observations = row.get("observations", [])
            failed = bool(observations and observations[-1]["state"] == "fail")
            incumbent = candidate["key"] == previous.get(name) and not failed
            return (not incumbent, failed, -round(reliability(history, candidate["key"]), 2),
                    load.get(candidate["provider"], 0), row.get("updated", 0),
                    candidate["provider"], candidate["key"])
        first = min(options, key=first_rank)
        load[first["provider"]] = load.get(first["provider"], 0) + 1
        remaining = sorted((candidate for candidate in options if candidate is not first), key=lambda c: (
            history["streams"].get(c["key"], {}).get("updated", 0),
            -reliability(history, c["key"]), c["provider"], c["key"]))
        rounds.append([first, *remaining])
    output = []
    for i in range(max((len(row) for row in rounds), default=0)):
        layer = [row[i] for row in rounds if len(row) > i]
        layer.sort(key=lambda c: (c.get("priority", 1), history["streams"].get(c["key"], {}).get("updated", 0)))
        output.extend(layer)
    return output


def validate_retention(previous, current, minimum=.8):
    # Names rather than raw counts catch replacing old channels with unrelated ones.
    if not current:
        raise ValueError("empty_playlist")
    if previous and len(previous & current) / len(previous) < minimum:
        raise ValueError("massive_channel_loss")


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)

