"""Product feed batch CLI. Python stdlib for discovery/planning; runtime for media/drafts."""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
VIDEO = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}
AUDIO = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}
IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
BACKEND = ROOT / 'vendor/jianying-editor'
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def command(args, timeout=120):
    # No shell and no interpolation; do not leak signed URLs or raw upstream responses.
    r = subprocess.run([str(x) for x in args], capture_output=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    if r.returncode:
        raise RuntimeError(f'{Path(str(args[0])).name} failed, exit={r.returncode}: '
                           + (r.stderr or r.stdout)[-1200:])
    return r.stdout


def ffmpeg():
    value = os.getenv('PFB_FFMPEG') or shutil.which('ffmpeg')
    if value:
        return value
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError('FFmpeg missing; ask permission to install runtime dependencies')


def probe(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f'Missing file: {path}')
    if path.suffix.lower() in IMAGE:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            im.verify()
        return {'path': str(path), 'duration_ms': 0, 'width': w, 'height': h, 'audio': False, 'kind': 'image'}
    p = os.getenv('PFB_FFPROBE') or shutil.which('ffprobe')
    if p:
        o = json.loads(command([p, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))
        v = next((s for s in o['streams'] if s.get('codec_type') == 'video'), {})
        ms = math.floor(float(v.get('duration') or o.get('format', {}).get('duration') or 0) * 1000)
        rotation = next((abs(int(x.get('rotation', 0))) for x in v.get('side_data_list', []) if 'rotation' in x), 0)
        w, h = int(v.get('width', 0)), int(v.get('height', 0))
        if rotation in (90, 270):
            w, h = h, w
        return {'path': str(path), 'duration_ms': ms, 'width': w, 'height': h,
                'rotation': rotation, 'audio': any(s.get('codec_type') == 'audio' for s in o['streams']),
                'kind': 'video' if v else 'audio'}
    # MediaInfo is also used by the bundled backend; avoids depending on ffprobe on Windows.
    from pymediainfo import MediaInfo
    info = MediaInfo.parse(str(path))
    v = next(iter(info.video_tracks), None)
    duration = float(v.duration) if v and v.duration else next((float(t.duration) for t in info.tracks if getattr(t, 'duration', None)), 0)
    rotation = abs(round(float(getattr(v, 'rotation', 0) or 0))) if v else 0
    w, h = (int(v.width), int(v.height)) if v else (0, 0)
    if rotation in (90, 270):
        w, h = h, w
    return {'path': str(path), 'duration_ms': math.floor(duration), 'width': w, 'height': h,
            'rotation': rotation, 'audio': bool(info.audio_tracks), 'kind': 'video' if v else 'audio'}


def discover():
    local = Path(os.getenv('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    user_data = local / 'JianyingPro/User Data'
    installations = []
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for base in ('Software/Microsoft/Windows/CurrentVersion/Uninstall',
                         'Software/WOW6432Node/Microsoft/Windows/CurrentVersion/Uninstall'):
                try:
                    with winreg.OpenKey(hive, base.replace('/', '\\')) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as child:
                                values = {}
                                for j in range(winreg.QueryInfoKey(child)[1]):
                                    k, v, _ = winreg.EnumValue(child, j); values[k] = v
                                if re.search('剪映|jianying', str(values.get('DisplayName', '')), re.I):
                                    installations.append({k: values.get(k) for k in
                                                          ('DisplayName', 'DisplayVersion', 'InstallLocation', 'DisplayIcon')})
                except OSError:
                    pass
    apps = local / 'JianyingPro/Apps'
    if apps.exists():
        for exe in apps.glob('*/JianyingPro.exe'):
            installations.append({'InstallLocation': str(exe.parent), 'DisplayVersion': exe.parent.name})
    candidates = []
    configured = os.getenv('PFB_DRAFT_ROOT')
    if configured:
        candidates.append({'path': str(Path(configured).resolve()), 'source': 'PFB_DRAFT_ROOT'})
    # Scoped user settings inspection, never a whole-disk scan.
    settings = user_data / 'Config'
    if settings.exists():
        for f in list(settings.glob('*'))[:80]:
            if not f.is_file() or f.stat().st_size > 2_000_000:
                continue
            try:
                o = read(f)
            except Exception:
                continue
            def visit(v):
                if isinstance(v, dict):
                    for k, x in v.items():
                        if isinstance(x, str) and re.search('draft.*(path|folder|root)|project.*(path|root)', k, re.I):
                            p = Path(x)
                            if p.is_dir(): candidates.append({'path': str(p.resolve()), 'source': str(f) + ':' + k})
                        visit(x)
                elif isinstance(v, list):
                    for x in v: visit(x)
            visit(o)
    default = user_data / 'Projects/com.lveditor.draft'
    if default.is_dir(): candidates.append({'path': str(default.resolve()), 'source': 'default_existing'})
    uniq = {x['path']: x for x in reversed(candidates)}
    roots = list(reversed(list(uniq.values())))
    return {'installations': installations, 'user_data': str(user_data),
            'draft_roots': roots, 'draft_root': roots[0]['path'] if roots else None,
            'draft_root_ambiguous': len(roots) > 1}


def doctor():
    o = discover()
    checks = {'python': sys.executable, 'python_version': sys.version.split()[0],
              'node': shutil.which('node'),
              'ffprobe': os.getenv('PFB_FFPROBE') or shutil.which('ffprobe'),
              'backend': (BACKEND / 'scripts/jy_wrapper.py').is_file()}
    if checks['node']:
        try: checks['node_version'] = command([checks['node'], '--version']).strip()
        except Exception: checks['node_version'] = None
    try: checks['ffmpeg'] = ffmpeg()
    except RuntimeError: checks['ffmpeg'] = None
    checks['python_dependencies'] = {m: bool(importlib.util.find_spec(m))
                                     for m in ('PIL', 'pymediainfo', 'imageio_ffmpeg')}
    if os.name == 'nt': checks['python_dependencies']['uiautomation'] = bool(importlib.util.find_spec('uiautomation'))
    pkg = ROOT / 'vendor/jianying-subtitle/package.json'
    checks['subtitle_version'] = read(pkg).get('version') if pkg.is_file() else None
    missing = []
    if not o['installations']: missing.append('剪映未在常见安装记录发现；安装或提供现有安装位置')
    if not o['draft_root']: missing.append('默认草稿位置未确定；首次启动剪映后重检设置')
    for k in ('node', 'ffmpeg'):
        if not checks[k]: missing.append(k)
    if checks.get('node_version') and int(checks['node_version'].lstrip('v').split('.')[0]) < 18:
        missing.append('Node >=18 required')
    if sys.version_info < (3,10): missing.append('Python >=3.10 required')
    missing.extend(k for k, v in checks['python_dependencies'].items() if not v)
    if checks['subtitle_version'] != '0.2.3': missing.append('jianying-subtitle@0.2.3')
    return {**o, 'checks': checks, 'missing': missing, 'platform': sys.platform,
            'ready': not missing, 'installation_requires_user_confirmation': True}


def setup(approved):
    if not approved: raise ValueError('Need user installation consent before --approved')
    runtime = ROOT / 'runtime'; runtime.mkdir(exist_ok=True)
    venv = runtime / 'venv'
    if not venv.exists(): command([sys.executable, '-m', 'venv', venv])
    py = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    command([py, '-m', 'pip', 'install', '-r', ROOT / 'requirements.txt'], timeout=600)
    pkg = ROOT / 'vendor/jianying-subtitle/package.json'
    if not pkg.is_file() or read(pkg).get('version') != '0.2.3':
        raise ValueError('Bundled subtitle source missing or wrong version; repair this Skill package')
    return {'python': str(py), 'subtitle_version': '0.2.3', 'next': 'run doctor using this Python'}


def resources():
    d = discover(); user = Path(d['user_data']); found = {'music': [], 'fonts': [], 'effects': []}
    missing_records = []; encrypted = 0
    for category, sub, exts in [('music', 'Cache/music', AUDIO), ('fonts', 'Cache/effect', {'.ttf', '.otf', '.ttc'}),
                               ('fonts', 'Cache/font', {'.ttf', '.otf', '.ttc'})]:
        base = user / sub
        if not base.exists(): continue
        for i, f in enumerate(base.rglob('*')):
            if i > 20000: break
            if f.is_file() and f.suffix.lower() in exts:
                found[category].append({'name': f.stem, 'path': str(f.resolve()), 'available': True, 'source': 'cache'})
    system = Path(os.getenv('WINDIR', 'C:/Windows')) / 'Fonts'
    if system.exists():
        for f in system.iterdir():
            if f.suffix.lower() in {'.ttf', '.otf', '.ttc'}:
                found['fonts'].append({'name': f.stem, 'path': str(f), 'available': True, 'source': 'system'})
    for category in ('effect', 'artistEffect'):
        base = user / 'Cache' / category
        if base.exists():
            for f in base.iterdir():
                if f.is_dir(): found['effects'].append({'id': f.name, 'path': str(f), 'verified': False})
    recent = []
    for r in d['draft_roots']:
        recent.extend(p for p in Path(r['path']).glob('*') if p.is_dir())
    recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for directory in recent[:10]:
        f = next((directory / n for n in ('draft_info.json', 'draft_content.json') if (directory / n).is_file()), None)
        if not f: continue
        try: o = read(f)
        except Exception: encrypted += 1; continue
        def walk(v):
            if isinstance(v, dict):
                p = v.get('font_path') or v.get('path')
                if isinstance(p, str) and p:
                    ext = Path(p).suffix.lower()
                    cat = 'music' if ext in AUDIO else 'fonts' if ext in {'.ttf', '.otf', '.ttc'} else None
                    if cat:
                        item = {'name': v.get('name') or v.get('material_name') or Path(p).stem,
                                'path': p, 'available': Path(p).is_file(), 'source': 'recent:' + directory.name}
                        (found[cat] if item['available'] else missing_records).append(item)
                for x in v.values(): walk(x)
            elif isinstance(v, list):
                for x in v: walk(x)
        walk(o)
    for k in ('music', 'fonts'):
        found[k] = list({x['path']: x for x in found[k]}.values())
    return {**found, 'missing_records': missing_records, 'unreadable_recent_drafts': encrypted,
            'note': '缓存存在不等于商用授权；效果必须另行验证，不自动进入随机集合'}


def paths(value, exts):
    if not value: return []
    values = value if isinstance(value, list) else [value]
    out = []
    for s in values:
        p = Path(s).expanduser().resolve()
        if p.is_dir(): out.extend(x for x in p.rglob('*') if x.is_file() and x.suffix.lower() in exts)
        elif p.is_file() and p.suffix.lower() in exts: out.append(p)
        else: raise ValueError(f'Invalid media location: {p}')
    return sorted(set(out), key=lambda p: str(p).casefold())


def job_settings(job):
    settings = read(ROOT / 'presets/defaults.json')
    settings.update(job.get('settings', {}))
    for key in ('width', 'height', 'fps'):
        if not isinstance(settings[key], int) or settings[key] <= 0: raise ValueError(key)
    for key in ('opening_no_broll_s', 'ending_no_broll_s', 'sfx_min_gap_s'):
        if not isinstance(settings[key], (float, int)) or settings[key] < 0: raise ValueError(key)
    for key in ('broll_ratio', 'broll_duration_s', 'broll_gap_s'):
        v = settings[key]
        if len(v) != 2 or not 0 <= v[0] <= v[1]: raise ValueError(key)
    if settings['broll_ratio'][1] > 1 or settings['broll_duration_s'][0] <= 0: raise ValueError('invalid broll range')
    for key in ('music_volume', 'sfx_volume', 'animation_probability', 'keyword_popup_probability'):
        if not 0 <= settings[key] <= 1: raise ValueError(key)
    if settings['sfx_mode'] not in ('none', 'keywords', 'every_cue'): raise ValueError('sfx_mode')
    if settings['background']['mode'] not in ('none', 'blur', 'image'): raise ValueError('background.mode')
    if settings['background']['mode'] == 'image' and not Path(settings['background']['path']).is_file():
        raise ValueError('background image missing')
    return settings


def inventory(job):
    if job.get('schema_version') != '1.0': raise ValueError('schema_version must be 1.0')
    count = job.get('count')
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0: raise ValueError('count must be positive integer')
    settings = job_settings(job)
    main = paths(job.get('main_videos'), VIDEO)
    if not main: raise ValueError('No main videos')
    media = paths(job.get('broll'), VIDEO)
    rows, broll, errors = [], [], []
    for key, files, result in [('main', main, rows), ('broll', media, broll)]:
        for f in files:
            try:
                x = probe(f)
                if x['kind'] != 'video' or x['duration_ms'] <= 0: raise ValueError('not timed video')
                if key == 'main' and not x['audio']: raise ValueError('Main video has no audio')
                x['sha256'] = digest(f); x['id'] = key + '_' + x['sha256'][:16]
                result.append(x)
            except Exception as e: errors.append({'path': str(f), 'error': str(e)})
    # Same file copied under another name is one source, never duplicate recognition.
    rows = list({x['id']: x for x in rows}.values())
    broll = list({x['id']: x for x in broll}.values())
    if not rows: raise ValueError('No usable main video')
    distribution = job.get('distribution')
    if distribution is None:
        q, remainder = divmod(count, len(rows))
        distribution = {x['id']: q + (i < remainder) for i, x in enumerate(rows)}
    if set(distribution) != {x['id'] for x in rows}: raise ValueError('distribution IDs differ from inventory')
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in distribution.values()) or sum(distribution.values()) != count:
        raise ValueError('distribution must sum to count')
    overlays = []
    for x in job.get('overlays', []):
        if x.get('kind') == 'image': overlays.append(probe(x['path']))
        elif x.get('kind') != 'text' or not x.get('text'): raise ValueError('overlay needs image path or text')
    ref = probe(job['reference_video']) if job.get('reference_video') else None
    catalog = read(job['resource_catalog']) if job.get('resource_catalog') else read(ROOT / 'presets/resources.json')
    for k in ('music', 'fonts', 'sfx'):
        for x in catalog.get(k, []):
            if not Path(x['path']).is_file(): errors.append({'path': x['path'], 'error': 'catalog resource missing'})
    d = discover()
    draft_root = job.get('draft_root') or d['draft_root']
    warnings = []
    for k in ('text_intro', 'flowers', 'video_intro', 'video_outro'):
        if not catalog.get(k): warnings.append(f'{k}: empty verified catalog; disabled')
    if not catalog.get('music'): warnings.append('No BGM candidate; batch will have no BGM')
    if not catalog.get('fonts'): warnings.append('No specified font; backend default font (unverified display)')
    if not broll: warnings.append('No product footage; talking-head only')
    return {'main': rows, 'broll': broll, 'count': count, 'distribution': distribution, 'overlays': overlays,
            'reference': ref, 'draft_root': draft_root, 'settings': settings, 'errors': errors, 'warnings': warnings,
            'estimated_timeline_s': sum(x['duration_ms'] * distribution[x['id']] for x in rows) / 1000,
            'source_bytes': sum(Path(x['path']).stat().st_size for x in rows + broll), 'catalog': catalog}


def validate_subtitles(data, source):
    if data.get('source_hash') != source['sha256']: raise ValueError('Subtitle source hash mismatch: ' + source['id'])
    if data.get('status') not in ('corrected', 'reviewed_asr'): raise ValueError('Subtitle must be AI corrected/reviewed')
    cues = data.get('cues', [])
    if not cues: raise ValueError('No subtitle cues: ' + source['id'])
    last = 0; ids = set()
    for c in cues:
        s, e = c['start_ms'], c['end_ms']
        if not 0 <= s < min(e, source['duration_ms']) or e > source['duration_ms'] + 40 or s < last:
            raise ValueError('Invalid/overlapping cue times')
        if c['id'] in ids or not c.get('text'): raise ValueError('Duplicate cue ID or empty text')
        ids.add(c['id']); last = e
        for k in c.get('keywords', []):
            a, b = k['char_start'], k['char_end']
            if not 0 <= a < b <= len(c['text']) or c['text'][a:b] != k['text']: raise ValueError('Keyword character range mismatch')
            if not s <= k['start_ms'] < k['end_ms'] <= e: raise ValueError('Keyword timing outside cue')
            if k.get('timing_precision') not in ('word', 'cue'): raise ValueError('Keyword timing precision missing')
    return cues


def transcribe(job, work):
    inv = inventory(job)
    if inv['errors']: raise ValueError('Resolve inventory errors before transcription')
    cli = ROOT / 'vendor/jianying-subtitle/bin/jianying-subtitle.mjs'
    pkg = cli.parents[1] / 'package.json'
    if not pkg.is_file() or read(pkg)['version'] != '0.2.3': raise ValueError('Bundled subtitle source missing or wrong version; repair this Skill package')
    node = shutil.which('node')
    if not node: raise ValueError('Node missing')
    out = []
    for x in inv['main']:
        directory = Path(work) / 'subtitles'; directory.mkdir(parents=True, exist_ok=True)
        raw = directory / (x['id'] + '.raw.json')
        if raw.is_file() and read(raw).get('source_hash') == x['sha256']:
            out.append({'source_id': x['id'], 'raw': str(raw), 'cached': True}); continue
        wav = directory / (x['id'] + '.wav')
        command([ffmpeg(), '-y', '-v', 'error', '-i', x['path'], '-vn', '-ac', '1', '-ar', '16000', wav])
        result = directory / (x['id'] + '.asr.json')
        # Retry is explicit: never loop uploads after an ambiguous task failure.
        try:
            command([node, cli, wav, '-o', result], timeout=600)
        except Exception:
            raise RuntimeError('Subtitle cloud request failed for ' + x['id'] +
                               '; no automatic upload retry. Check connectivity/service availability; local audio retained.') from None
        segments = read(result)
        cues = [{'id': f's{i+1:04}', 'text': c['text'], 'start_ms': c['startMs'], 'end_ms': c['endMs'],
                 'words': c.get('words', []), 'keywords': []} for i, c in enumerate(segments)]
        write(raw, {'schema_version': '1.0', 'source_id': x['id'], 'source_hash': x['sha256'],
                    'status': 'raw', 'asr_version': '0.2.3', 'cues': cues})
        out.append({'source_id': x['id'], 'raw': str(raw), 'cached': False})
    return {'subtitles': out, 'next': 'AI maps scripts, corrects text, adds keyword char ranges and saves corrected JSON'}


def import_srt(video, srt, out):
    x = probe(video); h = digest(video)
    text = Path(srt).read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    def ms(v):
        a, b, c, d = map(int, re.split('[:,.]', v)); return ((a * 60 + b) * 60 + c) * 1000 + d
    cues = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines(); i = next((i for i, l in enumerate(lines) if '-->' in l), None)
        if i is None: continue
        m = re.match(r'(\d\d:\d\d:\d\d[,\.]\d{3})\s*-->\s*(\d\d:\d\d:\d\d[,\.]\d{3})', lines[i])
        if not m: raise ValueError('Invalid SRT time')
        cues.append({'id': f's{len(cues)+1:04}', 'start_ms': ms(m[1]), 'end_ms': ms(m[2]),
                     'text': '\n'.join(lines[i+1:]), 'words': [], 'keywords': [], 'timing_precision': 'cue'})
    value = {'schema_version': '1.0', 'source_id': 'main_' + h[:16], 'source_hash': h, 'status': 'raw', 'cues': cues}
    write(out, value); return {'path': str(Path(out).resolve()), 'cues': len(cues), 'duration_ms': x['duration_ms']}


def frames(video, out, count):
    from PIL import Image, ImageDraw
    x = probe(video)
    if x['kind'] != 'video' or x['duration_ms'] <= 0: raise ValueError('Reference is not timed video')
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    count = max(2, min(30, count)); thumbs = []; rows = []
    for i in range(count):
        t = (i + 0.5) / count * x['duration_ms'] / 1000
        p = out / f'frame-{i+1:02}.jpg'
        command([ffmpeg(), '-y', '-v', 'error', '-ss', str(t), '-i', video, '-frames:v', '1', '-vf', 'scale=480:-2', p])
        with Image.open(p) as im:
            im = im.convert('RGB'); im.thumbnail((240, 300))
            tile = Image.new('RGB', (260, 330), '#202020'); tile.paste(im, ((260-im.width)//2, 5))
            ImageDraw.Draw(tile).text((10, 310), f'{t:.2f}s', fill='white'); thumbs.append(tile)
        rows.append({'time_s': round(t, 3), 'path': str(p.resolve())})
    sheet = Image.new('RGB', (260*4, 330*math.ceil(count/4)), '#101010')
    for i, tile in enumerate(thumbs): sheet.paste(tile, ((i%4)*260, (i//4)*330))
    sheet.save(out / 'contact-sheet.jpg'); write(out / 'frames.json', rows)
    return {'contact_sheet': str((out/'contact-sheet.jpg').resolve()), 'frames': rows}


def verify_catalog(catalog):
    for k in ('flowers', 'text_intro', 'video_intro', 'video_outro'):
        for x in catalog.get(k, []):
            if x.get('verified') is not True or not catalog.get('verified_jianying_version'):
                raise ValueError(f'{k} requires verified=true and verified_jianying_version')
            if k == 'flowers' and not x.get('effect_id'): raise ValueError('flower effect_id missing')
            if k != 'flowers' and not x.get('enum'): raise ValueError('animation enum missing')
            if x.get('cache_path') and not Path(x['cache_path']).is_dir(): raise ValueError('effect cache missing')


def validate_job(job):
    inv = inventory(job)
    if inv['errors']: raise ValueError(json.dumps(inv['errors'], ensure_ascii=False))
    verify_catalog(inv['catalog'])
    for x in inv['main']:
        if inv['distribution'][x['id']] == 0: continue
        p = job.get('subtitles', {}).get(x['id'])
        if not p: raise ValueError('Missing corrected subtitles for ' + x['id'])
        validate_subtitles(read(p), x)
    return inv


def choice(rng, catalog, key):
    a = catalog.get(key, []); return rng.choice(a) if a else None


def plan(inv):
    settings = inv['settings']; catalog = inv['catalog']; items = []; offset = 0; number = 0
    for source in inv['main']:
        for variant in range(inv['distribution'][source['id']]):
            number += 1
            seed = hashlib.sha256(f"{settings['seed']}:{source['id']}:{variant}".encode()).hexdigest()
            rng = random.Random(seed); duration = source['duration_ms']
            left = min(duration, round(settings['opening_no_broll_s']*1000))
            right = max(left, duration-round(settings['ending_no_broll_s']*1000))
            target = min(right-left, round(duration*rng.uniform(*settings['broll_ratio'])))
            broll = []; position = left; total = 0; previous = None
            while inv['broll'] and position < right and total < target:
                position += round(rng.uniform(*settings['broll_gap_s'])*1000)
                candidates = [x for x in inv['broll'] if x['id'] != previous] or inv['broll']
                footage = rng.choice(candidates)
                length = min(round(rng.uniform(*settings['broll_duration_s'])*1000),
                             footage['duration_ms'], right-position, target-total)
                if length < 400: break
                start = rng.randint(0, max(0, footage['duration_ms']-length))
                broll.append({'asset_id': footage['id'], 'path': footage['path'], 'start_ms': position,
                              'duration_ms': length, 'source_start_ms': start,
                              'intro': choice(rng, catalog, 'video_intro'), 'outro': choice(rng, catalog, 'video_outro')})
                previous = footage['id']; total += length; position += length
            items.append({'number': number, 'source_id': source['id'], 'source_path': source['path'],
                          'variant': variant+1, 'seed': seed, 'start_ms': offset, 'duration_ms': duration,
                          'broll': broll, 'music': choice(rng, catalog, 'music'), 'font': choice(rng, catalog, 'fonts'),
                          'effective_broll_ratio': round(total/duration, 4),
                          'target_broll_ratio': round(target/duration, 4)})
            offset += duration
    return {'items': items, 'duration_ms': offset, 'settings': settings, 'warnings': inv['warnings']}


class Status:
    def __init__(self, work, total):
        self.work = Path(work); self.lock = threading.RLock()
        self.data = {'phase': 'starting', 'total': total, 'completed': 0, 'items': [], 'events': []}

    def update(self, phase, **values):
        with self.lock:
            self.data.update(phase=phase, **values)
            self.data['events'].append({'time': time.time(), 'phase': phase, 'completed': self.data['completed']})
            write(self.work/'status.json', self.data)
        print(json.dumps({'phase': phase, **{k:v for k,v in values.items() if k != 'items'}}, ensure_ascii=False), flush=True)

    def server(self, port):
        state = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                with state.lock:
                    value = state.data if self.path == '/status' else state.data['items'] if self.path == '/items' else state.data['events'] if self.path == '/events' else {'ok': True} if self.path == '/health' else None
                    b = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(200 if value is not None else 404)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def build(job, inv, resolved, work, state):
    # Import the actual upstream wrapper. No monkeypatching and no invented draft format.
    sys.path.insert(0, str(BACKEND/'scripts'))
    sys.path.insert(0, str(BACKEND/'scripts/vendor'))
    from jy_wrapper import JyProject
    import pyJianYingDraft as draft
    root = inv['draft_root']
    if not root or not Path(root).is_dir(): raise ValueError('Detected draft root missing; do not guess output path')
    name = job.get('name', '')
    if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name in ('.', '..') or name.endswith(('.', ' ')):
        raise ValueError('Invalid/nonempty draft name required')
    directory = Path(root)/name
    if directory.exists(): raise FileExistsError('Draft already exists; choose new batch name')
    settings = inv['settings']; catalog = inv['catalog']
    project = JyProject(name, width=settings['width'], height=settings['height'], drafts_root=root, overwrite=False)
    script = project.script; script.fps = settings['fps']; project._explicit_res = True
    write(directory/'INCOMPLETE.json', {'phase': 'building'})
    state.update('copying_materials', draft_path=str(directory))
    stage_cache = {}; material_cache = {}
    def stage(path):
        p = Path(path).resolve(); h = digest(p)
        if h not in stage_cache:
            target = directory/'materials'/(h + p.suffix.lower()); target.parent.mkdir(exist_ok=True)
            shutil.copy2(p, target); stage_cache[h] = str(target.resolve())
        return stage_cache[h]
    def material(path, audio=False):
        p = stage(path); key = (p, audio)
        if key not in material_cache:
            material_cache[key] = draft.AudioMaterial(p) if audio else draft.VideoMaterial(p)
        return material_cache[key]
    def track(kind, name, index):
        script.add_track(kind, name, absolute_index=index)
    for kind, name, index in [(draft.TrackType.video,'Main',0), (draft.TrackType.video,'Broll',10),
                              (draft.TrackType.text,'Subtitles',30), (draft.TrackType.text,'Emphasis',40),
                              (draft.TrackType.text,'Title',50), (draft.TrackType.audio,'Music',0),
                              (draft.TrackType.audio,'SFX',0)]: track(kind,name,index)
    if settings['background']['mode'] == 'image': track(draft.TrackType.video,'Background',-10)
    for i, o in enumerate(job.get('overlays', [])):
        track(draft.TrackType.video if o['kind']=='image' else draft.TrackType.text, f'Overlay{i}', 60+i)
    def tr(start, length): return draft.Timerange(round(start*1000), round(length*1000))
    def transform(value): return draft.ClipSettings(**value)
    def video(path, start, length, source_start=0, volume=0, clip=None):
        return draft.VideoSegment(material(path), tr(start,length), source_timerange=tr(source_start,length),
                                  volume=volume, clip_settings=transform(clip or {}))
    def audio(path, start, length, source_start=0, volume=0.12):
        return draft.AudioSegment(material(path,True),tr(start,length),source_timerange=tr(source_start,length),volume=volume)
    def text(value,start,length,size,y,color,font=None,spans=None):
        seg = draft.TextSegment(value,tr(start,length),style=draft.TextStyle(size=size,color=tuple(color)),
                                border=draft.TextBorder(width=40),clip_settings=draft.ClipSettings(transform_y=y),
                                rich_spans=spans or [])
        if font:
            if font.get('enum'): seg.font = getattr(draft.FontType,font['enum']).value
            # Upstream fork lacks font_path constructor; inject explicitly after serialization.
            font_paths[seg.material_id] = stage(font['path'])
        return seg
    font_paths = {}; flower_materials = {}
    def animate(seg, item, enum_cls):
        if item: seg.add_animation(getattr(enum_cls,item['enum']),duration=min(300000,seg.duration//3))
    for kind in ('flowers','text_intro','video_intro','video_outro'):
        for x in catalog.get(kind,[]):
            if x.get('cache_path'):
                src = Path(x['cache_path']); dest = directory/'effect_resources'/kind/src.name
                shutil.copytree(src,dest,dirs_exist_ok=True)
    for item in resolved['items']:
        rng = random.Random(item['seed'] + ':text'); offset=item['start_ms']; duration=item['duration_ms']
        main = video(item['source_path'],offset,duration,volume=1,clip=settings['main_transform'])
        if settings['background']['mode']=='blur': main.add_background_filling('blur')
        script.add_segment(main,'Main')
        if settings['background']['mode']=='image':
            script.add_segment(video(settings['background']['path'],offset,duration),'Background')
        for b in item['broll']:
            v=video(b['path'],offset+b['start_ms'],b['duration_ms'],b['source_start_ms'],clip=settings['broll_transform'])
            animate(v,b['intro'],draft.IntroType); animate(v,b['outro'],draft.OutroType)
            script.add_segment(v,'Broll')
        cues=read(job['subtitles'][item['source_id']])['cues']; previous_sfx_end=offset; text_events=[]
        for c in cues:
            start=offset+c['start_ms']; length=min(c['end_ms'],duration)-c['start_ms']
            spans=[draft.RichTextSpan(k['char_start'],k['char_end'],color=tuple(settings['keyword_color']),bold=True) for k in c.get('keywords',[])]
            base=text(c['text'],start,length,settings['base_font_size'],settings['subtitle_y'],settings['subtitle_color'],item['font'],spans)
            intro=choice(rng,catalog,'text_intro') if rng.random()<settings['animation_probability'] else None
            animate(base,intro,draft.TextIntro); script.add_segment(base,'Subtitles')
            # Max one keyword popup per cue; avoid simultaneous overlap in emphasis track.
            popup=None
            if c.get('keywords') and rng.random()<settings['keyword_popup_probability']:
                k=rng.choice(c['keywords']); ps=offset+k['start_ms']; pe=min(offset+k['end_ms'],offset+duration)
                popup=text(k['text'],ps,pe-ps,settings['keyword_font_size'],settings['keyword_y'],settings['keyword_color'],item['font'])
                flower=choice(rng,catalog,'flowers')
                if flower:
                    # Upstream __contains__ does not handle TextEffect at save time.
                    # Keep original backend untouched; register its exported effect after save.
                    from pyJianYingDraft.text_segment import TextEffect
                    effect=TextEffect(flower['effect_id'],flower['effect_id'])
                    popup.extra_material_refs.append(effect.global_id)
                    flower_materials[popup.material_id]=(flower,effect.export_json())
                animate(popup,choice(rng,catalog,'text_intro'),draft.TextIntro); script.add_segment(popup,'Emphasis')
            event={'cue_id':c['id'],'intro':intro,'popup':bool(popup),'flower':flower if popup else None,'sfx':None}
            if settings['sfx_mode']=='every_cue' or (settings['sfx_mode']=='keywords' and c.get('keywords')):
                effect=choice(rng,catalog,'sfx')
                if effect and start>=previous_sfx_end+settings['sfx_min_gap_s']*1000:
                    mat=material(effect['path'],True); n=min(mat.duration//1000,offset+duration-start,1200)
                    if n>0:
                        script.add_segment(audio(effect['path'],start,n,volume=settings['sfx_volume']),'SFX')
                        previous_sfx_end=start+n; event['sfx']=effect
            text_events.append(event)
        item['subtitle_events']=text_events
        title=job.get('titles',{}).get(item['source_id'],job.get('title',''))
        if title: script.add_segment(text(title,offset,duration,settings['title_font_size'],settings['title_y'],[1,1,1],item['font']),'Title')
        for i,o in enumerate(job.get('overlays',[])):
            if o['kind']=='image':
                seg=video(o['path'],offset,duration,clip=o.get('transform',{'scale_x':0.35,'scale_y':0.35,'transform_y':0.55}))
            else:
                seg=text(o['text'],offset,duration,o.get('font_size',3),o.get('y',-0.92),o.get('color',[1,1,1]),item['font'])
            script.add_segment(seg,f'Overlay{i}')
        if item['music']:
            p=item['music']['path']; mat=material(p,True); n=mat.duration//1000
            if n<=0: raise ValueError('BGM zero duration')
            position=0; item['music_chunks']=[]
            while position<duration:
                length=min(n,duration-position); seg=audio(p,offset+position,length,volume=settings['music_volume'])
                seg.add_fade(200000 if position==0 else 0,min(500000,length*1000//3) if position+length==duration else 0)
                script.add_segment(seg,'Music'); item['music_chunks'].append({'start_ms':position,'duration_ms':length}); position+=length
        state.update('building',completed=item['number'],items=[{'number':x['number'],'source_id':x['source_id'],'start_ms':x['start_ms'],'duration_ms':x['duration_ms']} for x in resolved['items'][:item['number']]])
    # Upstream text segments reference their Speed but do not register it.
    speed_ids={s.global_id for s in script.materials.speeds}
    for layer in script.tracks.values():
        for seg in layer.segments:
            if isinstance(seg,draft.TextSegment) and seg.speed.global_id not in speed_ids:
                script.materials.speeds.append(seg.speed); speed_ids.add(seg.speed.global_id)
    project.save()
    content_path=directory/'draft_info.json'; content=read(content_path)
    # Write font paths and portable flower resources into serialized material nodes.
    flower_paths = {x['effect_id']:str((directory/'effect_resources'/'flowers'/Path(x['cache_path']).name).resolve())
                    for x in catalog.get('flowers',[]) if x.get('cache_path')}
    for t in content.get('materials',{}).get('texts',[]):
        inner=json.loads(t['content'])
        if t['id'] in flower_materials:
            flower,effect=flower_materials[t['id']]
            for style in inner.get('styles',[]):
                style['effectStyle']={'id':flower['effect_id'],'path':flower_paths.get(flower['effect_id'],'')}
            if flower['effect_id'] in flower_paths: effect['path']=flower_paths[flower['effect_id']]
            content['materials'].setdefault('effects',[]).append(effect)
        if t['id'] in font_paths:
            t['font_path']=font_paths[t['id']]
            for style in inner.get('styles',[]):
                if 'font' in style: style['font']['path']=font_paths[t['id']]
        for style in inner.get('styles',[]):
            effect=style.get('effectStyle',{})
            if effect.get('id') in flower_paths: effect['path']=flower_paths[effect['id']]
        t['content']=json.dumps(inner,ensure_ascii=False)
    for effect in content.get('materials',{}).get('effects',[]):
        if effect.get('effect_id') in flower_paths: effect['path']=flower_paths[effect['effect_id']]
    for kind in ('flowers','text_intro','video_intro','video_outro'):
        for x in catalog.get(kind,[]):
            if not x.get('cache_path'): continue
            original=str(Path(x['cache_path']).resolve()).replace('\\','/')
            dest=str((directory/'effect_resources'/kind/Path(x['cache_path']).name).resolve()).replace('\\','/')
            def relocate(v):
                if isinstance(v,dict):
                    for k,value in v.items():
                        if isinstance(value,str) and value.replace('\\','/').startswith(original): v[k]=value.replace('\\','/').replace(original,dest,1)
                        else: relocate(value)
                elif isinstance(v,list):
                    for value in v: relocate(value)
            relocate(content)
    write(content_path,content)
    meta_path=directory/'draft_meta_info.json'; meta=read(meta_path)
    meta.update(draft_name=name,draft_fold_path=str(directory.resolve()),draft_root_path=str(Path(root).resolve()),
                tm_duration=resolved['duration_ms']*1000,tm_draft_modified=int(time.time()*1e6))
    write(meta_path,meta)
    write(Path(work)/'resolved-plan.json',resolved); write(directory/'batch-resolved-plan.json',resolved)
    write(directory/'batch-job.json',job)
    index=directory/'segments-index.csv'
    with index.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['number','source_id','variant','start_s','end_s','duration_s','seed']); writer.writeheader()
        for x in resolved['items']:
            writer.writerow({'number':x['number'],'source_id':x['source_id'],'variant':x['variant'],'start_s':x['start_ms']/1000,
                             'end_s':(x['start_ms']+x['duration_ms'])/1000,'duration_s':x['duration_ms']/1000,'seed':x['seed']})
    report=verify_draft(directory,resolved)
    report.update(warnings=inv['warnings'],jianying_open_verified=False,root_registration_verified=False,
                  materials_copied=len(stage_cache),draft_path=str(directory.resolve()))
    write(directory/'build-report.json',report)
    if not report['ok']: raise RuntimeError('Draft structural verification failed: '+str(report['errors']))
    (directory/'INCOMPLETE.json').unlink()
    return report


def verify_draft(directory,resolved=None):
    directory=Path(directory)
    content=read(directory/'draft_info.json')
    if resolved is None: resolved=read(directory/'batch-resolved-plan.json')
    errors=[]; tracks=content.get('tracks',[]); materials=content.get('materials',{})
    videos={m['id']:m for m in materials.get('videos',[])}
    for group in ('videos','audios'):
        for m in materials.get(group,[]):
            p=m.get('path')
            if p and not Path(p).is_file(): errors.append('Missing material: '+p)
    for t in materials.get('texts',[]):
        if t.get('font_path') and not Path(t['font_path']).is_file(): errors.append('Missing font: '+t['font_path'])
    total=resolved['duration_ms']*1000
    main=next((t for t in tracks if t.get('name')=='Main'),None)
    if not main or len(main['segments'])!=len(resolved['items']): errors.append('Main count mismatch')
    else:
        cursor=0
        for seg,item in zip(main['segments'],resolved['items']):
            t=seg['target_timerange']
            if t['start']!=cursor or t['duration']!=item['duration_ms']*1000: errors.append('Main continuity/duration mismatch')
            cursor+=t['duration']
    material_ids={m['id'] for group in materials.values() if isinstance(group,list) for m in group if isinstance(m,dict) and 'id' in m}
    for track in tracks:
        prior_end = 0
        for seg in track.get('segments',[]):
            t=seg['target_timerange']; start=t['start']; end=start+t['duration']
            if t['duration']<=0 or start<0 or end>total: errors.append('Segment outside timeline')
            if start < prior_end: errors.append('Overlap within track: '+str(track.get('name')))
            prior_end = end
            if seg.get('material_id') not in material_ids: errors.append('Missing segment material reference')
            for reference in seg.get('extra_material_refs',[]):
                if reference not in material_ids: errors.append('Missing extra material reference: '+reference)
            item=next((x for x in resolved['items'] if x['start_ms']*1000<=start<(x['start_ms']+x['duration_ms'])*1000),None)
            if not item or end>(item['start_ms']+item['duration_ms'])*1000: errors.append('Segment crosses output boundary')
            if track.get('name')=='Broll' and item:
                local=start-item['start_ms']*1000
                if local<resolved['settings']['opening_no_broll_s']*1e6 or end>(item['start_ms']+item['duration_ms'])*1000-resolved['settings']['ending_no_broll_s']*1e6:
                    errors.append('Broll inside protected opening/ending')
                if seg.get('volume')!=0: errors.append('Broll not muted')
                source=seg.get('source_timerange',{}); mat=videos.get(seg['material_id'],{})
                if source.get('start',0)+source.get('duration',0)>mat.get('duration',0)+40000: errors.append('Broll exceeds source')
    return {'ok':not errors,'errors':errors,'tracks':[(t.get('name'),len(t.get('segments',[]))) for t in tracks],
            'timeline_s':total/1e6,'count':len(resolved['items'])}


def run(job, work, confirmed, serve=False, port=0):
    if not confirmed: raise ValueError('User batch confirmation required before --confirmed')
    work=Path(work).resolve(); work.mkdir(parents=True,exist_ok=True)
    inv=validate_job(job); resolved=plan(inv); write(work/'inventory.json',inv); write(work/'planned.json',resolved)
    state=Status(work,inv['count']); server=None
    if serve:
        server=state.server(port); url=f'http://127.0.0.1:{server.server_address[1]}'
        write(work/'monitor.json',{'url':url}); print(json.dumps({'monitor_url':url}),flush=True)
    try:
        state.update('validated')
        report=build(job,inv,resolved,work,state)
        state.update('complete',report=report)
        return report
    except Exception as e:
        state.update('failed',error=str(e)); raise
    finally:
        if server:
            # Short post-completion query window; no unbounded daemon or stale port.
            time.sleep(5); server.shutdown(); server.server_close()


def main():
    p=argparse.ArgumentParser(description='产品信息流批量剪映草稿工具')
    sub=p.add_subparsers(dest='cmd',required=True)
    for name in ('doctor','resources'):
        a=sub.add_parser(name); a.add_argument('--out')
    a=sub.add_parser('setup'); a.add_argument('--approved',action='store_true')
    for name in ('inspect','validate','plan'):
        a=sub.add_parser(name); a.add_argument('--job',required=True); a.add_argument('--out',required=True)
    a=sub.add_parser('transcribe'); a.add_argument('--job',required=True); a.add_argument('--work',required=True)
    a=sub.add_parser('import-srt'); a.add_argument('--video',required=True); a.add_argument('--srt',required=True); a.add_argument('--out',required=True)
    a=sub.add_parser('frames'); a.add_argument('--video',required=True); a.add_argument('--out',required=True); a.add_argument('--count',type=int,default=8)
    a=sub.add_parser('run'); a.add_argument('--job',required=True); a.add_argument('--work',required=True); a.add_argument('--confirmed',action='store_true'); a.add_argument('--serve',action='store_true'); a.add_argument('--port',type=int,default=0)
    a=sub.add_parser('verify'); a.add_argument('--draft',required=True)
    args=p.parse_args()
    if args.cmd=='doctor': result=doctor()
    elif args.cmd=='resources': result=resources()
    elif args.cmd=='setup': result=setup(args.approved)
    elif args.cmd=='inspect': result=inventory(read(args.job))
    elif args.cmd=='validate': result=validate_job(read(args.job))
    elif args.cmd=='plan': result=plan(validate_job(read(args.job)))
    elif args.cmd=='transcribe': result=transcribe(read(args.job),args.work)
    elif args.cmd=='import-srt': result=import_srt(args.video,args.srt,args.out)
    elif args.cmd=='frames': result=frames(args.video,args.out,args.count)
    elif args.cmd=='run': result=run(read(args.job),args.work,args.confirmed,args.serve,args.port)
    elif args.cmd=='verify': result=verify_draft(args.draft)
    if getattr(args,'out',None) and args.cmd not in ('frames','import-srt'): write(args.out,result)
    print(json.dumps({'ok':result.get('ok',True),'data':result},ensure_ascii=False),flush=True)
    if result.get('ok') is False: sys.exit(1)


if __name__=='__main__':
    try: main()
    except Exception as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr)
        if os.getenv('PFB_DEBUG'): traceback.print_exc()
        sys.exit(1)
