import difflib
import hashlib
import os
import re
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

# HTTP Session Pooled untuk menghindari SSL handshake berulang
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(HEADERS)
_adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=1)
HTTP_SESSION.mount("http://", _adapter)
HTTP_SESSION.mount("https://", _adapter)

JAV_MAKER_PREFIXES = {
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
    "IMA", "IMD", "IMG", "IMK", "IMT", "IMV", "ING", "INU", "INV",
    "IPSD", "IPTD", "IQPA", "IRD", "IRO", "ISRD", "ISTU", "ITR",
    "ITSR", "IWAN", "JAC", "JAD", "JAE", "JAG", "JAH", "JAI", "JAJ", "JAK",
    "JAL", "JAM", "JAN", "JAP", "JAR", "JAS", "JAT", "JAU", "JAV", "JAW",
    "JAX", "JAY", "JAZ", "JBD", "JCD", "JCS", "JDD", "JDE", "JDG", "JDH",
    "JDI", "JDJ", "JDK", "JDL", "JDM", "JDN", "JDO", "JDP", "JDQ", "JDR",
    "JDS", "JDT", "JDU", "JDV", "JDW", "JDX", "JDY", "JDZ", "JED", "JEE",
    "JEF", "JEG", "JEH", "JEI", "JEJ", "JEK", "JEL", "JEM", "JEN", "JEO",
    "JEP", "JEQ", "JER", "JES", "JET", "JEU", "JEV", "JEW", "JEX", "JEY",
    "JEZ", "JFE", "JFF", "JFG", "JFH", "JFI", "JFJ", "JFK", "JFL", "JFM",
    "JFN", "JFO", "JFP", "JFQ", "JFR", "JFS", "JFT", "JFU", "JFV", "JFW",
    "JFX", "JFY", "JFZ", "JGD", "JGE", "JGF", "JGG", "JGH", "JGI", "JGJ",
    "JGK", "JGL", "JGM", "JGN", "JGO", "JGP", "JGQ", "JGR", "JGS", "JGT",
    "JGU", "JGV", "JGW", "JGX", "JGY", "JGZ", "JHD", "JHE", "JHF", "JHG",
    "JHH", "JHI", "JHJ", "JHK", "JHL", "JHM", "JHN", "JHO", "JHP", "JHQ",
    "JHR", "JHS", "JHT", "JHU", "JHV", "JHW", "JHX", "JHY", "JHZ", "JID",
    "JIE", "JIF", "JIG", "JIH", "JII", "JIJ", "JIK", "JIL", "JIM", "JIN",
    "JIO", "JIP", "JIQ", "JIR", "JIS", "JIT", "JIU", "JIV", "JIW", "JIX",
    "JIY", "JIZ", "JJD", "JJE", "JJF", "JJG", "JJH", "JJI", "JJJ", "JJK",
    "JJL", "JJM", "JJN", "JJO", "JJP", "JJQ", "JJR", "JJS", "JJT", "JJU",
    "JJV", "JJW", "JJX", "JJY", "JJZ", "JKD", "JKE", "JKF", "JKG", "JKH",
    "JKI", "JKJ", "JKK", "JKL", "JKM", "JKN", "JKO", "JKP", "JKQ", "JKR",
    "JKS", "JKT", "JKU", "JKV", "JKW", "JKX", "JKY", "JKZ", "JLD", "JLE",
    "JLF", "JLG", "JLH", "JLI", "JLJ", "JLK", "JLL", "JLM", "JLN", "JLO",
    "JLP", "JLQ", "JLR", "JLS", "JLT", "JLU", "JLV", "JLW", "JLX", "JLY",
    "JLZ", "JMD", "JME", "JMF", "JMG", "JMH", "JMI", "JMJ", "JMK", "JML",
    "JMM", "JMN", "JMO", "JMP", "JMQ", "JMR", "JMS", "JMT", "JMU", "JMV",
    "JMW", "JMX", "JMY", "JMZ", "JND", "JNE", "JNF", "JNG", "JNH", "JNI",
    "JNJ", "JNK", "JNL", "JNM", "JNN", "JNO", "JNP", "JNQ", "JNR", "JNS",
    "JNT", "JNU", "JNV", "JNW", "JNX", "JNY", "JNZ", "JOD", "JOE", "JOF",
    "JOG", "JOH", "JOI", "JOJ", "JOK", "JOL", "JOM", "JON", "JOO", "JOP",
    "JOQ", "JOR", "JOS", "JOT", "JOU", "JOV", "JOW", "JOX", "JOY", "JOZ",
    "JPD", "JPE", "JPF", "JPG", "JPH", "JPI", "JPJ", "JPK", "JPL", "JPM",
    "JPN", "JPO", "JPP", "JPQ", "JPR", "JPS", "JPT", "JPU", "JPV", "JPW",
    "JPX", "JPY", "JPZ", "JQD", "JQE", "JQF", "JQG", "JQH", "JQI", "JQJ",
    "JQK", "JQL", "JQM", "JQN", "JQO", "JQP", "JQQ", "JQR", "JQS", "JQT",
    "JQU", "JQV", "JQW", "JQX", "JQY", "JQZ", "JRD", "JRE", "JRF", "JRG",
    "JRH", "JRI", "JRJ", "JRK", "JRL", "JRM", "JRN", "JRO", "JRP", "JRQ",
    "JRR", "JRS", "JRT", "JRU", "JRV", "JRW", "JRX", "JRY", "JRZ", "JSD",
    "JSE", "JSF", "JSG", "JSH", "JSI", "JSJ", "JSK", "JSL", "JSM", "JSN",
    "JSO", "JSP", "JSQ", "JSR", "JSS", "JST", "JSU", "JSV", "JSW", "JSX",
    "JSY", "JSZ", "JTD", "JTE", "JTF", "JTG", "JTH", "JTI", "JTJ", "JTK",
    "JTL", "JTM", "JTN", "JTO", "JTP", "JTQ", "JTR", "JTS", "JTT", "JTU",
    "JTV", "JTW", "JTX", "JTY", "JTZ", "JUD", "JUE", "JUF", "JUG", "JUH",
    "JUI", "JUJ", "JUK", "JUM", "JUN", "JUO", "JUP", "JUS", "JUT", "JUU",
    "JUV", "JUW", "JUZ", "JVD", "JVE", "JVF", "JVG", "JVI", "JVJ", "JVK",
    "JVL", "JVM", "JVN", "JVO", "JVP", "JVQ", "JVR", "JVS", "JVT", "JVU",
    "JVV", "JVW", "JVX", "JVY", "JVZ", "JWD", "JWE", "JWF", "JWG", "JWH",
    "JWI", "JWJ", "JWK", "JWL", "JWM", "JWN", "JWO", "JWP", "JWQ", "JWR",
    "JWS", "JWT", "JWU", "JWV", "JWW", "JWX", "JWY", "JWZ", "JXD", "JXE",
    "JXF", "JXG", "JXH", "JXI", "JXJ", "JXK", "JXL", "JXM", "JXN", "JXO",
    "JXP", "JXQ", "JXR", "JXS", "JXT", "JXU", "JXV", "JXW", "JXX", "JXY",
    "JXZ", "JYD", "JYE", "JYF", "JYG", "JYH", "JYI", "JYJ", "JYK", "JYL",
    "JYM", "JYN", "JYO", "JYP", "JYQ", "JYR", "JYS", "JYT", "JYU", "JYV",
    "JYW", "JYX", "JYY", "JYZ", "JZD", "JZE", "JZF", "JZG", "JZH", "JZI",
    "JZJ", "JZK", "JZL", "JZM", "JZN", "JZO", "JZP", "JZQ", "JZR", "JZS",
    "JZT", "JZU", "JZV", "JZW", "JZX", "JZY", "JZZ", "uncencored", "mosaic removed"
}

