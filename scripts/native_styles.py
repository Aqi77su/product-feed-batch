"""Native rich-text recipes: retain fonts, styles, geometry and text animations.

Recipes contain no historical copy. Roles and selectors bind new text to whole
styles, rather than independently randomizing fonts, colors and animations.
"""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path

from caption_engine import (CaptionError, _font, _id, _segment, _stage_nested,
                            _style, _u16, _portable, _native_words, _native_animation)


def candidates(catalog, key, role, semantic_role=None):
    result=[]
    for entry in catalog.get(key, []):
        if entry.get('verified') is not True or entry.get('available', True) is False: continue
        roles=entry.get('roles', [entry.get('role', 'caption')])
        if key=='flowers' and roles==['inline']:roles=['caption']
        if role not in roles: continue
        semantics=entry.get('semantic_roles', [])
        if semantics and semantic_role not in semantics: continue
        result.append(entry)
    return result


def choose_font(catalog, names, fallback=None, strict=True):
    for name in names:
        for font in catalog.get('fonts', []):
            if font.get('available', True) is False or font.get('verified') is False: continue
            if name in (font.get('name','')+' '+Path(font.get('path','')).stem):
                _font(font,lambda p:p)
                return font
    if fallback and not strict: return fallback
    raise CaptionError('Required style font is missing: '+', '.join(names))


def slice_styles(styles, a, b):
    output=[]
    for style in styles:
        left,right=style['range'];left=max(a,left);right=min(b,right)
        if left<right:
            part=copy.deepcopy(style);part['range']=[left-a,right-a];output.append(part)
    return output


def measure_styles(text, styles, settings, scale=1, vertical=False):
    """Measure selected fonts after rich styles are applied, with effect padding.

    This is a conservative metric check, not native animation-frame validation.
    """
    width=float(settings.get('width',1080));factor=float(settings.get('subtitle_font_px_per_unit',4))*width/1080*scale
    lines=text.split('\n');cursor=0;line_widths=[];line_heights=[];methods=set()
    for line in lines:
        w=0;h=0
        for style in styles:
            a,b=style['range'];chunk=text[max(a,cursor):min(b,cursor+len(line))]
            if not chunk: continue
            px=float(style['size'])*factor
            try:
                from PIL import ImageFont
                face=ImageFont.truetype(style['font']['path'], max(1,round(px)))
                cw=float(face.getlength(chunk));methods.add('selected_font_metrics')
            except (OSError,ImportError,KeyError,ValueError):
                import unicodedata
                cw=sum(1 if unicodedata.east_asian_width(c) in 'WF' else .56 for c in chunk)*px
                methods.add('selected_style_estimate')
            sw=max([float(x.get('width',0)) for x in style.get('strokes',[])]+[0])*px
            padding=float(settings.get('subtitle_effect_padding_px',12))*width/1080
            # Textures, strokes and animation overshoot cannot be measured by a font alone.
            ext=max(sw,padding if style.get('effectStyle') else 0)
            if vertical: w=max(w,px+2*ext);h+=len(chunk)*px*1.18
            else: w+=cw+2*ext;h=max(h,px*1.25+2*ext)
        line_widths.append(w);line_heights.append(h);cursor+=len(line)+1
    padding=float(settings.get('subtitle_effect_padding_px',12))*width/1080
    return {'width_px':round((sum(line_widths) if vertical else max(line_widths,default=0))+padding*2,2),
            'height_px':round((max(line_heights,default=0) if vertical else sum(line_heights))+padding*2,2),
            'method':'+'.join(sorted(methods)), 'native_preview_required':True}


def text_animation(entry, duration_us, stage):
    node=copy.deepcopy(entry.get('animation',entry))
    kind=node.get('type')
    if kind not in ('in','out','loop'): raise CaptionError('Text animation must be in, out or loop')
    path=entry.get('cache_path',node.get('path'));rid=str(entry.get('resource_id',node.get('resource_id','')))
    if not rid or not path: raise CaptionError('Text animation requires a local resource and ID')
    length=min(duration_us,int(node.get('duration') or 500000))
    node.update(id=rid,resource_id=rid,third_resource_id=rid,path=_portable(path,stage),
                duration=length,start=duration_us-length if kind=='out' else 0)
    return {'id':_id(),'type':'sticker_animation','animations':[node]}


def recipe_styles(text, recipe, stage, accent_ranges=()):
    pool=recipe.get('styles',[])
    if not pool: raise CaptionError('Native text recipe has no exported styles')
    prepared=[]
    for s in pool:
        ss=_stage_nested(copy.deepcopy(s),stage)
        if not ss.get('font',{}).get('path'): raise CaptionError('Native recipe has no font path')
        if float(ss.get('size',0))<=0: raise CaptionError('Native recipe has invalid size')
        prepared.append(ss)
    base=int(recipe.get('base_style',0));accent=int(recipe.get('accent_style',base))
    if not 0<=base<len(prepared) or not 0<=accent<len(prepared):raise CaptionError('Invalid style selector')
    selectors=[base]*len(text)
    if recipe.get('line_styles'):
        pos=0;choices=recipe['line_styles']
        for n,line in enumerate(text.split('\n')):
            selected=int(choices[min(n,len(choices)-1)])
            if not 0<=selected<len(prepared):raise CaptionError('Invalid line style selector')
            selectors[pos:pos+len(line)]=[selected]*len(line);pos+=len(line)+1
    for a,b in accent_ranges:
        if not 0<=a<b<=len(text):raise CaptionError('Invalid layout accent range')
        selectors[a:b]=[accent]*(b-a)
    out=[];start=0
    for i in range(1,len(text)+1):
        if i==len(text) or selectors[i]!=selectors[start]:
            s=copy.deepcopy(prepared[selectors[start]]);s['range']=[start,i];out.append(s);start=i
    return out


