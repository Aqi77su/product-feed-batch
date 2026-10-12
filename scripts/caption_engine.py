"""Compile reviewed captions into one non-duplicated Jianying subtitle stream.

Public cue times and word times are absolute milliseconds in the same local
composition timebase. ``offset_ms`` is added only when serializing the draft.
Native ``words`` are segment-relative milliseconds; draft ranges are microseconds.
Supported BMP text uses [start, end) character ranges. Non-BMP characters
(including many emoji) fail explicitly until their native indexing is verified.
No cloud requests, resource downloads, arbitrary code, or approximate word timing.
"""
from __future__ import annotations

import copy
import json
import math
import random
import re
import unicodedata
import uuid
from pathlib import Path


class CaptionError(ValueError):
    pass


def _id():
    return uuid.uuid4().hex.upper()


def _u16(text):
    if any(ord(ch)>0xffff for ch in text):
        raise CaptionError('Non-BMP caption characters are not supported until native range indexing is verified')
    return len(text)


def clean_text(text, preserve_linebreaks=False):
    """Return display text, an old-character-boundary map, and phrase boundaries."""
    text = str(text)
    out = []
    mapping = [0]
    boundaries = []
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ''
        nxt = text[i + 1] if i + 1 < len(text) else ''
        keep = True
        if preserve_linebreaks and ch in '\r\n':
            keep=not (ch=='\r' and nxt=='\n')
            ch='\n'
        elif ch.isspace():
            # CJK line breaks are editorial boundaries, not mandatory line feeds.
            left=out[-1]if out else ''
            keep = bool(left.isascii() and left.isalnum() and nxt.isascii() and nxt.isalnum())
            ch = ' '
        elif unicodedata.category(ch).startswith('P') or ch in '~～':
            keep = ((ch in '.,，．' and prev.isdigit() and nxt.isdigit()) or
                    (ch in '.．' and nxt.isdigit()) or
                    (ch == '-' and nxt.isdigit() and (not prev or prev.isspace() or prev in '(（')) or
                    (ch in '-—–~～/／' and prev.isalnum() and nxt.isalnum()) or
                    (ch in "'’" and prev.isascii() and prev.isalpha() and nxt.isascii() and nxt.isalpha()) or
                    (ch in '%％' and prev.isdigit()))
            if ch in '，；;。！？!?：:、\n':
                boundaries.append(len(out))
            if not keep and prev.isascii() and prev.isalnum() and nxt.isascii() and nxt.isalnum():
                keep=True;ch=' '
        if keep:
            out.append(ch)
        mapping.append(len(out))
    return ''.join(out), mapping, sorted(set(x for x in boundaries if x > 0))


def _finite(value, label):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise CaptionError(f'{label}: finite milliseconds required') from None
    if not math.isfinite(value):
        raise CaptionError(f'{label}: finite milliseconds required')
    return value


def _normal_words(cue, cleaned, mapping):
    """Only accept real, ordered words whose text covers the reviewed cue."""
    raw = cue.get('words') or []
    timebase = cue.get('words_timebase', 'absolute_ms')
    if isinstance(raw, dict):
        texts = raw.get('text', [])
        starts, ends = raw.get('start_time', []), raw.get('end_time', [])
        if not (len(texts) == len(starts) == len(ends)):
            return [], 'word arrays have unequal lengths'
        raw = [{'text': t, 'start_ms': s, 'end_ms': e} for t, s, e in zip(texts, starts, ends)]
        timebase = cue.get('words_timebase', 'cue_relative_ms')
    if not isinstance(raw, list):
        return [], 'words must be a list or native word arrays'
    if not raw:
        return [], 'no word timestamps'
    if timebase not in ('absolute_ms', 'cue_relative_ms'):
        return [], 'unknown word timebase'
    result = []
    cursor = 0
    previous = float(cue['start_ms'])
    for item in raw:
        if not isinstance(item, dict) or item.get('timing_precision', 'word') != 'word':
            return [], 'approximate word timings cannot drive native captions'
        value, _, _ = clean_text(item.get('text', item.get('word', '')))
        if not value:
            continue
        try:
            start = _finite(item.get('start_ms', item.get('startMs')), 'word start')
            end = _finite(item.get('end_ms', item.get('endMs')), 'word end')
        except CaptionError as e:
            return [], str(e)
        if timebase == 'cue_relative_ms':
            start += float(cue['start_ms']); end += float(cue['start_ms'])
        if not (float(cue['start_ms']) - .001 <= start < end <= float(cue['end_ms']) + .001) or start < previous - .001:
            return [], 'word timing outside cue or overlapping'
        while cursor < len(cleaned) and cleaned[cursor].isspace():
            cursor += 1
        if cleaned[cursor:cursor + len(value)] != value:
            return [], 'word text differs from corrected text; realignment required'
        result.append({'text': value, 'start_ms': start, 'end_ms': end,
                       'char_start': cursor, 'char_end': cursor + len(value), 'timing_precision': 'word'})
        cursor += len(value)
        previous = end
    if cleaned[cursor:].strip() or not result:
        return [], 'word text does not cover complete cue'
    return result, None


def _cjk_units(text):
    return sum(1.0 if unicodedata.east_asian_width(c) in 'WF' else .5 if c.isspace() else .56 for c in text)


def estimate_width(text, settings, font=None):
    """Conservative single-line estimate, never a claim of native rendered bounds."""
    width = float(settings.get('width', 1080))
    size = float(settings.get('base_font_size', 14))
    scale = float(settings.get('subtitle_scale', 1.15))
    px = size * float(settings.get('subtitle_font_px_per_unit', 4.0)) * width / 1080 * scale
    pixels = _cjk_units(text) * px
    method = 'cjk_estimate'
    if font and font.get('path'):
        try:
            from PIL import ImageFont
            f = ImageFont.truetype(str(font['path']), max(1, round(px)))
            pixels = float(f.getlength(text))
            method = 'font_metrics_estimate'
        except (ImportError, OSError, ValueError):
            pass
    padding = float(settings.get('subtitle_effect_padding_px', 12)) * width / 1080
    limit = width * float(settings.get('subtitle_max_width_ratio', .82))
    cap = settings.get('subtitle_max_cjk_chars')
    return {'width_px': round(pixels + padding * 2, 2), 'safe_width_px': round(limit, 2),
            'cjk_units': round(_cjk_units(text), 3), 'method': method,
            'fits': pixels + padding * 2 <= limit and (cap is None or _cjk_units(text) <= float(cap))}