JAV_NOISE_PREFIXES = {
    "H264", "H265", "X264", "X265", "AVC", "HEVC", "AAC", "AC3", "DTS", "FLAC", "MP3", "MP4",
    "MKV", "AVI", "WMV", "FHD", "UHD", "HD", "SD", "4K", "8K", "1080P", "720P", "2160P", "480P",
    "60FPS", "30FPS", "10BIT", "8BIT", "HDR", "DV", "WEB", "WEBRIP", "WEBDL", "BLURAY", "BDRIP",
    "DVDRIP", "HDRIP", "CAM", "TS", "UNCENSORED", "LEAKED", "LEAK", "CHINESE", "SUB", "ENG",
    "ITA", "JPN", "KOR", "SPA", "FRE", "GER", "RUS", "IND", "AMZN", "NF", "DSNP", "HMAX", "ATVP",
    "RAW", "PART", "DISC", "CD", "VOL", "EP", "CH", "SEASON", "NO", "VER", "NEW", "FULL",
    "VIP", "PRO", "MAX", "PLUS", "NET", "COM", "ORG", "JAV", "PORN", "XXX", "SEX", "VR",
    "DDP", "ATMOS", "TRUEHD", "REMUX", "PROPER", "REPACK", "EXTENDED", "THEATRICAL", "UNRATED",
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
    "boobs", "tits", "assfuck", "deepthroat", "defloration", "czech", "cuckold", 
}

# Hanya judul/istilah Donghua spesifik (bebas kata generik seperti "wonderland", "bilibili", dll.)
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

# Grup fansub resmi (dicocokkan secara exact word/bracket, bukan substring bebas)
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


