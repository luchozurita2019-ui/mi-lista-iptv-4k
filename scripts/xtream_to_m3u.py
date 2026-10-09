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
import unicodedata
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
    # No bloquear "películas/movies" por sí solo: también puede nombrar canales
    # lineales de cine. El VOD se identifica por su ruta, marcadores explícitos
    # de catálogo/a pedido o episodios individuales.
    r"\b(vod|video\s*on\s*demand|on\s*demand|a\s*la\s*carta|"
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
ADULT_RE = re.compile(r"\b(adultos?|adult|xxx|18\s*\+|porn(?:o|ography)?|porno|er[oó]tic[oa]s?|erotica|playboy|venus|hustler|penthouse|private\s*tv|brazzers|dorcel|red\s*light|sexy\s*hot|naughty|milf|babes?|hentai|sex\s*tv)\b", re.I)
NEWS_RE = re.compile(r"\b(noticias?|news|informativo|informativos|noticiero|noticieros|24\s*hs|24\s*horas|cnn|c5n|tn\b|a24|ln\+|teleSUR|breaking)\b", re.I)
SPORTS_RE = re.compile(r"\b(deportes?|sports?|f[uú]tbol|football|soccer|tyc|espn|fox\s*sports?|directv\s*sports?|tnt\s*sports?|gol\s*tv|bein\s*sports?|formula\s*1|f1|nba|tenis|boxeo|rugby|b[aá]squet)\b", re.I)
KIDS_RE = re.compile(r"\b(infantil|infantiles|ni[nñ]os|kids|disney\s*junior|cartoon\s*network|nick(elodeon)?|baby\s*tv|dreamworks)\b", re.I)
DOCU_RE = re.compile(r"\b(documentales?|documentary|history|nat\s*geo|national\s*geographic|discovery|animal\s*planet|investigation\s*discovery|discovery\s*science|smithsonian)\b", re.I)
MUSIC_RE = re.compile(r"\b(m[uú]sica|music|mtv|vh1|concert|conciertos?|top\s*music|stingray)\b", re.I)
ENTERTAINMENT_RE = re.compile(r"\b(comedia|comedy|entretenimiento|variedades|reality|cocina|cooking|estilo\s*de\s*vida|lifestyle|fashion|moda)\b", re.I)