def _cue_settings(cue, settings):
    effective=dict(settings)
    if cue.get('layout_role')=='opening_hook_card':
        for key,target in [('opening_hook_font_size','base_font_size'),('opening_hook_scale','subtitle_scale'),('opening_hook_y','subtitle_y')]:
            if key in settings:effective[target]=settings[key]
    return effective


def _layout_estimate(cue, text, settings, font):
    effective=_cue_settings(cue,settings)
    if cue.get('layout_role')!='opening_hook_card':return estimate_width(text,effective,font)
    lines=text.split('\n')
    if len(lines)not in (2,3)or any(not line.strip()for line in lines):
        raise CaptionError('opening_hook_card must contain two or three explicit, nonempty semantic lines')
    measures=[estimate_width(line,effective,font)for line in lines]
    return {'width_px':max(x['width_px']for x in measures),'safe_width_px':measures[0]['safe_width_px'],
            'line_count':len(lines),'lines':measures,'fits':all(x['fits']for x in measures),'method':'explicit_semantic_opening_lines'}


def _chunks(text, ends, boundaries, settings, font, protected=(), measure=None):
    """Greedy fit, preferring a real phrase boundary in the last half of a line."""
    result = []; start = 0
    while start < len(text):
        candidates = [e for e in ends if e > start and not any(a < e < b for a,b in protected)
                      and (measure(start,e) if measure else estimate_width(text[start:e], settings, font)['fits'])]
        if not candidates:
            raise CaptionError('A word/token exceeds subtitle safe width; re-segment the transcript or select a smaller verified preset')
        end = max(candidates)
        preferred = [e for e in candidates if e in boundaries and e >= start + (end - start) * .55]
        if preferred and end < len(text):
            end = max(preferred)
        result.append((start, end)); start = end
    return result


def _remap_keywords(raw, original_text, text, mapping, start, end):
    out = []
    for kw in raw:
        k = copy.deepcopy(kw)
        a, b = k.get('char_start'), k.get('char_end')
        if not isinstance(a, int) or not isinstance(b, int) or not 0 <= a < b <= len(original_text):
            raise CaptionError('Keyword needs valid source character bounds')
        if original_text[a:b] != str(k.get('text', '')):
            raise CaptionError('Keyword text and original character range disagree')
        a, b = mapping[a], mapping[b]
        if a == b:
            continue
        ks = _finite(k.get('start_ms', start), 'keyword start'); ke = _finite(k.get('end_ms', end), 'keyword end')
        if not start <= ks < ke <= end:
            raise CaptionError('Keyword timing outside cue')
        k.update(text=text[a:b], char_start=a, char_end=b, start_ms=ks, end_ms=ke)
        k.setdefault('timing_precision', 'cue')
        out.append(k)
    ordered = sorted(out, key=lambda k: (k['char_start'], k['char_end']))
    if any(a['char_end'] > b['char_start'] for a, b in zip(ordered, ordered[1:])):
        raise CaptionError('Overlapping keyword spans are ambiguous')
    return ordered


def prepare_document(data, settings, font=None):
    """Clean/split reviewed cues; preserve parent aliases and honest precision."""
    result = copy.deepcopy(data)
    result['cues'] = []; result['cue_aliases'] = {}
    warnings = list(result.get('warnings', []))
    seen = set(); last_end = -1
    for source in data.get('cues', []):
        cue = copy.deepcopy(source)
        if data.get('caption_profile'):cue.setdefault('caption_profile',data['caption_profile'])
        cid = str(cue.get('id', ''))
        if not cid or cid in seen:
            raise CaptionError('Missing or duplicate cue id')
        seen.add(cid)
        start = _finite(cue.get('start_ms'), 'cue start'); end = _finite(cue.get('end_ms'), 'cue end')
        if start < 0 or end <= start or start < last_end - .001:
            raise CaptionError(f'{cid}: invalid/overlapping cue times')
        last_end = end
        original = str(cue.get('text', ''))
        opening=cue.get('layout_role')=='opening_hook_card'
        cleaned, mapping, boundaries = clean_text(original,preserve_linebreaks=opening)
        _u16(cleaned)
        if not cleaned:
            warnings.append(f'{cid}: punctuation-only cue omitted'); result['cue_aliases'][cid] = []; continue
        keywords = _remap_keywords(cue.get('keywords', []), original, cleaned, mapping, start, end)
        words, reason = _normal_words(cue, cleaned, mapping)
        if reason and cue.get('words'):
            warnings.append(f'{cid}: {reason}; word animation disabled')
        measured=_layout_estimate(cue,cleaned,settings,font)
        if measured['fits'] or settings.get('_defer_caption_layout') or settings.get('caption_policy')=='semantic':
            chunks = [(0, len(cleaned))]; approximate = False
        elif opening:
            raise CaptionError(f'{cid}: opening hook line exceeds safe width; explicitly shorten/rewrite its semantic lines')
        elif words:
            chunks = _chunks(cleaned, [w['char_end'] for w in words], boundaries, settings, font,
                             [(k['char_start'],k['char_end']) for k in keywords]); approximate = False
        elif settings.get('subtitle_long_cue_policy', 'error') == 'approximate_equal':
            # Numeric values and Latin identifiers are atomic, not split at dots.
            ends = [m.end() for m in re.finditer(r'[A-Za-z0-9]+(?:[.\-—～/%][A-Za-z0-9]+)*|.', cleaned)]
            chunks = _chunks(cleaned, ends, boundaries, settings, font,
                             [(k['char_start'],k['char_end']) for k in keywords]); approximate = True
            warnings.append(f'{cid}: cue-level equal-time split is approximate, not word alignment')
        else:
            raise CaptionError(f'{cid}: long cue lacks reliable words; align/re-segment it or explicitly allow subtitle_long_cue_policy=approximate_equal')
        ids = []
        for index, (a, b) in enumerate(chunks):
            part = copy.deepcopy(cue)
            selected = [copy.deepcopy(w) for w in words if a <= w['char_start'] and w['char_end'] <= b]
            if len(chunks) == 1:
                ps, pe = start, end
            elif approximate:
                ps = start + (end-start)*index/len(chunks); pe = start + (end-start)*(index+1)/len(chunks)
            else:
                ps = start if index == 0 else selected[0]['start_ms']
                following = [w for w in words if w['char_start'] >= b]
                pe = following[0]['start_ms'] if following else end
            if pe-ps < float(settings.get('subtitle_min_duration_ms', 120)):
                raise CaptionError(f'{cid}: split creates a subtitle shorter than minimum duration')
            pid = cid if len(chunks) == 1 else f'{cid}~{index+1}'
            for w in selected:
                w['char_start'] -= a; w['char_end'] -= a
            outk = []
            for k in keywords:
                ka, kb = max(a, k['char_start']), min(b, k['char_end'])
                if ka >= kb:
                    continue
                kk = copy.deepcopy(k); ks, ke = max(ps, k['start_ms']), min(pe, k['end_ms'])
                if ke <= ks:
                    ks, ke = ps, pe
                kk.update(text=cleaned[ka:kb], char_start=ka-a, char_end=kb-a, start_ms=ks, end_ms=ke)
                if approximate or not words or ka != k['char_start'] or kb != k['char_end']:
                    kk['timing_precision'] = 'cue'
                outk.append(kk)
            part.update(id=pid, parent_cue_id=cue.get('parent_cue_id', cid), text=cleaned[a:b],
                        start_ms=ps, end_ms=pe, words=selected, words_timebase='absolute_ms', keywords=outk,
                        timing_precision='word' if selected else 'cue', approximate_timing=approximate,
                        layout_estimate=_layout_estimate(cue,cleaned[a:b], settings, font))
            part.pop('recognize_text', None); part.pop('base_content', None)
            ids.append(pid); result['cues'].append(part)
        result['cue_aliases'][cid] = ids
    result['warnings'] = warnings
    result['caption_prepared'] = True
    return result


