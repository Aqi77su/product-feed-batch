"""Product feed batch CLI. Python stdlib for discovery/planning; runtime for media/drafts."""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import difflib
import copy
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
from caption_engine import prepare_document, append_captions, validate_caption_content, validate_requested_resources
from semantic_plan import validate_annotations, map_document, plan_semantic_broll
from layout_engine import append_layout, resolve_overlays

ROOT = Path(__file__).resolve().parents[1]
VIDEO = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}
AUDIO = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}
IMAGE = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
BACKEND = ROOT / 'vendor/jianying-editor'
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def merge_dicts(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = merge_dicts(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def load_job(path):
    job = read(path)
    explicit_keys = set(job.get('settings', {}))
    selected = job.get('preset') or 'general-conversion'
    preset_path = ROOT/'presets/general-conversion/preset.json' if selected in ('default', 'general-conversion') else Path(selected).expanduser().resolve()
    preset = read(preset_path)
    if preset.get('schema_version') != '1.0': raise ValueError('Unsupported preset schema')
    inherited = read(preset_path.parent/preset['settings_file']) if preset.get('settings_file') else {}
    explicit_keys.update(preset.get('settings', {}))
    if selected not in ('default', 'general-conversion'): explicit_keys.update(inherited)
    job['settings'] = merge_dicts(merge_dicts(inherited, preset.get('settings', {})), job.get('settings', {}))
    job['layout'] = merge_dicts(preset.get('layout_defaults', {}), job.get('layout', {}))
    if not job.get('resource_catalog') and preset.get('resource_catalog'):
        job['resource_catalog'] = str((preset_path.parent/preset['resource_catalog']).resolve())
    if preset.get('resource_preferences'):
        job['resource_preferences'] = str((preset_path.parent/preset['resource_preferences']).resolve())
    job['preset_name'] = preset['name']
    job['preset_version'] = preset.get('version')
    job['_explicit_setting_keys'] = sorted(explicit_keys)
    return job


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
    from font_identity import collect_font_records,merge_fonts,enrich_font,font_record
    d = discover(); user = Path(d['user_data']); found = {'music': [], 'sfx': [], 'audio_candidates': [], 'fonts': [], 'effects': []}
    missing_records = []; encrypted = 0
    for category, sub, exts in [('audio_candidates', 'Cache/music', AUDIO), ('fonts', 'Cache/effect', {'.ttf', '.otf', '.ttc'}),
                               ('fonts', 'Cache/font', {'.ttf', '.otf', '.ttc'})]:
        base = user / sub
        if not base.exists(): continue
        for i, f in enumerate(base.rglob('*')):
            if i > 20000: break
            if f.is_file() and f.suffix.lower() in exts:
                if category=='fonts':
                    found['fonts'].append(font_record(f,source='cache'));continue
                found[category].append({'name': f.stem, 'path': str(f.resolve()), 'available': True, 'source': 'cache',
                                        'classification': 'unconfirmed_audio' if category == 'audio_candidates' else 'font_extension'})
    system = Path(os.getenv('WINDIR', 'C:/Windows')) / 'Fonts'
    if system.exists():
        for f in system.iterdir():
            if f.suffix.lower() in {'.ttf', '.otf', '.ttc'}:
                found['fonts'].append({'name': f.stem, 'path': str(f), 'available': True, 'source': 'system'})
    for category in ('effect', 'artistEffect'):
        base = user / 'Cache' / category
        if base.exists():
            for f in base.iterdir():
                if f.is_dir(): found['effects'].append({'id': f.name, 'path': str(f), 'cache_category':category,'verified': False})
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
                    cat = ({'music':'music','sound':'sfx'}.get(v.get('type'),'audio_candidates') if ext in AUDIO else 'fonts' if ext in {'.ttf', '.otf', '.ttc'} else None)
                    if cat:
                        item = {'name': v.get('name') or v.get('material_name') or Path(p).stem,
                                'path': str(Path(p).resolve()), 'available': Path(p).is_file(), 'source': 'recent:' + directory.name,
                                'classification': 'material_type' if cat in ('music','sfx') else 'font_extension' if cat == 'fonts' else 'unconfirmed_audio'}
                        (found[cat] if item['available'] else missing_records).append(item)
                for x in v.values(): walk(x)
            elif isinstance(v, list):
                for x in v: walk(x)
        walk(o)
        for font in collect_font_records(o,'recent:'+directory.name,base=directory):
            (found['fonts']if font['available']else missing_records).append(font)
    for k in ('music', 'sfx', 'audio_candidates'):
        found[k] = list({x['path']: x for x in found[k]}.values())
    found['fonts']=[enrich_font(f)for f in merge_fonts(found['fonts'])]
    classified = {str(Path(x['path']).resolve()).casefold() for k in ('music','sfx') for x in found[k]}
    found['audio_candidates'] = [x for x in found['audio_candidates'] if str(Path(x['path']).resolve()).casefold() not in classified]
    try:
        from PIL import ImageFont
        for font in found['fonts']:
            try:
                font['file_name'] = Path(font['path']).stem
                pil_name=' '.join(ImageFont.truetype(font['path'],16).getname())
                if pil_name not in font['aliases']:font['aliases'].append(pil_name)
                if not font.get('font_names'):font['name']=pil_name
            except Exception: pass
    except ImportError: pass
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
    settings = merge_dicts(settings, job.get('settings', {}))
    explicit_keys = set(job.get('_explicit_setting_keys', job.get('settings', {}).keys()))
    if (settings['width'], settings['height']) == (720, 1280):
        for key, value in {'base_font_size': 13, 'subtitle_y': -0.390625, 'subtitle_scale': 1.15}.items():
            if key not in explicit_keys: settings[key] = value
    for key in ('width', 'height', 'fps'):
        if not isinstance(settings[key], int) or settings[key] <= 0: raise ValueError(key)
    for key in ('opening_no_broll_s', 'ending_no_broll_s', 'sfx_min_gap_s'):
        if not isinstance(settings[key], (float, int)) or settings[key] < 0: raise ValueError(key)
    for key in ('broll_ratio', 'broll_duration_s', 'broll_gap_s'):
        v = settings[key]
        if len(v) != 2 or not 0 <= v[0] <= v[1]: raise ValueError(key)
    if settings['broll_ratio'][1] > 1 or settings['broll_duration_s'][0] <= 0: raise ValueError('invalid broll range')
    for key in ('music_volume', 'sfx_volume', 'keyword_popup_probability'):
        if not 0 <= settings[key] <= 1: raise ValueError(key)
    if settings['sfx_mode'] not in ('none', 'keywords', 'every_cue'): raise ValueError('sfx_mode')
    if settings['background']['mode'] not in ('none', 'blur', 'image'): raise ValueError('background.mode')
    if settings['background']['mode'] == 'image' and not Path(settings['background']['path']).is_file():
        raise ValueError('background image missing')
    if settings['broll_mode'] not in ('semantic','count','ratio'):raise ValueError('broll_mode')
    if settings['broll_boundary_policy'] not in ('allow','prefer_cover','within_segment'):raise ValueError('broll_boundary_policy')
    if settings['music_short_policy'] not in ('reject','allow_silence'):raise ValueError('music_short_policy')
    if not 0<=settings['subtitle_border_width']<=100:raise ValueError('subtitle_border_width')
    if settings['music_source_start_s']<0 or min(settings['music_fade_in_s'],settings['music_fade_out_s'])<0:raise ValueError('music timing')
    if len(settings['broll_count'])!=2 or any(not isinstance(x,int) or x<0 for x in settings['broll_count']) or settings['broll_count'][0]>settings['broll_count'][1]:raise ValueError('broll_count')
    for key in ('main_speed', 'subtitle_scale', 'subtitle_font_px_per_unit', 'base_font_size'):
        if not isinstance(settings[key], (int, float)) or isinstance(settings[key], bool) or not math.isfinite(settings[key]) or settings[key] <= 0:
            raise ValueError(key+' must be finite and positive')
    if not isinstance(settings['main_volume'], (int, float)) or not math.isfinite(settings['main_volume']) or settings['main_volume'] < 0: raise ValueError('main_volume')
    if settings['subtitle_long_cue_policy'] not in ('error', 'approximate_equal'): raise ValueError('subtitle_long_cue_policy')
    if settings['missing_effect_policy'] not in ('error', 'plain'): raise ValueError('missing_effect_policy')
    if settings['caption_animation_mode'] not in ('auto', 'none', 'word'): raise ValueError('caption_animation_mode')
    if settings['inline_emphasis_mode'] not in ('flower', 'border'): raise ValueError('inline_emphasis_mode')
    if not isinstance(settings.get('music_enabled'), bool): raise ValueError('music_enabled must be true or false')
    for key in ('emphasis_probability', 'template_probability', 'subtitle_max_width_ratio'):
        if not 0 <= settings[key] <= 1: raise ValueError(key)
    for key in ('subtitle_color', 'keyword_color'):
        value = settings[key]
        if key == 'keyword_color' and value is None: continue
        if not isinstance(value, list) or len(value) != 3 or any(not isinstance(c,(int,float)) or not math.isfinite(c) or not 0<=c<=1 for c in value): raise ValueError(key+' requires normalized RGB')
    y = settings['subtitle_y']
    if not isinstance(settings.get('broll_volume',0), (int,float)) or not 0 <= settings.get('broll_volume',0) <= 1:
        raise ValueError('broll_volume must be between 0 and 1')
    caption_height = settings['base_font_size']*settings['subtitle_font_px_per_unit']*settings['width']/1080*settings['subtitle_scale']*1.4
    if not isinstance(y,(int,float)) or not math.isfinite(y) or abs(y)+caption_height/settings['height']>.99: raise ValueError('subtitle_y/font size places ordinary captions outside the safe canvas')
    return settings


def resolve_catalog_paths(catalog, base):
    """Resolve portable catalog resource paths once, including nested styles."""
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith('path') and isinstance(item,str) and item:
                    p = Path(item).expanduser()
                    value[key] = str((p if p.is_absolute() else Path(base)/p).resolve())
                else: walk(item)
        elif isinstance(value,list):
            for item in value: walk(item)
    result = copy.deepcopy(catalog)
    walk(result)
    return result


def inventory(job):
    if job.get('schema_version') != '1.0': raise ValueError('schema_version must be 1.0')
    count = job.get('count')
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0: raise ValueError('count must be positive integer')
    settings = job_settings(job)
    main = paths(job.get('main_videos'), VIDEO)
    if not main: raise ValueError('No main videos')
    media = paths(job.get('broll') or [a['path'] for a in job.get('broll_assets', []) if a.get('path')], VIDEO)
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
    for annotation in job.get('broll_assets', []):
        matching = [x for x in broll if annotation.get('asset_id') == x['id'] or (annotation.get('path') and Path(annotation['path']).resolve() == Path(x['path']).resolve())]
        if len(matching) != 1: raise ValueError('broll_assets must identify one inventoried source')
        tags = annotation.get('tags', [])
        if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags): raise ValueError('broll asset tags must be nonempty strings')
        matching[0]['tags'] = sorted(set(tags))
        if annotation.get('usable_ranges') is not None: matching[0]['usable_ranges'] = copy.deepcopy(annotation['usable_ranges'])
    if not rows: raise ValueError('No usable main video')
    distribution = job.get('distribution')
    if distribution is None:
        q, remainder = divmod(count, len(rows))
        distribution = {x['id']: q + (i < remainder) for i, x in enumerate(rows)}
    if set(distribution) != {x['id'] for x in rows}: raise ValueError('distribution IDs differ from inventory')
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in distribution.values()) or sum(distribution.values()) != count:
        raise ValueError('distribution must sum to count')
    overlays = []
    overlay_specs = resolve_overlays(job, settings)
    for x in overlay_specs:
        if x.get('kind') == 'image': overlays.append(probe(x['path']))
        elif x.get('kind') != 'text' or not x.get('text'): raise ValueError('overlay needs image path or text')
    ref = probe(job['reference_video']) if job.get('reference_video') else None
    catalog_path = Path(job['resource_catalog']) if job.get('resource_catalog') else ROOT/'presets/resources.json'
    catalog = resolve_catalog_paths(read(catalog_path), catalog_path.parent)
    if not job.get('resource_catalog'):
        discovered=resources(); preferred=read(job.get('resource_preferences') or ROOT/'presets/preferred-resources.json')
        def canonical(value): return ''.join(c.lower() for c in value if c.isalnum())
        for key in ('music','fonts'):
            wanted=[canonical(x) for x in (settings['preferred_fonts'] if key == 'fonts' else preferred[key])]
            catalog[key]=[x for x in discovered[key] if any(name in canonical(x['name']) or name in canonical(Path(x['path']).stem) for name in wanted)]
        if not catalog['fonts']:
            raise ValueError('默认得意黑/俪金黑未定位；请确认安装字体或指定替代 catalog，不从全字体池随机')
    for k in ('music', 'fonts', 'sfx'):
        for x in catalog.get(k, []):
            if not Path(x['path']).is_file(): errors.append({'path': x['path'], 'error': 'catalog resource missing'})
            elif k in ('music','sfx'): x['duration_ms']=probe(x['path'])['duration_ms']
    d = discover()
    draft_root = job.get('draft_root') or d['draft_root']
    warnings = []
    for k in ('caption_animations', 'text_templates', 'flowers'):
        if not catalog.get(k): warnings.append(f'{k}: empty verified catalog; disabled')
    if not catalog.get('music'): warnings.append('No BGM candidate; batch will have no BGM')
    if not catalog.get('fonts'): warnings.append('No specified font; backend default font (unverified display)')
    if not broll: warnings.append('No product footage; talking-head only')
    if settings.get('keyword_popup_probability'): warnings.append('Deprecated keyword_popup_probability ignored: duplicated spoken popups are no longer generated')
    if settings['broll_mode'] != 'semantic': warnings.append('Legacy time-random broll mode explicitly enabled; semantic style is disabled')
    if settings['missing_effect_policy'] == 'plain': warnings.append('Explicit plain fallback permitted; missing native components will be reported as degraded, not faithful reproduction')
    return {'assembly': job.get('assembly'), 'main': rows, 'broll': broll, 'count': count, 'distribution': distribution, 'overlays': overlays, 'overlay_specs': overlay_specs,
            'reference': ref, 'draft_root': draft_root, 'settings': settings, 'errors': errors, 'warnings': warnings,
            'estimated_timeline_s': sum(round(x['duration_ms']/settings['main_speed']) * distribution[x['id']] for x in rows) / 1000,
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
                    'status': 'raw', 'asr_version': '0.2.3', 'timestamp_basis': 'source_media', 'cues': cues})
        out.append({'source_id': x['id'], 'raw': str(raw), 'cached': False})
    return {'subtitles': out, 'next': 'Correct once: align words, semantic keywords, broll_opportunities, talking_head_preferred and title_candidates; do not plan each variant separately'}


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
    value = {'schema_version': '1.0', 'source_id': 'main_' + h[:16], 'source_hash': h, 'status': 'raw', 'timestamp_basis': 'source_media', 'cues': cues}
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
    for kind in ('caption_animations', 'text_templates', 'text_styles', 'text_animations'):
        for item in catalog.get(kind, []):
            if item.get('verified') is not True or not catalog.get('verified_jianying_version'):
                raise ValueError(kind+' requires local verification and verified_jianying_version')
            if kind == 'text_templates' and not Path(item.get('component_path', '')).is_file(): raise ValueError('text template component missing')
            if kind == 'caption_animations' and (not item.get('resource_id') or not Path(item.get('cache_path', '')).is_dir()): raise ValueError('native caption animation resource missing')


