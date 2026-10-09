#!/usr/bin/env python3
"""Merge up to ten Xtream M3U sources; keep Spanish-language live TV only, prioritizing Argentine content.

Credentials belong in GitHub Actions Secrets, never in source control. The
output contains stream URLs and must be treated as sensitive.
"""
from __future__ import annotations

import os
import re
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path("dist/lista_clasica.m3u")
TIMEOUT = 20
PROBE_TIMEOUT = 6
PROBE_BYTES = 4096
PROBE_WORKERS = 12
MAX_PROBES = 1500
MAX_BYTES = 80 * 1024 * 1024

# M3U metadata is inconsistent across providers, so filtering uses group/title
# and tvg-country/tvg-language hints. It cannot verify the actual audio track.
ARGENTINA_RE = re.compile(
    r"\b(argentina|argentinos?|arg|buenos\s*aires|caba|cordoba|c[oó]rdoba|"
    r"rosario|santa\s*fe|mendoza|tucum[aá]n|telefe|eltrece|el\s*trece|"
    r"canal\s*9|canal\s*7|tv\s*p[uú]blica|america\s*tv|am[eé]rica\s*tv|"
    r"tn\b|c5n|cronica|cr[oó]nica|ln\+|a24|tyc\s*sports|t y c\s*sports)\b",
    re.I,
)
SPANISH_RE = re.compile(
    r"\b(espanol|español|spanish|castellano|latino|latina|latam|"
    r"es-la|spa|espan[aã]|peliculas|pel[ií]culas|series\s*es|"
    r"deportes\s*es|audio\s*es)\b",
    re.I,
)
EVENT_RE = re.compile(
    r"\b(eventos?|events?|en\s*vivo|live|ppv|deportes?|sports?|"
    r"f[uú]tbol|football|soccer|partidos?|liga|copa|mundial|"
    r"boxeo|tenis|basquet|b[aá]squet|formula\s*1|f1)\b",
    re.I,
)
# Explicit VOD/individual-title groups are not live TV channels.
VOD_RE = re.compile(
    # Xtream suele mezclar TV en vivo, películas y series en el mismo get.php.
    # Los nombres de grupo de VOD deben bloquearse antes de normalizar categorías.
    r"\b(vod|video\s*on\s*demand|on\s*demand|a\s*la\s*carta|"
    r"peliculas?|films?|movies?|series|tv\s*shows?|shows?\s*tv|anime|"
    r"catalogo|cat[aá]logo|descargas?|temporadas?|episodios?|"
    r"full\s*movies?|all\s*movies?|all\s*series?|"
    r"contenido\s*a\s*pedido|estrenos\s*vod)\b",
    re.I,
)
# Linear TV channels dedicated to films/series; these are sorted first.
CINEMA_CHANNEL_RE = re.compile(
    r"\b(hbo|cinemax|cinecanal|space|tnt|universal|warner|sony|axn|"
    r"star\s*channel|fox|fx|paramount|amc|studio\s*universal|"
    r"film\s*&\s*arts|golden|isat|a\s*\&\s*e|a\s*and\s*e|"
    r"mtv\s*live|comedy\s*central)\b",
    re.I,
)
# Categorías normalizadas para que la lista quede ordenada y prolija.
ADULT_RE = re.compile(r"\b(adultos?|adult|xxx|18\+|er[oó]tic[oa]s?|erotica|playboy|venus|hustler|penthouse|private\s*tv|brazzers|dorcel|redlight|sexy\s*hot)\b", re.I)
NEWS_RE = re.compile(r"\b(noticias?|news|informativo|informativos|noticiero|noticieros|24\s*hs|24\s*horas|cnn|c5n|tn\b|a24|ln\+|teleSUR|breaking)\b", re.I)
SPORTS_RE = re.compile(r"\b(deportes?|sports?|f[uú]tbol|football|soccer|tyc|espn|fox\s*sports?|directv\s*sports?|tnt\s*sports?|gol\s*tv|bein\s*sports?|formula\s*1|f1|nba|tenis|boxeo|rugby|b[aá]squet)\b", re.I)
KIDS_RE = re.compile(r"\b(infantil|infantiles|ni[nñ]os|kids|disney\s*junior|cartoon\s*network|nick(elodeon)?|baby\s*tv|dreamworks)\b", re.I)
DOCU_RE = re.compile(r"\b(documentales?|documentary|history|nat\s*geo|national\s*geographic|discovery|animal\s*planet|investigation\s*discovery|discovery\s*science|smithsonian)\b", re.I)
MUSIC_RE = re.compile(r"\b(m[uú]sica|music|mtv|vh1|concert|conciertos?|top\s*music|stingray)\b", re.I)
ENTERTAINMENT_RE = re.compile(r"\b(comedia|comedy|entretenimiento|variedades|reality|cocina|cooking|estilo\s*de\s*vida|lifestyle|fashion|moda)\b", re.I)

