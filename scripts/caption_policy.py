"""Plan subtitle variants from reviewed semantics, then compile native objects.

One semantic pass supplies roles and real source word times. Randomness is
restricted by profile, role, phrase boundaries, spacing and resource roles.
"""
from __future__ import annotations
import copy
import json
import math
import re
from pathlib import Path

from caption_engine import (CaptionError, _style, _font, _effect_style, _native_words,
    _chunks, _layout_estimate, clone_component, _fit_caption_template, validate_caption_content)
from native_styles import (candidates, choose_font, slice_styles, measure_styles,
                           append_rich_text, render_recipe, text_animation, recipe_styles)

ROOT=Path(__file__).resolve().parents[1]
ALIASES={'core_object':'object','product':'object','product_name':'object','audience':'audience',
 'audience_or_threshold':'audience','pain_or_opportunity':'opportunity','pain_point':'opportunity',
 'price_or_low_cost':'price','price_offer':'price','low_cost':'price','ease_or_low_barrier':'benefit',
 'usage_benefit':'benefit','use_benefit':'benefit','objection_or_evidence':'reassurance',
 'call_to_action':'action','action_or_urgency':'action','urgency':'action'}


def policy(settings):
    data=json.loads((ROOT/'presets/general-conversion/caption-policy.json').read_text(encoding='utf-8'))
    data.update(copy.deepcopy(settings.get('caption_policy_overrides',{})))
    return data


def role(keyword):
    value=keyword.get('role','benefit')
    return ALIASES.get(value,value if value in ('object','audience','opportunity','price','benefit','reassurance','action') else 'benefit')


def profile_for(cues,settings):
    requested={c.get('caption_profile') for c in cues if c.get('caption_profile')}
    if len(requested)>1:raise CaptionError('Conflicting caption profiles within one composition')
    value=next(iter(requested),settings.get('caption_profile','auto'))
    if value=='auto':value='conversion' if any(role(k)=='price' for c in cues for k in c.get('keywords',[])) else 'explainer'
    if value not in ('explainer','conversion'):raise CaptionError('caption_profile must be auto, explainer or conversion')
    return value


def weighted(rng,choices):
    choices={k:float(v) for k,v in choices.items() if float(v)>0}
    if not choices:return 'none'
    return rng.choices(list(choices),weights=list(choices.values()),k=1)[0]


def slice_cue(cue,a,b,start=None,end=None):
    """Slice only complete words and complete keyword spans."""
    text=cue['text'];words=cue.get('words',[])
    if isinstance(words,dict):raise CaptionError('Prepare native word arrays before slicing')
    selected=[copy.deepcopy(w) for w in words if a<=w['char_start'] and w['char_end']<=b]
    if a or b<len(text):
        if not selected or selected[0]['char_start']!=a or selected[-1]['char_end']!=b:
            raise CaptionError('Subtitle split needs complete, aligned word boundaries')
    out=copy.deepcopy(cue);out['text']=text[a:b];out['id']=cue['id']+f'@{a}:{b}'
    out['parent_cue_id']=cue.get('parent_cue_id',cue['id'])
    out['start_ms']=cue['start_ms'] if start is None and a==0 else (selected[0]['start_ms'] if start is None else start)
    following=next((w for w in words if w['char_start']>=b),None)
    out['end_ms']=end if end is not None else following['start_ms'] if following else cue['end_ms']
    for w in selected:w['char_start']-=a;w['char_end']-=a
    out['words']=selected;out['words_timebase']='absolute_ms';out['keywords']=[]
    for k in cue.get('keywords',[]):
        ka,kb=k['char_start'],k['char_end']
        if ka<b and kb>a and not a<=ka<kb<=b:raise CaptionError('Cannot split a protected keyword phrase')
        if a<=ka<kb<=b:
            kk=copy.deepcopy(k);kk['char_start']-=a;kk['char_end']-=a
            kk['start_ms']=max(out['start_ms'],kk['start_ms']);kk['end_ms']=min(out['end_ms'],kk['end_ms'])
            out['keywords'].append(kk)
    if out['end_ms']<=out['start_ms']:raise CaptionError('Subtitle split produced an empty time range')
    return out


