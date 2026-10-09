"""Offline, reviewed channel metadata for the Argentine pay-TV lineup.

No stream URL is supplied by this catalog. Provider transport identities remain
separate from canonical channel names; similarly named foreign feeds are rejected.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data/catalogo_argentina.json"
CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
CHANNELS = CATALOG["channels"]
CATEGORY_ORDER = {category: position for position, category in enumerate((
    "TV · Ficción", "Argentina · Deportes", "Argentina · Nacionales",
    "Argentina · Noticias", "Argentina · Infantiles", "Argentina · Documentales",
    "Argentina · Entretenimiento", "Argentina · Música", "Argentina · Cultura",
    "Argentina · Regionales"))}
PREMIUM_PACKS = {"HBO", "Universal+", "Fútbol"}

COUNTRIES = {
    "ar": "AR", "arg": "AR", "argentina": "AR", "argentinos": "AR",
    "us": "US", "usa": "US", "united states": "US", "estados unidos": "US",
    "br": "BR", "bra": "BR", "brasil": "BR", "brazil": "BR",
    "cl": "CL", "chl": "CL", "chile": "CL",
    "co": "CO", "col": "CO", "colombia": "CO",
    "mx": "MX", "mex": "MX", "mexico": "MX",
    "uy": "UY", "uru": "UY", "uruguay": "UY",
    "py": "PY", "paraguay": "PY", "pe": "PE", "peru": "PE",
    "ec": "EC", "ecuador": "EC", "ve": "VE", "venezuela": "VE",
    "bo": "BO", "bolivia": "BO", "gt": "GT", "gua": "GT",
    "gtm": "GT", "guatemala": "GT", "cr": "CR", "costa rica": "CR",
    "es": "ES", "esp": "ES", "spain": "ES", "espana": "ES",
    "uk": "GB", "gb": "GB", "united kingdom": "GB", "england": "GB",
    "ca": "CA", "canada": "CA", "pt": "PT", "portugal": "PT",
    "it": "IT", "italia": "IT", "fr": "FR", "france": "FR",
    "de": "DE", "germany": "DE", "cu": "CU", "cuba": "CU",
    "do": "DO", "dominicana": "DO", "pa": "PA", "panama": "PA",
    "hn": "HN", "honduras": "HN", "sv": "SV", "salvador": "SV",
    "ni": "NI", "nicaragua": "NI", "pr": "PR", "puerto rico": "PR",
}


def ascii_text(value):
    value = str(value)
    # A common double UTF-8 decode (AMÃ©RICA) is recoverable without guessing.
    for codec in ("latin1", "cp1252"):
        if any(marker in value for marker in ("Ã", "Â", "â")):
            try:
                value = value.encode(codec).decode("utf-8")
            except (UnicodeError, LookupError):
                pass
    value = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", value).casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def country_hints(value, explicit=False):
    text = ascii_text(value)
    found = set()
    if explicit:
        # country metadata and delimited group tags, not arbitrary city names.
        for token in re.split(r"[|,;/\[\]()]+", text):
            token = token.strip(" *:-_")
            if token in COUNTRIES:
                found.add(COUNTRIES[token])
    keys = "|".join(re.escape(key) for key in sorted(COUNTRIES, key=len, reverse=True))
    prefix = re.match(r"^\s*[\[(]?\s*(" + keys + r")\s*(?:\*+)?\s*(?:[|:;\]/-]|\bi\b)", text)
    if prefix:
        found.add(COUNTRIES[prefix[1]])
    suffix = re.search(r"[|\[(]\s*(" + keys + r")\s*[\])]?(?:\s*(?:hd|fhd|sd))?\s*$", text)
    if suffix:
        found.add(COUNTRIES[suffix[1]])
    # Full country words used as feed qualifiers, including ESPN Brasil.
    for token, code in COUNTRIES.items():
        if len(token) >= 4 and re.search(r"\b" + re.escape(token) + r"\b", text):
            found.add(code)
    return found


def fold_name(value):
    value = ascii_text(value).replace("⚽", "o").replace("&", " and ").replace("+", " plus ")
    # Restricted broadcaster-specific substitutions, never arbitrary fuzzy edit
    # distance that could turn an event or a different numbered feed into a channel.
    value = re.sub(r"(?<!\w)(?:e\$pn|\$spn|\$pn|espn)(?=\W|\d|$)", "espn", value)
    value = re.sub(r"\bf0x\b", "fox", value)
    value = re.sub(r"(?<!\w)\$p[o0]rts(?=\W|\d|$)|\bsp0rts\b", "sports", value)
    value = re.sub(r"\bpr3mium\b", "premium", value)
    value = re.sub(r"\b(hd|fhd|uhd|4k|sd|hdr|1080[pi]|720[pi]|480[pi]|2160[pi]|"
                   r"hevc|h265|h264|latino|latina|latam|argentina|arg|ar)\b", " ", value)
    value = re.sub(r"\b(?:op(?:c|cion)?|option)\s*\d+\b", " ", value)
    value = re.sub(r"\b(?:h|e)\s*265\b|\b60\s*fps\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


_ALIASES = {}
for _channel in CHANNELS:
    for _alias in [_channel["name"], *_channel["aliases"]]:
        _key = fold_name(_alias)
        if _key in _ALIASES and _ALIASES[_key]["name"] != _channel["name"]:
            raise ValueError("ambiguous_catalog_alias: " + _alias)
        _ALIASES[_key] = _channel
BY_NAME = {channel["name"]: channel for channel in CHANNELS}


def resolve(name, group="", country="", language=""):
    hints = country_hints(name) | country_hints(group, True) | country_hints(country, True)
    if hints - {"AR"}:
        return None
    lang = ascii_text(language)
    if lang and re.search(r"\b(en|eng|english|ingles|pt|por|portuguese|portugues|"
                          r"fr|fra|french|francais|de|deu|german|italian|italiano)\b", lang):
        return None
    folded = fold_name(name)
    # strip an Argentine prefix variant sometimes emitted as ARG I (not a name).
    folded = re.sub(r"^i\s+", "", folded) if re.match(r"^\s*arg\s+i\b", ascii_text(name)) else folded
    row = _ALIASES.get(folded)
    if row is None:
        return None
    # Generic numbered terrestrial names need an actual Argentine tag. Their
    # mere number never imports Canal 7 Guatemala or Canal 9 Mexico.
    if folded in {"canal 9", "canal nueve", "canal 13"} and "AR" not in hints:
        return None
    return row


def catalog_match(name, group=""):
    row = resolve(name, group)
    return (row["category"], row["name"]) if row else None


def catalog_logo(name):
    row = BY_NAME.get(name)
    return row.get("logo", "") if row else ""
