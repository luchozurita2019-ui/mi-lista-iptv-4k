#!/usr/bin/env python3
"""Merge up to ten Xtream M3U sources; select the Argentine pay-TV lineup from configured providers.

Credentials belong in GitHub Actions Secrets, never in source control. The
output contains stream URLs and must be treated as sensitive.
"""
from __future__ import annotations

import os
import re
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import defaultdict, deque
from stream_stability import (Probe, request_target, identity, load_history, observe,
                              choose, probe_order, validate_retention, atomic_json)
import urllib.parse
import urllib.request
from pathlib import Path
from channel_catalog import (CHANNELS, BY_NAME, PREMIUM_PACKS, CATEGORY_ORDER,
                             fold_name as _fold_name, catalog_match, resolve, catalog_logo)

OUT = Path("dist/lista_clasica.m3u")
TIMEOUT = 20
PROBE_WORKERS = 4
MAX_PROBES = 600
MAX_BYTES = 80 * 1024 * 1024

# Explicit VOD/individual-title groups are not live TV channels.
VOD_RE = re.compile(
    # No bloquear "películas/movies" por sí solo: también puede nombrar canales
    # lineales de cine. El VOD se identifica por su ruta, marcadores explícitos
    # de catálogo/a pedido o episodios individuales.
    r"\b(vod|video\s*on\s*demand|on\s*demand|a\s*la\s*carta|"
    r"catalogo|cat[aá]logo|descargas?|temporadas?|episodios?|"
    r"full\s*movies?|all\s*movies?|all\s*series?|"
    r"contenido\s*a\s*pedido|estrenos\s*vod)\b",
    re.I,
)
ADULT_RE = re.compile(r"\b(adultos?|adult|xxx|18\s*\+|porn(?:o|ography)?|porno|er[oó]tic[oa]s?|erotica|playboy|venus|hustler|penthouse|private\s*tv|brazzers|dorcel|red\s*light|sexy\s*hot|naughty|milf|babes?|hentai|sex\s*tv)\b", re.I)