def _portable(path, stage):
    if not path:
        raise CaptionError('Missing resource path')
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise CaptionError(f'Resource missing: {p.name}')
    return str(stage(str(p)))


def _font(font, stage):
    if not font or not font.get('path'):
        raise CaptionError('A locally available subtitle font is required')
    if font.get('available') is False or font.get('verified') is False:
        raise CaptionError('Subtitle font is unavailable/unverified')
    if not Path(font['path']).is_file():
        raise CaptionError('Subtitle font path must be a local file')
    return {'id': str(font.get('resource_id', font.get('id', ''))), 'path': _portable(font['path'], stage)}


def _candidates(catalog, key):
    return [x for x in catalog.get(key, []) if not x.get('binding_only') and x.get('verified') is True and x.get('available', True)]


def _choice(rng, items):
    return rng.choice(items) if items else None


def _track(content):
    tracks = content.setdefault('tracks', [])
    track = next((t for t in tracks if t.get('type') == 'text' and t.get('name') == 'Subtitles'), None)
    if track is None:
        track = {'id': _id(), 'type': 'text', 'name': 'Subtitles', 'attribute': 0, 'flag': 0,
                 'is_default_name': False, 'segments': []}
        tracks.append(track)
    return track


def _style(text, settings, font):
    width = float(settings.get('subtitle_border_width', 40)) / 100 * .2
    return {'range': [0, _u16(text)], 'size': float(settings.get('base_font_size', 14)),
            'font': copy.deepcopy(font), 'fill': {'alpha': 1.0, 'content': {'render_type': 'solid',
            'solid': {'alpha': 1.0, 'color': list(settings.get('subtitle_color', [1, 1, 1]))}}},
            'strokes': [{'alpha': 1.0, 'width': width, 'content': {'render_type': 'solid',
            'solid': {'alpha': 1.0, 'color': list(settings.get('subtitle_border_color', [0, 0, 0]))}}}],
            'useLetterColor': True}


def _stage_nested(value, stage):
    if isinstance(value, dict):
        return {k: (_portable(v, stage) if k.endswith('path') and isinstance(v, str) and v else _stage_nested(v, stage))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_stage_nested(v, stage) for v in value]
    return value


def _effect_style(effect, base, stage):
    if not effect.get('cache_path'):
        raise CaptionError('Verified flower lacks cache_path')
    effect_id = str(effect.get('effect_id', effect.get('resource_id', '')))
    if not effect_id:
        raise CaptionError('Verified flower lacks resource_id')
    # A real exported style carries texture/gradient/stroke composition. An ID
    # alone cannot promise the hand-edited visual appearance.
    if not isinstance(effect.get('style'), dict):
        raise CaptionError('Flower needs an exported style, not only an effect ID')
    style = copy.deepcopy(base)
    style.update(_stage_nested(copy.deepcopy(effect['style']), stage))
    style['effectStyle'] = {'id': effect_id, 'path': _portable(effect['cache_path'], stage)}
    style.setdefault('font', copy.deepcopy(base['font']))
    style['size'] = base['size']
    material = {'id': _id(), 'type': 'text_effect', 'effect_id': effect_id, 'resource_id': effect_id,
                'path': style['effectStyle']['path'], 'name': effect.get('name', ''), 'value': 1.0, 'source_platform': 1}
    return style, material


def _segment(mid, start_ms, end_ms, settings):
    scale = float(settings.get('subtitle_scale', 1.15))
    start_us, end_us = round(start_ms*1000), round(end_ms*1000)
    return {'id': _id(), 'material_id': mid, 'target_timerange': {'start': start_us, 'duration': end_us-start_us},
            'render_timerange': {}, 'source_timerange': None, 'speed': 1.0, 'volume': 1.0,
            'extra_material_refs': [], 'visible': True, 'track_attribute': 0, 'render_index': 30,
            'track_render_index': 30, 'common_keyframes': [], 'keyframe_refs': [],
            'clip': {'alpha': 1.0, 'rotation': 0.0, 'flip': {'horizontal': False, 'vertical': False},
                     'scale': {'x': scale, 'y': scale}, 'transform': {'x': float(settings.get('subtitle_x', 0)), 'y': float(settings.get('subtitle_y', -.39))}},
            'uniform_scale': {'on': True, 'value': 1.0}, 'enable_adjust': False, 'enable_lut': False,
            'enable_hsl': False, 'source': 'segmentsourcenormal'}


