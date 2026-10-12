"""Build media with the bundled backend, then compile native caption/layout layers."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import sys
import time

from caption_engine import append_captions
from layout_engine import append_layout


def build(job, inv, resolved, work, state, io):
    sys.path.insert(0, str(io.BACKEND/'scripts'))
    sys.path.insert(0, str(io.BACKEND/'scripts/vendor'))
    from jy_wrapper import JyProject
    import pyJianYingDraft as draft

    root = inv['draft_root']
    if not root or not Path(root).is_dir():
        raise ValueError('Detected draft root missing; supply a verified directory')
    name = job.get('name', '')
    if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name in ('.', '..') or name.endswith(('.', ' ')):
        raise ValueError('Invalid draft name')
    directory = Path(root)/name
    if directory.exists():
        raise FileExistsError('Draft already exists; choose a new batch name')
    settings, catalog = inv['settings'], inv['catalog']
    project = JyProject(name, width=settings['width'], height=settings['height'], drafts_root=root, overwrite=False)
    script = project.script
    script.fps = settings['fps']
    project._explicit_res = True
    io.write(directory/'INCOMPLETE.json', {'phase': 'building'})
    state.update('copying_materials', draft_path=str(directory))
    staged, materials = {}, {}

    def stage(path):
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError('Resource missing: '+str(p))
        key = str(p)
        if key in staged:
            return staged[key]
        if p.is_dir():
            files = sorted(x for x in p.rglob('*') if x.is_file())
            if not files:
                raise ValueError('Effect resource directory is empty: '+str(p))
            h = hashlib.sha256()
            for child in files:
                h.update(child.relative_to(p).as_posix().encode('utf-8'))
                h.update(io.digest(child).encode('ascii'))
            target = directory/'effect_resources'/h.hexdigest()
            shutil.copytree(p, target, dirs_exist_ok=True)
        else:
            target = directory/'materials'/(io.digest(p)+p.suffix.lower())
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                shutil.copy2(p, target)
        staged[key] = str(target.resolve())
        return staged[key]

    def material(path, audio=False):
        key = (stage(path), audio)
        if key not in materials:
            materials[key] = draft.AudioMaterial(key[0]) if audio else draft.VideoMaterial(key[0])
        return materials[key]

    def tr(start, length):
        return draft.Timerange(round(start*1000), round(length*1000))

    def video(path, start, length, source_start=0, source_length=None, volume=0, clip=None):
        mat = material(path)
        # With source + target ranges the backend derives speed. Avoid passing speed
        # again: its constructor would re-round and override the target duration.
        n = length if source_length is None else source_length
        if mat.duration and getattr(mat, 'material_type', '') != 'photo':
            n = min(n, mat.duration/1000-source_start)
        return draft.VideoSegment(mat, tr(start, length), source_timerange=tr(source_start, n),
                                  volume=volume, clip_settings=draft.ClipSettings(**(clip or {})))

    def audio(path, start, length, source_start=0, volume=0.36):
        return draft.AudioSegment(material(path, True), tr(start, length),
                                  source_timerange=tr(source_start, length), volume=volume)

    for kind, label, index in [(draft.TrackType.video, 'Main', 0), (draft.TrackType.video, 'Broll', 10),
                               (draft.TrackType.audio, 'Music', 0), (draft.TrackType.audio, 'SFX', 0)]:
        script.add_track(kind, label, absolute_index=index)
    if settings['background']['mode'] == 'image':
        script.add_track(draft.TrackType.video, 'Background', absolute_index=-10)
    image_overlays = [o for o in inv['overlay_specs'] if o['kind'] == 'image']
    for index, _ in enumerate(image_overlays):
        script.add_track(draft.TrackType.video, 'ImageOverlay'+str(index), absolute_index=60+index)

    for item in resolved['items']:
        offset, duration = item['start_ms'], item['duration_ms']
        for part in item['segments']:
            main = video(part['path'], offset+part['start_ms'], part['duration_ms'],
                         source_length=part['source_duration_ms'], volume=settings['main_volume'], clip=settings['main_transform'])
            if settings['background']['mode'] == 'blur':
                main.add_background_filling('blur')
            script.add_segment(main, 'Main')
        if settings['background']['mode'] == 'image':
            script.add_segment(video(settings['background']['path'], offset, duration), 'Background')
        for clip in item['broll']:
            seg = video(clip['path'], offset+clip['start_ms'], clip['duration_ms'], clip['source_start_ms'],
                        volume=settings.get('broll_volume', 0.0), clip=settings['broll_transform'])
            script.add_segment(seg, 'Broll')
        for index, overlay in enumerate(image_overlays):
            script.add_segment(video(overlay['path'], offset, duration, clip=overlay.get('transform', {})), 'ImageOverlay'+str(index))
        if item.get('music'):
            path = item['music']['path']
            available = material(path, True).duration//1000
            source_start = round(settings['music_source_start_s']*1000)
            if available <= source_start:
                raise ValueError('Music source start exceeds material')
            position, chunks = 0, []
            while position < duration:
                length = min(available-source_start, duration-position)
                seg = audio(path, offset+position, length, source_start, settings['music_volume'])
                fade_in = min(round(settings['music_fade_in_s']*1e6), length*1000//3) if position == 0 else 0
                fade_out = min(round(settings['music_fade_out_s']*1e6), length*1000//3) if position+length == duration else 0
                if fade_in or fade_out:
                    seg.add_fade(fade_in, fade_out)
                script.add_segment(seg, 'Music')
                chunks.append({'start_ms': position, 'duration_ms': length, 'source_start_ms': source_start,
                               'fade_in_ms': fade_in/1000, 'fade_out_ms': fade_out/1000})
                position += length
                if not settings['music_loop']:
                    break
                source_start = 0
            item['music_chunks'] = chunks
        state.update('building_media', completed=item['number'])

    project.save()
    content_path = directory/'draft_info.json'
    content = io.read(content_path)
    for item in resolved['items']:
        rng = random.Random(item['seed']+':caption')
        events = append_captions(content, item['captions'], settings, catalog, item['font'], stage, rng,
                                 offset_ms=item['start_ms'])
        item['subtitle_events'] = events
        item.setdefault('warnings', []).extend(w for event in events for w in event.get('warnings', []))
        item['layout_events'] = append_layout(content, item, job, settings, catalog, item['font'], stage,
                                             random.Random(item['seed']+':layout'))
        previous_end = item['start_ms'] - settings['sfx_min_gap_s']*1000
        candidates = []
        for event in events:
            if settings['sfx_mode'] == 'every_cue':
                candidates.append((event['start_ms'], event))
            elif settings['sfx_mode'] == 'keywords':
                for keyword in event.get('keyword_events', []):
                    candidates.append((keyword['start_ms'], event))
        for start, event in sorted(candidates, key=lambda pair: pair[0]):
            if not catalog.get('sfx') or start < previous_end+settings['sfx_min_gap_s']*1000:
                continue
            effect = rng.choice(catalog['sfx'])
            length = min(material(effect['path'], True).duration//1000, item['start_ms']+item['duration_ms']-start)
            if length <= 0:
                continue
            sound = audio(effect['path'], start, length, volume=settings['sfx_volume'])
            script.add_segment(sound, 'SFX')
            previous_end = start+length
            event.setdefault('sfx', []).append({'name': effect.get('name'), 'start_ms': start, 'duration_ms': length})
        state.update('building_captions', completed=item['number'], items=[
            {'number': x['number'], 'source_id': x['source_id'], 'start_ms': x['start_ms'], 'duration_ms': x['duration_ms']}
            for x in resolved['items'][:item['number']]])

    # Register the newly built sound effects without overwriting native caption
    # and layout materials added after the backend's initial serialization.
    for kind, entries in script.materials.export_json().items():
        known = {x.get('id') for x in content['materials'].setdefault(kind, [])}
        content['materials'][kind].extend(x for x in entries if x.get('id') not in known)
    sound_track = script.tracks['SFX'].export_json()
    for index, track in enumerate(content['tracks']):
        if track.get('name') == 'SFX':
            content['tracks'][index] = sound_track
    content['duration'] = resolved['duration_ms']*1000
    io.write(content_path, content)
    meta_path = directory/'draft_meta_info.json'
    meta = io.read(meta_path)
    meta.update(draft_name=name, draft_fold_path=str(directory.resolve()), draft_root_path=str(Path(root).resolve()),
                tm_duration=resolved['duration_ms']*1000, tm_draft_modified=int(time.time()*1e6))
    io.write(meta_path, meta)
    io.write(Path(work)/'resolved-plan.json', resolved)
    io.write(directory/'batch-resolved-plan.json', resolved)
    io.write(directory/'batch-job.json', job)
    with (directory/'segments-index.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['number', 'source_id', 'variant', 'start_s', 'end_s', 'duration_s', 'seed'])
        writer.writeheader()
        for item in resolved['items']:
            writer.writerow({'number': item['number'], 'source_id': item['source_id'], 'variant': item['variant'],
                             'start_s': item['start_ms']/1000, 'end_s': (item['start_ms']+item['duration_ms'])/1000,
                             'duration_s': item['duration_ms']/1000, 'seed': item['seed']})
    report = io.verify_draft(directory, resolved)
    report.update(warnings=inv['warnings'] + [w for item in resolved['items'] for w in item.get('warnings', [])],
                  jianying_open_verified=False, root_registration_verified=False,
                  resources_copied=len(staged), draft_path=str(directory.resolve()))
    io.write(directory/'build-report.json', report)
    if not report['ok']:
        raise RuntimeError('Draft structural verification failed: '+str(report['errors']))
    (directory/'INCOMPLETE.json').unlink()
    return report