def _select_emphasis(cues,settings,rng,p):
    if profile_for(cues,settings)=='explainer' and not settings.get('explainer_emphasis',False):return {}
    first=min(c['start_ms'] for c in cues);last=max(c['end_ms'] for c in cues)
    bins=[[],[],[]];chosen={};all_candidates=[]
    for ci,cue in enumerate(cues):
        if cue.get('layout_role')=='opening_hook_card':continue
        for ki,kw in enumerate(cue.get('keywords',[])):
            slot=min(2,int((kw['start_ms']-first)/max(1,last-first)*3))
            item=(ci,ki,kw,rng.random());bins[slot].append(item);all_candidates.append(item)
    gap=float(settings.get('emphasis_min_gap_s',p['min_gap_s']))*1000
    repeat=float(settings.get('emphasis_repeat_gap_s',p['repeat_gap_s']))*1000
    accepted=[]
    def accept(item):
        ci,ki,k,_=item
        if ci in chosen:return False
        if any(abs(k['start_ms']-a[2]['start_ms'])<gap or
               k['text']==a[2]['text'] and abs(k['start_ms']-a[2]['start_ms'])<repeat for a in accepted):return False
        chosen[ci]=ki;accepted.append(item);return True
    # At least one compatible opportunity in each populated third, subject to spacing.
    for group in bins:
        for item in sorted(group,key=lambda x:(x[2].get('priority')!='high',x[3])):
            if accept(item):break
    probability=float(settings.get('emphasis_probability',.65))
    if probability<=0:return {}
    for item in sorted(all_candidates,key=lambda x:x[3]):
        if item[3]<probability:accept(item)
    return chosen


def _animations(cue,profile,p,settings,catalog,rng,mode,semantic_role=None):
    if settings.get('caption_animation_mode')=='none':return []
    words,reliable=_native_words(cue)
    if not reliable:
        if settings.get('caption_animation_mode')=='word':raise CaptionError('Reliable word timing required')
        return []
    scheme=p['profiles'][profile]['animations']
    name=settings.get('_caption_scheme',{}).get('animation') or weighted(rng,scheme)
    if mode in ('flower','whole_flower'):
        direct=candidates(catalog,'text_animations','caption',semantic_role)
        if direct and rng.random()<p['direct_animation_probability']:return [rng.choice(direct)]
    if name=='none':return []
    pool=[a for a in candidates(catalog,'caption_animations','caption') if a.get('name')==name]
    if not pool:
        if settings.get('missing_effect_policy')=='plain':return []
        raise CaptionError('Missing verified caption animation: '+name)
    return [rng.choice(pool)]


