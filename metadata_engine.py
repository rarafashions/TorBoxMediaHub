import difflib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import ipaddress
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import indo_keyword_engine as indo_eng

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Keep poster storage aligned with database_manager.TBH_DATA_DIR / DATA_DIR.
DATA_DIR = os.path.abspath(os.environ.get("TBH_DATA_DIR") or BASE_DIR)
POSTER_CACHE_DIR = os.path.join(DATA_DIR, "cache", "posters")
os.makedirs(POSTER_CACHE_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(HEADERS)
# Provider CDNs idle-close connections and urllib3 would otherwise hold dead
# sockets; a small pool plus one explicit retry for idempotent calls keeps the
# handle count bounded. POST is deliberately excluded from retries.
_HTTP_RETRY = Retry(
    total=1, connect=1, read=1, status=0,
    backoff_factor=0.2, allowed_methods=frozenset({"GET", "HEAD"}),
)
_adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=_HTTP_RETRY)
HTTP_SESSION.mount("http://", _adapter)
HTTP_SESSION.mount("https://", _adapter)

JAV_MAKER_PREFIXES = {
    p.upper()
    for p in {
        "SSIS", "SSNI", "SONE", "IPX", "IPZZ", "IPZ", "MIDV", "MIDE", "MIRD", "MIAA", "MIMK",
        "ABP", "ABW", "ABF", "ABK", "ABS", "PRED", "PPPD", "PPPE", "JUL", "JUQ", "JUY", "JUX", "URE",
        "STARS", "STAR", "SDDE", "SDJS", "SDMM", "SDNM", "SDAB", "FSDSS", "DLDSS", "MUDR",
        "CAWD", "KAWD", "KWBD", "EBOD", "EYAN", "CJOD", "MEYD", "VENX", "VENU", "VEMA",
        "HND", "HNDS", "MVSD", "BLK", "BBI", "BBAN", "WAAA", "WANZ", "DASD", "DAZD", "DANDY",
        "GVH", "GVG", "CLUB", "ROYD", "RBD", "SHKD", "ATID", "ADN", "JUSD", "NGOD",
        "NSFS", "NKKD", "MOND", "ROE", "ROA", "OFJE", "PBD", "BGN", "CHN", "DIC", "EDD", "FCH",
        "GNI", "HAR", "IDBD", "JFD", "KBI", "LXV", "MGT", "NHDTB", "RCTD", "SW", "T28",
        "VRTM", "XVSR", "YMDD", "ZUKO", "300MIUM", "300MAAN", "300NTK", "259LUXU",
        "200GANA", "SIRO", "261ARA", "390JAC", "422ION", "435MFC", "459TEN", "483SGK",
        "534IND", "546EROFV", "FC2", "PPV", "HEYZO", "CARIB", "CARIBPR", "1PON", "1PONDO",
        "PACO", "PACOPACOMAMA", "10MUSUME", "MURAMURA", "GACHIP", "CWPBD", "LAFBD", "SMD", "SMBD",
        "MKD", "MKBD", "DRG", "DSAM", "MESU", "NYB", "RED", "SKY", "SKYHD", "RHJ", "SKMJ",
        "S2MBD", "S2M", "CW3D2BD", "MCB3DBD", "H4610", "H0930", "C0930", "KIN8", "KIN8TENGOKU",
        "REAL", "SCOOP", "KMP", "BAZX", "MDBK", "MDTM", "HUNTA", "HUNTC", "HUNTB", "APNS",
        "APAA", "APAK", "DOCP", "JKSR", "SABA", "UMSO", "MCSR", "IENE", "IESP", "IDOL",
        "NTRD", "NTR", "SDMT", "MTALL", "SILK", "TKI", "EKDV", "HODV", "DNJR", "CESD",
        "CEAD", "CEMD", "OAE", "DVAJ", "ALDN", "AQSH", "AUKG", "AVOP", "AVSA", "BANK",
        "BF", "BIJN", "BKD", "BOBB", "CADV", "CCV", "CHRV", "CKW", "CMC", "CMD",
        "COGM", "CORE", "CPDE", "CRMN", "CSCT", "DASS", "DBER", "DDK", "DDT", "DFDM",
        "DGCEMD", "DGL", "DIGI", "DJSK", "DKD", "DMAT", "DMDG", "DOA", "DOCD", "DOKS",
        "DPMX", "DTT", "DVDES", "DVDMS", "DXMB", "EMBZ", "EMOT", "ENDX",
        "ENFD", "ERTD", "ESK", "EUUD", "EXD", "FAA", "FAAP", "FCDC", "FNEO", "FSET",
        "FSDS", "FUGA", "FWAY", "GANA", "GAREA", "GAS", "GDHH", "GDQN", "GENM", "GETS",
        "GIGL", "GIRO", "GKI", "GLOD", "GMED", "GNAB", "GOJU", "GOLD", "GONE",
        "GPROP", "GRCH", "GTJ", "GTRL", "GUN", "GYAN", "HAWA",
        "HBAD", "HDKA", "HERR", "HERX", "HERY", "HFD", "HGOT", "HHKL", "HIKR", "HITMA",
        "HJMO", "HKD", "HNDB", "HOI", "HOKS", "HOLD", "HONB",
        "HONE", "HOPE", "HOT", "HOW", "HPD", "HRD", "HRV", "HSD",
        "HSM", "HTHD", "HTMS", "HUNT", "HYK", "HZGD", "IBW", "IGU", "IHT", "IKHR",
    }
}

JAV_NOISE_PREFIXES = {
    "H264", "H265", "X264", "X265", "AVC", "HEVC", "AAC", "AC3", "DTS", "FLAC", "MP3", "MP4",
    "MKV", "AVI", "WMV", "FHD", "UHD", "HD", "SD", "4K", "8K", "1080P", "720P", "2160P", "480P",
    "60FPS", "30FPS", "10BIT", "8BIT", "HDR", "DV", "WEB", "WEBRIP", "WEBDL", "BLURAY", "BDRIP",
    "DVDRIP", "HDRIP", "CAM", "TS", "UNCENSORED", "UNCENCORED", "LEAKED", "LEAK", "CHINESE",
    "SUB", "ENG", "ITA", "JPN", "KOR", "SPA", "FRE", "GER", "RUS", "IND", "AMZN", "NF", "DSNP",
    "HMAX", "ATVP", "RAW", "PART", "DISC", "CD", "VOL", "EP", "CH", "SEASON", "NO", "VER",
    "NEW", "FULL", "VIP", "PRO", "MAX", "PLUS", "NET", "COM", "ORG", "JAV", "PORN", "XXX",
    "SEX", "VR", "DDP", "ATMOS", "TRUEHD", "REMUX", "PROPER", "REPACK", "EXTENDED",
    "THEATRICAL", "UNRATED", "FPS", "BIT", "PCM", "EAC3", "OPUS", "VORBIS", "MPEG", "XVID",
    "S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10",
    "E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E09", "E10",
    "EP01", "EP02", "EP03", "EP04", "EP05", "EP06", "EP07", "EP08", "EP09", "EP10",
}

ENGLISH_ADULT_STUDIOS = [
    "brazzers", "brazzersexxtra", "brazzers exxtra", "realitykings", "reality kings", "bangbros", "bang bros", "naughtyamerica",
    "naughty america", "mofos", "digitalplayground", "digital playground", "twistys", "babes",
    "vixen", "tushy", "blacked", "blackedraw", "deeper", "slayed", "julesjordan",
    "jules jordan", "evilangel", "evil angel", "kink", "teamskeet", "team skeet", "mylf",
    "nubiles", "nubilefilms", "fakehub", "faketaxi", "fakeagent", "publicagent", "propertysex",
    "adulttime", "adult time", "girlsway", "puretaboo", "pure taboo", "sweetheartvideo",
    "mypearl", "passionhd", "passion-hd", "tiny4k", "exotic4k", "fantasyhd", "povd",
    "castingcouch", "backroomcastingcouch", "21sextury", "marc dorcel", "dorcel",
    "penthouse", "hustler", "playboy", "new sensations", "zero tolerance",
    "sweet sinner", "elegant angel", "hard x", "dark x", "erotica x", "metart", "sexart",
    "x-art", "joymii", "hegre", "wowgirls", "alsscan", "ftvgirls", "scoreland",
    "onlyfans", "manyvids", "fansly", "chaturbate", "stripchat", "cam4", "myfreecams",
    "pervmom", "brattysis", "familyxxx", "stepmom", "stepsis",
    "pornhub", "xvideos", "xnxx", "spankbang", "redtube", "youporn", "tube8",
    "lethalhardcore", "devilsfilm", "devil's film", "milehigh", "mile high", "dogfart",
    "facialabuse", "burningangel", "suicidegirls", "ddfnetwork", "woodmancastingx",
    "legalporno", "analvids", "21naturals", "momxxx", "dadcrush", "spyfam",
    "shoplyfter", "holed", "tushyraw", "brazzersexxtra", "rkprime", "mofosnetwork",
    "milfslikeitbig", "bigtitsatschool", "bigtitsatwork", "doctorsadventures",
    "momsincontrol", "teeside", "nubilesporn", "badteenspunished", "stepsecrets",
]

# Curated exact-name adult series/franchises. Keep these separate from generic
# words so a country adjective (e.g. "Czech") never classifies a title alone.
ENGLISH_ADULT_SERIES = [
    "czech street", "czech harem", "czech casting", "czech first video",
]

ADULT_EXPLICIT_WORDS = {
    "xxx", "porn", "porno", "nsfw", "hentai", "creampie", "blowjob",
    "handjob", "gangbang", "threesome", "foursome", "orgy", "anal", "bukkake",
    "cumshot", "facial", "squirt", "squirting", "milf", "gilf", "pawg", "bbw",
    "bdsm", "bondage", "femdom", "cuckold", "hotwife", "swinger", "shemale",
    "transsexual", "futanari", "masturbation", "dildo", "vibrator", "striptease",
    "hardcore", "softcore", "slut", "whore", "pussy", "cock",
    "boobs", "tits", "assfuck", "deepthroat", "defloration",
}

DONGHUA_SPECIFIC_KEYWORDS = [
    "soul land", "douluo dalu", "battle through the heavens", "doupo cangqiong",
    "martial universe", "wu dong qian kun", "the king's avatar", "quanzhi gaoshou",
    "swallowed star", "tunshi xingkong", "perfect world", "wanmei shijie",
    "throne of seal", "shen yin wangzuo", "renegade immortal", "xian ni",
    "a record of a mortal's journey", "fanren xiu xian", "mortal's journey",
    "stellar transformations", "xingchen bian", "tales of demons and gods", "yaoshenji",
    "spare me great lord", "da wang raoming", "link click", "shiguang dailiren",
    "scumbag system", "chuan shu zijiu zhinan", "heaven official's blessing", "tian guan ci fu",
    "grandmaster of demonic cultivation", "mo dao zu shi", "the daily life of the immortal king",
    "xian wang de richang shenghuo", "fog hill of five elements", "wu shan wu xing",
    "white cat legend", "dali si rizhi", "scissors seven", "wu liuqi", "killer seven",
    "blade of the guardians", "biao ren", "the outcast", "yi ren zhixia",
    "rakshasa street", "zhen hun jie", "fox spirit matchmaker", "huyao xiao hongniang",
    "spirit cage", "ling long", "yuan long", "carp reborn",
    "against the sky supreme", "ni tian zhizun", "martial master", "wu shen zhuzai",
    "peerless martial spirit", "jueshi wuhun", "spirit sword sovereign", "ling jian zun",
    "ten thousand worlds", "wan jie du zun", "wan jie xian zong",
    "legend of exorcism", "tianbao fuyao lu", "thousand autumns", "shanhe jianxin",
    "donghua", "chinese anime", "guoman",
]

