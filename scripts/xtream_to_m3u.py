#!/usr/bin/env python3
"""Build a merged M3U from up to ten Xtream Codes accounts supplied as env vars.

Never put credentials in source control. The generated M3U contains stream URLs
that may themselves include credentials; treat the output as sensitive.
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path("dist/lista_clasica.m3u")
TIMEOUT = 20
MAX_BYTES = 80 * 1024 * 1024


def env_provider(i: int):
    server = os.getenv(f"XTREAM_{i}_SERVER", "").strip().rstrip("/")
    username = os.getenv(f"XTREAM_{i}_USERNAME", "").strip()
    password = os.getenv(f"XTREAM_{i}_PASSWORD", "").strip()
    if not any((server, username, password)):
        return None
    if not all((server, username, password)):
        print(f"[WARN] Proveedor {i}: faltan datos; se omite.", file=sys.stderr)
        return None
    parsed = urllib.parse.urlparse(server)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print(f"[WARN] Proveedor {i}: servidor inválido; se omite.", file=sys.stderr)
        return None
    query = urllib.parse.urlencode({
        "username": username, "password": password,
        "type": "m3u_plus", "output": "ts"
    })
    return i, f"{server}/get.php?{query}"


def fetch_m3u(provider_id: int, url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "TVFULL-Playlist-Builder/1.0", "Accept": "*/*"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError("la lista supera el límite de 80 MiB")
    text = data.decode("utf-8-sig", errors="replace")
    if "#EXTM3U" not in text[:2048]:
        raise RuntimeError("la respuesta no parece una lista M3U válida")
    return text


def parse_entries(text: str):
    lines = [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]
    entries = []
    pending = []
    for line in lines:
        if line.startswith("#EXTINF"):
            pending = [line]
        elif line.startswith("#EXTVLCOPT") or line.startswith("#KODIPROP") or line.startswith("#EXTGRP"):
            if pending:
                pending.append(line)
        elif line.startswith("#"):
            if pending:
                pending.append(line)
        elif pending and re.match(r"^https?://", line, re.I):
            pending.append(line)
            entries.append(pending)
            pending = []
    return entries


def entry_name(entry):
    line = entry[0]
    return line.rsplit(",", 1)[-1].strip().casefold()


def main():
    providers = [p for i in range(1, 11) if (p := env_provider(i))]
    if not providers:
        print("No hay proveedores configurados. Añadí GitHub Actions Secrets XTREAM_1_SERVER, XTREAM_1_USERNAME y XTREAM_1_PASSWORD (hasta XTREAM_10_*).", file=sys.stderr)
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    merged, seen = [], set()
    for provider_id, url in providers:
        started = time.monotonic()
        try:
            content = fetch_m3u(provider_id, url)
            entries = parse_entries(content)
            if not entries:
                raise RuntimeError("la lista no contiene canales HTTP(S) válidos")
            added = 0
            for entry in entries:
                key = entry_name(entry)
                if not key:
                    key = entry[-1].casefold()
                if key in seen:
                    continue
                seen.add(key)
                merged.extend(entry)
                added += 1
            print(f"Proveedor {provider_id}: {len(entries)} canales válidos, {added} nuevos; {time.monotonic()-started:.1f}s")
        except Exception as exc:
            # Do not print source URLs or exception strings that could contain credentials.
            print(f"[WARN] Proveedor {provider_id}: no se pudo importar ({type(exc).__name__}).", file=sys.stderr)

    if not merged:
        print("ERROR: ningún proveedor entregó canales válidos; no se genera una lista vacía.", file=sys.stderr)
        return 1

    temp = OUT.with_suffix(".m3u.tmp")
    temp.write_text("#EXTM3U\n" + "\n".join(merged) + "\n", encoding="utf-8")
    temp.replace(OUT)
    print(f"Lista creada: {OUT} — {len(seen)} canales únicos.")
    print("IMPORTANTE: el archivo generado contiene URLs privadas. No lo publiques en un repositorio público.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
