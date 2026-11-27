"""Phase 7.2: non-destructive subtitle quality audit for TorBox Media Hub.

Audits SRT/VTT/ASS/SSA text files. It never deletes, rewrites, moves, or replaces
subtitle files. Language detection is deliberately heuristic and reports confidence.
"""
from __future__ import annotations
import os, re
from typing import Any, Dict, List, Optional

SUPPORTED_TEXT_FORMATS = {'.srt', '.vtt', '.ass', '.ssa'}
TIMESTAMP_RE = re.compile(r'(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{2,3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{2,3}')
ASS_DIALOGUE_RE = re.compile(r'^Dialogue:\s*[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,(.*)$', re.I)
ERROR_MARKERS = ('404 not found', 'access denied', 'cloudflare', 'captcha', 'subtitle not found', 'internal server error')
EN_MARKERS = {'the','and','you','that','this','with','have','for','not','what','your','are','was','but','know','love'}
ID_MARKERS = {'yang','dan','tidak','aku','kamu','dia','ini','itu','untuk','dengan','saya','ada','bisa','akan','sudah','belum'}

def _read_text(path: str) -> tuple[Optional[str], Optional[str]]:
    try:
        with open(path, 'rb') as f: raw = f.read(8 * 1024 * 1024 + 1)
    except OSError as exc:
        return None, f'read_error:{type(exc).__name__}'
    if len(raw) > 8 * 1024 * 1024:
        return None, 'file_too_large_for_text_audit'
    if not raw: return '', None
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try: return raw.decode(enc), None
        except UnicodeDecodeError: pass
    return None, 'decode_error'

def _extract_cues(text: str, ext: str) -> List[str]:
    cues=[]
    for line in text.splitlines():
        s=line.strip()
        if not s or s.isdigit() or s.startswith(('WEBVTT','NOTE','STYLE','Kind:','Language:','[Script Info]','[V4+ Styles]','[V4 Styles]','Format:')): continue
        m=ASS_DIALOGUE_RE.match(s) if ext in {'.ass','.ssa'} else None
        if '-->' in s: continue
        if s.startswith(('Dialogue:','Comment:')) and not m: continue
        if m: s=m.group(1).replace(r'\N',' ').replace(r'\n',' ').replace(r'{\i1}',' ').replace(r'{\i0}',' ')
        s=re.sub(r'<[^>]+>|\{[^}]*\}', ' ', s)
        s=re.sub(r'\\N|\\n',' ',s)
        s=re.sub(r'\s+',' ',s).strip()
        if re.search(r'[A-Za-zÀ-ÿ0-9]',s): cues.append(s)
    return cues

def _language_guess(cues: List[str]) -> Dict[str, Any]:
    words=re.findall(r"[a-zA-ZÀ-ÿ']+", ' '.join(cues).lower())
    tokens=set(words)
    en=len(tokens & EN_MARKERS); ind=len(tokens & ID_MARKERS)
    total=en+ind
    if total < 3: return {'detected_language':'unknown','language_confidence':'low','language_evidence':{'en_markers':en,'id_markers':ind}}
    if en == ind: lang='ambiguous'
    else: lang='en' if en>ind else 'id'
    confidence='medium' if abs(en-ind)>=2 else 'low'
    return {'detected_language':lang,'language_confidence':confidence,'language_evidence':{'en_markers':en,'id_markers':ind}}

def audit_subtitle_file(path: str, expected_language: Optional[str]=None) -> Dict[str, Any]:
    """Return a diagnostic report. Status: healthy, warning, invalid, or unsupported."""
    report={'path':path,'status':'warning','format':os.path.splitext(path or '')[1].lower(),'issues':[],'cue_count':0,'text_characters':0}
    ext=report['format']
    if ext not in SUPPORTED_TEXT_FORMATS:
        report.update(status='unsupported'); report['issues'].append('unsupported_or_binary_format'); return report
    if not path or not os.path.isfile(path):
        report.update(status='invalid'); report['issues'].append('file_missing'); return report
    try: size=os.path.getsize(path)
    except OSError: size=0
    report['file_size_bytes']=size
    if size < 20:
        report.update(status='invalid'); report['issues'].append('file_empty_or_too_small'); return report
    text,err=_read_text(path)
    if err:
        report.update(status='invalid'); report['issues'].append(err); return report
    cues=_extract_cues(text or '',ext)
    joined=' '.join(cues)
    report['cue_count']=len(cues); report['text_characters']=len(joined)
    if not cues:
        report.update(status='invalid'); report['issues'].append('no_subtitle_cues'); return report
    low=joined.lower()
    if any(marker in low for marker in ERROR_MARKERS): report['issues'].append('provider_error_page_or_message')
    if ext in {'.srt','.vtt'} and not TIMESTAMP_RE.search(text or ''): report['issues'].append('timestamp_structure_not_detected')
    if len(cues)<2 or len(joined)<30: report['issues'].append('very_short_subtitle_content')
    if len(cues)>0 and sum(1 for c in cues if len(c)>500)/len(cues)>0.25: report['issues'].append('unusually_long_cue_lines')
    lang=_language_guess(cues); report.update(lang)
    expected=(expected_language or '').lower()
    aliases={'eng':'en','english':'en','ind':'id','indonesian':'id'}; expected=aliases.get(expected,expected)
    if expected in {'en','id'} and lang['detected_language'] in {'en','id'} and lang['detected_language'] != expected:
        report['issues'].append('probable_language_mismatch')
    if 'provider_error_page_or_message' in report['issues'] or 'no_subtitle_cues' in report['issues']:
        report['status']='invalid'
    elif report['issues']:
        report['status']='warning'
    else: report['status']='healthy'
    return report