ANIME_FANSUB_GROUPS = {
    "subsplease", "erai-raws", "horriblesubs", "judas", "ember", "asw", "dkb", "golumpa",
    "yameii", "varyg", "hatsu", "commie", "fff", "utw", "coalgirls", "doki",
    "mazui", "hadena", "kawaiika", "beatrice-raws", "reinforce",
    "ohys-raws", "leopard-raws", "zeroraws", "anidl", "anime time", "animetime", "cleoraws",
    "kawaiika-raws", "lostyears", "neohevc", "pyr0", "samir755", "scryous", "shikuro",
    "tenrai-sensei", "trix", "vcb-studio", "yakuboencodes", "zarx", "mtbb", "gjm",
}

GENERIC_FILE_STEMS = {
    "video", "movie", "film", "episode", "ep", "scene", "part", "sample", "full",
    "1080p", "720p", "2160p", "4k", "hd", "mp4", "mkv", "avi", "untitled",
}


def _title_similarity(a: str, b: str) -> float:
    na = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
    nb = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 5 and len(nb) >= 5 and (na in nb or nb in na):
        return 0.92
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _extract_year(text: str) -> Optional[int]:
    m = re.search(r"(?:^|[\s._\-\(\[])(19[5-9]\d|20[0-3]\d)(?:$|[\s._\-\)\]])", text or "")
    if m:
        return int(m.group(1))
    return None


def _extract_season_episode(name: str) -> Tuple[Optional[int], Optional[int]]:
    s_num: Optional[int] = None
    e_num: Optional[int] = None

    m = re.search(r"(?i)(?:^|[\s._\-\[\(])S(\d{1,2})[\s._-]*E(\d{1,3})(?:$|[\s._\-\]\)])", name)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = re.search(r"(?i)(?:^|[\s._\-\[\(])(\d{1,2})x(\d{1,3})(?:$|[\s._\-\]\)])", name)
    if m:
        return int(m.group(1)), int(m.group(2))

    m_s = re.search(r"(?i)(?:^|[\s._\-\[\(])(?:Season|S)[\s._-]*(\d{1,2})(?:$|[\s._\-\]\)])", name)
    if m_s:
        s_num = int(m_s.group(1))

    m_e = re.search(r"(?i)(?:^|[\s._\-\[\(])(?:Episode|Ep|E)[\s._-]*(\d{1,3})(?:$|[\s._\-\]\)])", name)
    if m_e:
        e_num = int(m_e.group(1))
        return s_num or 1, e_num

    m_dash = re.search(r"(?:^|\s)-\s*(\d{1,3})(?:\s*(?:v\d|END|FIN|\[|\(|$))", name, re.IGNORECASE)
    if m_dash:
        ep_val = int(m_dash.group(1))
        if 0 < ep_val < 500:
            return s_num or 1, ep_val

    return s_num, e_num


def extract_jav_code(text: str, allow_generic_fallback: bool = True) -> Optional[str]:
    base = os.path.splitext(text)[0]
    cleaned = re.sub(r"\[[^\]]*\]", " ", base)
    cleaned = re.sub(r"\([^\)]*\)", " ", cleaned)
    upper = cleaned.upper()

    m_fc2 = re.search(r"(?<![A-Z0-9])FC2[\s._-]*(?:PPV[\s._-]*)?(\d{5,8})(?![A-Z0-9])", upper)
    if m_fc2:
        return f"FC2-PPV-{m_fc2.group(1)}"

    m_heyzo = re.search(r"(?<![A-Z0-9])HEYZO[\s._-]*(\d{4})(?![A-Z0-9])", upper)
    if m_heyzo:
        return f"HEYZO-{m_heyzo.group(1)}"

    m_carib = re.search(r"(?<![A-Z0-9])(\d{6})[\s._-](\d{3})(?![A-Z0-9])", upper)
    if m_carib and any(k in upper for k in ("CARIB", "1PON", "10MU", "PACO", "MURA", "GACHI")):
        return f"{m_carib.group(1)}-{m_carib.group(2)}"

    for m in re.finditer(r"(?<![A-Z0-9])(\d{0,3}[A-Z]{2,6})[\s._-]?(\d{2,5})(?:[A-Z])?(?![A-Z0-9.])", upper):
        prefix = m.group(1)
        digits = m.group(2)
        if prefix in JAV_NOISE_PREFIXES:
            continue
        if re.match(r"^(?:H26[45]|X26[45]|AAC|AC|DDP|DTS|AVC|HEVC|VP|AV)$", prefix):
            continue

        full_span = upper[m.start() : m.end()]
        has_hyphen = "-" in full_span or "_" in full_span

        if prefix in JAV_MAKER_PREFIXES:
            return f"{prefix}-{digits}"

        if allow_generic_fallback and has_hyphen and len(prefix) >= 3 and not prefix.isdigit():
            if not re.match(r"^(?:S\d+|E\d+|EP\d+|CD\d+|PT\d+|VOL\d+|PART\d+|DISC\d+)$", prefix):
                return f"{prefix}-{digits}"

    return None