def validate_job(job):
    inv = inventory(job)
    if inv['errors']: raise ValueError(json.dumps(inv['errors'], ensure_ascii=False))
    verify_catalog(inv['catalog'])
    if inv['settings'].get('music_enabled', True) and not inv['catalog'].get('music'):
        raise ValueError('BGM requested but no confirmed usable music; select a local track or explicitly set music_enabled=false')
    inv['documents'] = {}
    inv['layout_job']={k:copy.deepcopy(job[k])for k in ('layout','titles','title','title_candidates','overlays')if k in job}
    for x in inv['main']:
        if not inv.get('assembly') and inv['distribution'][x['id']] == 0: continue
        p = job.get('subtitles', {}).get(x['id'])
        if not p: raise ValueError('Missing corrected subtitles for ' + x['id'])
        data = read(p)
        validate_subtitles(data, x)
        annotation_settings = {**inv['settings'], 'available_asset_tags': sorted({tag for asset in inv['broll'] for tag in asset.get('tags', [])})}
        annotation_errors = validate_annotations(data, x, annotation_settings)
        if annotation_errors: raise ValueError('Semantic annotation errors for '+x['id']+': '+str(annotation_errors))
        if inv['settings']['broll_mode'] == 'semantic' and inv['broll'] and 'broll_opportunities' not in data:
            raise ValueError('Mark broll_opportunities once for '+x['id']+' before semantic planning')
        font = inv['catalog'].get('fonts', [None])[0] if inv['catalog'].get('fonts') else None
        inv['documents'][x['id']] = prepare_document(data, inv['settings'], font)
        # Compile into memory before any media copying. The same native compiler
        # catches missing requested resources, bad template dependencies and
        # unsafe text geometry during validate/plan, not halfway through a batch.
        inv['warnings'].extend(inv['documents'][x['id']].get('warnings', []))
        if inv['settings'].get('caption_policy')=='semantic':continue
        for candidate_font in inv['catalog'].get('fonts', [None]):
            inv['warnings'].extend(validate_requested_resources(inv['documents'][x['id']]['cues'], inv['settings'],
                inv['catalog'], candidate_font, lambda path: str(Path(path).resolve())))
            preflight = append_captions({'tracks': [], 'materials': {}}, inv['documents'][x['id']]['cues'],
                inv['settings'], inv['catalog'], candidate_font, lambda path: str(Path(path).resolve()),
                random.Random('preflight:'+x['id']))
            inv['warnings'].extend(w for event in preflight for w in event.get('warnings', []))
    validate_assembly(inv)
    if inv.get('assembly'):
        for group in inv['assembly']['groups']:
            for section in group['sections']:
                for source_id in section['sources']:
                    data=read(job['subtitles'][source_id])
                    if data.get('script_id')!=group['script_id'] or data.get('section_id')!=section['section_id']:
                        raise ValueError('Corrected subtitle script_id/section_id differ from confirmed assembly: '+source_id)
    if inv['main'] and inv['settings'].get('caption_policy')=='semantic':
        inv['_preflight_native']=True
        checked=plan(inv)
        inv['preflight_summary']={'count':len(checked['items']),'all_variants_compiled':True}
    return inv