def _validate_catalog(catalog,settings,cues,p,font):
    _font(font,lambda p:p)
    profile=profile_for(cues,settings)
    # Check all enabled entries before drawing; broken candidates cannot hide behind the seed.
    for entry in catalog.get('flowers',[]):
        if entry.get('binding_only'):continue
        if entry.get('verified') is True:_effect_style(entry,_style('预检',settings,_font(font,lambda p:p)),lambda p:p)
    for entry in catalog.get('text_styles',[]):
        if entry.get('verified') is True:
            recipe_styles('样式预检',entry,lambda p:p)
            for a in entry.get('animations',[]):text_animation(a,1000000,lambda p:p)
    for entry in catalog.get('text_animations',[]):
        if entry.get('binding_only'):continue
        if entry.get('verified') is True:text_animation(entry,1000000,lambda p:p)
    from caption_engine import _native_animation
    for entry in catalog.get('caption_animations',[]):
        if entry.get('verified') is True:_native_animation(entry,1000000,lambda p:p)
    from component_tools import load_component
    for entry in catalog.get('text_templates',[]):
        if entry.get('verified') is True:
            comp=load_component(entry['component_path'])
            # Validate dependencies and sanitized text slots with a real synthetic word time.
            preview={'text':'预检','start_ms':0,'end_ms':1000,'words_timebase':'absolute_ms',
                     'words':[{'text':'预检','start_ms':0,'end_ms':1000}]}
            clone_component(comp,'预检',0,1000,lambda p:p,cue=preview)
    if settings.get('missing_effect_policy')=='plain':return
    if settings.get('caption_animation_mode')!='none' and any(_native_words(c)[1] for c in cues):
        for name,weight in p['profiles'][profile]['animations'].items():
            if name!='none' and weight>0 and not any(a.get('name')==name for a in candidates(catalog,'caption_animations','caption')):
                raise CaptionError('Missing verified caption animation: '+name)
    if profile=='conversion' and settings.get('emphasis_probability',.65)>0:
        for kw in [k for c in cues for k in c.get('keywords',[])]:
            modes={kw['presentation']:1} if kw.get('presentation') else p['roles'][role(kw)]
            for mode,weight in modes.items():
                if weight<=0:continue
                if mode in ('template','badge') and settings.get('template_probability',.35)<=0 and not kw.get('presentation'):continue
                key='flowers' if mode in ('flower','whole_flower') else 'text_templates' if mode in ('template','badge') else None
                target='price_badge' if mode=='badge' else 'caption'
                if key and not candidates(catalog,key,target,role(kw)):
                    raise CaptionError(f'Missing compatible {key} for {target}/{role(kw)}')
    if any(c.get('layout_role')=='opening_hook_card' for c in cues) and not candidates(catalog,'text_styles','opening_hook'):
        raise CaptionError('Opening cards require a verified native opening_hook text recipe')


def _base_styles(cue,settings,catalog,font,rng,mode,kw,profile,p):
    effective=copy.deepcopy(settings)
    color=settings.get('_caption_scheme',{}).get('color') or weighted(rng,p['profiles'][profile]['base_colors'])
    if profile=='conversion' and color=='gold':
        effective['subtitle_color']=[1,.870588243,0]
    base=_style(cue['text'],effective,_font(font,lambda p:p))
    if mode not in ('flower','border','whole_flower') or not kw:return [base],effective
    a,b=(0,len(cue['text'])) if mode=='whole_flower' else (kw['char_start'],kw['char_end'])
    styled=copy.deepcopy(base)
    if mode in ('flower','whole_flower'):
        pool=candidates(catalog,'flowers','caption',role(kw))
        if pool:styled,_=_effect_style(rng.choice(pool),base,lambda p:p)
        elif settings.get('missing_effect_policy')!='plain':raise CaptionError('Missing compatible native flower')
        else:mode='border'
    if mode=='border':
        styled['fill']['content']={'render_type':'solid','solid':{'alpha':1.,'color':[1,1,1]}}
        styled['strokes'][0]['content']['solid']['color']=[1,.0941176489,.2156862766]
        if settings.get('keyword_color') is not None:styled['fill']['content']['solid']['color']=settings['keyword_color']
    out=[]
    if a:plain=copy.deepcopy(base);plain['range']=[0,a];out.append(plain)
    styled['range']=[a,b];out.append(styled)
    if b<len(cue['text']):plain=copy.deepcopy(base);plain['range']=[b,len(cue['text'])];out.append(plain)
    effective['_effective_caption_mode']=mode
    return out,effective