def _native_words(cue):
    if not cue.get('words') or cue.get('words_timebase') != 'absolute_ms':
        return {}, False
    checked, reason = _normal_words(cue, cue['text'], list(range(len(cue['text'])+1)))
    if reason:
        return {}, False
    return {'text': [w['text'] for w in checked],
            'start_time': [round(w['start_ms']-cue['start_ms']) for w in checked],
            'end_time': [round(w['end_ms']-cue['start_ms']) for w in checked]}, True


def _native_animation(item, duration_us, stage):
    node = copy.deepcopy(item.get('animation', {}))
    if node.get('type','caption')!='caption':
        raise CaptionError('A generic intro/outro animation cannot be relabeled as native caption animation')
    resource = str(item.get('resource_id', node.get('resource_id', '')))
    path = item.get('cache_path', node.get('path'))
    if not resource or not path:
        raise CaptionError('Native caption animation needs resource_id and local cache_path')
    node.update(type='caption', id=resource, resource_id=resource, third_resource_id=resource,
                name=item.get('name', node.get('name', '')), path=_portable(path, stage),
                start=0, duration=duration_us, material_type='sticker', source_platform=1)
    return {'id': _id(), 'type': 'sticker_animation', 'animations': [node]}


def _material_ids(materials):
    return {m['id']: (kind, m) for kind, rows in materials.items() if isinstance(rows, list)
            for m in rows if isinstance(m, dict) and m.get('id')}


def clone_component(component, text, start_ms, end_ms, stage, settings=None, cue=None):
    """Clone a sanitized component and replace every declared text slot.

    Protocol: {schema_version:'1.0',segment:{...},materials:{...},
      text_slots:[{material_id:ID,role:'primary'|'mirror'}]}.
    Every component text must be a slot containing only {{TEXT}}. Independent
    secondary labels require an explicit future protocol and are rejected.
    """
    component = copy.deepcopy(component)
    if component.get('schema_version') != '1.0':
        raise CaptionError('Unsupported text component schema')
    segment = component.get('segment', {})
    materials = component.get('materials', {})
    ids = _material_ids(materials)
    root_id = segment.get('material_id')
    if root_id not in ids or ids[root_id][0] != 'text_templates':
        raise CaptionError('Component root must be a full text_template')
    slots = component.get('text_slots', [])
    if not slots or any(s.get('role') not in ('primary', 'mirror') for s in slots):
        raise CaptionError('Unsupported complex text slots; only primary/mirror are allowed')
    text_ids = {mid for mid, (k, _) in ids.items() if k == 'texts'}
    if {s.get('material_id') for s in slots} != text_ids or len(slots) != len(text_ids):
        raise CaptionError('All template child texts must have exactly one replacement slot')
    needs_words = any(a.get('type') == 'caption' for m in materials.get('material_animations', []) for a in m.get('animations', []))
    words, reliable = _native_words(cue) if cue else ({}, False)
    if needs_words and not reliable:
        raise CaptionError('Word-driven template requires current, reliable word timing')
    for mid in text_ids:
        m = ids[mid][1]
        inner = json.loads(m.get('content', '{}'))
        if inner.get('text') != '{{TEXT}}':
            raise CaptionError('Component contains historical text; extract/sanitize it first')
        styles = inner.get('styles', [])
        if len(styles) != 1:
            raise CaptionError('Unsupported complex within-slot styles')
        inner['text'] = text; inner['styles'][0]['range'] = [0, _u16(text)]
        m['content'] = json.dumps(inner, ensure_ascii=False)
        m['line_feed']=0
        m['force_apply_line_max_width']=False
        m.pop('fixed_width',None)
        m.pop('base_content', None); m.pop('recognize_text', None); m.pop('recognize_task_id', None)
        m['words'] = copy.deepcopy(words); m['current_words'] = {}; m['subtitle_keywords'] = {}
    # Remap every ID and every exact matching internal reference, including IDs
    slot_by_id={s['material_id']:s for s in slots}
    from native_styles import measure_styles
    for root in materials.get('text_templates',[]):
        for child in root.get('text_info_resources',[]):
            mid=child.get('text_material_id');slot=slot_by_id.get(mid,{})
            old_width=slot.get('reference_text_width')
            if old_width and float(old_width)>0:
                inner=json.loads(ids[mid][1]['content'])
                measured=measure_styles(text,inner['styles'],{'width':1080,'subtitle_effect_padding_px':0},1)['width_px']
                attach=child.get('attach_info',{})
                if attach.get('original_size_width'):
                    attach['original_size_width']=float(attach['original_size_width'])*measured/float(old_width)
    # Remap every ID and every exact matching internal reference, including IDs
    # embedded in content JSON. External resource IDs remain unchanged.
    mapping = {old: _id() for old in ids}
    def collect_ids(v):
        if isinstance(v, dict):
            ident = v.get('id')
            if isinstance(ident, str) and re.fullmatch(r'[0-9A-Fa-f-]{32,36}', ident):
                mapping.setdefault(ident, _id())
            for vv in v.values(): collect_ids(vv)
        elif isinstance(v, list):
            for vv in v: collect_ids(vv)
    collect_ids(segment); collect_ids(materials)
    def rewrite(v, key=''):
        if isinstance(v, dict):
            return {k: rewrite(vv, k) for k, vv in v.items() if k not in ('request_id','recognize_task_id','recognize_text','base_content','origin_word_info','current_word_info')}
        if isinstance(v, list): return [rewrite(vv, key) for vv in v]
        if isinstance(v, str):
            if key == 'content':
                try: return json.dumps(rewrite(json.loads(v)), ensure_ascii=False)
                except json.JSONDecodeError: pass
            if v in mapping: return mapping[v]
            if key.endswith('path') and v: return _portable(v, stage)
        return v
    segment = rewrite(segment); materials = rewrite(materials)
    old_duration = component.get('segment', {}).get('target_timerange', {}).get('duration', 0)
    new_duration = round(end_ms*1000)-round(start_ms*1000)
    if not old_duration or new_duration <= 0:
        raise CaptionError('Component/caption duration must be positive')
    ratio = new_duration / old_duration
    def retime(v):
        if isinstance(v, dict):
            for k, vv in list(v.items()):
                if k == 'words':
                    continue  # Current cue-relative word times must never be scaled by the old template duration.
                if k in ('duration', 'start', 'start_time', 'end_time', 'time_offset') and isinstance(vv, (int, float)):
                    v[k] = round(vv*ratio)
                else: retime(vv)
        elif isinstance(v, list):
            for vv in v: retime(vv)
    retime(materials)
    # Local keyframes use segment-local microseconds, not global timeline time.
    retime(segment.get('common_keyframes', []))
    segment['id'] = _id()
    segment['target_timerange'] = {'start': round(start_ms*1000), 'duration': new_duration}
    segment['render_timerange'] = {}; segment['render_index'] = 30; segment['track_render_index'] = 30
    segment['visible'] = True; segment['track_attribute'] = 0
    if settings and settings.get('template_preserve_position', False) is not True:
        segment.setdefault('clip', {}).setdefault('transform', {}).update(x=float(settings.get('subtitle_x', 0)), y=float(settings.get('subtitle_y', -.39)))
    return segment, materials