def choice(rng, catalog, key):
    a = catalog.get(key, []); return rng.choice(a) if a else None


def validate_assembly(inv):
    assembly = inv.get('assembly')
    if not assembly: return
    if assembly.get('confirmed') is not True: raise ValueError('Assembly mode/groups require user confirmation')
    known = {x['id'] for x in inv['main']}
    if assembly.get('mode') != 'splice': raise ValueError('assembly.mode must be splice; omit for single mode')
    groups = assembly.get('groups', [])
    if not groups: raise ValueError('No assembly script groups')
    if sum(g['count'] for g in groups) != inv['count']: raise ValueError('Script group counts must sum to total count')
    for g in groups:
        sections = g.get('sections', [])
        if not sections or not g.get('script_id'): raise ValueError('Missing script/sections')
        previous = 0
        for section in sections:
            ids = section.get('sources', [])
            if not ids or len(set(ids)) != len(ids) or not set(ids) <= known: raise ValueError('Invalid section source pool')
            if section['char_start'] != previous or section['char_end'] <= previous: raise ValueError('Section gap/overlap')
            previous = section['char_end']
        if previous != g['script_length']: raise ValueError('Sections must cover complete normalized script')
        pool = math.prod(len(x['sources']) for x in sections)
        if g['count'] > pool and not assembly.get('allow_duplicate_combinations', False):
            raise ValueError(f'Request {g["count"]} exceeds combination pool {pool}; confirm duplicates explicitly')