# Catálogo de nombres canónicos: permite reconocer canales aunque el proveedor
# no marque idioma/país y consolidar variantes como "HBO HD" y "HBO FHD".
# Solo se seleccionan URLs que aparezcan realmente en alguno de los proveedores.
CHANNEL_CATALOG = [
    ("Cine y Series", "HBO", ("hbo",)),
    ("Cine y Series", "HBO 2", ("hbo 2", "hbo2")),
    ("Cine y Series", "HBO Plus", ("hbo plus", "hboplus")),
    ("Cine y Series", "HBO Family", ("hbo family",)),
    ("Cine y Series", "HBO Signature", ("hbo signature",)),
    ("Cine y Series", "HBO Mundi", ("hbo mundi",)),
    ("Cine y Series", "HBO Xtreme", ("hbo xtreme",)),
    ("Cine y Series", "Cinemax", ("cinemax",)),
    ("Cine y Series", "Cinecanal", ("cinecanal",)),
    ("Cine y Series", "Space", ("space",)),
    ("Cine y Series", "TNT", ("tnt",)),
    ("Cine y Series", "TNT Series", ("tnt series",)),
    ("Cine y Series", "Warner Channel", ("warner channel", "warner")),
    ("Cine y Series", "Universal TV", ("universal tv", "studio universal")),
    ("Cine y Series", "Universal Cinema", ("universal cinema",)),
    ("Cine y Series", "Sony Channel", ("sony channel", "sony")),
    ("Cine y Series", "AXN", ("axn",)),
    ("Cine y Series", "Star Channel", ("star channel", "fox channel")),
    ("Cine y Series", "FX", ("fx",)),
    ("Cine y Series", "AMC", ("amc",)),
    ("Cine y Series", "Paramount Network", ("paramount network", "paramount")),
    ("Cine y Series", "Golden", ("golden",)),
    ("Cine y Series", "Golden Edge", ("golden edge",)),
    ("Cine y Series", "A&E", ("a&e", "a and e")),
    ("Cine y Series", "Film & Arts", ("film & arts", "film and arts")),
    ("Cine y Series", "TCM", ("tcm",)),
    ("Cine y Series", "I-Sat", ("i-sat", "isat")),
    ("Cine y Series", "Europa Europa", ("europa europa",)),
    ("Cine y Series", "Lifetime", ("lifetime",)),
    ("Noticias", "TN", ("tn", "todo noticias")),
    ("Noticias", "C5N", ("c5n",)),
    ("Noticias", "A24", ("a24",)),
    ("Noticias", "Crónica TV", ("cronica tv", "crónica tv")),
    ("Noticias", "LN+", ("ln+", "ln mas")),
    ("Noticias", "Canal 26", ("canal 26",)),
    ("Noticias", "TV Pública", ("tv publica", "tv pública")),
    ("Noticias", "América TV", ("america tv", "américa tv")),
    ("Noticias", "Telefe", ("telefe",)),
    ("Noticias", "El Trece", ("eltrece", "el trece")),
    ("Deportes", "TyC Sports", ("tyc sports", "t y c sports")),
    ("Deportes", "ESPN", ("espn",)),
    ("Deportes", "ESPN 2", ("espn 2", "espn2")),
    ("Deportes", "ESPN 3", ("espn 3", "espn3")),
    ("Deportes", "Fox Sports", ("fox sports",)),
    ("Deportes", "TNT Sports", ("tnt sports",)),
    ("Deportes", "DirecTV Sports", ("directv sports", "d sports")),
    ("Deportes", "DeporTV", ("deportv",)),
    ("Infantiles", "Cartoon Network", ("cartoon network",)),
    ("Infantiles", "Nickelodeon", ("nickelodeon", "nick")),
    ("Infantiles", "Disney Channel", ("disney channel",)),
    ("Infantiles", "Disney Junior", ("disney junior",)),
    ("Infantiles", "Discovery Kids", ("discovery kids",)),
    ("Infantiles", "Tooncast", ("tooncast",)),
    ("Documentales", "Discovery Channel", ("discovery channel",)),
    ("Documentales", "Animal Planet", ("animal planet",)),
    ("Documentales", "National Geographic", ("national geographic", "nat geo")),
    ("Documentales", "History", ("history channel", "history")),
    ("Documentales", "Discovery Science", ("discovery science",)),
    ("Música", "MTV", ("mtv",)),
    ("Música", "VH1", ("vh1",)),
    ("Música", "Quiero Música", ("quiero musica", "quiero música")),
    ("Música", "MuchMusic", ("muchmusic",)),
    ("Entretenimiento", "Comedy Central", ("comedy central",)),
    ("Entretenimiento", "E! Entertainment", ("e! entertainment", "e entertainment")),
    ("Entretenimiento", "Food Network", ("food network",)),
    ("Entretenimiento", "El Gourmet", ("el gourmet",)),
    ("Argentina · Noticias", "TN", ("tn", "todo noticias")),
    ("Argentina · Noticias", "C5N", ("c5n",)),
    ("Argentina · Noticias", "A24", ("a24",)),
    ("Argentina · Noticias", "Crónica TV", ("cronica tv", "crónica tv", "cronica")),
    ("Argentina · Noticias", "LN+", ("ln+", "ln mas")),
    ("Argentina · Noticias", "Canal 26", ("canal 26",)),
    ("Argentina · Noticias", "IP Noticias", ("ip noticias",)),
    ("Argentina · Noticias", "TV Pública", ("tv publica", "tv pública", "tvp argentina")),
    ("Argentina · Noticias", "América TV", ("america tv", "américa tv", "america")),
    ("Argentina · Noticias", "Telefe", ("telefe",)),
    ("Argentina · Noticias", "El Trece", ("eltrece", "el trece", "canal 13 argentina")),
    ("Argentina · Noticias", "Net TV", ("net tv",)),
    ("Argentina · Noticias", "Bravo TV", ("bravo tv",)),
    ("Argentina · Noticias", "Canal 9", ("canal 9 argentina", "canal nueve argentina")),
    ("Argentina · Noticias", "El Nueve", ("el nueve", "elnueve", "canal nueve", "canal 9 argentina", "canal nueve argentina")),
    ("Argentina · Noticias", "Canal de la Ciudad", ("canal de la ciudad", "ciudad tv")),
    ("Argentina · Noticias", "Canal 9 Litoral", ("canal 9 litoral",)),
    ("Argentina · Noticias", "Canal 10 Río Negro", ("canal 10 rio negro", "canal 10 río negro")),
    ("Argentina · Noticias", "Canal 7 Neuquén", ("canal 7 neuquen", "canal 7 neuquén")),
    ("Argentina · Noticias", "Canal 7 Chubut", ("canal 7 chubut",)),
    ("Argentina · Noticias", "Canal 11 Formosa", ("canal 11 formosa",)),
    ("Argentina · Noticias", "Canal 12 Misiones", ("canal 12 misiones",)),
    ("Argentina · Noticias", "Canal 9 Resistencia", ("canal 9 resistencia",)),
    ("Argentina · Noticias", "Canal 5 Rosario", ("canal 5 rosario",)),
    ("Argentina · Noticias", "Canal 6 Posadas", ("canal 6 posadas",)),
    ("Argentina · Noticias", "Canal 10 Mar del Plata", ("canal 10 mar del plata",)),
    ("Argentina · Noticias", "Canal 4 Jujuy", ("canal 4 jujuy",)),
    ("Argentina · Noticias", "Canal 11 Ushuaia", ("canal 11 ushuaia",)),
    ("Argentina · Noticias", "Canal 12 Córdoba", ("canal 12 cordoba", "canal 12 córdoba")),
    ("Argentina · Deportes", "TyC Sports", ("tyc sports", "t y c sports")),
    ("Argentina · Deportes", "DeporTV", ("deportv",)),
    ("Argentina · Deportes", "TNT Sports Argentina", ("tnt sports argentina",)),
    ("Argentina · Deportes", "ESPN Argentina", ("espn argentina",)),
    ("Argentina · Deportes", "Fox Sports Argentina", ("fox sports argentina",)),
    ("Argentina · Deportes", "ESPN Premium", ("espn premium",)),
    ("Argentina · Deportes", "ESPN 2", ("espn 2", "espn2")),
    ("Argentina · Deportes", "ESPN 3", ("espn 3", "espn3")),
    ("Argentina · Deportes", "ESPN 4", ("espn 4", "espn4")),
    ("Argentina · Deportes", "ESPN 5", ("espn 5", "espn5")),
    ("Argentina · Deportes", "ESPN 6", ("espn 6", "espn6")),
    ("Argentina · Deportes", "ESPN 7", ("espn 7", "espn7")),
    ("Argentina · Deportes", "Fox Sports 2", ("fox sports 2", "foxsports2")),
    ("Argentina · Deportes", "Fox Sports 3", ("fox sports 3", "foxsports3")),
    ("Argentina · Deportes", "DSports", ("dsports", "d sports")),
    ("Argentina · Deportes", "DSports 2", ("dsports 2", "d sports 2")),
    ("Argentina · Deportes", "DSports 3", ("dsports 3", "d sports 3")),
    ("Argentina · Deportes", "Golf Channel", ("golf channel",)),
    ("Argentina · Deportes", "TyC Sports Internacional", ("tyc sports internacional",)),
    ("Argentina · Deportes", "TyC Sports 2", ("tyc sports 2", "tyc sports interior")),
    ("Argentina · Deportes", "ESPN Premium", ("espn premium",)),
    ("Argentina · Deportes", "ESPN Extra", ("espn extra",)),
    ("Argentina · Deportes", "Fox Sports Premium", ("fox sports premium",)),
    ("Argentina · Deportes", "DeporTV", ("deportv", "depor tv")),
    ("Argentina · Deportes", "Motorplay", ("motorplay",)),
    ("Argentina · Deportes", "Canal Rural", ("canal rural", "el rural")),
    ("Argentina · Deportes", "AFA Play", ("afa play",)),
    ("Argentina · Deportes", "LPF Play", ("lpf play",)),
    ("Argentina · Deportes", "Torneos", ("torneos",)),
    ("Argentina · Deportes", "Polo TV", ("polo tv",)),
    ("Argentina · Deportes", "Canal Showsport", ("showsport", "show sport")),
    ("Argentina · Deportes", "TyC Sports Play", ("tyc sports play",)),
    ("Deportes", "beIN Sports", ("bein sports", "bein sports ñ", "bein sports espanol")),
    ("Deportes", "GolTV", ("gol tv", "goltv")),
    ("Deportes", "Claro Sports", ("claro sports",)),
    ("Deportes", "Win Sports", ("win sports",)),
    ("Deportes", "Eurosport 1", ("eurosport 1", "eurosport")),
    ("Deportes", "Eurosport 2", ("eurosport 2",)),
    ("Deportes", "NBA TV", ("nba tv",)),
    ("Deportes", "NFL Network", ("nfl network",)),
    ("Deportes", "MLB Network", ("mlb network",)),
    ("Deportes", "NHL Network", ("nhl network",)),
    ("Deportes", "UFC", ("ufc", "ufc network")),
    ("Deportes", "Fight Network", ("fight network",)),
    ("Deportes", "Motorvision", ("motorvision",)),
    ("Deportes", "Racing TV", ("racing tv",)),
    ("Deportes", "Tennis Channel", ("tennis channel",)),
    ("Deportes", "Cricket Network", ("cricket network",)),
    ("Argentina · Cultura", "Encuentro", ("canal encuentro", "encuentro")),
    ("Argentina · Cultura", "Pakapaka", ("pakapaka", "paka paka")),
    ("Argentina · Cultura", "Construir TV", ("construir tv",)),
    ("Argentina · Cultura", "Canal Rural", ("canal rural", "el rural")),
    ("Argentina · Entretenimiento", "Ciudad Magazine", ("ciudad magazine",)),
    ("Argentina · Entretenimiento", "KZO", ("kzo", "kzo tv")),
    ("Argentina · Entretenimiento", "Canal 21", ("canal 21 argentina", "canal 21")),
    ("Argentina · Entretenimiento", "Canal Orbe 21", ("orbe 21",)),
    ("Argentina · Entretenimiento", "Televisión Pública Internacional", ("tv publica internacional", "television publica internacional")),
    ("Argentina · Entretenimiento", "El Destape", ("el destape", "eldestape")),
    ("Argentina · Entretenimiento", "Canal E", ("canal e",)),
    ("Argentina · Entretenimiento", "Aire de Santa Fe", ("aire de santa fe",)),
    ("Argentina · Entretenimiento", "Luzu TV", ("luzu tv", "luzu")),
    ("Argentina · Entretenimiento", "OLGA", ("olga streaming", "olga tv")),
    ("Argentina · Entretenimiento", "Bondi Live", ("bondi live", "bondi tv")),
    ("Argentina · Entretenimiento", "Urbana Play", ("urbana play",)),
    ("Argentina · Entretenimiento", "Blender", ("blender", "blender tv")),
    ("Argentina · Entretenimiento", "Gelatina", ("gelatina", "gelatina tv")),
    ("Argentina · Entretenimiento", "La Casa", ("la casa streaming",)),
    ("Argentina · Entretenimiento", "Quiero Música", ("quiero musica", "quiero música")),
    ("Argentina · Entretenimiento", "El Gourmet", ("el gourmet argentina",)),
    ("Argentina · Regionales", "Canal 10 Córdoba", ("canal 10 cordoba", "canal 10 córdoba")),
    ("Argentina · Regionales", "El Doce Córdoba", ("el doce cordoba", "el doce córdoba", "canal 12 cordoba")),
    ("Argentina · Regionales", "Canal 8 Tucumán", ("canal 8 tucuman", "canal 8 tucumán")),
    ("Argentina · Regionales", "Canal 10 Tucumán", ("canal 10 tucuman", "canal 10 tucumán")),
    ("Argentina · Regionales", "Canal 7 Mendoza", ("canal 7 mendoza",)),
    ("Argentina · Regionales", "Canal 3 Rosario", ("canal 3 rosario",)),
]

