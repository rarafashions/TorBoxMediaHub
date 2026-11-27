"""Phase 7.1 conservative subtitle candidate matching and diagnostics.

This module never declares a subtitle a guaranteed match. It scores provider metadata,
rejects only explicit language/season/episode/year conflicts, and exposes reasons.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

_WORD_RE = re.compile(r"[a-z0-9]+", re.I)

def _tokens(value: Any) -> set[str]:
    return {x for x in _WORD_RE.findall(str(value or "").lower()) if len(x) > 1}

def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None

def evaluate_candidate(candidate: Dict[str, Any], *, title: str = "", year: Any = None,
                       season: Any = None, episode: Any = None,
                       language: Optional[str] = None) -> Dict[str, Any]:
    """Return {accepted, confidence, reasons}; accepted only fails on explicit conflicts."""
    reasons: List[str] = []
    score = 0.50
    requested = (language or "").lower()
    actual = str(candidate.get("SubLanguageID") or candidate.get("ISO639") or "").lower()
    aliases = {"eng": "en", "english": "en", "ind": "id", "indonesian": "id"}
    actual = aliases.get(actual, actual)
    if requested in ("en", "id") and actual:
        if actual != requested:
            return {"accepted": False, "confidence": 0.0, "reasons": ["language_conflict"]}
        score += 0.15
        reasons.append("language_match")
    elif requested:
        reasons.append("language_unconfirmed")

    wanted_tokens = _tokens(title)
    release = " ".join(str(candidate.get(k) or "") for k in
                        ("MovieReleaseName", "MovieName", "SeriesName", "SubFileName"))
    got_tokens = _tokens(release)
    if wanted_tokens and got_tokens:
        overlap = len(wanted_tokens & got_tokens) / max(1, len(wanted_tokens))
        score += min(0.25, overlap * 0.25)
        reasons.append("title_overlap" if overlap >= 0.5 else "weak_title_overlap")
    else:
        reasons.append("title_metadata_missing")

    requested_year = _int_or_none(year)
    candidate_year = _int_or_none(candidate.get("MovieYear"))
    if requested_year and candidate_year:
        if requested_year != candidate_year:
            return {"accepted": False, "confidence": 0.0, "reasons": ["year_conflict"]}
        score += 0.10
        reasons.append("year_match")

    for key, wanted, label in (("SeriesSeason", season, "season"),
                               ("SeriesEpisode", episode, "episode")):
        w = _int_or_none(wanted)
        got = _int_or_none(candidate.get(key))
        if w is not None and got is not None:
            if w != got:
                return {"accepted": False, "confidence": 0.0, "reasons": [f"{label}_conflict"]}
            score += 0.10
            reasons.append(f"{label}_match")

    return {"accepted": True, "confidence": round(min(score, 1.0), 3), "reasons": reasons or ["metadata_sparse"]}

def rank_candidates(candidates: Iterable[Dict[str, Any]], **expected: Any) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    ranked = []
    for candidate in candidates:
        verdict = evaluate_candidate(candidate, **expected)
        if verdict["accepted"]:
            ranked.append((candidate, verdict))
    return sorted(ranked, key=lambda pair: pair[1]["confidence"], reverse=True)