def normalize_script(value):
    return ''.join(c.lower() for c in value if c.isalnum())


def detect_mode(job, work):
    inv = inventory(job); scripts = job.get('scripts', [])
    if not scripts: raise ValueError('Provide AI-parsed scripts [{id,text}] for mode detection')
    mappings=[]
    for source in inv['main']:
        path=job.get('subtitles',{}).get(source['id']) or str(Path(work)/'subtitles'/(source['id']+'.raw.json'))
        data=read(path); text=normalize_script(''.join(c['text'] for c in data['cues']))
        candidates=[]
        for script in scripts:
            full=normalize_script(script['text'])
            matcher=difflib.SequenceMatcher(None,text,full,autojunk=False)
            blocks=[x for x in matcher.get_matching_blocks() if x.size]
            if not blocks: continue
            start=blocks[0].b; end=blocks[-1].b+blocks[-1].size
            span=full[start:end]
            similarity=difflib.SequenceMatcher(None,text,span,autojunk=False).ratio()
            matched=sum(x.size for x in blocks)/max(1,len(text))
            candidates.append({'script_id':script['id'],'char_start':start,'char_end':end,'script_length':len(full),
                               'confidence':round(min(similarity,matched),4),'coverage':round((end-start)/max(1,len(full)),4),
                               'differences':[] if text==span else ['ASR/script differ; inspect omissions/repeats before confirmation']})
        candidates.sort(key=lambda x:x['confidence'],reverse=True)
        best=candidates[0] if candidates else None
        ambiguous=not best or best['confidence']<.8 or (len(candidates)>1 and candidates[0]['confidence']-candidates[1]['confidence']<.05)
        mappings.append({'source_id':source['id'],'uncertain':ambiguous,'match':best,'alternatives':candidates[1:3]})
    suggestions=[]
    for script in scripts:
        matches=[x for x in mappings if x['match'] and x['match']['script_id']==script['id']]
        length=len(normalize_script(script['text']))
        sections={}
        for x in matches:
            m=x['match']; sections.setdefault((m['char_start'],m['char_end']),[]).append(x['source_id'])
        ordered=[{'section_id':f'section_{i+1}','char_start':a,'char_end':b,'sources':ids} for i,((a,b),ids) in enumerate(sorted(sections.items()))]
        complete=ordered and ordered[0]['char_start']==0 and ordered[-1]['char_end']==length and all(a['char_end']==b['char_start'] for a,b in zip(ordered,ordered[1:]))
        full=bool(matches) and all(x['match']['coverage']>=.95 for x in matches)
        mode='uncertain' if any(x['uncertain'] for x in matches) or not matches else 'single' if full else 'splice' if complete else 'uncertain'
        suggestions.append({'script_id':script['id'],'mode':mode,'script_length':length,'sections':ordered,
                            'combination_pool':math.prod(len(x['sources']) for x in ordered) if mode=='splice' else None})
    return {'requires_user_confirmation':True,'mappings':mappings,'scripts':suggestions,
            'note':'Proposal only. AI resolves script variants/repeated phrases and records confirmed section groups; no automatic production.'}