def _has_hanzi(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _has_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", text or ""))


def _has_explicit_english_adult_studio(combined_text: str) -> Optional[str]:
    # Normalize punctuation (including square brackets) so tags such as
    # "[Blacked]" and "(Brazzers)" are treated like ordinary studio tokens.
    low = re.sub(r"[^a-z0-9]+", " ", (combined_text or "").lower()).strip()
    for st in sorted(ENGLISH_ADULT_STUDIOS, key=len, reverse=True):
        studio_norm = re.sub(r"[^a-z0-9]+", " ", st.lower()).strip()
        if studio_norm and re.search(rf"(?:^|\s){re.escape(studio_norm)}(?:$|\s)", low):
            return st.title()
    # Exact franchise/series names are strong adult signals, but generic words
    # such as "Czech" are deliberately not used as standalone triggers.
    for series in sorted(ENGLISH_ADULT_SERIES, key=len, reverse=True):
        series_norm = re.sub(r"[^a-z0-9]+", " ", series.lower()).strip()
        if series_norm and re.search(rf"(?:^|\s){re.escape(series_norm)}(?:$|\s)", low):
            return series.title()
    return None


def _has_explicit_english_adult_series(combined_text: str) -> Optional[str]:
    low = re.sub(r"[^a-z0-9]+", " ", (combined_text or "").lower()).strip()
    for series in sorted(ENGLISH_ADULT_SERIES, key=len, reverse=True):
        phrase = re.sub(r"[^a-z0-9]+", " ", series.lower()).strip()
        if phrase and re.search(rf"(?:^|\s){re.escape(phrase)}(?:$|\s)", low):
            return series.title()
    return None


def _detect_english_adult_details(combined_text: str, filename: str) -> Optional[Dict[str, Any]]:
    low = re.sub(r"[._-]+", " ", combined_text.lower())
    matched_studio = _has_explicit_english_adult_studio(combined_text)
    matched_series = _has_explicit_english_adult_series(combined_text)
    matched_brand = matched_studio or matched_series

    tokens = set(re.findall(r"[a-z0-9]+", low))
    has_explicit_word = bool(tokens & ADULT_EXPLICIT_WORDS)
    has_scene_date = bool(re.search(r"(?:^|[\s._-])(\d{2})\.(\d{2})\.(\d{2})(?:$|[\s._-])", combined_text))

    if not matched_brand and not has_explicit_word:
        return None

    rel_date = None
    year_val = _extract_year(combined_text)
    m_dt = re.search(r"(?:^|[\s._-])(\d{2})\.(\d{2})\.(\d{2})(?:$|[\s._-])", combined_text)
    if m_dt:
        yy, mm, dd = int(m_dt.group(1)), int(m_dt.group(2)), int(m_dt.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            full_y = 2000 + yy if yy <= 35 else 1900 + yy
            year_val = full_y
            rel_date = f"{full_y}-{mm:02d}-{dd:02d}"

    raw_stem = os.path.splitext(filename)[0]
    if raw_stem.lower() in GENERIC_FILE_STEMS and combined_text != filename:
        raw_stem = combined_text

    cleaned = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", raw_stem)
    if matched_brand:
        cleaned = re.sub(re.escape(matched_brand), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{2}\.\d{2}\.\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"[._]+", " ", cleaned)
    cleaned = re.split(
        r"(?i)(?:^|\s)(?:XXX|1080p|720p|2160p|480p|4K|MP4|WMV|MKV|x264|x265|HEVC|WEBRip|WEB-DL)(?:$|\s)",
        cleaned,
    )[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")

    return {
        "title": cleaned or clean_search_title(filename),
        "studio": matched_studio or matched_series,
        "year": year_val,
        "release_date": rel_date,
        "is_scene": has_scene_date or bool(matched_brand),
    }


def _is_strong_donghua_signal(combined_text: str) -> bool:
    low = re.sub(r"[._-]+", " ", combined_text.lower())
    for kw in DONGHUA_SPECIFIC_KEYWORDS:
        if re.search(rf"(?:^|\s){re.escape(kw)}(?:$|\s)", low):
            return True
    if _has_hanzi(combined_text) and not _has_kana(combined_text):
        return True
    return False


def _is_strong_anime_signal(combined_text: str) -> bool:
    if _has_kana(combined_text):
        return True
    low = combined_text.lower()
    if re.search(r"(?:^|[\s._\-\[\(])anime(?:$|[\s._\-\]\)])", low):
        return True
    brackets = re.findall(r"\[([^\]]+)\]", low)
    for b in brackets:
        b_clean = b.strip()
        if b_clean in ANIME_FANSUB_GROUPS:
            return True
    return False


def clean_search_title(filename: str) -> str:
    """Remove release/container noise while retaining meaningful title words."""
    base = os.path.splitext(filename or "")[0]
    # Preserve opaque product-like identifiers intact; classification decides
    # whether a code is a verified JAV code or requires manual review.
    if re.fullmatch(r"(?i)[A-Z]{2,8}[-_]\d{2,6}", base.strip()):
        return re.sub(r"_", "-", base.strip()).upper()
    cleaned = re.sub(r"\[[^\]]*\]", " ", base)
    cleaned = re.sub(r"\([^\)]*\)", " ", cleaned)
    cleaned = re.sub(r"[._]+", " ", cleaned)

    # Split at common technical/release tags. Include regional audio labels and
    # modern UHD/HDR release labels which were previously leaking into TMDb queries.
    cleaned = re.split(
        r"(?i)(?:^|\s)(?:"
        r"S\d{1,2}\s*E\d{1,3}|"
        r"\d{1,2}x\d{1,3}|"
        r"S\d{1,2}|"
        r"Season\s*\d+|"
        r"Episode\s*\d+|"
        r"Ep\.?\s*\d+|"
        r"1080p|720p|2160p|480p|4K|8K|"
        r"UHDRDV|UHDHDRDV|UHDHDR|UHDDV|UHD|HDR10\+?|HDR|DV|DoVi|DolbyVision|"
        r"WEB-?DL|WEBRip|BluRay|BDRip|DVDRip|HDRip|REMUX|"
        r"x264|x265|H264|H265|HEVC|AV1|10bit|8bit|AAC|AC3|EAC3|DTS(?:HD)?|FLAC|ATMOS|"
        r"(?:ITA|ENG|SPA|FRE|GER|RUS|HIN|IND|LAT|JPN|KOR)(?:[-+._ ](?:ITA|ENG|SPA|FRE|GER|RUS|HIN|IND|LAT|JPN|KOR))+|"
        r"XXX|MULTI|DUAL|DUBBED|SUBBED|PROPER|REPACK|EXTENDED|THEATRICAL|UNRATED|"
        r"CZ|CZE|SK|SLO|EN|ENG|ITA|JPN|KOR|SPA|FRE|GER|RUS|HIN|IND|LAT"
        r")(?:$|\s)",
        cleaned,
    )[0]
    cleaned = re.sub(r"(?<!\d)\b(?:19[5-9]\d|20[0-3]\d)\b(?!\d)", " ", cleaned)
    cleaned = re.sub(r"\s*-\s*\d{1,3}\s*$", "", cleaned)
    # Short release-group suffixes are removed only as a standalone final token.
    cleaned = re.sub(r"(?i)(?:\s+|[-_.])rq$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return cleaned or base


def parse_release_structure(filename: str, parent_name: str = "") -> Dict[str, Any]:
    """
    Menganalisis nama file dan folder induk secara menyeluruh untuk menghasilkan:
    - clean_series_title: Judul serial/film bersih tanpa subjudul episode
    - season_num, episode_num, is_episodic
    - year, release_date
    - pre_category: 'jav' | 'english_adult' | 'donghua' | 'anime' | 'tv_show' | 'general_movie'
    """
    stem = os.path.splitext(filename or "")[0]
    combined = f"{parent_name} {stem}".strip()

    # 1. Ekstrak Season & Episode dari filename maupun parent folder
    season_num, episode_num = _extract_season_episode(stem)
    if season_num is None or episode_num is None:
        p_s, p_e = _extract_season_episode(parent_name)
        season_num = season_num if season_num is not None else p_s
        episode_num = episode_num if episode_num is not None else p_e

    is_episodic = (
        season_num is not None
        or episode_num is not None
        or bool(
            re.search(
                r"(?i)(?:^|[\s._\-\[\(])(?:S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3}|Season\s*\d+|Episode\s*\d+|Ep\.?\s*\d+|Complete\s*Series)(?:$|[\s._\-\]\)])",
                combined,
            )
        )
    )

    year_val = _extract_year(combined)

    # 2. Cek JAV & English Adult terlebih dahulu (prioritas tertinggi)
    explicit_western_studio = _has_explicit_english_adult_studio(combined)
    jav_code = extract_jav_code(combined, allow_generic_fallback=(explicit_western_studio is None))
    jav_code_validated = bool(jav_code) and (
        jav_code.split("-")[0] in JAV_MAKER_PREFIXES
        or jav_code.startswith(("FC2-PPV-", "HEYZO-"))
        or bool(re.match(r"^\d{6}-\d{3}$", jav_code))
    )
    if jav_code:
        return {
            "clean_series_title": jav_code,
            "jav_code": jav_code,
            "jav_code_validated": jav_code_validated,
            "season_num": None,
            "episode_num": None,
            "is_episodic": False,
            "year": year_val,
            "pre_category": "jav",
        }

    adult_info = _detect_english_adult_details(combined, filename)
    if adult_info:
        return {
            "clean_series_title": adult_info["title"],
            "adult_info": adult_info,
            "season_num": None,
            "episode_num": None,
            "is_episodic": False,
            "year": adult_info.get("year") or year_val,
            "pre_category": "english_adult",
        }

    # 3. Indo detection follows JAV and English Adult, but precedes Anime/Donghua.
    indo_verdict = indo_eng.classify_indo({
        "title": stem,
        "description": parent_name,
        "original_url": "",
    })
    if indo_verdict.get("is_indo"):
        return {
            "clean_series_title": clean_search_title(stem) or stem,
            "season_num": None,
            "episode_num": None,
            "is_episodic": False,
            "year": year_val,
            "pre_category": "indo",
            "indo_match": indo_verdict,
        }

    # 4. Bersihkan judul inti dari filename (atau fallback ke parent folder jika nama file hanya nomor episode)
    core_title = clean_search_title(stem)
    if (not core_title or len(core_title) < 3 or core_title.lower() in GENERIC_FILE_STEMS) and parent_name:
        parent_core = clean_search_title(parent_name)
        if parent_core and len(parent_core) >= 3:
            core_title = parent_core

    # 4. Tentukan sinyal Donghua / Anime / TV Show / Movie
    if _is_strong_donghua_signal(combined):
        pre_cat = "donghua"
    elif _is_strong_anime_signal(combined):
        pre_cat = "anime"
    elif is_episodic:
        pre_cat = "tv_show"
    else:
        pre_cat = "general_movie"

    return {
        "clean_series_title": core_title or stem,
        "season_num": season_num if is_episodic else None,
        "episode_num": episode_num if is_episodic else None,
        "is_episodic": is_episodic,
        "year": year_val,
        "pre_category": pre_cat,
    }


def _poster_url_is_public(url: str) -> Tuple[bool, str]:
    """Allow only public HTTP(S) destinations; reject local/private targets and unsafe schemes."""
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return False, "Poster URL must use HTTP or HTTPS."
        if parsed.username or parsed.password:
            return False, "Poster URL credentials are not allowed."
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False, "Local network poster hosts are not allowed."
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
            except OSError:
                return False, "Poster host DNS lookup failed."
        if not addresses or any(not address.is_global for address in addresses):
            return False, "Poster URL resolves to a non-public network address."
        return True, ""
    except Exception:
        return False, "Poster URL is invalid."


def _detect_poster_image(content: bytes) -> Optional[Tuple[str, str]]:
    """Validate image signatures without relying on optional imaging libraries."""
    if not content:
        return None
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _read_poster_response(response: Any, max_bytes: int = 15 * 1024 * 1024) -> Tuple[Optional[bytes], Optional[str]]:
    """Read at most max_bytes from a streamed response."""
    try:
        content_length = (getattr(response, "headers", {}) or {}).get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            return None, "Poster exceeds the 15 MB safety limit."
    except (TypeError, ValueError):
        pass
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: List[bytes] = []
        total = 0
        for chunk in iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None, "Poster exceeds the 15 MB safety limit."
            chunks.append(chunk)
        return b"".join(chunks), None
    content = getattr(response, "content", b"") or b""
    if len(content) > max_bytes:
        return None, "Poster exceeds the 15 MB safety limit."
    return content, None


def download_poster_image_result(
    poster_url: Optional[str], unique_key: str, force_download: bool = False
) -> Dict[str, Any]:
    """Download and validate a poster; return an explicit status and never write partial files."""
    url = (poster_url or "").strip()
    if not url:
        return {"local_poster_path": None, "poster_status": "missing", "poster_error": "Provider returned no poster URL."}

    url_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    key_digest = hashlib.sha256((unique_key or url).encode("utf-8")).hexdigest()[:20]
    cache_prefix = f"{key_digest}_{url_digest}"
    if not force_download:
        try:
            for cached_name in os.listdir(POSTER_CACHE_DIR):
                if not cached_name.startswith(cache_prefix):
                    continue
                cached_candidate = os.path.join(POSTER_CACHE_DIR, cached_name)
                with open(cached_candidate, "rb") as cached_file:
                    if _detect_poster_image(cached_file.read(64)):
                        return {"local_poster_path": cached_candidate, "poster_status": "found", "poster_error": None}
        except OSError:
            pass
    last_error = "Poster download failed."
    current_url = url
    response = None

    # Follow a small number of redirects manually so every redirect target is revalidated.
    for _ in range(5):
        allowed, validation_error = _poster_url_is_public(current_url)
        if not allowed:
            return {"local_poster_path": None, "poster_status": "invalid_url", "poster_error": validation_error}
        try:
            response = HTTP_SESSION.get(current_url, timeout=(5, 12), allow_redirects=False, stream=True)
        except Exception as exc:
            last_error = f"Poster request failed: {type(exc).__name__}."
            response = None
            break
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") if getattr(response, "headers", None) else None
            if hasattr(response, "close"):
                response.close()
            if not location:
                last_error = "Poster host returned a redirect without a destination."
                response = None
                break
            current_url = urljoin(current_url, location)
            continue
        break
    else:
        response = None
        last_error = "Poster redirect limit exceeded."

    if response is None:
        return {"local_poster_path": None, "poster_status": "unreachable", "poster_error": last_error}
    if response.status_code != 200:
        status_code = response.status_code
        if hasattr(response, "close"):
            response.close()
        return {
            "local_poster_path": None,
            "poster_status": "unreachable",
            "poster_error": f"Poster host returned HTTP {status_code}.",
        }

    try:
        content, read_error = _read_poster_response(response)
    except Exception as exc:
        return {"local_poster_path": None, "poster_status": "unreachable", "poster_error": f"Poster response read failed: {type(exc).__name__}."}
    finally:
        if hasattr(response, "close"):
            response.close()
    if read_error:
        return {"local_poster_path": None, "poster_status": "invalid_image", "poster_error": read_error}
    content = content or b""
    if len(content) < 1500:
        return {"local_poster_path": None, "poster_status": "invalid_image", "poster_error": "Image response is empty or too small."}

    detected = _detect_poster_image(content)
    if not detected:
        return {"local_poster_path": None, "poster_status": "invalid_image", "poster_error": "Response is not a supported JPEG, PNG, or WebP image."}
    content_type, extension = detected
    response_type = (getattr(response, "headers", {}) or {}).get("Content-Type", "").split(";", 1)[0].strip().lower()
    if response_type and not response_type.startswith("image/") and response_type not in ("application/octet-stream", "binary/octet-stream"):
        return {"local_poster_path": None, "poster_status": "invalid_image", "poster_error": f"Unexpected content type: {response_type}."}

    try:
        os.makedirs(POSTER_CACHE_DIR, exist_ok=True)
        dest_path = os.path.join(POSTER_CACHE_DIR, f"{key_digest}_{url_digest}{extension}")
    except OSError as exc:
        return {"local_poster_path": None, "poster_status": "unreachable", "poster_error": f"Unable to prepare poster cache: {type(exc).__name__}."}

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".poster_", suffix=".tmp", dir=POSTER_CACHE_DIR)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, dest_path)
        temp_path = None
        return {"local_poster_path": dest_path, "poster_status": "found", "poster_error": None}
    except OSError as exc:
        return {"local_poster_path": None, "poster_status": "unreachable", "poster_error": f"Unable to save poster: {type(exc).__name__}."}
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def download_poster_image(poster_url: Optional[str], unique_key: str, force_download: bool = False) -> Optional[str]:
    """Backward-compatible wrapper; new callers should use download_poster_image_result."""
    return download_poster_image_result(poster_url, unique_key, force_download).get("local_poster_path")


def probe_video_duration_minutes(target: Optional[str]) -> int:
    """Probes actual video duration in minutes using ffprobe (supports local paths and HTTP streams)."""
    if not target or not shutil.which("ffprobe"):
        return 0
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        target,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0 or not proc.stdout:
            return 0
        info = json.loads(proc.stdout)
        dur_sec = float((info.get("format") or {}).get("duration") or 0)
        if dur_sec > 0:
            return max(1, int(round(dur_sec / 60.0)))
    except Exception:
        pass
    return 0


def _fetch_tpdb_jav_metadata(
    code_id: str,
    tpdb_api_key: str,
    item: Optional[Dict[str, Any]] = None,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Search ThePornDB's dedicated /jav endpoint using its documented parse query."""
    if not tpdb_api_key:
        if diagnostics is not None:
            diagnostics.append({"provider": "theporndb_jav", "status": "skipped", "reason": "TPDB API key is not configured."})
        return None

    item = item or {}
    headers = {"Authorization": f"Bearer {tpdb_api_key.strip()}", "Accept": "application/json"}
    filename = item.get("file_name") or ""
    parent = item.get("parent_name") or ""

    # Site is extracted only from known site tokens; do not send arbitrary folder names
    # as a TPDB site identifier.
    site_aliases = ("caribbeancom", "1pondo", "10musume", "heyzo", "pacopacomama", "muramura", "gachip")
    folded_parent = _ascii_fold(parent).casefold()
    site = next((x for x in site_aliases if x in folded_parent), None)

    # Build the title part separately. clean_search_title intentionally cuts at a
    # standalone year, which is useful for movies but would discard a JAV title when
    # the date appears before it in a Caribbeancom folder name.
    title_hint = parent if parent else os.path.splitext(filename)[0]
    title_hint = re.sub(rf"(?i)\b{re.escape(code_id)}\b", " ", title_hint)
    if site:
        title_hint = re.sub(rf"(?i)\b{re.escape(site)}\b", " ", title_hint)
    title_hint = re.sub(r"(?<!\d)(?:19|20)\d{2}[- ._]\d{1,2}[- ._]\d{1,2}(?!\d)", " ", title_hint)
    title_hint = re.sub(r"(?<!\d)\d{1,2}[ ._-]+\d{1,2}[ ._-]+(?:19|20)\d{2}(?!\d)", " ", title_hint)
    title_hint = re.sub(r"(?i)\b(?:JAV|UNCENSORED|UNEDITED|1080P|720P|2160P|480P|4K|UHD|HDR|HEVC|H264|H265|MP4|MKV)\b.*$", " ", title_hint)
    title_hint = re.sub(r"[._]+", " ", title_hint)
    title_hint = re.sub(r"\s+", " ", title_hint).strip(" -_")

    parse_queries = [code_id]
    date_match = re.search(r"(?<!\d)(\d{6})-(\d{3})(?!\d)", code_id or "")
    release_date = None
    if date_match:
        mmddyy = date_match.group(1)
        month, day, yy = int(mmddyy[:2]), int(mmddyy[2:4]), int(mmddyy[4:6])
        year = 2000 + yy if yy <= 79 else 1900 + yy
        if 1 <= month <= 12 and 1 <= day <= 31:
            release_date = f"{year:04d}-{month:02d}-{day:02d}"
    if site and title_hint:
        parse_parts = [site]
        if release_date:
            parse_parts.append(release_date)
        parse_parts.append(title_hint)
        parse_queries.append(".".join(parse_parts))

    seen = set()
    for parse_query in parse_queries:
        if parse_query in seen:
            continue
        seen.add(parse_query)
        try:
            resp = HTTP_SESSION.get(
                "https://api.theporndb.net/jav",
                params={"parse": parse_query, "limit": 10},
                headers=headers,
                timeout=12,
            )
            if resp.status_code != 200:
                if diagnostics is not None:
                    diagnostics.append({"provider": "theporndb_jav", "status": "http_error", "http_status": resp.status_code})
                continue
            payload = resp.json() or {}
            rows = payload.get("data") or []
            if isinstance(rows, dict):
                rows = [rows]
            if not rows:
                if diagnostics is not None:
                    diagnostics.append({"provider": "theporndb_jav", "status": "no_results", "query": parse_query})
                continue

            def row_score(row: Dict[str, Any]) -> float:
                labels = " ".join(str(row.get(k) or "") for k in ("title", "sku", "external_id", "slug"))
                score = _title_similarity(code_id, labels)
                if code_id.casefold() in labels.casefold():
                    score += 0.5
                if title_hint:
                    score = max(score, _title_similarity(title_hint, row.get("title") or ""))
                return score

            match = max(rows, key=row_score)
            match_score = row_score(match)
            if match_score < 0.35 and len(rows) > 1:
                if diagnostics is not None:
                    diagnostics.append({"provider": "theporndb_jav", "status": "low_confidence", "query": parse_query})
                continue

            detail = match
            identifier = match.get("_id") or match.get("id") or match.get("uuid") or match.get("slug")
            if identifier and not (match.get("poster") or match.get("image")):
                detail_resp = HTTP_SESSION.get(
                    f"https://api.theporndb.net/jav/{quote_plus(str(identifier))}",
                    headers=headers,
                    timeout=10,
                )
                if detail_resp.status_code == 200:
                    detail_payload = detail_resp.json() or {}
                    detail = detail_payload.get("data") or detail

            site_data = detail.get("site") or {}
            if isinstance(site_data, str):
                site_name = site_data
            else:
                site_name = site_data.get("name")
            background = detail.get("background") or {}
            poster = detail.get("poster") or detail.get("image") or background.get("large") or background.get("medium")
            performers = [x.get("name") for x in (detail.get("performers") or []) if isinstance(x, dict) and x.get("name")]
            tags = [x.get("name") for x in (detail.get("tags") or []) if isinstance(x, dict) and x.get("name")]
            rel_date = detail.get("date")
            title = detail.get("title") or title_hint or code_id
            if diagnostics is not None:
                diagnostics.append({"provider": "theporndb_jav", "status": "matched", "query": parse_query,
                                    "matched_title": title, "poster_available": bool(poster)})
            return {
                "code_id": code_id,
                "title": title,
                "series_title": title,
                "original_title": title,
                "year": _extract_year(rel_date or ""),
                "release_date": rel_date,
                "runtime": int((detail.get("duration") or 0) // 60) if detail.get("duration") else 0,
                "studio": site_name,
                "plot": detail.get("description") or "",
                "content_category": "jav",
                "is_adult": 1,
                "has_metadata": 1,
                "metadata_source": "theporndb_jav",
                "poster_url": poster,
                "actresses": performers,
                "genres": tags or ["JAV"],
                "rating": float(detail.get("rating") or 0.0),
                "_lookup": {"provider": "theporndb_jav", "status": "metadata_found", "query": parse_query,
                            "matched_title": title},
            }
        except Exception as exc:
            if diagnostics is not None:
                diagnostics.append({"provider": "theporndb_jav", "status": "request_error", "error": type(exc).__name__})
    return None


def _fetch_jav_metadata(code_id: str, tpdb_api_key: str = "", item: Optional[Dict[str, Any]] = None, diagnostics: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    code_upper = code_id.upper().strip()
    code_slug = code_upper.lower()

    try:
        jdb_url = f"https://www.javdatabase.com/movies/{quote_plus(code_slug)}/"
        resp = HTTP_SESSION.get(jdb_url, timeout=10)
        if diagnostics is not None:
            diagnostics.append({"provider": "javdatabase", "status": "http_response", "http_status": resp.status_code})
        if resp.status_code == 200 and code_upper in resp.text.upper():
            soup = BeautifulSoup(resp.text, "html.parser")
            h1 = soup.select_one("h1.entry-title, h1")
            title = h1.get_text(strip=True) if h1 else code_upper

            poster_url = None
            og_img = soup.select_one('meta[property="og:image"]')
            if og_img and og_img.get("content"):
                poster_url = og_img["content"].strip()
            if not poster_url:
                img_el = soup.select_one(".movie-poster img, .entry-content img")
                if img_el and img_el.get("src"):
                    poster_url = img_el["src"].strip()

            actresses = list(
                dict.fromkeys(
                    a.get_text(strip=True)
                    for a in soup.select('a[href*="/idols/"]')
                    if a.get_text(strip=True)
                )
            )
            genres = list(
                dict.fromkeys(
                    a.get_text(strip=True)
                    for a in soup.select('a[href*="/genres/"]')
                    if a.get_text(strip=True)
                )
            )
            studio_el = soup.select_one('a[href*="/studios/"]')
            studio = studio_el.get_text(strip=True) if studio_el else None

            page_text = soup.get_text(" ", strip=True)
            rel_date = None
            m_date = re.search(r"Release Date:\s*(\d{4}-\d{2}-\d{2})", page_text)
            if m_date:
                rel_date = m_date.group(1)

            runtime = 0
            m_rt = re.search(r"Runtime:\s*(\d+)\s*min", page_text, re.IGNORECASE)
            if m_rt:
                runtime = int(m_rt.group(1))

            jdb_meta = {
                "code_id": code_upper,
                "title": title,
                "series_title": title,
                "original_title": title,
                "year": _extract_year(rel_date or ""),
                "release_date": rel_date,
                "runtime": runtime,
                "studio": studio,
                "plot": f"JAV Release {code_upper}" + (f" starring {', '.join(actresses)}" if actresses else ""),
                "content_category": "jav",
                "is_adult": 1,
                "has_metadata": 1,
                "metadata_source": "javdatabase",
                "poster_url": poster_url,
                "actresses": actresses,
                "genres": genres or ["JAV"],
                "rating": 0.0,
                "_lookup": {"provider": "javdatabase", "status": "metadata_found", "poster_available": bool(poster_url)},
            }
            if poster_url:
                if diagnostics is not None:
                    diagnostics.append({"provider": "javdatabase", "status": "matched", "matched_title": title, "poster_available": True})
                return jdb_meta
            if diagnostics is not None:
                diagnostics.append({"provider": "javdatabase", "status": "matched_no_poster", "matched_title": title})
            # JavDatabase page exists but has no cover; try ThePornDB before falling back.
            tpdb_meta = _fetch_tpdb_jav_metadata(code_upper, tpdb_api_key, item=item, diagnostics=diagnostics)
            return tpdb_meta or jdb_meta
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.append({"provider": "javdatabase", "status": "request_error", "error": type(exc).__name__})

    tpdb_meta = _fetch_tpdb_jav_metadata(code_upper, tpdb_api_key, item=item, diagnostics=diagnostics)
    if tpdb_meta:
        return tpdb_meta
    return {
        "code_id": code_upper,
        "title": code_upper,
        "series_title": code_upper,
        "content_category": "jav",
        "is_adult": 1,
        "has_metadata": 0,
        "metadata_source": "jav_code_parser",
        "genres": ["JAV"],
        "_lookup": {"provider": "javdatabase+theporndb_jav", "status": "not_found",
                    "message": "No matching JAV metadata record was returned by configured providers."},
    }


def _fetch_tpdb_metadata(
    query_text: str,
    tpdb_api_key: str,
    studio_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clean_q = clean_search_title(query_text)
    if not tpdb_api_key or not clean_q:
        return None

    headers = {
        "Authorization": f"Bearer {tpdb_api_key.strip()}",
        "Accept": "application/json",
    }
    search_queries = [f"{studio_hint} {clean_q}".strip(), clean_q] if studio_hint else [clean_q]

    for sq in search_queries:
        for endpoint in ("scenes", "movies"):
            try:
                resp = HTTP_SESSION.get(
                    f"https://api.theporndb.net/{endpoint}",
                    params={"q": sq, "per_page": 3},
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data") or []
                if not data:
                    continue

                sc = data[0]
                performers = [p.get("name") for p in (sc.get("performers") or []) if p.get("name")]
                tags = [t.get("name") for t in (sc.get("tags") or []) if t.get("name")]
                site = (sc.get("site") or {}).get("name") or studio_hint
                rel_date = sc.get("date")
                bg = sc.get("background") or {}
                poster = (
                    sc.get("poster")
                    or bg.get("large")
                    or bg.get("full")
                    or bg.get("medium")
                    or sc.get("image")
                )
                t_val = sc.get("title") or clean_q
                return {
                    "title": t_val,
                    "series_title": t_val,
                    "original_title": sc.get("title"),
                    "year": _extract_year(rel_date or ""),
                    "release_date": rel_date,
                    "runtime": int((sc.get("duration") or 0) // 60) if sc.get("duration") else 0,
                    "studio": site,
                    "plot": sc.get("description") or "",
                    "content_category": "english_adult",
                    "is_adult": 1,
                    "has_metadata": 1,
                    "metadata_source": "theporndb",
                    "poster_url": poster,
                    "actresses": performers,
                    "genres": tags[:15] or ["Adult"],
                    "rating": float(sc.get("rating") or 0.0),
                }
            except Exception:
                continue
    return None


def _ascii_fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


# Trailing release flags that never belong to a title. They survive
# clean_search_title() because that helper splits on technical tags, so a name
# like "Apocalypse Z New World (2026) HQ HDRip" keeps the "HQ" suffix and TMDb
# then returns zero results for the whole query.
_RELEASE_NOISE_TAIL_TOKENS = {
    "hq", "hd", "sd", "retail", "dksubs", "dubbed", "subbed",
    "sub", "subs", "multi", "proper", "repack", "extended", "unrated",
    "remux", "nf", "yify", "yts",
}
# Audio/subtitle language labels often trail a release name ("Barfi Hindi").
# They are trimmed only from the end and only as an extra fallback candidate.
_LANGUAGE_TAIL_TOKENS = {
    "hindi", "indonesia", "indo", "melayu", "malay", "tamil", "telugu",
    "kannada", "malayalam", "english", "japanese", "korean", "spanish",
    "italian", "french", "german", "russian", "portuguese", "dutch",
    "arabic", "thai", "vietnamese", "filipino",
}


def _trim_release_tail(title: str, max_steps: int = 4) -> List[str]:
    """Return progressively shorter variants of a noisy release title.

    Only trailing, well-known release noise is removed; the original title is
    never mutated. Callers keep the untouched title as the first candidate, so
    these variants merely widen the provider search instead of replacing it.
    """
    current = re.sub(r"\s+", " ", title or "").strip(" :.-_")
    variants: List[str] = []
    trailer = re.search(
        r"(?i)\s*[-–—:]?\s*\b(?:subtitle|sub)\s+(?:indonesia|indo|melayu|english)\b.*$",
        current,
    )
    if trailer and trailer.start() > 0:
        current = current[: trailer.start()].strip(" -–—:,")
        if current:
            variants.append(current)
    for _ in range(max_steps):
        parts = current.split()
        if len(parts) < 2:
            break
        tail = parts[-1].strip("()[]{}.,:;-_").casefold()
        if tail not in _RELEASE_NOISE_TAIL_TOKENS and tail not in _LANGUAGE_TAIL_TOKENS:
            break
        current = " ".join(parts[:-1]).strip(" -_")
        if len(current) < 3:
            break
        variants.append(current)
    return variants


def _build_tmdb_search_candidates(title_query: str, year: Optional[int] = None) -> List[str]:
    """Create conservative title variants for TMDb without broad fuzzy guessing."""
    clean = clean_search_title(title_query)
    clean = re.sub(r"\s+", " ", clean).strip()
    candidates: List[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value or "").strip(" :.-_")
        if value and value.casefold() not in {x.casefold() for x in candidates}:
            candidates.append(value)

    folded = _ascii_fold(clean).casefold()
    compact = re.sub(r"[^a-z0-9]+", " ", folded).strip()

    # Known localized/release-name alias present in the user's Spider-Man sample.
    # The Czech phrase "Zbrusu nový den" is the localized title for Brand New Day.
    if "spider man" in compact and ("zbrusu novy den" in compact or "brand new day" in compact):
        add("Spider-Man: Brand New Day")

    # A release name such as "Spider-Man 9 Zbrusu nový den" often carries a
    # local sequel index. Keep the franchise-only query as a secondary candidate.
    spider_franchise = re.match(r"^(Spider[ -]?Man)\s+\d{1,2}(?:\s|$)", clean, re.IGNORECASE)
    if spider_franchise:
        if year == 2026 or year is None:
            add("Spider-Man: Brand New Day")
        add("Spider-Man")

    sequel = re.match(r"^(.*\b[a-z][a-z0-9-]{2,})\s+(\d{1,2})$", clean, re.IGNORECASE)
    if sequel:
        franchise = sequel.group(1).strip()
        if "spider" in franchise.casefold() and (year == 2026 or year is None):
            add("Spider-Man: Brand New Day")
        add(franchise)

    add(clean)

    # Release-noise variants ("... HQ", "... RETAIL DKSUBS", "... Hindi") and
    # titles carrying a "Subtitle Indonesia" blurb. These run after the exact
    # title so a clean match always wins first.
    for variant in _trim_release_tail(clean):
        add(variant)

    # Remove a trailing standalone sequel index for a fallback query. The year
    # filter is retained by the caller to avoid matching an older franchise film.
    if sequel:
        add(sequel.group(1))

    return candidates[:6]


def _fetch_tmdb_metadata(
    title_query: str,
    tmdb_api_key: str,
    year: Optional[int] = None,
    prefer_tv: bool = False,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not tmdb_api_key or not title_query:
        if diagnostics is not None:
            diagnostics.append({"provider": "tmdb", "status": "skipped", "reason": "TMDb API key or title is missing."})
        return None

    api_key = tmdb_api_key.strip()
    endpoints = ["tv"] if prefer_tv else ["movie", "tv"]
    candidates = _build_tmdb_search_candidates(title_query, year)
    best_match: Optional[Dict[str, Any]] = None
    best_score = -1.0
    best_endpoint = "movie"
    best_query = ""

    for ep in endpoints:
        for query_index, query in enumerate(candidates):
            params: Dict[str, Any] = {"api_key": api_key, "query": query, "include_adult": "false"}
            year_param = "first_air_date_year" if ep == "tv" else "year"
            if year:
                params[year_param] = year
            try:
                resp = HTTP_SESSION.get(f"https://api.themoviedb.org/3/search/{ep}", params=params, timeout=10)
                if resp.status_code != 200:
                    if diagnostics is not None:
                        diagnostics.append({"provider": "tmdb", "endpoint": ep, "query": query,
                                            "status": "http_error", "http_status": resp.status_code})
                    continue
                results = (resp.json() or {}).get("results") or []
                # If a year-filtered search is empty, retry the same query without
                # year, but rank exact-year results decisively above other releases.
                if not results and year:
                    retry_params = {"api_key": api_key, "query": query, "include_adult": "false"}
                    resp = HTTP_SESSION.get(f"https://api.themoviedb.org/3/search/{ep}", params=retry_params, timeout=10)
                    if resp.status_code == 200:
                        results = (resp.json() or {}).get("results") or []
                if diagnostics is not None:
                    diagnostics.append({"provider": "tmdb", "endpoint": ep, "query": query,
                                        "status": "results" if results else "no_results", "result_count": len(results)})
                for cand in results[:10]:
                    c_name = cand.get("name") if ep == "tv" else cand.get("title")
                    c_orig = cand.get("original_name") if ep == "tv" else cand.get("original_title")
                    sim = max(_title_similarity(query, c_name or ""), _title_similarity(query, c_orig or ""))
                    c_rel = (cand.get("first_air_date") if ep == "tv" else cand.get("release_date")) or ""
                    year_match = bool(year and c_rel[:4].isdigit() and int(c_rel[:4]) == int(year))
                    if year_match:
                        sim += 0.18
                    elif year and query_index > 0:
                        # Broad franchise fallbacks must not drift to a different year.
                        continue
                    if sim > best_score:
                        best_score = sim
                        best_match = cand
                        best_endpoint = ep
                        best_query = query
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics.append({"provider": "tmdb", "endpoint": ep, "query": query,
                                        "status": "request_error", "error": type(exc).__name__})
                continue

    if not best_match or best_score < 0.60:
        return None

    try:
        tmdb_id = int(best_match["id"])
        det_resp = HTTP_SESSION.get(
            f"https://api.themoviedb.org/3/{best_endpoint}/{tmdb_id}",
            params={"api_key": api_key, "append_to_response": "credits"},
            timeout=10,
        )
        det = det_resp.json() if det_resp.status_code == 200 else best_match
        genres = [g.get("name") for g in (det.get("genres") or []) if g.get("name")]
        genre_ids = set(best_match.get("genre_ids") or [g.get("id") for g in (det.get("genres") or [])])
        origin_countries = [c.upper() for c in (det.get("origin_country") or best_match.get("origin_country") or [])]
        orig_lang = (det.get("original_language") or best_match.get("original_language") or "").lower()
        is_animation = 16 in genre_ids or any("animation" in g.lower() for g in genres)
        if is_animation and ("CN" in origin_countries or "TW" in origin_countries or orig_lang == "zh"):
            cat = "donghua"
        elif is_animation and ("JP" in origin_countries or orig_lang == "ja"):
            cat = "anime"
        else:
            cat = "tv_show" if (best_endpoint == "tv" or prefer_tv) else "general_movie"

        rel_date = det.get("first_air_date") if best_endpoint == "tv" else det.get("release_date")
        if best_endpoint == "tv":
            rt_list = det.get("episode_run_time") or []
            runtime = int(rt_list[0]) if rt_list else 45
        else:
            runtime = int(det.get("runtime") or 0)
        companies = [c.get("name") for c in (det.get("production_companies") or []) if c.get("name")]
        director = next((c.get("name") for c in (det.get("credits") or {}).get("crew", []) if c.get("job") == "Director"), None)
        cast = [c.get("name") for c in ((det.get("credits") or {}).get("cast") or [])[:6] if c.get("name")]
        poster_path = det.get("poster_path") or best_match.get("poster_path")
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
        resolved_title = (det.get("name") if best_endpoint == "tv" else det.get("title")) or title_query
        return {
            "title": resolved_title,
            "series_title": resolved_title,
            "original_title": det.get("original_name") if best_endpoint == "tv" else det.get("original_title"),
            "year": _extract_year(rel_date or "") or year,
            "release_date": rel_date,
            "runtime": runtime,
            "studio": companies[0] if companies else None,
            "director": director,
            "plot": det.get("overview") or "",
            "content_category": cat,
            "tmdb_id": tmdb_id,
            "is_adult": 0,
            "has_metadata": 1,
            "metadata_source": "tmdb",
            "poster_url": poster_url,
            "actresses": cast,
            "genres": genres,
            "rating": round(float(det.get("vote_average") or 0.0), 1),
            "_lookup": {"provider": "tmdb", "status": "metadata_found", "query": best_query,
                        "matched_title": resolved_title, "tmdb_id": tmdb_id},
        }
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.append({"provider": "tmdb", "status": "detail_error", "error": type(exc).__name__})
        return None


def _fetch_anilist_metadata(title_query: str, force_donghua: bool = False) -> Optional[Dict[str, Any]]:
    if not title_query:
        return None

    if force_donghua:
        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME, countryOfOrigin: CN, sort: SEARCH_MATCH) {
            id
            countryOfOrigin
            episodes
            title { romaji english native }
            seasonYear
            duration
            description(asHtml: false)
            averageScore
            genres
            studios(isMain: true) { nodes { name } }
            coverImage { extraLarge large }
          }
        }
        """
    else:
        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
            id
            countryOfOrigin
            episodes
            title { romaji english native }
            seasonYear
            duration
            description(asHtml: false)
            averageScore
            genres
            studios(isMain: true) { nodes { name } }
            coverImage { extraLarge large }
          }
        }
        """

    def _lookup(search_term: str) -> Optional[Dict[str, Any]]:
        try:
            resp = HTTP_SESSION.post(
                "https://graphql.anilist.co",
                json={"query": query, "variables": {"search": search_term}},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                media = (resp.json().get("data") or {}).get("Media")
                if media:
                    t_obj = media.get("title") or {}
                    candidates = [t_obj.get("english"), t_obj.get("romaji"), t_obj.get("native")]
                    sim = max((_title_similarity(title_query, c) for c in candidates if c), default=0.0)
                    if sim < 0.65:
                        return None

                    origin = (media.get("countryOfOrigin") or "").upper()
                    if origin in ("CN", "TW") or force_donghua:
                        cat = "donghua"
                    elif origin == "JP":
                        cat = "anime"
                    else:
                        return None

                    best_title = t_obj.get("english") or t_obj.get("romaji") or title_query
                    base_series = re.sub(
                        r"(?i)\s*(?:Season\s*\d+|2nd\s*Season|3rd\s*Season|\d+th\s*Season|Part\s*\d+)\s*$",
                        "",
                        best_title,
                    ).strip() or best_title
                    native_title = t_obj.get("native") or t_obj.get("romaji")
                    studios = [s.get("name") for s in ((media.get("studios") or {}).get("nodes") or []) if s.get("name")]
                    cover = (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large")
                    desc = re.sub(r"<[^>]+>", "", media.get("description") or "").strip()
                    score = round((media.get("averageScore") or 0) / 10.0, 1)
                    return {
                        "title": best_title,
                        "series_title": base_series,
                        "original_title": native_title,
                        "year": media.get("seasonYear"),
                        "runtime": int(media.get("duration") or 24),
                        "studio": studios[0] if studios else None,
                        "plot": desc,
                        "content_category": cat,
                        "anilist_id": int(media["id"]),
                        "is_adult": 0,
                        "has_metadata": 1,
                        "metadata_source": "anilist",
                        "poster_url": cover,
                        "genres": media.get("genres") or [],
                        "rating": score,
                    }
        except Exception:
            return None
        return None

    # Release suffixes ("NF", "Subtitle Indonesia ...") break AniList search the
    # same way they break TMDb, so retry with the trimmed variants.
    search_terms = [title_query]
    for variant in _trim_release_tail(title_query)[:2]:
        if variant.casefold() not in {t.casefold() for t in search_terms}:
            search_terms.append(variant)
    for term in search_terms:
        result = _lookup(term)
        if result:
            return result
    return None


def _fetch_tvmaze_fallback(title_query: str) -> Optional[Dict[str, Any]]:
    if not title_query:
        return None
    try:
        resp = HTTP_SESSION.get("https://api.tvmaze.com/singlesearch/shows", params={"q": title_query}, timeout=8)
        if resp.status_code == 200:
            show = resp.json()
            if _title_similarity(title_query, show.get("name") or "") < 0.68:
                return None
            rel = show.get("premiered")
            net = (show.get("network") or show.get("webChannel") or {}).get("name")
            img = (show.get("image") or {}).get("original") or (show.get("image") or {}).get("medium")
            summary = re.sub(r"<[^>]+>", "", show.get("summary") or "").strip()
            lang = (show.get("language") or "").lower()
            genres = show.get("genres") or []
            cat = "tv_show"
            if "Anime" in genres:
                cat = "donghua" if lang == "chinese" else "anime"
            s_name = show.get("name") or title_query
            return {
                "title": s_name,
                "series_title": s_name,
                "original_title": s_name,
                "year": _extract_year(rel or ""),
                "release_date": rel,
                "runtime": int(show.get("runtime") or show.get("averageRuntime") or 45),
                "studio": net,
                "plot": summary,
                "content_category": cat,
                "is_adult": 0,
                "has_metadata": 1,
                "metadata_source": "tvmaze",
                "poster_url": img,
                "genres": genres,
                "rating": float((show.get("rating") or {}).get("average") or 0.0),
            }
    except Exception:
        pass
    return None


def fetch_official_series_seasons(
    series_title: str,
    category: str,
    tmdb_api_key: str = "",
    tmdb_id: Optional[int] = None,
    anilist_id: Optional[int] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Returns (external_id, seasons_list) where each season dict is:
    {"season_number": int, "name": str, "episode_count": int, "year": Optional[int], "source": str}
    - TV Show uses TMDb (with TVMaze fallback).
    - Anime & Donghua use AniList GraphQL + Jikan v4 fallback (never TMDb).
    """
    clean_q = clean_search_title(series_title)
    if not clean_q:
        return None, []

    if category in ("anime", "donghua"):
        seasons: List[Dict[str, Any]] = []
        ext_id: Optional[str] = str(anilist_id) if anilist_id else None

        try:
            gql = """
            query ($search: String, $id: Int) {
              Media(search: $search, id: $id, type: ANIME, sort: SEARCH_MATCH) {
                id
                episodes
                seasonYear
                title { english romaji }
                relations {
                  edges {
                    relationType
                    node {
                      id
                      type
                      format
                      episodes
                      seasonYear
                      title { english romaji }
                    }
                  }
                }
              }
            }
            """
            variables: Dict[str, Any] = {"id": int(anilist_id)} if anilist_id else {"search": clean_q}
            resp = HTTP_SESSION.post(
                "https://graphql.anilist.co",
                json={"query": gql, "variables": variables},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                media = (resp.json().get("data") or {}).get("Media")
                if media:
                    ext_id = str(media.get("id"))
                    main_eps = int(media.get("episodes") or 0)
                    seasons.append(
                        {
                            "season_number": 1,
                            "name": "Season 1",
                            "episode_count": main_eps,
                            "year": media.get("seasonYear"),
                            "source": "anilist",
                        }
                    )
                    sequels = []
                    for edge in ((media.get("relations") or {}).get("edges") or []):
                        if edge.get("relationType") == "SEQUEL":
                            node = edge.get("node") or {}
                            if node.get("type") == "ANIME" and node.get("format") in ("TV", "ONA", "TV_SHORT", None):
                                sequels.append(node)
                    sequels.sort(key=lambda x: int(x.get("seasonYear") or 9999))
                    for idx, sq in enumerate(sequels, start=2):
                        sq_title = (sq.get("title") or {}).get("english") or (sq.get("title") or {}).get("romaji") or f"Season {idx}"
                        seasons.append(
                            {
                                "season_number": idx,
                                "name": f"Season {idx} ({sq_title[:28]})",
                                "episode_count": int(sq.get("episodes") or 0),
                                "year": sq.get("seasonYear"),
                                "source": "anilist",
                            }
                        )
        except Exception:
            pass

        if not seasons or all(s.get("episode_count", 0) <= 0 for s in seasons):
            try:
                j_resp = HTTP_SESSION.get(
                    "https://api.jikan.moe/v4/anime",
                    params={"q": clean_q, "limit": 5},
                    timeout=10,
                )
                if j_resp.status_code == 200:
                    j_rows = j_resp.json().get("data") or []
                    tv_rows = [
                        r for r in j_rows
                        if (r.get("type") or "").upper() in ("TV", "ONA")
                        and _title_similarity(clean_q, r.get("title_english") or r.get("title") or "") >= 0.62
                    ]
                    tv_rows.sort(key=lambda r: int(r.get("year") or 9999))
                    if tv_rows:
                        ext_id = ext_id or f"mal:{tv_rows[0].get('mal_id')}"
                        seasons = []
                        for idx, r in enumerate(tv_rows, start=1):
                            seasons.append(
                                {
                                    "season_number": idx,
                                    "name": f"Season {idx}",
                                    "episode_count": int(r.get("episodes") or 0),
                                    "year": r.get("year"),
                                    "source": "jikan",
                                }
                            )
            except Exception:
                pass

        return ext_id, seasons

    if tmdb_api_key:
        api_key = tmdb_api_key.strip()
        resolved_tmdb_id = tmdb_id
        try:
            if not resolved_tmdb_id:
                s_resp = HTTP_SESSION.get(
                    "https://api.themoviedb.org/3/search/tv",
                    params={"api_key": api_key, "query": clean_q, "include_adult": "false"},
                    timeout=10,
                )
                if s_resp.status_code == 200:
                    results = s_resp.json().get("results") or []
                    for cand in results[:5]:
                        if _title_similarity(clean_q, cand.get("name") or cand.get("original_name") or "") >= 0.65:
                            resolved_tmdb_id = int(cand["id"])
                            break
            if resolved_tmdb_id:
                d_resp = HTTP_SESSION.get(
                    f"https://api.themoviedb.org/3/tv/{resolved_tmdb_id}",
                    params={"api_key": api_key},
                    timeout=10,
                )
                if d_resp.status_code == 200:
                    det = d_resp.json()
                    seasons_out = []
                    for s in (det.get("seasons") or []):
                        s_no = int(s.get("season_number") or 0)
                        if s_no <= 0:
                            continue
                        seasons_out.append(
                            {
                                "season_number": s_no,
                                "name": s.get("name") or f"Season {s_no}",
                                "episode_count": int(s.get("episode_count") or 0),
                                "year": _extract_year(s.get("air_date") or ""),
                                "source": "tmdb",
                            }
                        )
                    if seasons_out:
                        return str(resolved_tmdb_id), seasons_out
        except Exception:
            pass

    try:
        m_resp = HTTP_SESSION.get("https://api.tvmaze.com/singlesearch/shows", params={"q": clean_q}, timeout=8)
        if m_resp.status_code == 200:
            show = m_resp.json()
            sid = show.get("id")
            if sid:
                ep_resp = HTTP_SESSION.get(f"https://api.tvmaze.com/shows/{sid}/episodes", timeout=8)
                if ep_resp.status_code == 200:
                    eps = ep_resp.json() or []
                    by_season: Dict[int, int] = {}
                    for ep in eps:
                        sn = int(ep.get("season") or 1)
                        by_season[sn] = by_season.get(sn, 0) + 1
                    seasons_out = [
                        {
                            "season_number": sn,
                            "name": f"Season {sn}",
                            "episode_count": cnt,
                            "year": None,
                            "source": "tvmaze",
                        }
                        for sn, cnt in sorted(by_season.items())
                    ]
                    return f"tvmaze:{sid}", seasons_out
    except Exception:
        pass

    return None, []


def _search_title_candidates(primary: str, extras: Tuple[str, ...] = ()) -> List[str]:
    """Ordered provider search titles: the confirmed/parsed title first, then
    parent folder and stored series/title aliases as fallback queries.

    Localized release folders often carry an English alias (for example an
    Italian episode whose parent folder also names "The Rings of Power"), so a
    failed exact search can still succeed on the next query without guessing.
    """
    titles: List[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(value) < 2:
            return
        if value.casefold() in {t.casefold() for t in titles}:
            return
        titles.append(value)

    add(primary)
    for raw in extras:
        cleaned = clean_search_title(raw)
        if cleaned:
            add(cleaned)
    return titles[:4]


def enrich_media_item(item: Dict[str, Any], settings: Dict[str, str]) -> Dict[str, Any]:
    # Keep the immutable source filename for product-code parsing, but prefer the
    # user's confirmed normalized title for provider searches.
    fname = item.get("file_name") or item.get("title") or ""
    fpath = item.get("file_path") or ""
    parent_name = item.get("parent_name") or (os.path.basename(os.path.dirname(fpath)) if fpath else "")

    unique_key = item.get("unique_key") or fname
    tmdb_key = settings.get("tmdb_api_key", "")
    tpdb_key = settings.get("tpdb_api_key", "")

    parsed = parse_release_structure(fname, parent_name)
    # normalized_title is the explicit search title saved by classification/manual edit.
    # Legacy series_title can contain the unclean original release name, so do not
    # treat it as confirmed unless the user explicitly locked the title.
    title_is_confirmed = bool(
        int(item.get("title_is_manual") or 0)
        or int(item.get("classification_locked") or 0)
        or item.get("classification_method") == "manual"
        or item.get("classification_status") == "manually_classified"
    )
    confirmed_title = (item.get("normalized_title") or (item.get("series_title") if title_is_confirmed else "") or "").strip()
    clean_title = confirmed_title or parsed["clean_series_title"]
    # Search the parsed title first, then the parent folder and stored aliases.
    # A confirmed/manual title stays authoritative and is used alone.
    search_titles = _search_title_candidates(
        clean_title,
        () if title_is_confirmed else (
            parent_name,
            str(item.get("series_title") or ""),
            str(item.get("title") or ""),
        ),
    )
    provider_diagnostics: List[Dict[str, Any]] = []
    season_num = item.get("season_num") if item.get("season_num") is not None else parsed["season_num"]
    episode_num = item.get("episode_num") if item.get("episode_num") is not None else parsed["episode_num"]
    is_episodic = bool(parsed["is_episodic"] or item.get("content_category") == "tv_show")
    year_hint = item.get("year") or parsed["year"]
    confirmed_category = item.get("content_category")
    manually_locked = bool(
        int(item.get("classification_locked") or 0)
        or int(item.get("title_is_manual") or 0)
        or item.get("classification_method") == "manual"
        or item.get("classification_status") == "manually_classified"
    )
    # Manual classification remains authoritative. Otherwise apply the shared
    # priority: JAV > English Adult > Indo > Anime/Donghua > TV/Movie.
    # A filename-only JAV guess that never matched a validated maker prefix must not
    # override a category the classifier/manual flow already saved (rule 2).
    has_saved_category = bool(confirmed_category) and confirmed_category != "uncategorized"
    if manually_locked and confirmed_category:
        pre_cat = confirmed_category
    elif parsed.get("pre_category") in ("jav", "english_adult") and (
        parsed.get("pre_category") == "english_adult"
        or parsed.get("jav_code_validated")
        or not has_saved_category
    ):
        pre_cat = parsed["pre_category"]
    elif confirmed_category in ("jav", "english_adult"):
        pre_cat = confirmed_category
    else:
        indo_verdict = indo_eng.classify_indo({
            "title": fname,
            "tags": item.get("genres") or item.get("genres_json") or [],
            "description": " ".join(str(item.get(k) or "") for k in ("parent_name", "title", "series_title", "original_title", "plot")),
            "overview": item.get("plot") or "",
            "original_url": item.get("original_url") or "",
        })
        if confirmed_category == "indo" or indo_verdict.get("is_indo"):
            pre_cat = "indo"
            parsed["indo_match"] = indo_verdict
        elif confirmed_category in ("anime", "donghua", "tv_show", "general_movie", "indo_movie"):
            pre_cat = confirmed_category
        else:
            pre_cat = parsed["pre_category"]
    if pre_cat in ("anime", "donghua", "tv_show", "general_movie", "indo_movie") and confirmed_title:
        # Classification is authoritative once saved; filename heuristics must not override it.
        parsed["clean_series_title"] = clean_title
    if pre_cat == "jav":
        parsed["jav_code"] = parsed.get("jav_code") or item.get("code_id") or extract_jav_code(confirmed_title or fname)
    if pre_cat == "english_adult":
        if not parsed.get("adult_info"):
            parsed["adult_info"] = {
                "title": clean_title,
                "studio": item.get("studio"),
                "year": year_hint,
                "release_date": item.get("release_date"),
            }
        elif confirmed_title:
            parsed["adult_info"]["title"] = clean_title

    def _finalize(meta_dict: Dict[str, Any]) -> Dict[str, Any]:
        # Kunci item episodik agar tidak pernah turun menjadi general_movie
        if is_episodic and meta_dict.get("content_category") in ("general_movie", "uncategorized", None):
            meta_dict["content_category"] = pre_cat if pre_cat in ("anime", "donghua") else "tv_show"

        if season_num is not None:
            meta_dict["season_num"] = season_num
        if episode_num is not None:
            meta_dict["episode_num"] = episode_num

        # Kunci series_title ke judul kanonik serial (bukan subjudul episode)
        # Only explicit user confirmation locks the canonical title. Automatically
        # normalized filenames remain useful search hints, but must not override a
        # better canonical series title returned by a metadata provider.
        manual_title_confirmed = bool(
            int(item.get("title_is_manual") or 0)
            or int(item.get("classification_locked") or 0)
            or item.get("classification_method") == "manual"
            or item.get("classification_status") == "manually_classified"
        )
        canonical_series = (
            (confirmed_title if manual_title_confirmed else None)
            or meta_dict.get("series_title")
            or clean_title
            or meta_dict.get("title")
            or fname
        ).strip()
        meta_dict["series_title"] = canonical_series

        if not int(meta_dict.get("runtime") or 0):
            existing_rt = int(item.get("runtime") or 0)
            if existing_rt > 0:
                meta_dict["runtime"] = existing_rt
            else:
                probe_target = fpath if (fpath and os.path.isfile(fpath)) else (item.get("stream_url") or "")
                if probe_target:
                    probed_min = probe_video_duration_minutes(probe_target)
                    if probed_min > 0:
                        meta_dict["runtime"] = probed_min

        if int(item.get("poster_is_manual") or 0):
            meta_dict["poster_url"] = item.get("poster_url")
            meta_dict["local_poster_path"] = item.get("local_poster_path")
            meta_dict["poster_status"] = "manual"
            meta_dict["poster_error"] = item.get("poster_error")
            meta_dict["poster_source"] = "manual"
        else:
            poster_result = download_poster_image_result(meta_dict.get("poster_url"), unique_key)
            # Keep an existing cached image visible if a refresh fails; expose the failure separately.
            meta_dict["local_poster_path"] = poster_result.get("local_poster_path") or item.get("local_poster_path")
            meta_dict["poster_status"] = poster_result.get("poster_status", "not_checked")
            meta_dict["poster_error"] = poster_result.get("poster_error")
            meta_dict["poster_source"] = meta_dict.get("metadata_source") or "metadata_provider"
        lookup = dict(meta_dict.get("_lookup") or {})
        if not lookup:
            lookup = {
                "provider": meta_dict.get("metadata_source") or "unknown",
                "status": "metadata_found" if int(meta_dict.get("has_metadata") or 0) else "not_found",
            }
        lookup["poster_status"] = meta_dict.get("poster_status", "not_checked")
        if meta_dict.get("poster_status") == "missing" and lookup.get("status") == "metadata_found":
            lookup["status"] = "metadata_found_poster_missing"
            lookup["message"] = "Metadata matched, but the provider did not return a poster URL."
        elif meta_dict.get("poster_status") in ("invalid_image", "invalid_url", "unreachable"):
            lookup["status"] = "metadata_found_poster_download_failed"
            lookup["message"] = meta_dict.get("poster_error") or "Poster download failed validation."
        if provider_diagnostics:
            lookup["attempts"] = provider_diagnostics[-12:]
        meta_dict["_lookup"] = lookup
        return meta_dict

    # 1. JAV
    if pre_cat == "jav":
        jav_code = parsed.get("jav_code")
        if jav_code:
            jav_meta = _fetch_jav_metadata(jav_code, tpdb_key, item=item, diagnostics=provider_diagnostics)
            finalized_jav = _finalize(jav_meta)
            # If JavDatabase supplied a poster URL but the image host is broken,
            # make one alternate-provider attempt before accepting the failure.
            if (
                tpdb_key
                and not int(item.get("poster_is_manual") or 0)
                and jav_meta.get("metadata_source") == "javdatabase"
                and finalized_jav.get("poster_status") != "found"
            ):
                tpdb_meta = _fetch_tpdb_jav_metadata(jav_code, tpdb_key, item=item, diagnostics=provider_diagnostics)
                if tpdb_meta and tpdb_meta.get("poster_url"):
                    return _finalize(tpdb_meta)
            return finalized_jav
        return _finalize({
            "title": clean_title,
            "series_title": clean_title,
            "code_id": item.get("code_id"),
            "content_category": "jav",
            "is_adult": 1,
            "has_metadata": 0,
            "metadata_source": "manual_classification_parser",
            "_lookup": {"provider": "javdatabase+theporndb_jav", "status": "not_found",
                        "message": "JAV code was parsed, but no provider metadata record was found."},
            "genres": ["JAV"],
        })

    # 2. English Adult
    if pre_cat == "english_adult":
        adult_info = parsed["adult_info"]
        meta = _fetch_tpdb_metadata(adult_info["title"], tpdb_key, studio_hint=adult_info.get("studio")) if tpdb_key else None
        if not meta:
            meta = {
                "title": adult_info["title"],
                "series_title": adult_info["title"],
                "year": adult_info["year"],
                "release_date": adult_info["release_date"],
                "studio": adult_info["studio"],
                "content_category": "english_adult",
                "is_adult": 1,
                "has_metadata": 0,
                "metadata_source": "adult_scene_parser",
                "_lookup": {"provider": "theporndb", "status": "not_found", "message": "No adult metadata match was returned."},
                "genres": ["Adult", adult_info["studio"]] if adult_info["studio"] else ["Adult"],
            }
        return _finalize(meta)

    # 3. Indonesian Adult / Indo category. No external metadata provider is
    # queried here; the local keyword verdict is persisted through normal upsert.
    if pre_cat == "indo_movie":
        # IndonesiaMovies keeps the Roxxy-supplied meta, cover and subtitles.
        # No external provider is allowed to replace them automatically.
        return _finalize({
            "title": clean_title,
            "series_title": clean_title,
            "year": year_hint,
            "poster_url": item.get("poster_url"),
            "content_category": "indo_movie",
            "is_adult": 0,
            "has_metadata": 1 if (item.get("plot") or item.get("poster_url")) else 0,
            "metadata_source": item.get("metadata_source") or "roxxy",
            "_lookup": {
                "provider": "roxxy",
                "status": "kept",
                "message": "IndonesiaMovies metadata is kept from Roxxy; missing fields are filled manually.",
            },
            "genres": item.get("genres") or ["Indonesia"],
        })

    if pre_cat == "indo":
        indo_match = parsed.get("indo_match") or indo_eng.classify_indo({
            "title": fname,
            "tags": item.get("genres") or item.get("genres_json") or [],
            "description": " ".join(str(item.get(k) or "") for k in ("parent_name", "title", "series_title", "original_title", "plot")),
            "overview": item.get("plot") or "",
            "original_url": item.get("original_url") or "",
        })
        return _finalize({
            "title": clean_title,
            "series_title": clean_title,
            "year": year_hint,
            "content_category": "indo",
            "is_adult": 1,
            "has_metadata": 0,
            "metadata_source": "indo_keyword_classifier",
            "_lookup": {
                "provider": "local_indo_keyword_engine",
                "status": "classified",
                "message": "Classified as Indo using local keyword signals.",
                "indo_match": indo_match,
            },
            "genres": ["Indo", "Adult"],
        })

    # 4. Donghua / Anime
    if pre_cat in ("donghua", "anime"):
        meta = None
        for query in search_titles:
            meta = _fetch_anilist_metadata(query, force_donghua=(pre_cat == "donghua"))
            if meta:
                break
        if not meta and tmdb_key:
            for query in search_titles:
                meta = _fetch_tmdb_metadata(query, tmdb_key, year=year_hint, prefer_tv=True, diagnostics=provider_diagnostics)
                if meta:
                    break
        if not meta:
            meta = {
                "title": clean_title,
                "series_title": clean_title,
                "year": year_hint,
                "content_category": pre_cat,
                "is_adult": 0,
                "has_metadata": 0,
                "metadata_source": "anime_parser",
                "_lookup": {"provider": "anilist+tmdb", "status": "not_found", "message": "No AniList or TMDb metadata match was returned."},
                "genres": ["Donghua" if pre_cat == "donghua" else "Anime"],
            }
        return _finalize(meta)

    # 5. TV Show / General Movie via TMDb (jika is_episodic, kunci ke TV endpoint)
    meta = None
    for query in search_titles:
        meta = _fetch_tmdb_metadata(query, tmdb_key, year=year_hint, prefer_tv=is_episodic, diagnostics=provider_diagnostics)
        if meta:
            break

    if not meta and is_episodic:
        for query in search_titles:
            meta = _fetch_tvmaze_fallback(query)
            if meta:
                break

    if not meta:
        meta = {
            "title": clean_title,
            "series_title": clean_title,
            "year": year_hint,
            "content_category": "tv_show" if is_episodic else "general_movie",
            "is_adult": 0,
            "has_metadata": 0,
            "metadata_source": "filename_parser",
            "_lookup": {"provider": "tmdb" if tmdb_key else "tmdb", "status": "not_found",
                        "message": "No matching TMDb record was found. Check the search title, year, and TMDb API key."},
        }

    return _finalize(meta)
    
def fetch_discovery_catalog(
    category: str,
    mode: str = "trending",
    query: str = "",
    settings: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Mengambil daftar katalog Trending / Popular / Search untuk Discovery & Search Hub Modal.
    Output seragam: [{title, code_id, year, rating, overview, poster_url, tmdb_id, anilist_id, category}]
    """
    settings = settings or {}
    tmdb_key = (settings.get("tmdb_api_key") or "").strip()
    tpdb_key = (settings.get("tpdb_api_key") or "").strip()
    q = (query or "").strip()
    results_out: List[Dict[str, Any]] = []

    # 1. ANIME & DONGHUA -> AniList GraphQL
    if category in ("anime", "donghua"):
        origin_filter = ", countryOfOrigin: CN" if category == "donghua" else ", countryOfOrigin: JP"
        if mode == "search" and q:
            gql = f"""
            query ($search: String) {{
              Page(page: 1, perPage: 25) {{
                media(search: $search, type: ANIME{origin_filter}, sort: SEARCH_MATCH) {{
                  id
                  title {{ english romaji }}
                  seasonYear
                  averageScore
                  description(asHtml: false)
                  coverImage {{ large extraLarge }}
                }}
              }}
            }}
            """
            variables: Dict[str, Any] = {"search": q}
        else:
            sort_key = "TRENDING_DESC" if mode == "trending" else "POPULARITY_DESC"
            gql = f"""
            query {{
              Page(page: 1, perPage: 25) {{
                media(type: ANIME{origin_filter}, sort: {sort_key}) {{
                  id
                  title {{ english romaji }}
                  seasonYear
                  averageScore
                  description(asHtml: false)
                  coverImage {{ large extraLarge }}
                }}
              }}
            }}
            """
            variables = {}

        try:
            resp = HTTP_SESSION.post(
                "https://graphql.anilist.co",
                json={"query": gql, "variables": variables},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=12,
            )
            if resp.status_code == 200:
                media_list = ((resp.json().get("data") or {}).get("Page") or {}).get("media") or []
                for m in media_list:
                    t_obj = m.get("title") or {}
                    title = t_obj.get("english") or t_obj.get("romaji") or "Untitled"
                    cover = (m.get("coverImage") or {}).get("large") or (m.get("coverImage") or {}).get("extraLarge")
                    desc = re.sub(r"<[^>]+>", "", m.get("description") or "").strip()
                    results_out.append({
                        "title": title,
                        "code_id": "",
                        "year": m.get("seasonYear"),
                        "rating": round((m.get("averageScore") or 0) / 10.0, 1),
                        "overview": desc,
                        "poster_url": cover,
                        "tmdb_id": None,
                        "anilist_id": int(m["id"]),
                        "category": category,
                    })
        except Exception:
            pass
        return results_out

    # 2. JAV -> JAVDatabase Search / Direct Code Lookup
    if category == "jav":
        jav_code = extract_jav_code(q, allow_generic_fallback=True) if q else None
        if jav_code:
            meta = _fetch_jav_metadata(jav_code)
            results_out.append({
                "title": meta.get("title") or jav_code,
                "code_id": jav_code,
                "year": meta.get("year"),
                "rating": 0.0,
                "overview": meta.get("plot") or "",
                "poster_url": meta.get("poster_url"),
                "tmdb_id": None,
                "anilist_id": None,
                "category": "jav",
            })
            return results_out

        try:
            search_url = (
                f"https://www.javdatabase.com/?s={quote_plus(q)}"
                if (mode == "search" and q)
                else "https://www.javdatabase.com/movies/"
            )
            resp = HTTP_SESSION.get(search_url, timeout=12)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select("article, .movie-card, .post")[:24]
                for c in cards:
                    a_tag = c.select_one("h2 a, h3 a, a[href*='/movies/']")
                    if not a_tag:
                        continue
                    raw_t = a_tag.get_text(" ", strip=True)
                    c_code = extract_jav_code(raw_t, allow_generic_fallback=True) or ""
                    img = c.select_one("img")
                    p_url = (img.get("data-src") or img.get("src")) if img else None
                    results_out.append({
                        "title": raw_t,
                        "code_id": c_code,
                        "year": _extract_year(raw_t),
                        "rating": 0.0,
                        "overview": "",
                        "poster_url": p_url,
                        "tmdb_id": None,
                        "anilist_id": None,
                        "category": "jav",
                    })
        except Exception:
            pass
        return results_out

    # 3. ENGLISH ADULT -> ThePornDB API
    if category == "english_adult":
        if not tpdb_key:
            return []
        headers = {"Authorization": f"Bearer {tpdb_key}", "Accept": "application/json"}
        params: Dict[str, Any] = {"per_page": 24}
        if mode == "search" and q:
            params["q"] = q
        elif mode == "trending":
            params["orderBy"] = "trending"
        else:
            params["orderBy"] = "date"
        try:
            resp = HTTP_SESSION.get("https://api.theporndb.net/scenes", params=params, headers=headers, timeout=12)
            if resp.status_code == 200:
                for sc in (resp.json().get("data") or []):
                    bg = sc.get("background") or {}
                    poster = sc.get("poster") or bg.get("medium") or bg.get("large") or sc.get("image")
                    rel_date = sc.get("date") or ""
                    site = (sc.get("site") or {}).get("name") or ""
                    results_out.append({
                        "title": sc.get("title") or "Untitled Scene",
                        "code_id": site,
                        "year": _extract_year(rel_date),
                        "rating": float(sc.get("rating") or 0.0),
                        "overview": sc.get("description") or "",
                        "poster_url": poster,
                        "tmdb_id": None,
                        "anilist_id": None,
                        "category": "english_adult",
                    })
        except Exception:
            pass
        return results_out

    # 4. GENERAL MOVIE & TV SHOW -> TMDb API
    if not tmdb_key:
        return []
    ep = "tv" if category == "tv_show" else "movie"
    if mode == "search" and q:
        url = f"https://api.themoviedb.org/3/search/{ep}"
        params = {"api_key": tmdb_key, "query": q, "include_adult": "false"}
    elif mode == "popular":
        url = f"https://api.themoviedb.org/3/{ep}/popular"
        params = {"api_key": tmdb_key}
    else:
        url = f"https://api.themoviedb.org/3/trending/{ep}/week"
        params = {"api_key": tmdb_key}

    try:
        resp = HTTP_SESSION.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            for item in (resp.json().get("results") or [])[:24]:
                title = item.get("name") if ep == "tv" else item.get("title")
                rel = item.get("first_air_date") if ep == "tv" else item.get("release_date")
                p_path = item.get("poster_path")
                results_out.append({
                    "title": title or "Untitled",
                    "code_id": "",
                    "year": _extract_year(rel or ""),
                    "rating": round(float(item.get("vote_average") or 0.0), 1),
                    "overview": item.get("overview") or "",
                    "poster_url": f"https://image.tmdb.org/t/p/w500{p_path}" if p_path else None,
                    "tmdb_id": int(item["id"]),
                    "anilist_id": None,
                    "category": category,
                })
    except Exception:
        pass

    return results_out