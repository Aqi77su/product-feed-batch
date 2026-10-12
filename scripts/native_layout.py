"""Persistent header and side text compiled from complete native style recipes."""
from __future__ import annotations
import copy
import re
from caption_engine import CaptionError,clone_component,_id,_fit_caption_template
from native_styles import candidates,render_recipe
from layout_engine import _layout,_headline,_copy_value,_component,_append_text,_append_panel

LABELS={'headline':'Headline','subheadline':'Subheadline','side_benefit':'SideBenefit'}


def _side(layout,item,rng):
    explicit=_component(_copy_value(layout.get('side_benefit'),item))
    if explicit:return explicit
    pool=item.get('side_candidates',[]) or layout.get('side_candidates',[])
    choices=[_component(x) for x in pool];choices=[x for x in choices if x]
    if choices:return copy.deepcopy(rng.choice(choices))
    # Copy only reviewed words; never fabricate a price/benefit from an old preset.
    from caption_policy import role
    words=[k for c in item.get('captions',[]) for k in c.get('keywords',[]) if role(k) in ('price','benefit','action') and len(k['text'])<=10]
    words.sort(key=lambda k:role(k)!='price')
    return {'text':words[0]['text'],'accent_ranges':[[0,len(words[0]['text'])]]} if words else None


def layout_inputs(item,job,settings,rng):
    layout=_layout(job,settings)
    if layout.get('enabled',True) is False:return layout,[]
    headline=_headline(layout,job,item,rng) if layout.get('headline_enabled',True) else None
    side=_side(layout,item,rng) if layout.get('side_enabled',True) else None
    if layout.get('headline_enabled',True) and not headline:
        raise CaptionError('Default headline is enabled: supply reviewed headline/title_candidates, or explicitly disable headline_enabled')
    if layout.get('side_enabled',True) and not side:
        raise CaptionError('Default side text is enabled: supply side_benefit/side_candidates or reviewed benefit/price keywords, or explicitly disable side_enabled')
    components=[]
    for role,value in [('headline',headline),('subheadline',_component(_copy_value(layout.get('subheadline'),item))),('side_benefit',side)]:
        if value:components.append((role,value))
    return layout,components


def _native_component(content,entry,text,start,end,settings,stage,role):
    from component_tools import load_component
    comp=load_component(entry['component_path'])
    seg,mats=clone_component(comp,text,start,end,stage,{**settings,'template_preserve_position':True})
    # The template owns its font, geometry and animation. Validate its actual role.
    pos=seg.get('clip',{}).get('transform',{});x=float(pos.get('x',0));y=float(pos.get('y',0))
    if role in ('headline','subheadline') and not .6<y<.97:raise CaptionError('Header template has incompatible placement')
    if role=='side_benefit' and not .65<abs(x)<.96:raise CaptionError('Side template has incompatible placement')
    fit=_fit_caption_template(seg,mats,text,{**settings,'base_font_size':8,'subtitle_scale':1,
                                          'subtitle_max_width_ratio':min(.94,.99-abs(x))})
    for kind,values in mats.items():content.setdefault('materials',{}).setdefault(kind,[]).extend(values)
    name='Layout:'+LABELS.get(role,role);tracks=content.setdefault('tracks',[])
    track=next((t for t in tracks if t.get('name')==name),None)
    if track is None:track={'id':_id(),'type':'text','name':name,'segments':[]};tracks.append(track)
    seg.update(render_index=15000+tracks.index(track),track_render_index=tracks.index(track));track['segments'].append(seg)
    return {'component':role,'text':text,'segment_id':seg['id'],'start_ms':start,'duration_ms':end-start,
            'fit':fit,'style_name':entry.get('name'),'transform':seg.get('clip')}