def plan(inv):
    from planner import plan as make_plan
    return make_plan(inv, sys.modules[__name__])


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
    from generator import build as build_draft
    return build_draft(job, inv, resolved, work, state, sys.modules[__name__])


def verify_draft(directory,resolved=None):
    directory=Path(directory)
    content=read(directory/'draft_info.json')
    if resolved is None: resolved=read(directory/'batch-resolved-plan.json')
    errors=[]; tracks=content.get('tracks',[]); materials=content.get('materials',{})
    errors.extend(validate_caption_content(content))
    videos={m['id']:m for m in materials.get('videos',[])}
    for group in ('videos','audios'):
        for m in materials.get(group,[]):
            p=m.get('path')
            if p and not Path(p).is_file(): errors.append('Missing material: '+p)
    for t in materials.get('texts',[]):
        if t.get('font_path') and not Path(t['font_path']).is_file(): errors.append('Missing font: '+t['font_path'])
    total=resolved['duration_ms']*1000
    main=next((t for t in tracks if t.get('name')=='Main'),None)
    expected=[(item,part) for item in resolved['items'] for part in item['segments']]
    if not main or len(main['segments'])!=len(expected): errors.append('Main count mismatch')
    else:
        cursor=0
        for seg,(item,part) in zip(main['segments'],expected):
            t=seg['target_timerange']
            if t.get('start',0)!=cursor or t['duration']!=part['duration_ms']*1000:errors.append('Main continuity/duration mismatch')
            if part.get('source_duration_ms'):
                source_range = seg.get('source_timerange', {})
                if abs(source_range.get('duration',0)/1000-part['source_duration_ms'])>2: errors.append('Main source duration mismatch')
                actual_speed = source_range.get('duration',0)/max(1,t['duration'])
                if abs(actual_speed-part.get('speed',1))>0.002: errors.append('Main speed/time mapping mismatch')
            cursor+=t['duration']
    material_ids={m['id'] for group in materials.values() if isinstance(group,list) for m in group if isinstance(m,dict) and 'id' in m}
    for track in tracks:
        prior_end = 0
        for seg in track.get('segments',[]):
            t=seg['target_timerange']; start=t.get('start',0); end=start+t['duration']
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
                if seg.get('volume')!=resolved['settings'].get('broll_volume',0): errors.append('Broll volume differs from explicit policy (default muted)')
                source=seg.get('source_timerange',{}); mat=videos.get(seg['material_id'],{})
                if source.get('start',0)+source.get('duration',0)>mat.get('duration',0)+40000: errors.append('Broll exceeds source')
                if resolved['settings'].get('broll_boundary_policy') == 'within_segment':
                    if not any(p['start_ms']*1000<=local and end-item['start_ms']*1000 <= (p['start_ms']+p['duration_ms'])*1000 for p in item['segments']): errors.append('Broll crosses speaker boundary')
                if resolved['settings'].get('broll_mode') == 'semantic':
                    planned = next((p for p in item.get('broll',[]) if p['start_ms']*1000==local and p['duration_ms']*1000==t['duration']),None)
                    if not planned or not planned.get('opportunity_id'): errors.append('Broll not backed by semantic opportunity')
    for material in materials.get('text_templates', []):
        for child in material.get('text_info_resources', []):
            if child.get('text_material_id') not in material_ids: errors.append('Template child text missing')
    all_ids=[m['id'] for group in materials.values() if isinstance(group,list) for m in group if isinstance(m,dict) and 'id' in m]
    if len(all_ids)!=len(set(all_ids)): errors.append('Duplicate material IDs')
    return {'ok':not errors,'errors':errors,'tracks':[(t.get('name'),len(t.get('segments',[]))) for t in tracks],
            'timeline_s':total/1e6,'count':len(resolved['items'])}