def _render_plain(content,cue,styles,settings,stage,animations,offset):
    scale=settings.get('subtitle_scale',1.15);limit=settings.get('width',1080)*settings.get('subtitle_max_width_ratio',.82)
    def fits(a,b):return measure_styles(cue['text'][a:b],slice_styles(styles,a,b),settings,scale)['width_px']<=limit
    if fits(0,len(cue['text'])):chunks=[(0,len(cue['text']))]
    else:
        if not cue.get('words'):raise CaptionError('Overlong selected style requires aligned word times; cannot safely split')
        protected=[(k['char_start'],k['char_end']) for k in cue.get('keywords',[])]
        protected.extend((m.start(),m.end()) for m in re.finditer(r'[A-Za-z0-9]+(?:[.\-—～/%][A-Za-z0-9]+)*',cue['text']))
        chunks=_chunks(cue['text'],[w['char_end'] for w in cue['words']],[],settings,None,protected,measure=fits)
    events=[]
    for a,b in chunks:
        part=slice_cue(cue,a,b);ss=slice_styles(styles,a,b)
        from caption_engine import _stage_nested
        ss=_stage_nested(ss,stage)
        seg=append_rich_text(content,part['text'],ss,part['start_ms']+offset,part['end_ms']+offset,settings,stage,cue=part,animations=animations)
        events.append({'cue_id':part['id'],'parent_cue_id':cue.get('parent_cue_id',cue['id']),
                       'text':part['text'],'segment_id':seg['id'],'start_ms':part['start_ms']+offset,
                       'end_ms':part['end_ms']+offset,'mode':cue.get('_mode','base'),'keyword_events':[],
                       'fit':measure_styles(part['text'],ss,settings,scale),'warnings':[]})
    return events


def _template(content,cue,settings,catalog,stage,rng,offset,kw,badge=False):
    from component_tools import load_component
    target='price_badge' if badge else 'caption'
    pool=candidates(catalog,'text_templates',target,role(kw));rng.shuffle(pool);failures=[]
    for entry in pool:
        if entry.get('max_chars') is not None and len(cue['text'])>int(entry['max_chars']):continue
        try:
            comp=load_component(entry['component_path'])
            cfg={**settings,'template_preserve_position':True,'subtitle_max_width_ratio':settings.get('template_max_width_ratio',.94)}
            seg,added=clone_component(comp,cue['text'],cue['start_ms']+offset,cue['end_ms']+offset,stage,cfg,cue=cue)
            fit=_fit_caption_template(seg,added,cue['text'],cfg)
            if badge:
                pos=seg.get('clip',{}).get('transform',{})
                if not .15<=abs(float(pos.get('x',0)))<=.75 or not -.20<=float(pos.get('y',0))<=.35:
                    raise CaptionError('Price badge must be spatially separate from spoken captions')
            else:
                pos=seg.get('clip',{}).get('transform',{})
                if not -.48<=float(pos.get('y',0))<=-.20 or abs(float(pos.get('x',0)))>.15:
                    raise CaptionError('Caption-role template has incompatible placement')
        except CaptionError as e:
            failures.append(str(e));continue
        for kind,entries in added.items():content.setdefault('materials',{}).setdefault(kind,[]).extend(entries)
        name='PriceBadges' if badge else 'Subtitles'
        from caption_engine import _id
        track=next((t for t in content.setdefault('tracks',[])if t.get('name')==name),None)
        if track is None:track={'id':_id(),'type':'text','name':name,'segments':[]};content['tracks'].append(track)
        if badge:seg.update(track_render_index=45,render_index=45)
        track['segments'].append(seg)
        return {'cue_id':cue['id'],'text':cue['text'],'segment_id':seg['id'],'start_ms':cue['start_ms']+offset,
                'end_ms':cue['end_ms']+offset,'mode':'price_badge' if badge else 'template_replace',
                'template':entry.get('name'),'fit':fit,'warnings':[],'keyword_events':[]}
    raise CaptionError('No readable compatible '+target+' template: '+'; '.join(failures[:3]))