def append_rich_text(content, text, styles, start_ms, end_ms, settings, stage,
                     *, track_name='Subtitles', cue=None, animations=(), vertical=False, role='caption'):
    _u16(text)
    covered=[]
    for s in styles: covered.extend(range(*s['range']))
    if covered!=list(range(len(text))): raise CaptionError('Rich text styles must cover each character exactly once')
    mid=_id();seg=_segment(mid,start_ms,end_ms,settings)
    words,reliable=_native_words(cue) if cue else ({},False)
    mat={'id':mid,'type':'subtitle' if cue else 'text','content':json.dumps({'text':text,'styles':styles},ensure_ascii=False),
         'font_path':styles[0]['font']['path'],'font_resource_id':styles[0]['font'].get('id',''),'font_size':styles[0]['size'],
         'alignment':3 if vertical else 1,'typesetting':1 if vertical else 0,
         'line_spacing':settings.get('text_line_spacing',.27 if '\n'in text else .02),'letter_spacing':0,
         'line_feed':0,'force_apply_line_max_width':False,'check_flag':15,'global_alpha':1.0,
         'words':words,'current_words':{},'subtitle_keywords':{}}
    mats=content.setdefault('materials',{});mats.setdefault('texts',[]).append(mat)
    for entry in animations:
        typ=entry.get('animation',entry).get('type','caption')
        if typ=='caption':
            if not reliable:raise CaptionError('Native caption animation requires reliable current words')
            anim=_native_animation(entry,seg['target_timerange']['duration'],stage)
        else:anim=text_animation(entry,seg['target_timerange']['duration'],stage)
        mats.setdefault('material_animations',[]).append(anim);seg['extra_material_refs'].append(anim['id'])
    for s in styles:
        fx=s.get('effectStyle')
        if fx:
            effect={'id':_id(),'type':'text_effect','effect_id':fx['id'],'resource_id':fx['id'],'path':fx['path'],'value':1.0,'source_platform':1}
            mats.setdefault('effects',[]).append(effect);seg['extra_material_refs'].append(effect['id'])
    tracks=content.setdefault('tracks',[])
    track=next((t for t in tracks if t.get('name')==track_name),None)
    if track is None:
        track={'id':_id(),'type':'text','name':track_name,'attribute':0,'flag':0,'segments':[]};tracks.append(track)
    if track_name.startswith('Layout:'):seg.update(track_render_index=tracks.index(track),render_index=15000+tracks.index(track))
    elif track_name!='Subtitles':seg.update(track_render_index=40+tracks.index(track),render_index=40+tracks.index(track))
    track['segments'].append(seg)
    return seg


def render_recipe(content, text, recipe, start_ms, end_ms, settings, stage,
                  *, config=None, accent_ranges=(), track_name='Subtitles', cue=None, role='caption'):
    config=config or {};clip=recipe.get('clip',{});sc=clip.get('scale',{});pos=clip.get('transform',{})
    scale=float(config.get('scale',sc.get('x',1)));x=float(config.get('x',pos.get('x',0)));y=float(config.get('y',pos.get('y',0)))
    vertical=bool(config.get('vertical',recipe.get('vertical',False)))
    styles=recipe_styles(text,recipe,stage,accent_ranges)
    if config.get('size') is not None:
        for s in styles:s['size']=float(config['size'])
    effective={**settings,'subtitle_scale':scale,'subtitle_x':x,'subtitle_y':y,
               'text_line_spacing':recipe.get('line_spacing',.27 if '\n'in text else .02)}
    measure=measure_styles(text,styles,effective,scale,vertical)
    width=float(settings.get('width',1080));height=float(settings.get('height',1920))
    limit_w=width*min(float(config.get('width',recipe.get('safe_width',.94))),max(0,.99-abs(x)))
    limit_h=height*min(float(config.get('height',recipe.get('safe_height',.9))),max(0,.99-abs(y)))
    ratio=min(1,limit_w/max(1,measure['width_px']),limit_h/max(1,measure['height_px']))
    minimum=float(config.get('minimum_scale_ratio',recipe.get('minimum_scale_ratio',.85)))
    if ratio<minimum:raise CaptionError(f'{role}: selected native style exceeds readable bounds; shorten text or choose another compatible style')
    effective['subtitle_scale']=scale*ratio
    seg=append_rich_text(content,text,styles,start_ms,end_ms,effective,stage,track_name=track_name,cue=cue,
                         animations=recipe.get('animations',[]),vertical=vertical,role=role)
    return {'component':role,'text':text,'segment_id':seg['id'],'start_ms':start_ms,'duration_ms':end_ms-start_ms,
            'transform':seg['clip'],'style_name':recipe.get('name'),'fit':{**measure,'scale_adjustment':ratio,
            'width_px':round(measure['width_px']*ratio,2),'height_px':round(measure['height_px']*ratio,2),
            'box_width_px':limit_w,'box_height_px':limit_h}}