def _fit_caption_template(segment, materials, text, settings):
    """Fit the complete component by one outer scale, preserving child layout.

    Measurements are deliberately labeled estimates. A component which would
    need unreadably small primary text fails instead of silently shrinking it.
    """
    idx=_material_ids(materials);kind,root=idx.get(segment['material_id'],('',{}))
    clip=segment.setdefault('clip',{});scale=clip.setdefault('scale',{'x':1,'y':1})
    sx,sy=abs(float(scale.get('x',1))),abs(float(scale.get('y',1)))
    if not sx or not sy:raise CaptionError('Caption component has zero outer scale')
    width=float(settings.get('width',1080));limit=width*float(settings.get('subtitle_max_width_ratio',.82))
    widest=0;tallest=0;relative_sizes=[]
    for child in root.get('text_info_resources',[]):
        _,m=idx.get(child.get('text_material_id'),('',{}));inner=json.loads(m.get('content','{}'))
        styles=inner.get('styles',[])
        if len(styles)!=1:raise CaptionError('Cannot estimate complex template typography')
        style=styles[0];attach=child.get('attach_info',{}).get('clip',{})
        cs=attach.get('scale',{});cx,cy=abs(float(cs.get('x',1))),abs(float(cs.get('y',1)))
        measured=estimate_width(text,{**settings,'base_font_size':float(style.get('size',0)),
            'subtitle_scale':sx*cx,'subtitle_max_cjk_chars':100000},style.get('font'))
        child_offset=abs(float(attach.get('transform',{}).get('x',0)))*sx
        widest=max(widest,measured['width_px']+child_offset*2)
        h=float(style.get('size',0))*float(settings.get('subtitle_font_px_per_unit',4))*width/1080*sy*cy*1.25
        yo=abs(float(attach.get('transform',{}).get('y',0)))*sy
        tallest=max(tallest,h+yo*2)
        relative_sizes.append(float(style.get('size',0))*sy*cy)
    for child in root.get('non_text_info_resources',[]):
        attach=child.get('attach_info',{});cc=attach.get('clip',{});cs=cc.get('scale',{})
        w=float(attach.get('original_size_width',0))*abs(float(cs.get('x',1)))*sx
        off=abs(float(cc.get('transform',{}).get('x',0)))*sx
        widest=max(widest,w+2*off)
        h=float(attach.get('original_size_height',0))*abs(float(cs.get('y',1)))*sy
        yo=abs(float(cc.get('transform',{}).get('y',0)))*sy
        tallest=max(tallest,h+2*yo)
    pos=clip.get('transform',{});limit=min(limit,width*max(0,.99-abs(float(pos.get('x',0)))))
    height_limit=float(settings.get('height',1920))*max(0,.99-abs(float(pos.get('y',0))))
    ratio=min(1.0,limit/max(1,widest),height_limit/max(1,tallest))
    minimum=float(settings.get('base_font_size',14))*float(settings.get('subtitle_scale',1.15))*float(settings.get('template_min_relative_text_size',1.0))
    if not relative_sizes or min(relative_sizes)*ratio<minimum-.01:
        raise CaptionError('Caption template would make its primary text smaller than base subtitles; choose another verified caption component')
    if ratio<1:
        scale['x']=float(scale.get('x',1))*ratio;scale['y']=float(scale.get('y',1))*ratio
    return {'method':'complete_component_font_metrics_estimate','source_width_px':round(widest,2),
            'safe_width_px':round(limit,2),'source_height_px':round(tallest,2),'safe_height_px':round(height_limit,2),
            'outer_scale_adjustment':round(ratio,6),'native_preview_required':True}


