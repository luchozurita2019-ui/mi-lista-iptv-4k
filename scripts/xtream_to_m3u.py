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
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path("dist/lista_clasica.m3u")
TIMEOUT = 20
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
    r"\b(vod|video\s*on\s*demand|on\s*demand|a\s*la\s*carta|"
    r"peliculas?\s*vod|movies?\s*vod|series?\s*vod|"
    r"catalogo|cat[aá]logo|descargas?|temporadas?|episodios?|"
    r"full\s*movies?|all\s*movies?|all\s*series?)\b",
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
NEWS_RE = re.compile(r"\\b(noticias?|news|informativo|informativos|noticiero|noticieros|24\\s*hs|24\\s*horas|cnn|c5n|tn\\b|a24|ln\\+|teleSUR|breaking)\\b", re.I)
SPORTS_RE = re.compile(r"\\b(deportes?|sports?|f[uú]tbol|football|soccer|tyc|espn|fox\\s*sports?|directv\\s*sports?|tnt\\s*sports?|gol\\s*tv|bein\\s*sports?|formula\\s*1|f1|nba|tenis|boxeo|rugby|b[aá]squet)\\b", re.I)
KIDS_RE = re.compile(r"\\b(infantil|infantiles|ni[nñ]os|kids|disney\\s*junior|cartoon\\s*network|nick(elodeon)?|baby\\s*tv|dreamworks)\\b", re.I)
DOCU_RE = re.compile(r"\\b(documentales?|documentary|history|nat\\s*geo|national\\s*geographic|discovery|animal\\s*planet|investigation\\s*discovery|discovery\\s*science|smithsonian)\\b", re.I)
MUSIC_RE = re.compile(r"\\b(m[uú]sica|music|mtv|vh1|concert|conciertos?|top\\s*music|stingray)\\b", re.I)
ENTERTAINMENT_RE = re.compile(r"\\b(comedia|comedy|entretenimiento|variedades|reality|cocina|cooking|estilo\\s*de\\s*vida|lifestyle|fashion|moda)\\b", re.I)

CATEGORY_ORDER = {
    "Cine y Series": 0,
    "Noticias": 1,
    "Deportes": 2,
    "Infantiles": 3,
    "Documentales": 4,
    "Música": 5,
    "Entretenimiento": 6,
    "General": 7,
    "Eventos": 8,
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

    # Exclude VOD catalogs and individual films/episodes: the target is live TV.
    if VOD_RE.search(group_name):
        return False
    if re.search(r"\b(S\d{1,2}E\d{1,2}|temporada\s+\d+|episodio\s+\d+)\b", name, re.I):
        return False

    # Explicit language/country metadata takes precedence over loose title hints.
    explicit_non_spanish = bool(CLEAR_NON_SPANISH_RE.search(lang))
    explicit_other_country = bool(country_name and NON_ARG_COUNTRY_RE.search(country_name))
    if explicit_non_spanish or explicit_other_country:
        return False

    is_argentina = bool(ARGENTINA_RE.search(extra))
    is_spanish = bool(
        SPANISH_RE.search(extra)
        or re.search(r"\b(es|spa|es-419|spanish|castellano|español)\b", lang)
        or is_argentina
    )
    is_event = bool(EVENT_RE.search(name + " " + group_name))

    # The playlist is Spanish-language only. Recognized Argentine channel
    # names/country tags count as a Spanish hint unless explicit non-Spanish
    # metadata above says otherwise. Unknown-language entries are excluded.
    if not is_spanish:
        return False

    # Retain Argentine content and Spanish-language events/channels; events
    # need not be permanent channel names to qualify.
    return is_argentina or is_spanish


def category_for(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    text = f"{name} {group}"
    # Cine/series en señales lineales primero; VOD ya se filtra antes.
    if CINEMA_CHANNEL_RE.search(name) or re.search(r"\\b(cine|cinema|pel[ií]culas|series|films?|movies?)\\b", group, re.I):
        return "Cine y Series"
    if NEWS_RE.search(text):
        return "Noticias"
    if SPORTS_RE.search(text):
        return "Eventos" if re.search(r"\\b(eventos?|ppv|partidos?\\s*en\\s*vivo)\\b", text, re.I) else "Deportes"
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
    if re.search(r'\\bgroup-title="[^"]*"', extinf, re.I):
        extinf = re.sub(r'\\bgroup-title="[^"]*"', lambda _: f'group-title="{category}"', extinf, count=1, flags=re.I)
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


def main():
    providers = [p for i in range(1, 11) if (p := env_provider(i))]
    if not providers:
        print("Configurá XTREAM_1_URL ... XTREAM_10_URL como Secrets (URL completa), o las variables XTREAM_n_SERVER/USERNAME/PASSWORD.", file=sys.stderr)
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    merged, seen = [], {}
    total_input = total_kept = 0
    for provider_id, url in providers:
        started = time.monotonic()
        try:
            content = fetch_m3u(provider_id, url)
            entries = parse_entries(content)
            if not entries:
                raise RuntimeError("la lista no contiene canales HTTP(S) válidos")
            kept = added = 0
            for entry in entries:
                total_input += 1
                if not keep_entry(entry):
                    continue
                kept += 1
                key = entry_name(entry)
                if not key:
                    key = entry[-1].casefold()
                category = category_for(entry)
                normalized = set_group_title(entry, category)
                if key in seen:
                    # Si otro proveedor trae el mismo canal con logo y el elegido no,
                    # preferimos el que tiene tvg-logo sin duplicar la señal.
                    old_index = seen[key]
                    old_entry = merged[old_index][1]
                    old_has_logo = bool(metadata(old_entry)[1].get("tvg-logo"))
                    new_has_logo = bool(metadata(normalized)[1].get("tvg-logo"))
                    if new_has_logo and not old_has_logo:
                        merged[old_index] = (priority(normalized), normalized)
                    continue
                seen[key] = len(merged)
                merged.append((priority(normalized), normalized))
                added += 1
            total_kept += kept
            print(f"Proveedor {provider_id}: {len(entries)} entradas; {kept} Argentina/español/eventos coincidentes; {added} nuevas; {time.monotonic()-started:.1f}s")
        except Exception as exc:
            # Never print source URLs or exception strings that may expose credentials.
            print(f"[WARN] Proveedor {provider_id}: no se pudo importar ({type(exc).__name__}).", file=sys.stderr)

    if not merged:
        print("ERROR: ningún proveedor entregó entradas que coincidan con los filtros; no se genera una lista vacía.", file=sys.stderr)
        return 1

    temp = OUT.with_suffix(".m3u.tmp")
    merged.sort(key=lambda item: item[0])
    playlist_lines = [line for _, entry in merged for line in entry]
    temp.write_text("#EXTM3U\n" + "\n".join(playlist_lines) + "\n", encoding="utf-8")
    temp.replace(OUT)
    print(f"Lista creada: {OUT} — {len(seen)} entradas únicas de {total_input} revisadas ({total_kept} coincidencias antes de deduplicar).")
    print("Categorías: Cine y Series, Noticias, Deportes, Infantiles, Documentales, Música, Entretenimiento, General y Eventos.")
    print("Logos: se conservan los tvg-logo originales; si hay duplicados, se prefiere la versión que sí trae logo.")
    print("IMPORTANTE: el archivo generado contiene URLs privadas. No lo publiques en un repositorio público.")
    print("Nota: el filtro usa nombres/grupos/metadatos M3U; no puede verificar el idioma real del audio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
