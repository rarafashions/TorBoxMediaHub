"""
TorBox Media Hub - deterministic media classification helpers.

This module performs local filename/folder analysis only. It does not call metadata
providers, TorBox, SQLite, or video probing, so classification itself has no network cost.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import metadata_engine as meta
import indo_keyword_engine as indo_eng


VALID_CATEGORIES = {
    "jav",
    "english_adult",
    "indo",
    "anime",
    "donghua",
    "tv_show",
    "general_movie",
}
NEEDS_REVIEW = "needs_review"
CLASSIFIED = "classified"
REVIEW_THRESHOLD = 70


def _plain_tokens(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def normalize_media_title(filename: str, parent_name: str = "") -> str:
    """
    Produce a search-friendly title without changing the source filename.

    Keeps meaningful bracketed title text (e.g. [REC]) but removes common
    release-group and technical bracket tags.
    """
    stem = os.path.splitext(os.path.basename(filename or ""))[0].strip()
    parent = (parent_name or "").strip()
    if stem.lower() in meta.GENERIC_FILE_STEMS and parent:
        stem = os.path.splitext(os.path.basename(parent))[0].strip()

    # Preserve meaningful bracketed title text (e.g. [REC]) while removing
    # fansub/release labels and multilingual audio notes.
    known_groups = set(meta.ANIME_FANSUB_GROUPS)
    technical_brackets = {
        "1080p", "720p", "2160p", "480p", "4k", "8k", "hevc",
        "x264", "x265", "h264", "h265", "10bit", "8bit", "web dl",
        "webrip", "bluray", "bdrip", "aac", "dual audio", "eng sub",
    }
    language_tokens = {"tel", "tam", "hin", "eng", "en", "multi", "ita", "jpn", "kor", "spa", "fre", "ger", "rus"}

    def clean_bracket(match: re.Match) -> str:
        content = match.group(1).strip()
        token = _plain_tokens(content)
        words = set(token.split())
        if token in known_groups or token in technical_brackets or (words and words.issubset(language_tokens)):
            return " "
        if re.fullmatch(r"[A-Z0-9]{2,12}", content) and token != "rec":
            return " "
        return f" {content} "

    bracket_cleaned = re.sub(r"\[([^\]]*)\]", clean_bracket, stem)
    # Use the same release-token cleaner as provider lookup after handling
    # meaningful bracketed title text.
    cleaned = meta.clean_search_title(bracket_cleaned)
    # Remove the year token itself, not everything after it. Meaningful suffixes
    # such as HQ may legitimately follow the year in a release filename.
    cleaned = re.sub(r"(?<!\d)\b(?:19[5-9]\d|20[0-3]\d)\b(?!\d)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    return cleaned or meta.clean_search_title(filename or "")


def _has_episode_structure(filename: str, parent_name: str) -> bool:
    combined = f"{parent_name or ''} {filename or ''}"
    return bool(re.search(
        r"(?i)(?:^|[\s._\-\[\(])(?:S\d{1,2}\s*E\d{1,3}|\d{1,2}x\d{1,3}|"
        r"Season\s*\d+|Episode\s*\d+|Ep\.?\s*\d+|Complete\s*Series)"
        r"(?:$|[\s._\-\]\)])",
        combined,
    ))


def classify_media(
    filename: str,
    parent_name: str = "",
    normalized_title: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Classify one media item using local filename/folder/provider metadata.

    Metadata is treated as evidence, not as an instruction: adult classification
    uses title/studio/tag signals, Indo uses title/tags/description, and episodic
    categories use explicit type/season/episode signals. No network/API calls occur.
    """
    filename = (filename or "").strip()
    parent_name = (parent_name or "").strip()
    working_name = (normalized_title or "").strip() or filename
    metadata = metadata if isinstance(metadata, dict) else {}
    def _as_text(value: Any) -> str:
        if isinstance(value, dict):
            return " ".join(str(v) for v in value.values() if v is not None)
        if isinstance(value, (list, tuple, set)):
            return " ".join(str(v) for v in value if v is not None)
        return str(value or "")

    tags_text = " ".join(_as_text(metadata.get(k)) for k in ("tags", "genres", "genre", "keywords", "cast"))
    metadata_title = _as_text(metadata.get("name") or metadata.get("title") or working_name)
    metadata_description = _as_text(metadata.get("description") or metadata.get("overview") or metadata.get("plot"))
    metadata_type = _as_text(metadata.get("type") or metadata.get("media_type") or metadata.get("content_type")).lower()
    # Descriptions can contain incidental explicit vocabulary. They are not used
    # alone to classify English Adult; use title + tags + named studio/brand.
    adult_evidence = f"{working_name} {tags_text} {_as_text(metadata.get('studio'))} {_as_text(metadata.get('network'))}".strip()
    taxonomy_evidence = f"{working_name} {tags_text}".strip()
    combined = f"{parent_name} {working_name} {tags_text}".strip()
    normalized = normalize_media_title(working_name, parent_name)
    season_num, episode_num = meta._extract_season_episode(os.path.splitext(working_name)[0])
    if season_num is None or episode_num is None:
        parent_season, parent_episode = meta._extract_season_episode(parent_name)
        season_num = season_num if season_num is not None else parent_season
        episode_num = episode_num if episode_num is not None else parent_episode
    signals: List[str] = []
    warnings: List[str] = []
    engine_results: List[Dict[str, str]] = []
    category: Optional[str] = None
    confidence = 0
    method = "no_category_match"

    # Category-specific evidence. Adult attributes are extracted separately
    # and never act as an Indonesian-language signal.
    taxonomy_evidence = f"{working_name} {tags_text}".strip()
    combined = f"{parent_name} {working_name} {tags_text}".strip()
    normalized = normalize_media_title(working_name, parent_name)
    season_num, episode_num = meta._extract_season_episode(os.path.splitext(working_name)[0])
    if season_num is None or episode_num is None:
        parent_season, parent_episode = meta._extract_season_episode(parent_name)
        season_num = season_num if season_num is not None else parent_season
        episode_num = episode_num if episode_num is not None else parent_episode

    def record(engine: str, result: str) -> None:
        engine_results.append({"engine": engine, "result": result})

    # 1. INDO: first priority. English sexual keywords are not in the Indo dictionary.
    indo_title_verdict = indo_eng.classify_indo({"title": working_name})
    indo_verdict = indo_eng.classify_indo({
        "title": working_name,
        "tags": tags_text,
        "description": f"{parent_name} {metadata_description}",
        "original_url": _as_text(metadata.get("url") or metadata.get("original_url")),
        "overview": metadata_description,
    })
    selected_indo = indo_title_verdict if indo_title_verdict.get("is_indo") else indo_verdict
    if selected_indo.get("is_indo"):
        category, confidence, method = "indo", 94, "indo_keyword_classifier"
        signals.extend([f"indo_keyword:{term}" for term in selected_indo.get("matched_keywords", [])])
        record("indo", "passed")
    else:
        record("indo", "not_matched")

    # 2. JAV: only a validated production code can pass.
    known_jav_code = meta.extract_jav_code(combined, allow_generic_fallback=False)
    if known_jav_code:
        code_number = known_jav_code.rsplit("-", 1)[-1]
        if code_number in {"480", "720", "1080", "2160"} and re.search(
            rf"(?i)(?:^|[\s._-]){code_number}p(?:$|[\s._-])", combined
        ):
            known_jav_code = None
    if category is None and known_jav_code:
        category, confidence, method = "jav", 99, "known_jav_code"
        normalized = known_jav_code
        signals.extend(["known_jav_prefix", "valid_production_code"])
        record("jav", "passed")
    elif category is None:
        record("jav", "not_matched")
    else:
        record("jav", "skipped")

    # 3. ENGLISH ADULT: studio/series or generic explicit vocabulary, only
    # after Indo and JAV have not matched.
    adult_evidence = f"{working_name} {tags_text} {_as_text(metadata.get('studio'))} {_as_text(metadata.get('network'))}".strip()
    western_studio = meta._has_explicit_english_adult_studio(adult_evidence)
    adult = meta._detect_english_adult_details(adult_evidence, working_name)
    if category is None and (western_studio or adult):
        category, confidence, method = "english_adult", (97 if western_studio else 88), ("explicit_adult_studio" if western_studio else "explicit_adult_keyword")
        normalized = (adult or {}).get("title") or normalized
        signals.append("explicit_adult_studio" if western_studio else "explicit_adult_keyword")
        record("english_adult", "passed")
    elif category is None:
        record("english_adult", "not_matched")
    else:
        record("english_adult", "skipped")

    hanzi = meta._has_hanzi(combined)
    kana = meta._has_kana(combined)
    taxonomy_low = re.sub(r"[._-]+", " ", taxonomy_evidence.lower())
    donghua_title = any(
        re.search(rf"(?:^|\s){re.escape(k.lower())}(?:$|\s)", taxonomy_low)
        for k in meta.DONGHUA_SPECIFIC_KEYWORDS
    )
    anime_signal = meta._is_strong_anime_signal(taxonomy_evidence)
    explicit_anime_tag = bool(re.search(
        r"(?i)(?:^|[\s._\-\[\(])anime(?:$|[\s._\-\]\)])", taxonomy_evidence,
    )) or any(
        _plain_tokens(group) in {_plain_tokens(x) for x in meta.ANIME_FANSUB_GROUPS}
        for group in re.findall(r"\[([^\]]+)\]", taxonomy_evidence)
    )
    generic_code_candidate = bool(re.search(
        r"(?i)(?<![A-Z0-9])[A-Z]{2,8}[-_]\d{2,6}(?![A-Z0-9])", combined,
    ))
    if generic_code_candidate and kana and not explicit_anime_tag:
        anime_signal = False
    donghua_explicit = donghua_title or bool(re.search(
        r"(?i)(?:^|[\s._\-\[\(])(?:donghua|guoman|chinese anime)(?:$|[\s._\-\]\)])",
        taxonomy_evidence,
    ))

    # 4. TV SHOW
    # An explicit Donghua title/tag is the more specific category, so the generic
    # episodic signal yields to it (rule 4).
    tv_type = metadata_type in {"series", "tv", "tv_show", "show", "episode"}
    episode_pattern = _has_episode_structure(working_name, parent_name)
    if category is None and (tv_type or episode_pattern) and not donghua_explicit:
        category, confidence, method = "tv_show", 94, ("provider_media_type_series" if tv_type else "explicit_episode_structure")
        signals.append("provider_media_type_series" if tv_type else "season_episode_pattern")
        record("tv_show", "passed")
        # Rule 6: Korean or dual-audio hints make Anime-vs-TV genuinely ambiguous.
        korean_hint = bool(re.search(r"(?i)\b(?:korean|korea|kor)\b", combined))
        dual_audio_hint = bool(re.search(r"(?i)\bdual[\s._-]?audio\b", combined))
        if episode_pattern and not explicit_anime_tag and (korean_hint or dual_audio_hint):
            warnings.append("Episodic title carries Korean or dual-audio hints; confirm Anime vs TV Show.")
            method = "ambiguous_episodic_language_hint"
    elif category is None:
        record("tv_show", "not_matched")
    else:
        record("tv_show", "skipped")

    # 5. ANIME
    donghua_only_anime_wording = donghua_explicit and not kana and not re.search(r"(?i)(?:^|\s)japanese(?:$|\s)", taxonomy_evidence)
    if donghua_only_anime_wording:
        anime_signal = False
        explicit_anime_tag = False
    if category is None and (anime_signal or explicit_anime_tag) and not (donghua_explicit and hanzi and not kana):
        category, confidence, method = "anime", 96, "explicit_anime_signal"
        signals.append("anime_language_tag_or_fansub")
        record("anime", "passed")
    elif category is None:
        record("anime", "not_matched")
    else:
        record("anime", "skipped")

    # 6. DONGHUA
    if category is None and donghua_explicit:
        category, confidence, method = "donghua", 98, "explicit_donghua_signal"
        signals.append("explicit_donghua_title_or_tag")
        record("donghua", "passed")
    elif category is None:
        if hanzi and not kana:
            warnings.append("Chinese characters found, but Donghua is not confirmed.")
            method = "ambiguous_language_signal"
        record("donghua", "not_matched")
    else:
        record("donghua", "skipped")

    # 7. UNVERIFIED CODE (rule 2): a code-like token whose prefix is not a
    # validated JAV maker is never assumed to be JAV and never falls through to a
    # title guess; it goes to Needs Review.
    opaque_code = bool(re.fullmatch(r"[A-Z]{2,8}[\s_-]?\d{2,6}", normalized))
    if category is None and (generic_code_candidate or opaque_code):
        confidence, method = 0, "unverified_code"
        warnings.append("A code-like token is present but its prefix is not a validated JAV maker.")
        record("uncategorized", "passed")

    # 8. GENERAL MOVIE
    if category is None and metadata_type in {"movie", "film", "feature", "feature_film"}:
        category, confidence, method = "general_movie", 90, "provider_media_type_movie"
        signals.append("provider_media_type_movie")
        record("general_movie", "passed")
    elif category is None:
        generic = normalized.lower().strip() in meta.GENERIC_FILE_STEMS
        if not generic and not opaque_code and len(normalized) >= 3:
            # Rule 7: a release year is required. A multiword or single-word name
            # alone is not enough corroboration to write a content category.
            if re.search(r"\b(?:19[5-9]\d|20[0-3]\d)\b", combined):
                category, confidence, method = "general_movie", 82, "title_plus_year"
                signals.extend(["recognizable_title", "release_year"])
        if category:
            record("general_movie", "passed")
        else:
            record("general_movie", "not_matched")
    else:
        record("general_movie", "skipped")

    # Attributes are independent of category: adult genres remain searchable,
    # but do not determine English Adult or override Indo.
    attribute_text = f"{working_name} {tags_text} {metadata_description}".lower()
    attribute_tokens = set(re.findall(r"[a-z0-9]+", attribute_text))
    adult_attributes = sorted(attribute_tokens & meta.ADULT_EXPLICIT_WORDS)
    descriptive_attributes = sorted(attribute_tokens & {
        "amateur", "asian", "homemade", "cheating", "romantic", "verified",
    })
    attributes = (["adult"] + adult_attributes if adult_attributes else []) + descriptive_attributes

    if category is None:
        category, confidence = "uncategorized", 0
        # Preserve a specific ambiguity marker (unverified_code, ambiguous_language_signal).
        if method in {"no_category_match", ""}:
            method = "no_engine_matched"
        record("uncategorized", "passed")

    status = CLASSIFIED if category != "uncategorized" and confidence >= REVIEW_THRESHOLD and not warnings else NEEDS_REVIEW
    return {
        "original_filename": filename,
        "parent_name": parent_name,
        "normalized_title": normalized,
        # Contract (docs/CLASSIFICATION_ENGINE_V1.2.md): a Needs Review result
        # never exposes a candidate category to the caller.
        "detected_category": category if status == CLASSIFIED else None,
        "category_confidence": confidence,
        "classification_method": method,
        "classification_status": status,
        "detected_code": known_jav_code,
        "season_num": season_num,
        "episode_num": episode_num,
        "signals": signals,
        "warnings": warnings,
        "metadata_type": metadata_type or None,
        "metadata_evidence": {"tags_present": bool(tags_text.strip()), "description_present": bool(metadata_description.strip())},
        "engine_results": engine_results,
        "attributes": attributes,
    }