def validate_requested_resources(cues, settings, catalog, font, stage=lambda p:p):
    """Validate every selectable resource before making any random decisions.

    Returns explicit-degradation warnings, or raises CaptionError. Pass identity
    staging for a read-only preflight; no random state is read or advanced.
    A bad verified candidate cannot escape validation by losing a random draw.
    """
    if settings.get('caption_policy')=='semantic':
        from caption_policy import _validate_catalog,policy
        _validate_catalog(catalog,settings,cues,policy(settings),font)
        return []
    warnings=[]
    native_font=_font(font,stage)
    policy=settings.get('missing_effect_policy','error')
    if policy not in ('error','plain'):raise CaptionError('missing_effect_policy must be error or plain')
    def fail(message):
        if policy=='error':raise CaptionError(message)
        warnings.append('Explicit plain fallback permitted: '+message)
    mode=settings.get('caption_animation_mode','auto')
    if mode not in ('auto','none','word'):raise CaptionError('caption_animation_mode must be auto, none or word')
    emphasis_mode=settings.get('inline_emphasis_mode','flower')
    if emphasis_mode not in ('flower','border'):raise CaptionError('inline_emphasis_mode must be flower or border')
    for key in ('emphasis_probability','template_probability'):
        value=float(settings.get(key,.65 if key=='emphasis_probability'else .35))
        if not 0<=value<=1:raise CaptionError(f'{key} must be within [0,1]')
    for cue in cues:
        _u16(cue.get('text',''))
        if not _layout_estimate(cue,cue.get('text',''),settings,font)['fits']:
            raise CaptionError(f'{cue.get("id")}: unprepared/overlong caption in resource preflight')
    keyword_cues=[c for c in cues if c.get('keywords')]if float(settings.get('emphasis_probability',.65))>0 else []
    if keyword_cues and emphasis_mode=='flower':
        flowers=_candidates(catalog,'flowers')
        if not flowers:fail('No verified inline flower with exported style; explicitly select inline_emphasis_mode=border to disable native flowers')
        for item in flowers:
            try:
                _effect_style(item,_style('资源预检',settings,native_font),stage)
            except (KeyError,OSError,ValueError,TypeError)as e:
                fail(f'Invalid selectable flower {item.get("name",item.get("effect_id","?"))}: {e}')
    if keyword_cues and float(settings.get('template_probability',.35))>0:
        from component_tools import load_component
        templates=_candidates(catalog,'text_templates');loaded={};checked=set()
        for cue in keyword_cues:
            if cue.get('layout_role')=='opening_hook_card':continue
            try:eligible=[t for t in templates if t.get('role')=='caption'and len(cue['text'])<=int(t.get('max_chars',13))]
            except (ValueError,TypeError)as e:raise CaptionError('Caption component max_chars must be an integer')from e
            if not eligible:
                fail(f'{cue.get("id")}: no verified caption-role component fits; explicitly set template_probability=0 to disable replacements')
            for item in eligible:
                path=str(item.get('component_path',''));key=(path,cue['text'],cue.get('layout_role'))
                if key in checked:continue
                checked.add(key)
                try:
                    if path not in loaded:loaded[path]=load_component(path)
                    seg,added=clone_component(loaded[path],cue['text'],float(cue['start_ms']),float(cue['end_ms']),stage,settings,cue=cue)
                    _fit_caption_template(seg,added,cue['text'],settings)
                    errors=validate_caption_content({'tracks':[{'name':'Subtitles','type':'text','segments':[seg]}],'materials':added})
                    if errors:raise CaptionError('; '.join(errors[:4]))
                except (KeyError,OSError,ValueError,TypeError)as e:
                    fail(f'Invalid selectable caption component {item.get("name",Path(path).name)}: {e}')
    if mode!='none':
        requests=[]
        for cue in cues:
            _,reliable=_native_words(cue)
            if reliable:requests.append(cue)
            elif mode=='word':fail(f'{cue.get("id")}: word caption mode requires reliable full word timing')
            else:warnings.append(f'{cue.get("id")}: native word animation skipped because reliable word timing is unavailable')
        if requests:
            animations=_candidates(catalog,'caption_animations')
            if not animations:fail('No verified native caption animation for word-aligned cues; explicitly set caption_animation_mode=none to disable')
            for item in animations:
                try:_native_animation(item,1000000,stage)
                except (KeyError,OSError,ValueError,TypeError)as e:
                    fail(f'Invalid selectable caption animation {item.get("name",item.get("resource_id","?"))}: {e}')
    return list(dict.fromkeys(warnings))


def append_captions(content, cues, settings, catalog, font, stage, rng, offset_ms=0):
    if settings.get('caption_policy') == 'semantic':
        from caption_policy import append_semantic_captions
        return append_semantic_captions(content,cues,settings,catalog,font,stage,rng,offset_ms)
    return append_legacy_captions(content,cues,settings,catalog,font,stage,rng,offset_ms)