def env_provider(i: int):
    # Optional full URL lets each source retain its own type/output parameters.
    full_url = os.getenv(f"XTREAM_{i}_URL", "").strip()
    if full_url:
        parsed = urllib.parse.urlparse(full_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.path.endswith("/get.php"):
            print(f"[WARN] Proveedor {i}: URL Xtream inválida; se omite.", file=sys.stderr)
            return None
        return i, full_url

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
        deadline = time.monotonic() + TIMEOUT
        chunks = []
        size = 0
        while size <= MAX_BYTES:
            if time.monotonic() >= deadline:
                raise TimeoutError("playlist_budget")
            chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        data = b"".join(chunks)
    if len(data) > MAX_BYTES:
        raise RuntimeError("la lista supera el límite de 80 MiB")
    text = data.decode("utf-8-sig", errors="replace")
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    if first_line != "#EXTM3U" and not first_line.startswith("#EXTM3U "):
        raise RuntimeError("la respuesta no parece una lista M3U válida")
    return text


def parse_entries(text: str):
    lines = [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]
    entries, pending = [], []
    for line in lines:
        if line.startswith("#EXTINF"):
            pending = [line]
        elif line.startswith("#"):
            if pending:
                pending.append(line)
        elif pending and re.match(r"^https?://", line, re.I):
            pending.append(line)
            entries.append(pending)
            pending = []
    return entries


def extinf_comma(line):
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ',' and not quoted:
            return index
    return -1


def metadata(entry):
    extinf = entry[0]
    comma = extinf_comma(extinf)
    name = extinf[comma + 1:].strip() if comma >= 0 else ""
    attrs = {}
    for key, value in re.findall(r'([\w-]+)="([^"]*)"', extinf):
        attrs[key.casefold()] = value
    group = attrs.get("group-title", "")
    country = attrs.get("tvg-country", "")
    language = attrs.get("tvg-language", "")
    extra = " ".join([name, group, country, language] + entry[1:-1])
    return name, attrs, group, country, language, extra


def exclusion_reason(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    path = urllib.parse.urlsplit(request_target(entry)[0]).path.casefold()
    if re.search(r"/(?:movie|movies|series|vod)(?:/|$)", path):
        return "vod"
    if VOD_RE.search(name + " " + group):
        return "vod"
    if re.search(r"\b(S\d{1,2}E\d{1,2}|temporada\s+\d+|episodio\s+\d+)\b", name, re.I):
        return "episode"
    if ADULT_RE.search(name + " " + group + " " + path):
        return "adult"
    if not resolve(name, group, country, language):
        return "outside_catalog_or_region"
    return None


def keep_entry(entry):
    return exclusion_reason(entry) is None


def category_for(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    row = resolve(name, group, country, language)
    return row["category"] if row else "Fuera del catálogo"


def set_group_title(entry, category):
    # Cambia solo group-title dentro de EXTINF; conserva tvg-logo, tvg-id y demás datos.
    extinf = entry[0]
    if re.search(r'\bgroup-title="[^"]*"', extinf, re.I):
        extinf = re.sub(r'\bgroup-title="[^"]*"', lambda _: f'group-title="{category}"', extinf, count=1, flags=re.I)
    else:
        comma = extinf_comma(extinf)
        if comma >= 0:
            extinf = extinf[:comma] + f' group-title="{category}"' + extinf[comma:]
    return [extinf, *entry[1:]]


def set_display_name(entry):
    """Fija el nombre visible del canal al nombre canónico del catálogo."""
    name, attrs, group, country, language, extra = metadata(entry)
    row = resolve(name, group, country, language)
    known = (row["category"], row["name"]) if row else None
    if not known:
        return entry
    extinf = entry[0]
    # Mantener coherentes tvg-name y el nombre que ve el usuario.
    if re.search(r'\btvg-name="[^"]*"', extinf, re.I):
        extinf = re.sub(
            r'\btvg-name="[^"]*"',
            lambda _: f'tvg-name="{known[1]}"',
            extinf, count=1, flags=re.I
        )
    comma = extinf_comma(extinf)
    if comma >= 0:
        extinf = extinf[:comma + 1] + known[1]
    return [extinf, *entry[1:]]


def priority(entry):
    category = category_for(entry)
    name = metadata(entry)[0]
    return CATEGORY_ORDER.get(category, 13), name.casefold()


def entry_name(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    row = resolve(name, group, country, language)
    if row:
        return "catalog:" + _fold_name(row["name"])
    return _fold_name(name)

def set_logo_if_missing(entry, logo_url):
    if not logo_url:
        return entry
    extinf = entry[0]
    if re.search(r'\btvg-logo="[^"]+"', extinf, re.I):
        return entry
    if re.search(r'\btvg-logo=""', extinf, re.I):
        extinf = re.sub(r'\btvg-logo=""', f'tvg-logo="{logo_url}"', extinf, count=1, flags=re.I)
    else:
        comma = extinf_comma(extinf)
        if comma >= 0:
            extinf = extinf[:comma] + f' tvg-logo="{logo_url}"' + extinf[comma:]
    return [extinf, *entry[1:]]

def fetch_logo_manifest():
    # Usa nombres reales de archivos de un repositorio público de logos; no inventa URLs.
    manifest = []
    for directory in ("argentina", "international", "world-latin-america"):
        url = f"https://api.github.com/repos/tv-logo/tv-logos/contents/countries/{directory}?per_page=1000"
        request = urllib.request.Request(url, headers={"User-Agent": "TVFULL-Logo-Finder/1.0", "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
            if isinstance(payload, list):
                for item in payload:
                    name = item.get("name", "")
                    if name.lower().endswith((".png", ".webp", ".jpg", ".jpeg")):
                        manifest.append((name, item.get("download_url", "")))
        except Exception:
            continue
    return manifest

def find_logo(name, manifest):
    if not manifest:
        return ""
    target = _fold_name(name).replace(" ", "-")
    if not target:
        return ""
    # Exact filename match first, then a conservative channel-name prefix match.
    ranked = []
    for filename, url in manifest:
        stem = filename.rsplit(".", 1)[0].casefold()
        stem_fold = _fold_name(stem.replace("-", " "))
        score = 2 if stem_fold == _fold_name(name) else 1 if stem_fold.startswith(_fold_name(name) + " ") else 0
        if score:
            ranked.append((score, len(stem), url))
    return max(ranked, default=(0, 0, ""))[2]


def probe_stream(url: str, headers=None):
    return Probe(verify_media=os.getenv("STABILITY_FFPROBE") == "1").run(url, headers)


def validate_playlist(entries):
    if not entries:
        raise ValueError("empty_playlist")
    for entry in entries:
        name, attrs, group, *_ = metadata(entry)
        url, headers = request_target(entry)
        target = urllib.parse.urlsplit(url)
        if target.scheme not in ("http", "https") or not target.netloc:
            raise ValueError("invalid_url")
        if ADULT_RE.search(name + " " + group + " " + target.path):
            raise ValueError("adult_content")
        if VOD_RE.search(name + " " + group) or re.search(r"/(?:movie|movies|series|vod)(?:/|$)", target.path, re.I):
            raise ValueError("vod_content")
        if not name or group not in CATEGORY_ORDER or not keep_entry(entry):
            raise ValueError("invalid_metadata")



def write_collections(entries, details, directory, available=None):
    """Derive every playlist from the same winners; never invent transport URLs."""
    folder = Path(directory) / "listas"
    folder.mkdir(parents=True, exist_ok=True)
    collections = {
        "argentina_premium": lambda row, state: True,
        "packs_premium": lambda row, state: row["pack"] in PREMIUM_PACKS,
        "hbo": lambda row, state: row["pack"] == "HBO",
        "universal": lambda row, state: row["pack"] == "Universal+",
        "cine_series": lambda row, state: row["category"] == "TV · Ficción",
        "deportes": lambda row, state: row["category"] == "Argentina · Deportes",
        "infantiles": lambda row, state: row["category"] == "Argentina · Infantiles",
        "documentales": lambda row, state: row["category"] == "Argentina · Documentales",
        "entretenimiento": lambda row, state: row["category"] == "Argentina · Entretenimiento",
        "musica": lambda row, state: row["category"] == "Argentina · Música",
        "estables": lambda row, state: state == "pass",
    }
    counts = {}
    for name, accept in collections.items():
        subset = [entry for entry in entries
                  if accept(BY_NAME[metadata(entry)[0]], details[metadata(entry)[0]]["state"])]
        counts[name] = len(subset)
        path = folder / (name + ".m3u")
        temp = path.with_suffix(".m3u.tmp")
        temp.write_text("#EXTM3U\n" + "".join("\n".join(entry) + "\n" for entry in subset), encoding="utf-8")
        temp.replace(path)
    available = available if available is not None else {name: row["options"] for name, row in details.items()}
    availability = []
    for row in CHANNELS:
        selected = details.get(row["name"])
        availability.append({
            "canal": row["name"], "categoria": row["category"], "pack": row["pack"],
            "estado": selected["state"] if selected else "sin_alternativas_utiles" if available.get(row["name"]) else "no_disponible_en_proveedores",
            "alternativas": available.get(row["name"], 0),
        })
    atomic_json(folder / "disponibilidad.json", {
        "catalogo_objetivo": len(CHANNELS), "canales_publicados": len(entries),
        "nota": "Catálogo de referencia, no garantía de disponibilidad. Solo pass entra en estables; partial y unknown siguen diferenciados.",
        "listas": counts, "canales": availability,
    })
    return counts


def main():
    providers = [p for i in range(1, 11) if (p := env_provider(i))]
    if not providers:
        print("Configurá XTREAM_1_URL ... XTREAM_10_URL como Secrets (URL completa), o las variables XTREAM_n_SERVER/USERNAME/PASSWORD.", file=sys.stderr)
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    candidates = []
    total_input = total_kept = 0
    exclusions = defaultdict(int)
    imported_providers = 0
    for provider_id, url in providers:
        started = time.monotonic()
        try:
            content = fetch_m3u(provider_id, url)
            entries = parse_entries(content)
            if not entries:
                raise RuntimeError("la lista no contiene canales HTTP(S) válidos")
            imported_providers += 1
            kept = 0
            for entry in entries:
                total_input += 1
                reason = exclusion_reason(entry)
                if reason:
                    exclusions[reason] += 1
                    continue
                kept += 1
                normalized = set_display_name(set_group_title(entry, category_for(entry)))
                candidates.append({
                    "provider": provider_id,
                    "entry": normalized,
                    "name": entry_name(normalized),
                    "has_logo": bool(metadata(normalized)[1].get("tvg-logo")),
                    "priority": 0 if BY_NAME[metadata(normalized)[0]]["pack"] in PREMIUM_PACKS else 1,
                })
            total_kept += kept
            print(f"Proveedor {provider_id}: {len(entries)} entradas; {kept} coinciden con los filtros; {time.monotonic()-started:.1f}s")
        except Exception as exc:
            # Never print source URLs or exception strings that may expose credentials.
            print(f"[WARN] Proveedor {provider_id}: no se pudo importar ({type(exc).__name__}).", file=sys.stderr)

    if not candidates:
        print("ERROR: ningún proveedor entregó entradas que coincidan con los filtros; no se genera una lista vacía.", file=sys.stderr)
        return 1

    # Reviewed logos are resolved offline. Never guess a filename or prefer a
    # generic broadcaster logo over a specific numbered/premium channel.
    logos_added = 0
    for candidate in candidates:
        entry = candidate["entry"]
        logo = catalog_logo(metadata(entry)[0])
        if logo and metadata(entry)[1].get("tvg-logo") != logo:
            extinf = re.sub(r'\btvg-logo="[^"]*"', '', entry[0], flags=re.I)
            candidate["entry"] = set_logo_if_missing([extinf, *entry[1:]], logo)
            candidate["has_logo"] = True
            logos_added += 1

    history_path = Path("stability_history.json")
    history = load_history(history_path)
    baseline = parse_entries(Path("lista_clasica.m3u").read_text(encoding="utf-8")) if Path("lista_clasica.m3u").exists() else []
    # Apply exactly the same scope to the baseline. Intentional removal of
    # foreign feeds, radios/events and duplicate aliases is not provider loss.
    eligible_baseline = [entry for entry in baseline if keep_entry(entry)]
    previous = {entry_name(entry): identity(*request_target(entry)) for entry in eligible_baseline}
    grouped = defaultdict(list)
    seen = set()
    for candidate in candidates:
        candidate["url"], candidate["headers"] = request_target(candidate["entry"])
        candidate["key"] = identity(candidate["url"], candidate["headers"])
        # A URL with different required headers is a different transport identity.
        pair = (candidate["name"], candidate["key"])
        if pair not in seen:
            grouped[candidate["name"]].append(candidate)
            seen.add(pair)
    selected = probe_order(grouped, history, previous)[:MAX_PROBES]
    # Serialize each account's checks so an authorized single-connection account
    # is never flooded by the selector. Only four provider jobs run concurrently.
    by_provider = defaultdict(list)
    for candidate in selected:
        source = dict(providers)[candidate["provider"]]
        parsed_source = urllib.parse.urlsplit(source)
        credentials = urllib.parse.parse_qs(parsed_source.query)
        account = identity(json.dumps([parsed_source.netloc, credentials.get("username"), credentials.get("password")]))
        by_provider[account].append(candidate)
    queues = {account: deque(batch) for account, batch in by_provider.items()}
    candidate_account = {c["key"]: account for account, batch in by_provider.items() for c in batch}
    ready = deque(queues)
    deadline = time.monotonic() + 15 * 60
    results = {}
    progress_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        active = {}
        while ready or active:
            if time.monotonic() >= deadline:
                ready.clear()
            while ready and len(active) < PROBE_WORKERS:
                account = ready.popleft()
                while queues[account] and queues[account][0]["key"] in results:
                    queues[account].popleft()
                if not queues[account]:
                    continue
                candidate = queues[account].popleft()
                future = pool.submit(probe_stream, candidate["url"], candidate["headers"])
                active[future] = (account, candidate)
            if not active:
                break
            completed, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                account, candidate = active.pop(future)
                key = candidate["key"]
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = {"state": "fail", "reason": "transport", "latency": None}
                if results[key].get("state") != "pass":
                    # Recheck an alternative for this channel promptly, rather
                    # than leaving its backups behind hundreds of unrelated URLs.
                    for fallback in grouped[candidate["name"]]:
                        fallback_account = candidate_account.get(fallback["key"])
                        queue = queues.get(fallback_account)
                        if queue and fallback in queue:
                            queue.remove(fallback)
                            queue.appendleft(fallback)
                            break
                if queues[account] and time.monotonic() < deadline:
                    ready.append(account)
            if time.monotonic() - progress_at >= 30:
                print(f"Comprobación: {len(results)} alternativas medidas, {len(active)} cuentas activas.", flush=True)
                progress_at = time.monotonic()
    for key, result in results.items():
        observe(history, key, result)
    winners = []
    winner_states = defaultdict(int)
    winner_details = {}
    winner_providers = defaultdict(int)
    for name, options in grouped.items():
        # Never introduce a URL measured as failed, or HLS conclusively marked VOD.
        options = [c for c in options if results.get(c["key"], {}).get("reason") != "vod"
                   and results.get(c["key"], {}).get("state") != "fail"]
        if not options:
            continue
        winner = choose(options, results, history, previous.get(name))
        state = results.get(winner["key"], {}).get("state", "unknown")
        winner_states[state] += 1
        winners.append((priority(winner["entry"]), winner["entry"]))
        winner_details[metadata(winner["entry"])[0]] = {"state": state, "options": len(grouped[name])}
        winner_providers[winner["provider"]] += 1
    # Save measurements even when publication is refused. They contain no URLs.
    atomic_json(history_path, history)
    report = {
        "canales_unicos": len(winners),
        "entradas_candidatas": len(candidates),
        "urls_probadas": len(results),
        "urls_con_datos": sum(1 for result in results.values() if result["state"] == "pass"),
        "ganadores_por_evidencia": dict(winner_states),
        "metodo": "muestras TS continuas / segmentos y avance HLS + historial ponderado; no garantiza reproducción",
        "ffprobe_habilitado": os.getenv("STABILITY_FFPROBE") == "1",
        "urls_parciales": sum(row["state"] == "partial" for row in results.values()),
        "urls_fallidas": sum(row["state"] == "fail" for row in results.values()),
        "urls_sin_probar": len(seen) - len(results),
        "historial_version": 1,
        "conexiones_por_proveedor": 1,
        "presupuesto_global_segundos": 900,
        "publicacion": "pendiente",
        "limite_pruebas": MAX_PROBES,
        "logos_completados": logos_added,
        "proveedores_configurados": len(providers),
        "proveedores_importados": imported_providers,
        "ganadores_por_proveedor": dict(winner_providers),
        "catalogo_objetivo": len(CHANNELS),
        "exclusiones_por_motivo": dict(exclusions),
        "limpieza_entradas_anteriores": len(baseline) - len(eligible_baseline),
        "nota": "No se guardan URLs, usuarios ni contraseñas en este informe.",
    }
    Path("dist/diagnostico_estabilidad.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if results and not any(row["state"] in ("pass", "partial") for row in results.values()):
        report["publicacion"] = "rechazada_sin_medios"
        atomic_json("dist/diagnostico_estabilidad.json", report)
        print("ERROR: ninguna muestra tiene evidencia de medios; se conserva la lista publicada.", file=sys.stderr)
        return 1

    winners.sort(key=lambda item: item[0])
    playlist_lines = [line for _, entry in winners for line in entry]
    # Compare logical identities before replacing the previous good playlist.
    final_entries = parse_entries("\n".join(playlist_lines))
    try:
        validate_playlist(final_entries)
        if len(final_entries) != len(winners):
            raise ValueError("entry_count_mismatch")
        validate_retention(set(previous), {entry_name(entry) for entry in final_entries})
    except ValueError as exc:
        report["publicacion"] = str(exc)
        atomic_json("dist/diagnostico_estabilidad.json", report)
        print(f"ERROR: publicación rechazada ({exc}); se conserva la lista anterior.", file=sys.stderr)
        return 1
    temp = OUT.with_suffix(".m3u.tmp")
    temp.write_text("#EXTM3U\n" + "\n".join(playlist_lines) + "\n", encoding="utf-8")
    temp.replace(OUT)
    available = {metadata(options[0]["entry"])[0]: len(options) for options in grouped.values()}
    report["listas"] = write_collections(final_entries, winner_details, OUT.parent, available)
    report["publicacion"] = "validada"
    atomic_json("dist/diagnostico_estabilidad.json", report)

    print(f"Lista creada: {OUT} — {len(winners)} canales únicos de {total_input} entradas revisadas.")
    print(f"Catálogo de TV paga para Argentina: {len(CHANNELS)} señales objetivo; disponibilidad: dist/listas/disponibilidad.json.")
    print(f"Proveedores configurados: {len(providers)}/10; logos completados: {logos_added}.")
    print(f"Estabilidad: {len(results)} URLs probadas; {sum(1 for result in results.values() if result['state'] == 'pass')} mostraron continuidad o avance HLS en la muestra.")
    print("Selección por evidencia actual, fiabilidad histórica y margen de cambio; el logo solo desempata.")
    if len(candidates) > len(results):
        print(f"Nota: hay alternativas sin medir por los límites de cantidad/tiempo; las alternativas no medidas se identifican en el informe y no aparecen en la lista de estables.")
    print("IMPORTANTE: los Secrets protegen las entradas, pero el M3U público puede exponer credenciales en sus URLs.")
    print("Nota: la prueba breve no demuestra estabilidad durante horas ni compatibilidad con todos los reproductores.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