CATEGORY_ORDER = {
    "Adultos": 0,
    "Cine y Series": 1,
    "Noticias": 2,
    "Deportes": 3,
    "Infantiles": 4,
    "Documentales": 5,
    "Música": 6,
    "Entretenimiento": 7,
    "General": 8,
    "Eventos": 9,
}

CLEAR_NON_SPANISH_RE = re.compile(
    r"\b(english|eng\b|ingles|ingl[eé]s|fran[cç]ais|french|deutsch|"
    r"german|italiano|italian|portugu[eê]s|portuguese|turk|arabic|"
    r"russian|hindi|japanese|korean)\b",
    re.I,
)
NON_ARG_COUNTRY_RE = re.compile(
    r"\b(brazil|brasil|chile|colombia|col[oô]mbia|peru|per[uú]|"
    r"mexico|m[eé]xico|venezuela|uruguay|paraguay|ecuador|bolivia|"
    r"spain|espa[nñ]a|usa|united states|uk|united kingdom|canada)\b",
    re.I,
)


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
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError("la lista supera el límite de 80 MiB")
    text = data.decode("utf-8-sig", errors="replace")
    if "#EXTM3U" not in text[:2048]:
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


def metadata(entry):
    extinf = entry[0]
    name = extinf.rsplit(",", 1)[-1].strip()
    attrs = {}
    for key, value in re.findall(r'([\w-]+)="([^"]*)"', extinf):
        attrs[key.casefold()] = value
    group = attrs.get("group-title", "")
    country = attrs.get("tvg-country", "")
    language = attrs.get("tvg-language", "")
    extra = " ".join([name, group, country, language] + entry[1:-1])
    return name, attrs, group, country, language, extra