def extract_jav_code(text: str) -> Optional[str]:
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
        full_span = upper[m.start() : m.end()]
        has_hyphen = "-" in full_span or "_" in full_span
        if prefix in JAV_MAKER_PREFIXES:
            return f"{prefix}-{digits}"
        if has_hyphen and len(prefix) >= 3 and not prefix.isdigit():
            if not re.match(r"^(?:S\d+|E\d+|EP\d+|CD\d+|PT\d+|VOL\d+)$", prefix):
                return f"{prefix}-{digits}"

    return None


def _has_hanzi(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _has_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", text or ""))


def _detect_english_adult_details(combined_text: str, filename: str) -> Optional[Dict[str, Any]]:
    low = re.sub(r"[._-]+", " ", combined_text.lower())
    matched_studio = None
    for st in ENGLISH_ADULT_STUDIOS:
        if re.search(rf"(?:^|\s){re.escape(st)}(?:$|\s)", low):
            matched_studio = st.title()
            break

    tokens = set(re.findall(r"[a-z0-9]+", low))
    has_explicit_word = bool(tokens & ADULT_EXPLICIT_WORDS)
    has_scene_date = bool(re.search(r"(?:^|[\s._-])(\d{2})\.(\d{2})\.(\d{2})(?:$|[\s._-])", combined_text))

    if not matched_studio and not has_explicit_word:
        return None

    # Ekstrak tanggal rilis format YY.MM.DD -> 20YY-MM-DD
    rel_date = None
    year_val = _extract_year(combined_text)
    m_dt = re.search(r"(?:^|[\s._-])(\d{2})\.(\d{2})\.(\d{2})(?:$|[\s._-])", combined_text)
    if m_dt:
        yy, mm, dd = int(m_dt.group(1)), int(m_dt.group(2)), int(m_dt.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            full_y = 2000 + yy if yy <= 35 else 1900 + yy
            year_val = full_y
            rel_date = f"{full_y}-{mm:02d}-{dd:02d}"

    # Bersihkan judul scene dari nama studio, tanggal, dan tag kualitas
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
    # Cocokkan nama grup fansub yang berada di dalam kurung siku [SubsPlease] atau tanda hubung -SubsPlease
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

    cleaned = re.split(
        r"(?i)(?:^|\s)(?:S\d{1,2}E\d{1,3}|S\d{1,2}|Season\s*\d+|Episode\s*\d+|Ep\s*\d+|1080p|720p|2160p|480p|4K|WEB-?DL|WEBRip|BluRay|BDRip|DVDRip|HDRip|x264|x265|H264|H265|HEVC|10bit|AAC|FLAC|MULTI|DUAL|PROPER|REPACK|EXTENDED|THEATRICAL|UNRATED)(?:$|\s)",
        cleaned,
    )[0]
    cleaned = re.sub(r"(?:^|\s)(19[5-9]\d|20[0-3]\d)(?:$|\s).*$", "", cleaned)
    cleaned = re.sub(r"\s*-\s*\d{1,3}\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return cleaned or base


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
                return {
                    "title": sc.get("title") or clean_q,
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
    endpoints = ["tv", "movie"] if prefer_tv else ["movie", "tv"]

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

            # Pilih kandidat terbaik dengan ambang kemiripan ketat (>= 0.65)
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
                # Coba cari menggunakan 2-3 kata awal jika judul memiliki subjudul (misal "Apocalypse Z New World" -> "Apocalypse Z")
                short_words = title_query.split()
                if len(short_words) >= 3:
                    short_q = " ".join(short_words[:2])
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

            if not best_top or best_score < 0.65:
                continue

            tmdb_id = best_top["id"]
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

            # Klasifikasi akurat berdasarkan negara asal & genre resmi TMDb
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

            return {
                "title": (det.get("name") if ep == "tv" else det.get("title")) or title_query,
                "original_title": det.get("original_name") if ep == "tv" else det.get("original_title"),
                "year": _extract_year(rel_date or "") or year,
                "release_date": rel_date,
                "runtime": runtime,
                "studio": companies[0] if companies else None,
                "director": director,
                "plot": det.get("overview") or "",
                "content_category": cat,
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
    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
        id
        countryOfOrigin
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
                # Wajib lolos similarity >= 0.65 tanpa pengecualian agar tidak salah tangkap judul acak
                if sim < 0.65:
                    return None

                origin = (media.get("countryOfOrigin") or "").upper()
                if origin == "CN" or origin == "TW" or force_donghua:
                    cat = "donghua"
                elif origin == "JP":
                    cat = "anime"
                else:
                    return None

                best_title = t_obj.get("english") or t_obj.get("romaji") or title_query
                native_title = t_obj.get("native") or t_obj.get("romaji")
                studios = [s.get("name") for s in ((media.get("studios") or {}).get("nodes") or []) if s.get("name")]
                cover = (media.get("coverImage") or {}).get("extraLarge") or (media.get("coverImage") or {}).get("large")
                desc = re.sub(r"<[^>]+>", "", media.get("description") or "").strip()
                score = round((media.get("averageScore") or 0) / 10.0, 1)
                return {
                    "title": best_title,
                    "original_title": native_title,
                    "year": media.get("seasonYear"),
                    "runtime": int(media.get("duration") or 24),
                    "studio": studios[0] if studios else None,
                    "plot": desc,
                    "content_category": cat,
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
            return {
                "title": show.get("name") or title_query,
                "original_title": show.get("name"),
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


def enrich_media_item(item: Dict[str, Any], settings: Dict[str, str]) -> Dict[str, Any]:
    fname = item.get("file_name") or item.get("title") or ""
    fpath = item.get("file_path") or ""
    parent_name = item.get("parent_name") or (os.path.basename(os.path.dirname(fpath)) if fpath else "")
    combined_hint = f"{parent_name} {fname}".strip()

    unique_key = item.get("unique_key") or fname
    tmdb_key = settings.get("tmdb_api_key", "")
    tpdb_key = settings.get("tpdb_api_key", "")

    # Pilih sumber judul terbaik (jika nama file generik seperti "scene01.mp4", pakai parent_name)
    stem_low = os.path.splitext(fname)[0].lower().strip()
    primary_name_for_title = parent_name if (stem_low in GENERIC_FILE_STEMS and parent_name) else fname

    season_num, episode_num = _extract_season_episode(combined_hint)
    year_hint = _extract_year(combined_hint)
    clean_title = clean_search_title(primary_name_for_title)

    # 1. JAV (berdasarkan kode produksi resmi JAV)
    jav_code = extract_jav_code(combined_hint)
    if jav_code:
        meta = _fetch_jav_metadata(jav_code)
        meta["local_poster_path"] = download_poster_image(meta.get("poster_url"), unique_key)
        return meta

    # 2. English Adult (studio/tag eksplisit - jangan pernah dilempar ke TMDb/AniList)
    adult_info = _detect_english_adult_details(combined_hint, fname)
    if adult_info:
        meta = _fetch_tpdb_metadata(adult_info["title"], tpdb_key) if tpdb_key else None
        if not meta:
            meta = {
                "title": adult_info["title"],
                "year": adult_info["year"],
                "release_date": adult_info["release_date"],
                "studio": adult_info["studio"],
                "content_category": "english_adult",
                "is_adult": 1,
                "has_metadata": 1,
                "metadata_source": "adult_scene_parser",
                "genres": ["Adult", adult_info["studio"]] if adult_info["studio"] else ["Adult"],
            }
        meta["local_poster_path"] = download_poster_image(meta.get("poster_url"), unique_key)
        return meta

    # 3. Donghua / Anime dengan sinyal kuat (Fansub resmi, karakter Hanzi/Kana, atau judul Donghua spesifik)
    donghua_strong = _is_strong_donghua_signal(combined_hint)
    anime_strong = _is_strong_anime_signal(combined_hint)

    if donghua_strong or anime_strong:
        meta = _fetch_anilist_metadata(clean_title, force_donghua=donghua_strong)
        if not meta and tmdb_key:
            meta = _fetch_tmdb_metadata(clean_title, tmdb_key, year=year_hint, prefer_tv=True)
        if not meta:
            meta = {
                "title": clean_title or fname,
                "year": year_hint,
                "content_category": "donghua" if donghua_strong else "anime",
                "is_adult": 0,
                "has_metadata": 1,
                "metadata_source": "anime_parser",
                "genres": ["Donghua" if donghua_strong else "Anime"],
            }
        if season_num is not None:
            meta["season_num"] = season_num
        if episode_num is not None:
            meta["episode_num"] = episode_num
        meta["local_poster_path"] = download_poster_image(meta.get("poster_url"), unique_key)
        return meta

    # 4. General Movie & TV Show via TMDb (otomatis mendeteksi Anime JP / Donghua CN jika terindeks di TMDb)
    prefer_tv = season_num is not None or episode_num is not None
    meta = _fetch_tmdb_metadata(clean_title, tmdb_key, year=year_hint, prefer_tv=prefer_tv)

    if not meta and prefer_tv:
        meta = _fetch_tvmaze_fallback(clean_title)

    if not meta:
        meta = {
            "title": clean_title or fname,
            "year": year_hint,
            "content_category": "tv_show" if prefer_tv else "general_movie",
            "is_adult": 0,
            "has_metadata": 1,
            "metadata_source": "filename_parser",
        }

    if season_num is not None:
        meta["season_num"] = season_num
    if episode_num is not None:
        meta["episode_num"] = episode_num

    meta["local_poster_path"] = download_poster_image(meta.get("poster_url"), unique_key)
    return meta