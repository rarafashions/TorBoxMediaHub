"""Local keyword classifier for the Indo Discovery category.

No metadata or subtitle services are called here. The classifier evaluates
provider-supplied title, tags, description, canonical page URL, plus the
downloaded file name and parent folder when the caller provides them.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List

# Strong terms can classify a result on their own. Weights are intentionally
# conservative; ambiguous words require multiple independent signals.
DEFAULT_KEYWORDS: Dict[str, Dict[str, int]] = {
    "strong": {
        "binor": 4, "bini orang": 4, "istri orang": 4, "suami orang": 3,
        "bokep indo": 4, "bokep indonesia": 4, "video indo": 4,
        "bahasa indonesia": 4, "cindo": 3, "chindo": 3,
        "di selingkuhi": 3, "diselingkuhi": 3,
        # The infidelity terms are unambiguous Indonesian/Malay regional
        # vocabulary; titles like "lagi selingkuh ..." must land in Indo even
        # when the provider also attaches English adult genre tags.
        "selingkuh": 3, "selingkuhan": 3, "perselingkuhan": 3, "berselingkuh": 3,
    },
    "supporting": {
        "curhat": 2,
        "indonesia": 1, "indonesian": 1,
        "hijab": 1, "jilbab": 1, "berhijab": 1,
        "lokal": 1, "amatir": 1,
        # Kinship and household roles. These are regional vocabulary, not
        # English adult descriptors, so they stay in the supporting group.
        "janda": 1, "duda": 1, "mertua": 1, "ipar": 1, "keponakan": 1,
        "sepupu": 1, "pembantu": 1, "majikan": 1, "tante": 1, "bos": 1,
        "pasangan": 1, "tetangga": 1,
        # Work and social roles.
        "guru": 1, "dosen": 1, "perawat": 1, "bidan": 1, "pramugari": 1,
        "satpam": 1, "ojol": 1, "tukang": 1, "petani": 1, "nelayan": 1,
        "karyawan": 1, "buruh": 1, "pabrik": 1, "tambang": 1,
        # Settings that are specific to Indonesian housing and daily life.
        # "kos" intentionally stands alone: keeping both "kos" and "anak kos"
        # would count one phrase as two independent clues.
        "kos": 1, "kontrakan": 1, "kampung": 1,
        "sawah": 1, "kebun": 1, "warung": 1, "pasar": 1,
        # Indonesian function words and particles. Two of them already prove the
        # language even when the title has no regional noun.
        "udah": 1, "sudah": 1, "belum": 1, "masih": 1, "tidak": 1,
        "banget": 1, "bgt": 1, "gak": 1, "nggak": 1, "enggak": 1, "tak": 1,
        "aja": 1, "lagi": 1, "sama": 1, "buat": 1, "biar": 1, "juga": 1,
        "yang": 1, "dari": 1, "kalau": 1, "kalo": 1, "karena": 1, "jadi": 1,
        "emang": 1, "memang": 1, "cuma": 1, "doang": 1, "bikin": 1,
        "bisa": 1, "mau": 1, "harus": 1, "punya": 1, "orang": 1,
        "ini": 1, "itu": 1, "gitu": 1, "gini": 1, "kayak": 1,
        "sih": 1, "deh": 1, "dong": 1, "kok": 1, "nih": 1, "tuh": 1,
        # Address terms that are Indonesian rather than English.
        "mas": 1, "mbak": 1, "kak": 1, "abang": 1, "adik": 1, "kakak": 1,
        "bapak": 1, "ibu": 1,
        # Regions, ethnic groups, and cities.
        "sunda": 1, "jawa": 1, "batak": 1, "bugis": 1, "madura": 1,
        "banjar": 1, "aceh": 1, "padang": 1, "manado": 1, "ambon": 1,
        "papua": 1, "lombok": 1,
        "jakarta": 1, "bandung": 1, "surabaya": 1, "medan": 1,
        "bali": 1, "semarang": 1, "yogyakarta": 1, "jogja": 1, "malang": 1,
        "makassar": 1, "palembang": 1, "bekasi": 1, "depok": 1,
        "tangerang": 1, "bogor": 1, "cirebon": 1, "jember": 1,
        "banyuwangi": 1, "sukabumi": 1,
    },
}

# Indonesian titles routinely attach possessive and emphatic suffixes to a
# base word ("tetangganya", "jandanya", "tantenya"). Exact phrase matching
# misses all of them, so every dictionary phrase also accepts these suffixes.
_SUFFIX_VARIANTS = ("nya", "ku", "mu", "lah", "kah", "pun")


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[_./\\-]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    # Word-boundary-like matching prevents e.g. 'bali' matching inside a longer word.
    return f" {normalize_text(phrase)} " in f" {text} "


def _phrase_present(text: str, phrase: str) -> bool:
    """Match a dictionary phrase, including common Indonesian suffixes."""
    if _contains_phrase(text, phrase):
        return True
    return any(_contains_phrase(text, f"{phrase}{suffix}") for suffix in _SUFFIX_VARIANTS)


def classify_indo(item: Dict[str, Any], min_score: int = 3) -> Dict[str, Any]:
    """Return classification details; never mutates the input item."""
    fields: List[str] = []
    for key in ("title", "tags", "description", "overview", "original_url", "url", "filename", "parent_name"):
        value = item.get(key)
        if isinstance(value, (list, tuple, set)):
            fields.extend(str(v) for v in value)
        elif value:
            fields.append(str(value))
    text = normalize_text(" ".join(fields))
    matched: List[str] = []
    score = 0
    for group in ("strong", "supporting"):
        for phrase, weight in DEFAULT_KEYWORDS[group].items():
            if _phrase_present(text, phrase):
                matched.append(phrase)
                score += weight
    # Generic supporting terms alone are insufficient; require two independent
    # supporting signals when no strong Indonesian indicator is present. Two
    # weight-1 clues therefore reach the documented minimum score of 2, while a
    # single clue still cannot classify a title.
    strong_match = any(term in matched for term in DEFAULT_KEYWORDS["strong"])
    supporting_matches = [term for term in matched if term in DEFAULT_KEYWORDS["supporting"]]
    # A single country adjective (Indonesia/Indonesian) is weak metadata and
    # cannot classify a title by itself. Strong local phrases may pass alone;
    # otherwise require at least two independent Indonesian supporting clues.
    # The documented policy is two clues, so the default threshold is 2 even
    # though the historic argument default was 3.
    threshold = min(min_score, 2)
    accepted = score >= threshold and (strong_match or len(supporting_matches) >= 2)
    return {
        "category": "indo" if accepted else None,
        "is_indo": accepted,
        "score": score,
        "matched_keywords": matched,
        "classifier": "indo-keyword-v2-no-english-adult-terms",
    }


def filter_indo_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return copies of matching items tagged with the Indo category."""
    results: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        verdict = classify_indo(item)
        if not verdict["is_indo"]:
            continue
        identity = str(item.get("id") or item.get("original_url") or item.get("url") or "").strip().casefold()
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        enriched = dict(item)
        enriched["category"] = "indo"
        enriched["indo_match"] = verdict
        results.append(enriched)
    return results