def keep_entry(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    group_name = group.casefold()
    country_name = country.casefold()
    lang = language.casefold()

    # Excluir por tipo de URL Xtream cuando el proveedor lo identifica explícitamente.
    # /live/ se conserva; /movie/ y /series/ nunca deben entrar en TV en vivo.
    stream_path = urllib.parse.urlparse(entry[-1]).path.casefold() if entry else ""
    if re.search(r"/(?:movie|movies|series|vod)(?:/|$)", stream_path):
        return False

    # Excluir grupos de catálogo VOD, aunque el proveedor los llame "Películas",
    # "Series", "Movies", "Anime", etc. antes de normalizar categorías.
    if VOD_RE.search(name + " " + group_name):
        return False
    if re.search(r"\b(S\d{1,2}E\d{1,2}|temporada\s+\d+|episodio\s+\d+)\b", name, re.I):
        return False

    # Explicit language/country metadata takes precedence over loose title hints.
    explicit_non_spanish = bool(CLEAR_NON_SPANISH_RE.search(lang))
    explicit_other_country = bool(country_name and NON_ARG_COUNTRY_RE.search(country_name))
    if explicit_non_spanish or explicit_other_country:
        return False

    is_argentina = bool(ARGENTINA_RE.search(extra))
    is_adult = bool(ADULT_RE.search(name + " " + group_name))
    is_spanish = bool(
        SPANISH_RE.search(extra)
        or re.search(r"\b(es|spa|es-419|spanish|castellano|español)\b", lang)
        or is_argentina
    )
    is_event = bool(EVENT_RE.search(name + " " + group_name))

    # The playlist is Spanish-language only. Recognized Argentine channel
    # names/country tags count as a Spanish hint unless explicit non-Spanish
    # metadata above says otherwise. Unknown-language entries are excluded.
    # Adult channels are retained in their own category even when the provider
    # omits language metadata; explicit non-Spanish metadata is still rejected.
    if not is_spanish and not is_adult:
        return False

    # Retain Argentine content and Spanish-language events/channels; events
    # need not be permanent channel names to qualify.
    return is_argentina or is_spanish or is_adult


def category_for(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    text = f"{name} {group}"
    # Primero noticias/deportes para no clasificar, por ejemplo, TNT Sports como cine.
    if ADULT_RE.search(text):
        return "Adultos"
    if NEWS_RE.search(text):
        return "Noticias"
    if SPORTS_RE.search(text):
        return "Eventos" if re.search(r"\b(eventos?|ppv|partidos?\s*en\s*vivo)\b", text, re.I) else "Deportes"
    # Cine/series en señales lineales; VOD ya se filtra antes.
    if CINEMA_CHANNEL_RE.search(name) or re.search(r"\b(cine|cinema|pel[ií]culas|series|films?|movies?)\b", group, re.I):
        return "Cine y Series"
    if KIDS_RE.search(text):
        return "Infantiles"
    if DOCU_RE.search(text):
        return "Documentales"
    if MUSIC_RE.search(text):
        return "Música"
    if ENTERTAINMENT_RE.search(text):
        return "Entretenimiento"
    if EVENT_RE.search(text):
        return "Eventos"
    return "General"


def set_group_title(entry, category):
    # Cambia solo group-title dentro de EXTINF; conserva tvg-logo, tvg-id y demás datos.
    extinf = entry[0]
    if re.search(r'\bgroup-title="[^"]*"', extinf, re.I):
        extinf = re.sub(r'\bgroup-title="[^"]*"', lambda _: f'group-title="{category}"', extinf, count=1, flags=re.I)
    else:
        comma = extinf.rfind(",")
        if comma >= 0:
            extinf = extinf[:comma] + f' group-title="{category}"' + extinf[comma:]
    return [extinf, *entry[1:]]


def priority(entry):
    category = category_for(entry)
    name = metadata(entry)[0]
    return CATEGORY_ORDER[category], name.casefold()


def entry_name(entry):
    return metadata(entry)[0].casefold()


def probe_stream(url: str):
    """Prueba breve de una URL sin descargar la transmisión completa ni registrar la URL."""
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TVFULL-Stability-Check/1.0",
            "Accept": "*/*",
            "Range": f"bytes=0-{PROBE_BYTES - 1}",
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT) as response:
            status = response.status
            data = response.read(PROBE_BYTES)
            elapsed = time.monotonic() - started
            # Algunos servidores ignoran Range y devuelven 200; se acepta si entregan datos.
            ok = status in (200, 206) and bool(data)
            return {
                "ok": ok,
                "elapsed": elapsed,
                "bytes": len(data),
                "status": status,
                "score": (1 if ok else 0, -elapsed, len(data)),
            }
    except Exception:
        return {
            "ok": False,
            "elapsed": time.monotonic() - started,
            "bytes": 0,
            "status": 0,
            "score": (0, -9999.0, 0),
        }


def main():
    providers = [p for i in range(1, 11) if (p := env_provider(i))]
    if not providers:
        print("Configurá XTREAM_1_URL ... XTREAM_10_URL como Secrets (URL completa), o las variables XTREAM_n_SERVER/USERNAME/PASSWORD.", file=sys.stderr)
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    candidates = []
    total_input = total_kept = 0
    for provider_id, url in providers:
        started = time.monotonic()
        try:
            content = fetch_m3u(provider_id, url)
            entries = parse_entries(content)
            if not entries:
                raise RuntimeError("la lista no contiene canales HTTP(S) válidos")
            kept = 0
            for entry in entries:
                total_input += 1
                if not keep_entry(entry):
                    continue
                kept += 1
                normalized = set_group_title(entry, category_for(entry))
                candidates.append({
                    "provider": provider_id,
                    "entry": normalized,
                    "name": entry_name(normalized),
                    "has_logo": bool(metadata(normalized)[1].get("tvg-logo")),
                })
            total_kept += kept
            print(f"Proveedor {provider_id}: {len(entries)} entradas; {kept} coinciden con los filtros; {time.monotonic()-started:.1f}s")
        except Exception as exc:
            # Never print source URLs or exception strings that may expose credentials.
            print(f"[WARN] Proveedor {provider_id}: no se pudo importar ({type(exc).__name__}).", file=sys.stderr)

    if not candidates:
        print("ERROR: ningún proveedor entregó entradas que coincidan con los filtros; no se genera una lista vacía.", file=sys.stderr)
        return 1

    # Probar primero los canales duplicados, donde elegir la mejor fuente aporta más.
    group_counts = {}
    for candidate in candidates:
        group_counts[candidate["name"]] = group_counts.get(candidate["name"], 0) + 1
    probe_order = sorted(
        range(len(candidates)),
        key=lambda i: (group_counts[candidates[i]["name"]] > 1, group_counts[candidates[i]["name"]]),
        reverse=True,
    )
    selected_indices = probe_order[:MAX_PROBES]
    results = {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        futures = {
            pool.submit(probe_stream, candidates[i]["entry"][-1]): i
            for i in selected_indices
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception:
                results[i] = {"ok": False, "elapsed": 9999.0, "bytes": 0, "status": 0, "score": (0, -9999.0, 0)}

    # Elegir una sola URL por canal: primero las que entregan datos, luego las más rápidas.
    # Si todas fallan o quedaron sin probar, mantener una alternativa como respaldo.
    grouped = {}
    for i, candidate in enumerate(candidates):
        grouped.setdefault(candidate["name"], []).append((i, candidate))
    winners = []
    tested = passed = 0
    for name, options in grouped.items():
        def rank(item):
            i, candidate = item
            result = results.get(i)
            if result is None:
                # Sin prueba por el límite de seguridad: neutral, por detrás de una prueba exitosa.
                return (0, -9998.0, int(candidate["has_logo"]))
            return (*result["score"], int(candidate["has_logo"]))
        winner_i, winner = max(options, key=rank)
        if winner_i in results:
            tested += 1
            if results[winner_i]["ok"]:
                passed += 1
        winners.append((priority(winner["entry"]), winner["entry"]))

    winners.sort(key=lambda item: item[0])
    playlist_lines = [line for _, entry in winners for line in entry]
    temp = OUT.with_suffix(".m3u.tmp")
    temp.write_text("#EXTM3U\n" + "\n".join(playlist_lines) + "\n", encoding="utf-8")
    temp.replace(OUT)

    report = {
        "canales_unicos": len(winners),
        "entradas_candidatas": len(candidates),
        "urls_probadas": len(results),
        "urls_con_datos": sum(1 for result in results.values() if result["ok"]),
        "canales_ganadores_probados_y_correctos": passed,
        "metodo": "prueba HTTP breve con Range; no garantiza reproducción sostenida",
        "limite_pruebas": MAX_PROBES,
        "nota": "No se guardan URLs, usuarios ni contraseñas en este informe.",
    }
    Path("dist/diagnostico_estabilidad.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Lista creada: {OUT} — {len(winners)} canales únicos de {total_input} entradas revisadas.")
    print(f"Estabilidad: {len(results)} URLs probadas; {sum(1 for result in results.values() if result['ok'])} entregaron datos en la prueba breve.")
    print("La mejor alternativa por canal se elige por respuesta válida, tiempo de respuesta y disponibilidad de logo.")
    if len(candidates) > len(results):
        print(f"Nota: se alcanzó el límite de {MAX_PROBES} pruebas; los canales restantes conservan una alternativa sin medir.")
    print("IMPORTANTE: el archivo generado contiene URLs privadas. No lo publiques en un repositorio público.")
    print("Nota: la prueba breve no demuestra estabilidad durante horas ni compatibilidad con todos los reproductores.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
