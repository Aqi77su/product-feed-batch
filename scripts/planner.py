"""Reproducible composition planning; semantic decisions are supplied once."""
from __future__ import annotations
import copy
import hashlib
import math
import random
from semantic_plan import map_document, plan_semantic_broll, choose_music


def _legacy_broll(segments, assets, settings, rng, duration):
    left = min(duration, round(settings['opening_no_broll_s']*1000))
    right = max(left, duration-round(settings['ending_no_broll_s']*1000))
    clips, total, previous = [], 0, None
    boundaries = [p['start_ms'] for p in segments[1:]]
    target = min(right-left, round(duration*rng.uniform(*settings['broll_ratio'])))
    requested = rng.randint(*settings['broll_count']) if settings['broll_mode'] == 'count' else 10000
    for _ in range(200):
        if len(clips) >= requested or (settings['broll_mode'] == 'ratio' and total >= target):
            break
        pool = [a for a in assets if a['id'] != previous and (a['duration_ms'] >= settings['broll_duration_s'][0]*1000 or settings['broll_short_policy'] == 'allow')]
        if not pool:
            break
        asset = rng.choice(pool)
        length = min(round(rng.uniform(*settings['broll_duration_s'])*1000), asset['duration_ms'], right-left)
        if settings['broll_mode'] == 'ratio':
            length = min(length, target-total)
        if length < 400:
            break
        start = rng.randint(left, max(left, right-length))
        if settings['broll_boundary_policy'] == 'prefer_cover' and boundaries:
            start = max(left, min(right-length, rng.choice(boundaries)-length//2))
        if settings['broll_boundary_policy'] == 'within_segment' and any(start < b < start+length for b in boundaries):
            continue
        gap = settings['broll_gap_s'][0]*1000
        if any(start < b['start_ms']+b['duration_ms']+gap and start+length+gap > b['start_ms'] for b in clips):
            continue
        clips.append({'asset_id': asset['id'], 'path': asset['path'], 'start_ms': start, 'duration_ms': length,
                      'source_start_ms': 0, 'intro': None, 'outro': None})
        total += length
        previous = asset['id']
    return {'clips': sorted(clips, key=lambda c: c['start_ms']), 'opportunities': [],
            'warnings': ['Explicit legacy broll mode: semantic annotations do not control these cuts']}


def plan(inv, io):
    settings, catalog = inv['settings'], inv['catalog']
    sources = {source['id']: source for source in inv['main']}
    master = random.Random(settings['seed'])
    units = []
    assembly = inv.get('assembly')
    if assembly:
        io.validate_assembly(inv)
        for group in assembly['groups']:
            sizes = [len(section['sources']) for section in group['sections']]
            pool = math.prod(sizes)
            indices = master.sample(range(pool), min(group['count'], pool))
            indices += [master.randrange(pool) for _ in range(group['count']-len(indices))]
            for index in indices:
                cursor, selected = index, []
                for section, size in reversed(list(zip(group['sections'], sizes))):
                    selected.append(section['sources'][cursor % size])
                    cursor //= size
                units.append((group['script_id'], list(reversed(selected)), index))
    else:
        for source in inv['main']:
            for variant in range(inv['distribution'][source['id']]):
                script_id = inv.get('documents', {}).get(source['id'], {}).get('script_id') or source['id']
                units.append((script_id, [source['id']], variant))
    items, offset, music_state = [], 0, {}
    for number, (script_id, ids, combination) in enumerate(units, 1):
        seed = hashlib.sha256(f"{settings['seed']}:{script_id}:{combination}:{number}".encode()).hexdigest()
        rng = random.Random(seed)
        parts, documents, duration = [], {}, 0
        captions, titles, sides, timing_warnings = [], [], [], []
        for source_id in ids:
            source = sources[source_id]
            length = round(source['duration_ms']/settings['main_speed'])
            part = {'source_id': source_id, 'path': source['path'], 'start_ms': duration,
                    'duration_ms': length, 'source_duration_ms': source['duration_ms'], 'speed': settings['main_speed']}
            parts.append(part)
            if source_id not in inv.get('documents', {}):
                raise ValueError('Planning needs validated, prepared subtitles for '+source_id)
            mapped = map_document(inv['documents'][source_id], part, settings['main_speed'])
            documents[source_id] = mapped
            timing_warnings.extend(mapped.get('timing_warnings', []))
            for original in mapped['cues']:
                cue = copy.deepcopy(original)
                cue['source_cue_id'] = cue['id']
                cue['source_id'] = source_id
                cue['id'] = source_id+':'+cue['id']
                captions.append(cue)
            titles.extend(mapped.get('title_candidates', []))
            sides.extend(mapped.get('side_candidates', []))
            duration += length
        if settings['broll_mode'] == 'semantic':
            planned = plan_semantic_broll(parts, documents, inv['broll'], settings, rng)
        else:
            planned = _legacy_broll(parts, inv['broll'], settings, rng, duration)
        music = choose_music(catalog.get('music', []) if settings.get('music_enabled', True) else [], duration, settings, music_state, master)
        fonts = catalog.get('fonts', [])
        preferred=settings.get('preferred_fonts',[])
        preferred_pool=[f for f in fonts if any(n in (f.get('name','')+' '+str(f.get('path',''))) for n in preferred)]
        if preferred_pool:fonts=preferred_pool
        if not fonts:
            raise ValueError('A readable local font is required; backend default font is not a faithful fallback')
        clips = planned['clips']
        item = {'number': number, 'script_id': script_id, 'source_id': ids[0], 'source_path': parts[0]['path'],
                'segments': parts, 'combination_index': combination, 'variant': number, 'seed': seed,
                'start_ms': offset, 'duration_ms': duration, 'captions': captions,
                'title_candidates': titles, 'side_candidates':sides,'broll': clips, 'broll_opportunities': planned['opportunities'],
                'music': music, 'font': rng.choice(fonts),
                'warnings': timing_warnings+planned['warnings'],
                'effective_broll_ratio': round(sum(c['duration_ms'] for c in clips)/duration, 4)}
        items.append(item)
        if inv.get('_preflight_native'):
            from caption_engine import append_captions
            from layout_engine import append_layout
            scratch={'tracks':[],'materials':{}}
            ev=append_captions(scratch,captions,settings,catalog,item['font'],lambda p:p,random.Random(seed+':caption'),offset_ms=offset)
            lv=append_layout(scratch,item,inv.get('layout_job',{}),settings,catalog,item['font'],lambda p:p,random.Random(seed+':layout'))
            item['preflight']={'subtitle_segments':len(ev),'layout_roles':[e.get('component')for e in lv]}
            item['warnings'].extend(w for e in ev+lv for w in e.get('warnings',[]))
        offset += duration
    return {'schema_version': '1.1', 'items': items, 'duration_ms': offset, 'settings': settings,
            'semantic_annotations_reused': True, 'music_usage': music_state.get('music_usage', {})}