def _fold_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("&", " and ")
    value = re.sub(r"\b(hd|fhd|uhd|4k|sd|1080p|720p|hevc|h265|h264|latino|argentina|arg)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()

def catalog_match(name: str, group: str = ""):
    # Match a known channel by its actual name, not only by group-title.
    folded = " " + _fold_name(name) + " "
    catalog = sorted(CHANNEL_CATALOG, key=lambda row: (row[0].startswith("Argentina ·"), max(len(_fold_name(a)) for a in row[2])), reverse=True)
    for category, canonical, aliases in catalog:
        for alias in sorted(aliases, key=lambda a: len(_fold_name(a)), reverse=True):
            needle = " " + _fold_name(alias) + " "
            if needle.strip() and needle in folded:
                # Avoid classifying TNT Sports as the movie channel TNT.
                if canonical == "TNT" and " sports " in folded:
                    continue
                if canonical == "HBO" and re.search(r"\bhbo\s*(2|plus|family|signature|mundi|xtreme)\b", name, re.I):
                    continue
                return category, canonical
    return None

CATEGORY_ORDER = {
    "Argentina · Noticias": 0,
    "Argentina · Deportes": 1,
    "Argentina · Cultura": 2,
    "Argentina · Entretenimiento": 3,
    "Argentina · Regionales": 4,
    "Cine y Series": 5,
    "Noticias": 6,
    "Deportes": 7,
    "Infantiles": 8,
    "Documentales": 9,
    "Música": 10,
    "Entretenimiento": 11,
    "Eventos": 12,
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

    # La coincidencia con un canal conocido permite incluirlo aunque el proveedor
    # omita país/idioma (caso típico de HBO, Space, Cinecanal, etc.).
    known = catalog_match(name, group)
    # Explicit language/country metadata still blocks a clearly foreign-language
    # feed unless its channel is a recognized Argentine/Spanish service.
    explicit_non_spanish = bool(CLEAR_NON_SPANISH_RE.search(lang))
    explicit_other_country = bool(country_name and NON_ARG_COUNTRY_RE.search(country_name))
    if (explicit_non_spanish or explicit_other_country) and not known:
        return False

    is_argentina = bool(ARGENTINA_RE.search(extra))
    is_adult = bool(ADULT_RE.search(name + " " + group_name))
    # Esta lista es exclusivamente de TV en vivo; no se admite contenido adulto.
    if is_adult:
        return False
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
    # Aceptar los canales del catálogo reconocido (incluidos los lineales de cine,
    # deportes, infantiles y documentales), además de canales identificados como argentinos.
    # Nunca incluir VOD ni adultos: esos filtros se aplican arriba.
    if not known and not is_argentina:
        return False
    return True


def category_for(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    text = f"{name} {group}"
    known = catalog_match(name, group)
    if known and known[0].startswith("Argentina ·"):
        return known[0]
    if known and ARGENTINA_RE.search(f"{name} {group} {attrs.get('tvg-country', '')}"):
        return known[0] if known[0] != "General" else "Argentina · Regionales"
    # Primero noticias/deportes para no clasificar, por ejemplo, TNT Sports como cine.
    if ADULT_RE.search(text):
        return "Argentina · Canales identificados"  # normalmente se filtra antes
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
    # No crear el grupo genérico "General".
    return "Argentina · Canales identificados"


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


def set_display_name(entry):
    """Fija el nombre visible del canal al nombre canónico del catálogo."""
    name, attrs, group, country, language, extra = metadata(entry)
    known = catalog_match(name, group)
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
    comma = extinf.rfind(",")
    if comma >= 0:
        extinf = extinf[:comma + 1] + known[1]
    return [extinf, *entry[1:]]


def priority(entry):
    category = category_for(entry)
    name = metadata(entry)[0]
    return CATEGORY_ORDER.get(category, 13), name.casefold()


def entry_name(entry):
    name, attrs, group, country, language, extra = metadata(entry)
    known = catalog_match(name, group)
    if known:
        return "catalog:" + _fold_name(known[1])
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
        comma = extinf.rfind(",")
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


def probe_stream(url: str):
    """Prueba breve de la URL y descarta respuestas HTML/de error que simulan un stream."""
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TVFULL-Stream-Check/1.1",
            "Accept": "video/*,application/vnd.apple.mpegurl,application/x-mpegURL,application/octet-stream,*/*",
            "Range": f"bytes=0-{PROBE_BYTES - 1}",
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT) as response:
            status = response.status
            data = response.read(PROBE_BYTES)
            content_type = response.headers.get("Content-Type", "").casefold()
            elapsed = time.monotonic() - started
            sample = data[:1024].lstrip().casefold()
            # HTTP 200 no alcanza: algunos proveedores devuelven una página HTML
            # de error/autenticación en lugar del video.
            error_page = (
                sample.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
                or any(token in sample for token in (
                    b"invalid username", b"invalid password", b"unauthorized",
                    b"access denied", b"not found", b"account expired",
                    b"stream not found", b"server error",
                ))
            )
            hls_manifest = sample.startswith(b"#extm3u")
            ts_packet = len(data) >= 377 and data[0] == 0x47 and data[188] == 0x47 and data[376] == 0x47
            media_type = any(token in content_type for token in (
                "video/", "audio/", "mpegurl", "mp2t", "octet-stream", "mp4",
            ))
            ok = status in (200, 206) and bool(data) and not error_page and (hls_manifest or ts_packet or media_type or not content_type)
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
                normalized = set_display_name(set_group_title(entry, category_for(entry)))
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

    # Rellenar logos faltantes con archivos existentes del catálogo público.
    # Se conserva siempre el logo del proveedor si ya lo trae.
    logo_manifest = fetch_logo_manifest()
    logos_added = 0
    for candidate in candidates:
        entry = candidate["entry"]
        name, attrs, group, country, language, extra = metadata(entry)
        if not attrs.get("tvg-logo"):
            known = catalog_match(name, group)
            logo = find_logo(known[1] if known else name, logo_manifest)
            if logo:
                candidate["entry"] = set_logo_if_missing(entry, logo)
                candidate["has_logo"] = True
                logos_added += 1

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
    # Validación final: impedir publicar una lista vacía, adulta o con entradas VOD.
    final_entries = parse_entries("\\n".join(playlist_lines))
    if not final_entries:
        print("ERROR: la lista final quedó vacía; se cancela la publicación.", file=sys.stderr)
        return 1
    invalid_final = []
    for entry in final_entries:
        final_name, final_attrs, final_group, *_ = metadata(entry)
        final_path = urllib.parse.urlparse(entry[-1]).path.casefold()
        if ADULT_RE.search(final_name + " " + final_group):
            invalid_final.append("adulto")
        if VOD_RE.search(final_name + " " + final_group) or re.search(r"/(?:movie|movies|series|vod)(?:/|$)", final_path):
            invalid_final.append("vod")
        if final_group == "General":
            invalid_final.append("General")
    if invalid_final:
        print("ERROR: validación final detectó entradas prohibidas; no se publica la lista.", file=sys.stderr)
        return 1
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
        "logos_completados": logos_added,
        "proveedores_configurados": len(providers),
        "nota": "No se guardan URLs, usuarios ni contraseñas en este informe.",
    }
    Path("dist/diagnostico_estabilidad.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Lista creada: {OUT} — {len(winners)} canales únicos de {total_input} entradas revisadas.")
    print(f"Proveedores configurados: {len(providers)}/10; logos completados: {logos_added}.")
    print(f"Estabilidad: {len(results)} URLs probadas; {sum(1 for result in results.values() if result['ok'])} entregaron datos en la prueba breve.")
    print("La mejor alternativa por canal se elige por respuesta válida, tiempo de respuesta y disponibilidad de logo.")
    if len(candidates) > len(results):
        print(f"Nota: se alcanzó el límite de {MAX_PROBES} pruebas; los canales restantes conservan una alternativa sin medir.")
    print("IMPORTANTE: el archivo generado contiene URLs privadas. No lo publiques en un repositorio público.")
    print("Nota: la prueba breve no demuestra estabilidad durante horas ni compatibilidad con todos los reproductores.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