def append_semantic_captions(content,cues,settings,catalog,font,stage,rng,offset=0):
    if not cues:return []
    p=policy(settings);profile=profile_for(cues,settings);_validate_catalog(catalog,settings,cues,p,font)
    schemes=p['profiles'][profile].get('schemes',[])
    if schemes:
        selected=rng.choices(schemes,weights=[x['weight'] for x in schemes],k=1)[0]
        settings={**settings,'_caption_scheme':selected}
    selection=_select_emphasis(cues,settings,rng,p);events=[]
    for ci,original in enumerate(cues):
        cue=copy.deepcopy(original);kw=cue.get('keywords',[])[selection[ci]] if ci in selection else None
        warnings=[]
        if settings.get('caption_animation_mode')!='none' and not _native_words(cue)[1]:
            warnings.append('Native word animation skipped: reliable current word timing is unavailable')
        if cue.get('layout_role')=='opening_hook_card':
            pool=[r for r in candidates(catalog,'text_styles','opening_hook') if r.get('line_count',len(cue['text'].split('\n')))==len(cue['text'].split('\n'))];rng.shuffle(pool)
            if not pool:raise CaptionError('Opening hook requires its own verified native multi-line recipe')
            failures=[]
            for recipe in pool:
                try:
                    event=render_recipe(content,cue['text'],recipe,cue['start_ms']+offset,cue['end_ms']+offset,settings,stage,cue=cue,role='opening_hook')
                    break
                except CaptionError as e:failures.append(str(e))
            else:raise CaptionError('No readable opening recipe: '+'; '.join(failures[:3]))
            event.update(cue_id=cue['id'],mode='opening_hook',end_ms=cue['end_ms']+offset,keyword_events=[],warnings=[]);events.append(event);continue
        mode='base'
        if kw:
            choices=dict(p['roles'][role(kw)])
            if settings.get('template_probability',.35)<=0:
                choices.pop('template',None);choices.pop('badge',None)
            mode=kw.get('presentation') or weighted(rng,choices)
            if mode not in ('base','flower','whole_flower','border','template','badge'):raise CaptionError('Invalid keyword presentation')
        if mode in ('template','badge'):
            a,b=kw['char_start'],kw['char_end'];words=cue.get('words',[])
            aligned=bool(words) and any(w['char_start']==a for w in words) and any(w['char_end']==b for w in words)
            if (a or b<len(cue['text'])) and not aligned:
                warnings.append('Keyword replacement kept inline: exact phrase boundaries lack reliable word times');mode='border'
            else:
                isolated=slice_cue(cue,a,b)
                try:te=_template(content,isolated,settings,catalog,stage,rng,offset,kw,badge=mode=='badge')
                except CaptionError:
                    if settings.get('missing_effect_policy')!='plain':raise
                    warnings.append('Explicit plain fallback: native template unavailable');mode='border'
                else:
                    if mode=='badge':parts=[cue]
                    else:parts=([slice_cue(cue,0,a)] if a else [])+([slice_cue(cue,b,len(cue['text']))] if b<len(cue['text']) else [])
                    for part in parts:
                        ss,ef=_base_styles(part,settings,catalog,font,rng,'base',None,profile,p)
                        aa=_animations(part,profile,p,settings,catalog,rng,'base')
                        events.extend(_render_plain(content,part,ss,ef,stage,aa,offset))
                    te['keyword_events']=[{'text':kw['text'],'start_ms':te['start_ms'],'end_ms':te['end_ms'],'kind':te['mode']}]
                    events.append(te);continue
        cue['_mode']=mode
        styles,effective=_base_styles(cue,settings,catalog,font,rng,mode,kw,profile,p)
        actual=effective.get('_effective_caption_mode',mode)
        if actual!=mode:warnings.append('Explicit plain fallback: native flower unavailable; in-place border used')
        cue['_mode']=actual
        animations=_animations(cue,profile,p,settings,catalog,rng,mode,role(kw) if kw else None)
        generated=_render_plain(content,cue,styles,effective,stage,animations,offset)
        for e in generated:
            e['warnings'].extend(warnings);e['profile']=profile
            e['base_scheme']=settings.get('_caption_scheme')
            if kw and actual!='base' and e['start_ms']<=kw['start_ms']+offset<e['end_ms']:
                e['keyword_events']=[{'text':kw['text'],'start_ms':kw['start_ms']+offset,'end_ms':kw['end_ms']+offset,'kind':actual}]
        events.extend(generated)
    for track in content.get('tracks',[]):track.get('segments',[]).sort(key=lambda s:s['target_timerange']['start'])
    errors=validate_caption_content(content)
    if errors:raise CaptionError('; '.join(errors[:8]))
    return sorted(events,key=lambda e:e['start_ms'])