def append_native_layout(content,item,job,settings,catalog,font,stage,rng):
    layout,components=layout_inputs(item,job,settings,rng)
    events=[];start=item['start_ms'];end=start+item['duration_ms']
    if layout.get('start_policy','after_opening_hook')=='after_opening_hook':
        hooks=[c['end_ms'] for c in item.get('captions',[]) if c.get('layout_role')=='opening_hook_card' and c['start_ms']<3000]
        if hooks:start+=max(hooks)
    elif layout.get('start_policy')!='composition_start':raise CaptionError('Unknown layout start_policy')
    start+=float(layout.get('start_offset_ms',0))
    if components and not item['start_ms']<=start<end:raise CaptionError('Persistent layout has no valid display interval')
    # Choose a whole compatible family once. Optional family labels prevent
    # individually random combinations of unrelated header/side treatments.
    family=layout.get('style_family')
    if not family:
        sets=[{e.get('family','manual-general') for key in ('text_styles','text_templates') for e in candidates(catalog,key,r)} for r,_ in components]
        common=set.intersection(*sets) if sets else set()
        if common:family=rng.choice(sorted(common))
        elif components and settings.get('missing_effect_policy')!='plain':raise CaptionError('No compatible native header/side style family')
    for role,obj in components:
        pool=[('recipe',e) for e in candidates(catalog,'text_styles',role)]+[('template',e) for e in candidates(catalog,'text_templates',role)]
        if family:pool=[(k,e)for k,e in pool if e.get('family','manual-general')==family]
        rng.shuffle(pool);failures=[]
        accents=obj.get('accent_ranges')
        if accents is None:accents=[(m.start(),m.end()) for m in re.finditer(r'(?:[¥￥])?\d+(?:\.\d+)?(?:元|块|折|岁|%|％)?',obj['text'])]
        for kind,entry in pool:
            try:
                trial=copy.deepcopy(content)
                if kind=='template':event=_native_component(trial,entry,obj['text'],start,end,settings,stage,role)
                else:
                    config={k:obj[k] for k in ('x','y','size','scale','width','height','minimum_scale_ratio') if k in obj}
                    if role=='side_benefit':
                        config['vertical']=True
                        which=obj.get('side',layout.get('side'))
                        if which is not None:
                            if which not in ('left','right'):raise CaptionError('side must be left or right')
                            config['x']=abs(float(entry.get('clip',{}).get('transform',{}).get('x',.843)))*(1 if which=='right' else -1)
                    if entry.get('panel'):
                        _append_panel(trial,entry['panel'],{**item,'start_ms':start,'duration_ms':end-start},settings)
                    event=render_recipe(trial,obj['text'],entry,start,end,settings,stage,config=config,
                                        accent_ranges=accents,track_name='Layout:'+LABELS.get(role,role),role=role)
                content.clear();content.update(trial);events.append(event);break
            except CaptionError as e:failures.append(str(e))
        else:
            if settings.get('missing_effect_policy')!='plain':
                raise CaptionError(f'No readable verified {role} style: '+('; '.join(failures[:3]) or 'extract a native style pack first'))
            config={'x':.83 if role=='side_benefit' else 0,'y':.16 if role=='side_benefit' else .84 if role=='headline' else .71,
                    'size':15 if role=='side_benefit' else 13,'scale':.72 if role=='side_benefit' else 1.15,
                    'width':.12 if role=='side_benefit' else .90,'height':.6 if role=='side_benefit' else .15}
            obj={**obj,'vertical':role=='side_benefit'}
            event=_append_text(content,LABELS.get(role,role),obj,config,{**item,'start_ms':start,'duration_ms':end-start},settings,font,stage)
            event['warnings']=['Explicit plain fallback: native '+role+' styling unavailable'];events.append(event)
    # Formal copy keeps its own typography and punctuation. Existing explicit
    # legacy overlays are still honored when automatic decoration is disabled.
    disclosure=_component(_copy_value(layout.get('disclosure'),item)) if layout.get('enabled',True) else None
    if disclosure:
        events.append(_append_text(content,'Disclosure',disclosure,{'x':.246,'y':-.719,'size':5,'scale':.873,'width':.56,'height':.20},item,settings,font,stage))
    for i,o in enumerate(job.get('overlays',[])):
        if o.get('kind')=='text' and _component(o):
            events.append(_append_text(content,'TextOverlay'+str(i),o,{'x':0,'y':-.8,'size':5,'scale':1,'width':.88,'height':.14},item,settings,font,stage))
    return events