def append_legacy_captions(content, cues, settings, catalog, font, stage, rng, offset_ms=0):
    """Append one visible subtitle body per cue; return event metadata for SFX."""
    # Read-only validation precedes all random draws and output mutation. It is
    # intentionally independent of the generation seed and chosen candidates.
    preflight_warnings=validate_requested_resources(cues,settings,catalog,font,lambda p:p)
    track = _track(content); materials = content.setdefault('materials', {})
    native_font = _font(font, stage)
    flowers = _candidates(catalog, 'flowers')
    templates = _candidates(catalog, 'text_templates')
    animations = _candidates(catalog, 'caption_animations')
    events = []
    for cue in cues:
        start, end = float(cue['start_ms'])+offset_ms, float(cue['end_ms'])+offset_ms
        if end <= start or not cue.get('text'):
            raise CaptionError('Empty or negative-duration compiled cue')
        opening=cue.get('layout_role')=='opening_hook_card'
        if ('\n' in cue['text'] and not opening) or not _layout_estimate(cue,cue['text'],settings,font)['fits']:
            raise CaptionError(f'{cue.get("id")}: caption was not prepared for single-line safe layout')
        event = {'cue_id': cue['id'], 'parent_cue_id': cue.get('parent_cue_id', cue['id']),
                 'start_ms': start, 'end_ms': end, 'mode': 'base', 'keyword_events': [],
                 'template': None, 'caption_animation': None, 'warnings': list(preflight_warnings)if not events else []}
        selected_keywords = cue.get('keywords', []) if rng.random() < float(settings.get('emphasis_probability', .65)) else []
        use_template = not opening and selected_keywords and rng.random() < float(settings.get('template_probability', .35))
        eligible = [t for t in templates if t.get('role') == 'caption' and len(cue['text']) <= int(t.get('max_chars', 13))]
        template = _choice(rng, eligible) if use_template else None
        if template:
            try:
                from component_tools import load_component
                component = load_component(template['component_path'])
                seg, added = clone_component(component, cue['text'], start, end, stage, settings,cue=cue)
                event['template_layout_estimate']=_fit_caption_template(seg,added,cue['text'],settings)
                for kind, vals in added.items(): materials.setdefault(kind, []).extend(vals)
                track['segments'].append(seg)
                event.update(mode='template_replace', template=template.get('name', template.get('resource_id')), segment_id=seg['id'])
                event['keyword_events'] = [{'text': k['text'], 'start_ms': float(k['start_ms'])+offset_ms,
                    'end_ms': float(k['end_ms'])+offset_ms, 'kind': 'template_replace', 'timing_precision': k.get('timing_precision', 'cue')} for k in selected_keywords]
                events.append(event); continue
            except (KeyError, OSError, ValueError) as e:
                if settings.get('missing_effect_policy', 'error') != 'plain': raise CaptionError(str(e)) from e
                event['warnings'].append('Template unavailable; explicit plain fallback: '+str(e))
        effective=_cue_settings(cue,settings)
        base = _style(cue['text'], effective, native_font)
        if opening:event['layout_role']='opening_hook_card'
        emphasis_mode=settings.get('inline_emphasis_mode','flower')
        if emphasis_mode not in ('flower','border'):raise CaptionError('inline_emphasis_mode must be flower or border')
        if selected_keywords and emphasis_mode=='flower' and not flowers:
            reason='No verified inline flower with exported style; choose inline_emphasis_mode=border for an explicit border-only preset'
            if settings.get('missing_effect_policy','error')!='plain':raise CaptionError(reason)
            event['warnings'].append('Flower plain fallback: '+reason)
        style_parts = []; cursor = 0; effects = []
        for kw in sorted(selected_keywords, key=lambda k:k['char_start']):
            a,b = kw['char_start'],kw['char_end']
            if a < cursor or not 0 <= a < b <= len(cue['text']) or cue['text'][a:b] != kw['text']:
                raise CaptionError('Invalid/overlapping prepared keyword span')
            if a > cursor:
                plain = copy.deepcopy(base); plain['range'] = [_u16(cue['text'][:cursor]),_u16(cue['text'][:a])]; style_parts.append(plain)
            highlighted = copy.deepcopy(base)
            flower = _choice(rng, flowers) if emphasis_mode=='flower' else None
            if flower:
                try:
                    highlighted, effect = _effect_style(flower, base, stage); effects.append(effect)
                except (OSError, ValueError) as e:
                    if settings.get('missing_effect_policy', 'error') != 'plain': raise
                    event['warnings'].append('Flower unavailable; explicit plain fallback: '+str(e)); flower=None
            if not flower:
                # In-place red border is a real hand-edited style and remains
                # legible without pretending an unavailable flower is present.
                highlighted['strokes'][0]['content']['solid']['color'] = list(settings.get('keyword_border_color', [1, .09411765, .21568628]))
                if settings.get('keyword_color') is not None:
                    highlighted['fill']['content'] = {'render_type':'solid','solid':{'alpha':1.0,'color':list(settings['keyword_color'])}}
            highlighted['range'] = [_u16(cue['text'][:a]),_u16(cue['text'][:b])]
            style_parts.append(highlighted); cursor=b
            event['keyword_events'].append({'text': kw['text'], 'start_ms':float(kw['start_ms'])+offset_ms,
                'end_ms':float(kw['end_ms'])+offset_ms,'kind':'inline_flower' if flower else 'inline_style',
                'timing_precision':kw.get('timing_precision','cue')})
        if cursor < len(cue['text']):
            plain=copy.deepcopy(base);plain['range']=[_u16(cue['text'][:cursor]),_u16(cue['text'])];style_parts.append(plain)
        if not style_parts: style_parts=[base]
        mid=_id(); seg=_segment(mid,start,end,effective)
        words,reliable=_native_words(cue)
        mat={'id':mid,'type':'subtitle','content':json.dumps({'text':cue['text'],'styles':style_parts},ensure_ascii=False),
             'font_path':native_font['path'],'font_resource_id':native_font['id'],'font_size':base['size'],
             'alignment':1,'line_spacing':float(settings.get('opening_hook_line_spacing',.27))if opening else .02,'letter_spacing':0,'typesetting':0,'line_feed':1,
             'force_apply_line_max_width':False,'line_max_width':float(settings.get('subtitle_max_width_ratio',.82)),
             'check_flag':15,'global_alpha':1.0,'words':words,'current_words':{},'subtitle_keywords':{}}
        mode=settings.get('caption_animation_mode','auto')
        if mode not in ('none','auto','word'):raise CaptionError('caption_animation_mode must be auto, none or word')
        if reliable and mode!='none' and not animations:
            reason='No verified native caption animation for reliable word-aligned subtitles; explicitly choose caption_animation_mode=none to disable'
            if settings.get('missing_effect_policy','error')!='plain':raise CaptionError(reason)
            event['warnings'].append('Native animation plain fallback: '+reason)
        if reliable and mode!='none' and animations:
            item=_choice(rng,animations)
            try:
                animation=_native_animation(item,seg['target_timerange']['duration'],stage)
                materials.setdefault('material_animations',[]).append(animation);seg['extra_material_refs'].append(animation['id'])
                event['caption_animation']=item.get('name',item.get('resource_id'))
            except (OSError,ValueError) as e:
                if settings.get('missing_effect_policy','error')!='plain':raise
                event['warnings'].append('Native animation unavailable; explicit plain fallback: '+str(e))
        elif mode not in ('none','auto') and not reliable:
            reason='Word-based caption animation requires reliable full word timing'
            if settings.get('missing_effect_policy','error')!='plain':raise CaptionError(reason)
            event['warnings'].append('Native animation plain fallback: '+reason)
        elif not reliable and mode=='auto':
            event['warnings'].append('Native word animation skipped: no reliable word alignment')
        materials.setdefault('texts',[]).append(mat)
        materials.setdefault('effects',[]).extend(effects)
        seg['extra_material_refs'].extend(e['id'] for e in effects)
        track['segments'].append(seg)
        event.update(mode='inline_emphasis' if selected_keywords else 'base',segment_id=seg['id'])
        events.append(event)
    track['segments'].sort(key=lambda s:s['target_timerange']['start'])
    errors=validate_caption_content(content)
    if errors: raise CaptionError('; '.join(errors[:8]))
    return events


