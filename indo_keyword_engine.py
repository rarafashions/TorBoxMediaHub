"""Local keyword classifier for the Indo Discovery category.

No metadata or subtitle services are called here. The classifier only evaluates
provider-supplied title, tags, description and canonical page URL.
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
        "jakarta": 1, "bandung": 1, "surabaya": 1, "medan": 1,
        "bali": 1, "tetangga": 1, "kontrakan": 1,
        "anak kos": 1, "pasangan": 1, "tante": 1, "bos": 1,
        "karyawan": 1,
    },
}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[_./\\-]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    # Word-boundary-like matching prevents e.g. 'bali' matching inside a longer word.
    return f" {normalize_text(phrase)} " in f" {text} "


def classify_indo(item: Dict[str, Any], min_score: int = 3) -> Dict[str, Any]:
    """Return classification details; never mutates the input item."""
    fields: List[str] = []
    for key in ("title", "tags", "description", "overview", "original_url", "url"):
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
            if _contains_phrase(text, phrase):
                matched.append(phrase)
                score += weight
    # Generic supporting terms alone are insufficient; require two independent
    # supporting signals when no strong Indonesian indicator is present.
    strong_match = any(term in matched for term in DEFAULT_KEYWORDS["strong"])
    supporting_matches = [term for term in matched if term in DEFAULT_KEYWORDS["supporting"]]
    # A single country adjective (Indonesia/Indonesian) is weak metadata and
    # cannot classify a title by itself. Strong local phrases may pass alone;
    # otherwise require at least two independent Indonesian supporting clues.
    accepted = score >= min_score and (strong_match or len(supporting_matches) >= 2)
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
