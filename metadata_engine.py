import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POSTER_CACHE_DIR = os.path.join(BASE_DIR, "cache", "posters")
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
_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=1)
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
        "MKD", "MKBD", "DRG", "DSAM", "MESU", "NYB", "RED", "SKY", "SKYHD", "RHJ",
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
    "brazzers", "realitykings", "reality kings", "bangbros", "bang bros", "naughtyamerica",
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

ADULT_EXPLICIT_WORDS = {
    "xxx", "porn", "porno", "nsfw", "hentai", "creampie", "blowjob",
    "handjob", "gangbang", "threesome", "foursome", "orgy", "anal", "bukkake",
    "cumshot", "facial", "squirt", "squirting", "milf", "gilf", "pawg", "bbw",
    "bdsm", "bondage", "femdom", "cuckold", "hotwife", "swinger", "shemale",
    "transsexual", "futanari", "masturbation", "dildo", "vibrator", "striptease",
    "hardcore", "softcore", "slut", "whore", "pussy", "cock",
    "boobs", "tits", "assfuck", "deepthroat", "defloration", "czech",
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
    low = re.sub(r"[._-]+", " ", combined_text.lower())
    for st in ENGLISH_ADULT_STUDIOS:
        if re.search(rf"(?:^|\s){re.escape(st)}(?:$|\s)", low):
            return st.title()
    return None


def _detect_english_adult_details(combined_text: str, filename: str) -> Optional[Dict[str, Any]]:
    low = re.sub(r"[._-]+", " ", combined_text.lower())
    matched_studio = _has_explicit_english_adult_studio(combined_text)

    tokens = set(re.findall(r"[a-z0-9]+", low))
    has_explicit_word = bool(tokens & ADULT_EXPLICIT_WORDS)
    has_scene_date = bool(re.search(r"(?:^|[\s._-])(\d{2})\.(\d{2})\.(\d{2})(?:$|[\s._-])", combined_text))

    if not matched_studio and not has_explicit_word:
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
    if matched_studio:
        cleaned = re.sub(re.escape(matched_studio), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{2}\.\d{2}\.\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"[._]+", " ", cleaned)
    cleaned = re.split(
        r"(?i)(?:^|\s)(?:XXX|1080p|720p|2160p|480p|4K|MP4|WMV|MKV|x264|x265|HEVC|WEBRip|WEB-DL)(?:$|\s)",
        cleaned,
    )[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")

    return {
        "title": cleaned or clean_search_title(filename),
        "studio": matched_studio,
        "year": year_val,
        "release_date": rel_date,
        "is_scene": has_scene_date or bool(matched_studio),
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
    base = os.path.splitext(filename)[0]
    cleaned = re.sub(r"\[[^\]]*\]", " ", base)
    cleaned = re.sub(r"\([^\)]*\)", " ", cleaned)
    cleaned = re.sub(r"[._]+", " ", cleaned)

    # Potong tepat sebelum penanda episode/season (S03E03, 3x03, Season 3, Ep 03) atau tag kualitas
    cleaned = re.split(
        r"(?i)(?:^|\s)(?:"
        r"S\d{1,2}\s*E\d{1,3}|"
        r"\d{1,2}x\d{1,3}|"
        r"S\d{1,2}|"
        r"Season\s*\d+|"
        r"Episode\s*\d+|"
        r"Ep\.?\s*\d+|"
        r"1080p|720p|2160p|480p|4K|WEB-?DL|WEBRip|BluRay|BDRip|DVDRip|HDRip|"
        r"x264|x265|H264|H265|HEVC|10bit|AAC|AC3|DTS|FLAC|MULTI|DUAL|ITA|ENG|SUB|"
        r"PROPER|REPACK|EXTENDED|THEATRICAL|UNRATED"
        r")(?:$|\s)",
        cleaned,
    )[0]
    cleaned = re.sub(r"(?:^|\s)(19[5-9]\d|20[0-3]\d)(?:$|\s).*$", "", cleaned)
    cleaned = re.sub(r"\s*-\s*\d{1,3}\s*$", "", cleaned)
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
    if jav_code:
        return {
            "clean_series_title": jav_code,
            "jav_code": jav_code,
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

    # 3. Bersihkan judul inti dari filename (atau fallback ke parent folder jika nama file hanya nomor episode)
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


def download_poster_image(poster_url: Optional[str], unique_key: str, force_download: bool = False) -> Optional[str]:
    if not poster_url or not poster_url.startswith("http"):
        return None
    digest = hashlib.sha256(unique_key.encode("utf-8")).hexdigest()[:24]
    ext = ".jpg"
    low_url = poster_url.lower()
    if ".png" in low_url:
        ext = ".png"
    elif ".webp" in low_url:
        ext = ".webp"
    dest_path = os.path.join(POSTER_CACHE_DIR, f"{digest}{ext}")

    if not force_download and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1500:
        return dest_path

    try:
        resp = HTTP_SESSION.get(poster_url, timeout=12)
        if resp.status_code == 200 and len(resp.content) > 1500:
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            return dest_path
    except Exception:
        pass
    return dest_path if (os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1500) else None


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


def _fetch_jav_metadata(code_id: str) -> Dict[str, Any]:
    code_upper = code_id.upper().strip()
    code_slug = code_upper.lower()

    try:
        jdb_url = f"https://www.javdatabase.com/movies/{quote_plus(code_slug)}/"
        resp = HTTP_SESSION.get(jdb_url, timeout=10)
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

            return {
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
                "has_metadata": 1 if poster_url else 0,
                "metadata_source": "javdatabase",
                "poster_url": poster_url,
                "actresses": actresses,
                "genres": genres or ["JAV"],
                "rating": 0.0,
            }
    except Exception:
        pass

    return {
        "code_id": code_upper,
        "title": code_upper,
        "series_title": code_upper,
        "content_category": "jav",
        "is_adult": 1,
        "has_metadata": 0,
        "metadata_source": "jav_code_parser",
        "genres": ["JAV"],
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


def _fetch_tmdb_metadata(
    title_query: str,
    tmdb_api_key: str,
    year: Optional[int] = None,
    prefer_tv: bool = False,
) -> Optional[Dict[str, Any]]:
    if not tmdb_api_key or not title_query:
        return None
    api_key = tmdb_api_key.strip()
    endpoints = ["tv"] if prefer_tv else ["movie", "tv"]

    for ep in endpoints:
        try:
            params: Dict[str, Any] = {"api_key": api_key, "query": title_query, "include_adult": "false"}
            if year:
                params["first_air_date_year" if ep == "tv" else "year"] = year
            resp = HTTP_SESSION.get(f"https://api.themoviedb.org/3/search/{ep}", params=params, timeout=10)
            if resp.status_code != 200:
                continue
            results = resp.json().get("results") or []
            if not results and year:
                params.pop("first_air_date_year", None)
                params.pop("year", None)
                resp = HTTP_SESSION.get(f"https://api.themoviedb.org/3/search/{ep}", params=params, timeout=10)
                results = resp.json().get("results") or [] if resp.status_code == 200 else []
            if not results:
                continue

            best_top = None
            best_score = -1.0
            for cand in results[:5]:
                c_name = cand.get("name") if ep == "tv" else cand.get("title")
                c_orig = cand.get("original_name") if ep == "tv" else cand.get("original_title")
                sim = max(_title_similarity(title_query, c_name or ""), _title_similarity(title_query, c_orig or ""))
                if year:
                    c_rel = (cand.get("first_air_date") if ep == "tv" else cand.get("release_date")) or ""
                    if str(year) in c_rel:
                        sim += 0.08
                if sim > best_score:
                    best_score = sim
                    best_top = cand

            if not best_top or best_score < 0.65:
                words = title_query.split()
                for trim_len in range(min(len(words) - 1, 6), 1, -1):
                    short_q = " ".join(words[:trim_len])
                    r2 = HTTP_SESSION.get(
                        f"https://api.themoviedb.org/3/search/{ep}",
                        params={"api_key": api_key, "query": short_q, "include_adult": "false"},
                        timeout=10,
                    )
                    res2 = r2.json().get("results") or [] if r2.status_code == 200 else []
                    for cand in res2[:5]:
                        c_name = cand.get("name") if ep == "tv" else cand.get("title")
                        c_orig = cand.get("original_name") if ep == "tv" else cand.get("original_title")
                        sim = max(_title_similarity(short_q, c_name or ""), _title_similarity(short_q, c_orig or ""))
                        if sim > best_score:
                            best_score = sim
                            best_top = cand
                    if best_top and best_score >= 0.72:
                        break

            if not best_top or best_score < 0.60:
                continue

            tmdb_id = int(best_top["id"])
            det_resp = HTTP_SESSION.get(
                f"https://api.themoviedb.org/3/{ep}/{tmdb_id}",
                params={"api_key": api_key, "append_to_response": "credits"},
                timeout=10,
            )
            det = det_resp.json() if det_resp.status_code == 200 else best_top

            genres = [g.get("name") for g in (det.get("genres") or []) if g.get("name")]
            genre_ids = set(best_top.get("genre_ids") or [g.get("id") for g in (det.get("genres") or [])])
            origin_countries = [c.upper() for c in (det.get("origin_country") or best_top.get("origin_country") or [])]
            orig_lang = (det.get("original_language") or best_top.get("original_language") or "").lower()

            is_animation = 16 in genre_ids or any("animation" in g.lower() for g in genres)
            if is_animation and ("CN" in origin_countries or "TW" in origin_countries or orig_lang == "zh"):
                cat = "donghua"
            elif is_animation and ("JP" in origin_countries or orig_lang == "ja"):
                cat = "anime"
            else:
                cat = "tv_show" if (ep == "tv" or prefer_tv) else "general_movie"

            rel_date = det.get("first_air_date") if ep == "tv" else det.get("release_date")
            if ep == "tv":
                rt_list = det.get("episode_run_time") or []
                runtime = int(rt_list[0]) if rt_list else 45
            else:
                runtime = int(det.get("runtime") or 0)

            companies = [c.get("name") for c in (det.get("production_companies") or []) if c.get("name")]
            director = None
            for c in (det.get("credits") or {}).get("crew") or []:
                if c.get("job") == "Director":
                    director = c.get("name")
                    break

            cast = [c.get("name") for c in ((det.get("credits") or {}).get("cast") or [])[:6] if c.get("name")]
            poster_path = det.get("poster_path") or best_top.get("poster_path")
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
            resolved_title = (det.get("name") if ep == "tv" else det.get("title")) or title_query

            return {
                "title": resolved_title,
                "series_title": resolved_title,
                "original_title": det.get("original_name") if ep == "tv" else det.get("original_title"),
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
            }
        except Exception:
            continue
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

    try:
        resp = HTTP_SESSION.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": {"search": title_query}},
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
        pass
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


def enrich_media_item(item: Dict[str, Any], settings: Dict[str, str]) -> Dict[str, Any]:
    fname = item.get("file_name") or item.get("title") or ""
    fpath = item.get("file_path") or ""
    parent_name = item.get("parent_name") or (os.path.basename(os.path.dirname(fpath)) if fpath else "")

    unique_key = item.get("unique_key") or fname
    tmdb_key = settings.get("tmdb_api_key", "")
    tpdb_key = settings.get("tpdb_api_key", "")

    parsed = parse_release_structure(fname, parent_name)
    clean_title = parsed["clean_series_title"]
    season_num = parsed["season_num"]
    episode_num = parsed["episode_num"]
    is_episodic = parsed["is_episodic"]
    year_hint = parsed["year"]
    pre_cat = parsed["pre_category"]

    def _finalize(meta_dict: Dict[str, Any]) -> Dict[str, Any]:
        # Kunci item episodik agar tidak pernah turun menjadi general_movie
        if is_episodic and meta_dict.get("content_category") in ("general_movie", "uncategorized", None):
            meta_dict["content_category"] = pre_cat if pre_cat in ("anime", "donghua") else "tv_show"

        if season_num is not None:
            meta_dict["season_num"] = season_num
        if episode_num is not None:
            meta_dict["episode_num"] = episode_num

        # Kunci series_title ke judul kanonik serial (bukan subjudul episode)
        canonical_series = (meta_dict.get("series_title") or clean_title or meta_dict.get("title") or fname).strip()
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

        meta_dict["local_poster_path"] = download_poster_image(meta_dict.get("poster_url"), unique_key)
        return meta_dict

    # 1. JAV
    if pre_cat == "jav":
        return _finalize(_fetch_jav_metadata(parsed["jav_code"]))

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
                "has_metadata": 1,
                "metadata_source": "adult_scene_parser",
                "genres": ["Adult", adult_info["studio"]] if adult_info["studio"] else ["Adult"],
            }
        return _finalize(meta)

    # 3. Donghua / Anime
    if pre_cat in ("donghua", "anime"):
        meta = _fetch_anilist_metadata(clean_title, force_donghua=(pre_cat == "donghua"))
        if not meta and tmdb_key:
            meta = _fetch_tmdb_metadata(clean_title, tmdb_key, year=year_hint, prefer_tv=True)
        if not meta:
            meta = {
                "title": clean_title,
                "series_title": clean_title,
                "year": year_hint,
                "content_category": pre_cat,
                "is_adult": 0,
                "has_metadata": 1,
                "metadata_source": "anime_parser",
                "genres": ["Donghua" if pre_cat == "donghua" else "Anime"],
            }
        return _finalize(meta)

    # 4. TV Show / General Movie via TMDb (jika is_episodic, kunci ke TV endpoint)
    meta = _fetch_tmdb_metadata(clean_title, tmdb_key, year=year_hint, prefer_tv=is_episodic)

    if not meta and is_episodic:
        meta = _fetch_tvmaze_fallback(clean_title)

    if not meta:
        meta = {
            "title": clean_title,
            "series_title": clean_title,
            "year": year_hint,
            "content_category": "tv_show" if is_episodic else "general_movie",
            "is_adult": 0,
            "has_metadata": 1,
            "metadata_source": "filename_parser",
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