def validate_caption_content(content):
    """Validate rendered captions, not unreferenced history materials."""
    errors=[]; idx=_material_ids(content.get('materials',{})); rows=[]; displayed=[]; font_exists={}
    def visible_text(mid):
        kind,mat=idx.get(mid,('',{}))
        if kind=='text_templates':
            return '\n'.join(dict.fromkeys(visible_text(c.get('text_material_id')) for c in mat.get('text_info_resources',[])))
        try:return json.loads(mat.get('content','{}')).get('text','')
        except (ValueError,TypeError):return ''
    def check_text(mid,seg):
        kind,mat=idx.get(mid,('',{}))
        if kind!='texts':errors.append(f'{seg}: missing child text {mid}');return
        try: inner=json.loads(mat.get('content','{}'))
        except (ValueError,TypeError):errors.append(f'{seg}: invalid text JSON');return
        text=inner.get('text','');length=_u16(text);cursor=0
        if not text or '{{TEXT}}'in text:errors.append(f'{seg}: blank/unreplaced template text')
        for style in inner.get('styles',[]):
            rr=style.get('range',[])
            if len(rr)!=2 or rr[0]!=cursor or not cursor<rr[1]<=length:
                errors.append(f'{seg}: incomplete/overlapping style coverage');break
            cursor=rr[1]
            if not style.get('font',{}).get('path'):errors.append(f'{seg}: missing inner font path')
            else:
                path=style['font']['path']
                if path not in font_exists:font_exists[path]=Path(path).is_file()
                if not font_exists[path]:errors.append(f'{seg}: inner font file missing')
        if cursor!=length:errors.append(f'{seg}: styles do not cover complete caption')
    def check_words(mid,sid,tr,required=False):
        mat=idx.get(mid,('',{}))[1];words=mat.get('words')or{}
        starts,ends,texts=words.get('start_time',[]),words.get('end_time',[]),words.get('text',[])
        if required and not texts:errors.append(f'{sid}: native caption template missing current words')
        if starts or ends or texts:
            if not len(starts)==len(ends)==len(texts):errors.append(f'{sid}: native word arrays have unequal lengths');return
            if any(not 0<=a<b<=math.ceil(tr.get('duration',0)/1000)for a,b in zip(starts,ends)):
                errors.append(f'{sid}: native word time outside segment')
            if any(a<b for a,b in zip(starts[1:],ends[:-1])):errors.append(f'{sid}: native word times overlap')
            actual=json.loads(mat.get('content','{}')).get('text','')
            if clean_text(''.join(texts))[0]!=clean_text(actual)[0]:errors.append(f'{sid}: native words differ from current text')
    for t in content.get('tracks',[]):
        if t.get('type')!='text':continue
        if t.get('name') in ('Subtitles','Emphasis'):
            for s in t.get('segments',[]):
                tr=s.get('target_timerange',{});st=tr.get('start',0)
                if s.get('visible',True):displayed.append((st,st+tr.get('duration',0),s.get('id'),t.get('name'),visible_text(s.get('material_id'))))
        if t.get('name')!='Subtitles':continue
        for s in t.get('segments',[]):
            sid=s.get('id','?');mid=s.get('material_id');kind,mat=idx.get(mid,('',{}));tr=s.get('target_timerange',{})
            st=tr.get('start',0);end=st+tr.get('duration',0)
            if end<=st:errors.append(f'{sid}: nonpositive caption duration')
            if s.get('visible',True):rows.append((st,end,sid))
            if kind=='texts':
                check_text(mid,sid)
                check_words(mid,sid,tr)
                words=mat.get('words')or{}
                starts,ends,wordtexts=words.get('start_time',[]),words.get('end_time',[]),words.get('text',[])
                if starts or ends or wordtexts:
                    if len(starts)!=len(ends)or len(starts)!=len(wordtexts):
                        errors.append(f'{sid}: native word arrays have unequal lengths')
                    elif any(not 0<=a<b<=math.ceil(tr.get('duration',0)/1000) for a,b in zip(starts,ends)):
                        errors.append(f'{sid}: native word time outside segment')
                for ref in s.get('extra_material_refs',[]):
                    rk,rm=idx.get(ref,('',{}))
                    if rk=='material_animations'and any(a.get('type')=='caption'for a in rm.get('animations',[]))and not wordtexts:
                        errors.append(f'{sid}: native caption animation missing real words')
            elif kind=='text_templates':
                children=mat.get('text_info_resources',[])
                if not children:errors.append(f'{sid}: template has no child text')
                for child in children:check_text(child.get('text_material_id'),sid)
                component_refs=list(s.get('extra_material_refs',[]))+list(mat.get('extra_material_refs',[]))
                for child in children:component_refs.extend(child.get('extra_material_refs',[]))
                needs_words=any(a.get('type')=='caption' for ref in component_refs for a in idx.get(ref,('',{}))[1].get('animations',[]))
                for child in children:check_words(child.get('text_material_id'),sid,tr,needs_words)
                def refs(v):
                    if isinstance(v,dict):
                        for k,val in v.items():
                            if k=='extra_material_refs':
                                for ref in val:
                                    if ref not in idx:errors.append(f'{sid}: missing template dependency {ref}')
                            else:refs(val)
                    elif isinstance(v,list):
                        for val in v:refs(val)
                refs(mat)
            else:errors.append(f'{sid}: missing/unsupported caption material')
            for ref in s.get('extra_material_refs',[]):
                if ref not in idx:errors.append(f'{sid}: missing segment dependency {ref}')
    rows.sort()
    for prev,nxt in zip(rows,rows[1:]):
        if nxt[0]<prev[1]:errors.append(f'{prev[2]}/{nxt[2]}: simultaneous duplicate subtitle bodies')
    groups={}
    for row in displayed:
        if row[4]:groups.setdefault(row[4],[]).append(row)
    for same_text in groups.values():
        if len({r[3]for r in same_text})<2:continue
        active=[]
        for b in sorted(same_text):
            active=[a for a in active if a[1]>b[0]]
            for a in active:
                if a[3]!=b[3]:errors.append(f'{a[2]}/{b[2]}: full caption repeated in Emphasis track')
            active.append(b)
    return errors