def run(job, work, confirmed, serve=False, port=0):
    if not confirmed: raise ValueError('User batch confirmation required before --confirmed')
    work=Path(work).resolve(); work.mkdir(parents=True,exist_ok=True)
    inv=validate_job(job); resolved=plan(inv); write(work/'inventory.json',inv); write(work/'planned.json',resolved)
    for source_id, doc in inv.get('documents', {}).items(): write(work/'prepared-subtitles'/(source_id+'.prepared.json'), doc)
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
    a=sub.add_parser('detect-mode'); a.add_argument('--job',required=True); a.add_argument('--work',required=True); a.add_argument('--out',required=True)
    a=sub.add_parser('transcribe'); a.add_argument('--job',required=True); a.add_argument('--work',required=True)
    a=sub.add_parser('import-srt'); a.add_argument('--video',required=True); a.add_argument('--srt',required=True); a.add_argument('--out',required=True)
    a=sub.add_parser('frames'); a.add_argument('--video',required=True); a.add_argument('--out',required=True); a.add_argument('--count',type=int,default=8)
    a=sub.add_parser('run'); a.add_argument('--job',required=True); a.add_argument('--work',required=True); a.add_argument('--confirmed',action='store_true'); a.add_argument('--serve',action='store_true'); a.add_argument('--port',type=int,default=0)
    a=sub.add_parser('verify'); a.add_argument('--draft',required=True)
    args=p.parse_args()
    if args.cmd=='doctor': result=doctor()
    elif args.cmd=='resources': result=resources()
    elif args.cmd=='setup': result=setup(args.approved)
    elif args.cmd=='inspect': result=inventory(load_job(args.job))
    elif args.cmd=='validate': result=validate_job(load_job(args.job))
    elif args.cmd=='plan': result=plan(validate_job(load_job(args.job)))
    elif args.cmd=='detect-mode': result=detect_mode(load_job(args.job),args.work)
    elif args.cmd=='transcribe': result=transcribe(load_job(args.job),args.work)
    elif args.cmd=='import-srt': result=import_srt(args.video,args.srt,args.out)
    elif args.cmd=='frames': result=frames(args.video,args.out,args.count)
    elif args.cmd=='run': result=run(load_job(args.job),args.work,args.confirmed,args.serve,args.port)
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
