"""Extract portable, text-sanitized native Jianying text-template components.

Use only with an explicitly authorized local draft. This module does not scan
history, decrypt, upload, install software, or enable an unverified resource.
The output is user data and must remain outside the published skill repository.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
from pathlib import Path

from caption_engine import CaptionError, _material_ids, _u16, clone_component, _effect_style, _native_animation


def _read(path):
    p=Path(path)
    if p.is_dir():
        candidates=[p/'draft_info.json',p/'draft_content.json']
        p=next((x for x in candidates if x.is_file()),p)
    try:return json.loads(p.read_text('utf-8-sig'))
    except (OSError,ValueError) as e:raise CaptionError('Draft must be an already-decrypted local JSON file') from e


def _source_segment(draft, segment_id):
    doc=_read(draft)if not isinstance(draft,dict)else copy.deepcopy(draft)
    seg=next((s for t in doc.get('tracks',[])for s in t.get('segments',[])if s.get('id')==segment_id),None)
    if seg is None:raise CaptionError('Requested segment not found in draft')
    return doc,seg,_material_ids(doc.get('materials',{}))


def _portable_catalog(value, out_path, draft):
    """Copy only explicitly referenced resource files/trees; retain relative paths."""
    out=Path(out_path).resolve();out.parent.mkdir(parents=True,exist_ok=True);cache={}
    def relocate(v):
        if isinstance(v,dict):
            for k,vv in list(v.items()):
                if k.endswith('path')and isinstance(vv,str)and vv:
                    src=Path(vv)
                    if not src.is_absolute()and not isinstance(draft,dict):src=Path(draft).resolve().parent/src
                    src=src.resolve()
                    if not src.exists():raise CaptionError(f'Resource missing: {src.name}')
                    if str(src)not in cache:
                        key=hashlib.sha256(str(src).encode('utf-8')).hexdigest()[:16]
                        dest=out.parent/'resources'/key/src.name
                        if src.is_dir():shutil.copytree(src,dest,dirs_exist_ok=True)
                        else:dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
                        cache[str(src)]=dest.relative_to(out.parent).as_posix()
                    v[k]=cache[str(src)]
                else:relocate(vv)
        elif isinstance(v,list):
            for vv in v:relocate(vv)
    result=copy.deepcopy(value);relocate(result)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    return {'catalog_path':str(out),'verified':False,'resources_copied':len(cache),
            'relative_path_base':str(out.parent)}


def extract_flower(draft, segment_id, out_path, *, style_index=None, text_material_id=None):
    """Export an exact inline-flower style into a directly loadable catalog."""
    doc,seg,ids=_source_segment(draft,segment_id)
    kind,material=ids.get(seg.get('material_id'),('',{}))
    if kind=='text_templates':
        child_ids=[x['text_material_id']for x in material.get('text_info_resources',[])]
        if text_material_id is None and len(child_ids)!=1:
            raise CaptionError('Multiple template child texts; select --text-material-id explicitly')
        text_material_id=text_material_id or child_ids[0]
        if text_material_id not in child_ids:raise CaptionError('Selected text material is not a child of this template')
        kind,material=ids.get(text_material_id,('',{}))
    if kind!='texts':raise CaptionError('Segment does not reference editable text')
    inner=json.loads(material.get('content','{}'));styles=inner.get('styles',[])
    candidates=[i for i,s in enumerate(styles)if s.get('effectStyle',{}).get('id')]
    if style_index is None:
        if len(candidates)!=1:raise CaptionError('Choose --style-index: zero or multiple inline flower ranges found')
        style_index=candidates[0]
    if style_index not in candidates:raise CaptionError('Selected style is not an inline flower')
    style=copy.deepcopy(styles[style_index]);fx=style['effectStyle'];rid=str(fx['id'])
    if not fx.get('path'):raise CaptionError('Selected flower has no locally available cache path')
    if not style.get('font',{}).get('path'):raise CaptionError('Selected flower has no font resource')
    effect=next((m for m in doc.get('materials',{}).get('effects',[])if str(m.get('effect_id'))==rid),{})
    # Character boundaries never reveal the old phrase, nor survive as ranges
    # for the new phrase. The compiler assigns actual [start,end) bounds later.
    style['range']=[0,len('{{TEXT}}')]
    entry={'name':effect.get('name')or'Selected inline flower','effect_id':rid,'resource_id':rid,
           'cache_path':fx['path'],'style':style,'role':'caption','available':True,'verified':False}
    result=_portable_catalog({'schema_version':'1.0','flowers':[entry]},out_path,draft)
    result.update(resource_kind='flowers',effect_id=rid)
    return result


def extract_caption_animation(draft, segment_id, out_path, *, animation_index=None):
    """Export only a referenced native caption animation, never an intro alias."""
    doc,seg,ids=_source_segment(draft,segment_id);candidates=[]
    for ref in dict.fromkeys(seg.get('extra_material_refs',[])):
        kind,material=ids.get(ref,('',{}))
        if kind=='material_animations':
            candidates.extend(copy.deepcopy(a)for a in material.get('animations',[])if a.get('type')=='caption')
    if animation_index is None:
        if len(candidates)!=1:raise CaptionError('Choose --animation-index: zero or multiple native caption animations found')
        animation_index=0
    if not 0<=animation_index<len(candidates):raise CaptionError('Caption animation index outside referenced candidates')
    node=candidates[animation_index];rid=str(node.get('resource_id')or node.get('id')or'')
    if not rid or not node.get('path'):raise CaptionError('Native caption animation lacks resource_id or local path')
    node.pop('request_id',None)
    entry={'name':node.get('name')or'Native caption animation','resource_id':rid,'cache_path':node['path'],
           'animation':node,'role':'caption','available':True,'verified':False}
    result=_portable_catalog({'schema_version':'1.0','caption_animations':[entry]},out_path,draft)
    result.update(resource_kind='caption_animations',resource_id=rid)
    return result


def _dependencies(value, ids):
    found=set()
    def walk(v):
        if isinstance(v,dict):
            for vv in v.values():walk(vv)
        elif isinstance(v,list):
            for vv in v:walk(vv)
        elif isinstance(v,str):
            if v in ids:found.add(v)
            elif v.startswith('{') or v.startswith('['):
                try:walk(json.loads(v))
                except ValueError:pass
    walk(value)
    return found


def extract_text_style(draft,segment_id,out_path,*,role,family='manual-general',base_style_index=None,accent_style_index=None,panel_segment_id=None):
    """Export complete direct-text styling without copy, character counts or old word times."""
    if role not in ('headline','subheadline','side_benefit','opening_hook','caption','price_badge'):
        raise CaptionError('Unknown native text role')
    doc,seg,ids=_source_segment(draft,segment_id)
    kind,mat=ids.get(seg.get('material_id'),('',{}))
    if kind!='texts':raise CaptionError('Use extract for a complete text template')
    inner=json.loads(mat['content']);text=inner['text'];originals=inner.get('styles',[])
    if not originals:raise CaptionError('No native styles')
    styles=[]
    def scrub(v):
        if isinstance(v,dict):return {k:scrub(x) for k,x in v.items() if k not in ('request_id','recognize_task_id','user_id','range')}
        if isinstance(v,list):return [scrub(x)for x in v]
        return v
    for original in originals:styles.append(scrub(original))
    def plain_black(s):
        color=s.get('strokes',[{}])[0].get('content',{}).get('solid',{}).get('color',[0,0,0]) if s.get('strokes') else [0,0,0]
        return not s.get('effectStyle') and sum(color)<.2
    base=base_style_index if base_style_index is not None else next((i for i,s in enumerate(styles) if plain_black(s)),0)
    accent=accent_style_index if accent_style_index is not None else next((i for i,s in enumerate(styles) if not plain_black(s)),base)
    anims=[]
    for ref in seg.get('extra_material_refs',[]):
        for node in ids.get(ref,('',{}))[1].get('animations',[]):
            if node.get('type') in ('in','out','loop'):
                node=scrub(node);anims.append({'name':node.get('name','Native text animation'),'resource_id':str(node.get('resource_id')or node.get('id')),
                    'cache_path':node.get('path'),'animation':node})
    entry={'name':'Native '+role+' style','roles':[role],'family':family,'styles':styles,'base_style':base,'accent_style':accent,
           'clip':copy.deepcopy(seg.get('clip',{})),'vertical':mat.get('typesetting')==1,
           'line_spacing':mat.get('line_spacing',.02),'animations':anims,'verified':False,'available':True,
           'minimum_scale_ratio':.85,'safe_width':.94,'safe_height':.9}
    if '\n'in text:
        indices=[];pos=0
        for line in text.split('\n'):
            indices.append(max(range(len(originals)),key=lambda i:max(0,min(pos+len(line),originals[i]['range'][1])-max(pos,originals[i]['range'][0]))));pos+=len(line)+1
        entry['line_styles']=indices
    if panel_segment_id:
        _,panel,pids=_source_segment(doc,panel_segment_id);pk,pm=pids.get(panel.get('material_id'),('',{}))
        if pk!='shapes':raise CaptionError('Panel must reference an actual native shape')
        canvas=doc.get('canvas_config',{});size=pm.get('shape_size',[]);pos=panel.get('clip',{}).get('transform',{})
        if len(size)!=2:raise CaptionError('Panel has no rectangular dimensions')
        entry['panel']={'width':size[0]/canvas['width'],'height':size[1]/canvas['height'],
                        'x':pos.get('x',0),'y':pos.get('y',.85),'color':pm.get('fill_render_style',{}).get('color',{}).get('solid',{}).get('color')}
        if not entry['panel']['color']:raise CaptionError('Panel has no solid fill')
    return _portable_catalog({'schema_version':'1.0','text_styles':[entry]},out_path,draft)


def extract_text_animation(draft,segment_id,out_path,*,animation_index=None,role='caption'):
    doc,seg,ids=_source_segment(draft,segment_id)
    nodes=[copy.deepcopy(a)for ref in seg.get('extra_material_refs',[])for a in ids.get(ref,('',{}))[1].get('animations',[]) if a.get('type')in('in','out','loop')]
    if animation_index is None:
        if len(nodes)!=1:raise CaptionError('Choose --animation-index for the native text animation')
        animation_index=0
    if not 0<=animation_index<len(nodes):raise CaptionError('Invalid animation index')
    node=nodes[animation_index];node.pop('request_id',None)
    entry={'name':node.get('name','Native text animation'),'roles':[role],'resource_id':str(node.get('resource_id')or node.get('id')),
           'cache_path':node.get('path'),'animation':node,'available':True,'verified':False}
    return _portable_catalog({'schema_version':'1.0','text_animations':[entry]},out_path,draft)


def _validate_dependency_refs(value, ids):
    errors=[]
    def walk(v):
        if isinstance(v,dict):
            for k,vv in v.items():
                if k=='extra_material_refs':
                    errors.extend(f'Missing referenced material: {x}'for x in vv if x not in ids)
                elif k in ('text_material_id','material_id') and vv and vv not in ids:
                    errors.append(f'Missing referenced material: {vv}')
                else:walk(vv)
        elif isinstance(v,list):
            for vv in v:walk(vv)
    walk(value)
    return errors


def extract_component(draft, segment_id, out_path, *, copy_resources=True):
    """Extract complete dependencies, replacing identical child texts by a slot.

    Multiple text children must mirror exactly the same text. Distinct labels,
    per-word multi-style layouts, and different captions require explicit manual
    adapter work and fail here rather than leaking old product copy.
    """
    doc=_read(draft) if not isinstance(draft,dict) else copy.deepcopy(draft)
    seg=next((s for t in doc.get('tracks',[]) for s in t.get('segments',[]) if s.get('id')==segment_id),None)
    if seg is None:raise CaptionError('Requested segment not found in draft')
    ids=_material_ids(doc.get('materials',{}));mid=seg.get('material_id')
    if mid not in ids or ids[mid][0]!='text_templates':
        raise CaptionError('Segment must reference a native text_template, not a flower-only text')
    pending=_dependencies(seg,ids);selected={}
    while pending:
        current=pending.pop()
        if current in selected:continue
        kind,m=ids[current];selected[current]=(kind,copy.deepcopy(m))
        pending.update(_dependencies(m,ids)-selected.keys())
    texts={i:m for i,(k,m) in selected.items() if k=='texts'}
    if not texts:raise CaptionError('Template contains no editable child text')
    originals=[];reference_widths={}
    for mid,material in texts.items():
        try:inner=json.loads(material['content'])
        except (KeyError,ValueError):raise CaptionError('Template child has invalid content JSON') from None
        text=inner.get('text','')
        if not text:raise CaptionError('Unsupported blank/complex text slot')
        if len(inner.get('styles',[]))!=1:
            raise CaptionError('Unsupported multi-style child slot; requires a reviewed component adapter')
        originals.append(text)
        from native_styles import measure_styles
        reference_widths[mid]=measure_styles(text,inner['styles'],{'width':1080,'subtitle_effect_padding_px':0},1)['width_px']
    if len(set(originals))!=1:
        raise CaptionError('Unsupported complex template: child slots contain different historical text')
    for material in texts.values():
        inner=json.loads(material['content']);inner['text']='{{TEXT}}';inner['styles'][0]['range']=[0,_u16('{{TEXT}}')]
        material['content']=json.dumps(inner,ensure_ascii=False)
        for key in ('base_content','recognize_text','recognize_task_id','text_to_audio_ids'):
            material.pop(key,None)
        material.update(words={},current_words={},subtitle_keywords={})
    # Only component-relevant fields are retained, never draft titles, task IDs,
    # history, user identity, original scripts or source media paths.
    def scrub(v):
        if isinstance(v,dict):
            return {k:scrub(vv)for k,vv in v.items() if k not in ('base_content','recognize_text','recognize_task_id','request_id','origin_word_info','current_word_info','text_to_audio_ids')}
        if isinstance(v,list):return [scrub(vv)for vv in v]
        return v
    mats={}
    for kind,m in selected.values():mats.setdefault(kind,[]).append(scrub(m))
    component={'schema_version':'1.0','segment':scrub(copy.deepcopy(seg)),'materials':mats,
                'text_slots':[{'material_id':mid,'role':'primary' if n==0 else 'mirror','reference_text_width':reference_widths[mid]}for n,mid in enumerate(texts)],
               'verified':False,'verification_note':'Extracted structurally; verify appearance in the target Jianying version before enabling.'}
    duration=component['segment'].get('target_timerange',{}).get('duration',0)
    component['segment']['target_timerange']={'start':0,'duration':duration}
    if duration<=0:raise CaptionError('Source template has no positive duration')
    errors=_validate_dependency_refs(component,_material_ids(mats))
    if errors:raise CaptionError('; '.join(errors))
    out=Path(out_path).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    cache={}
    def portable_path(value):
        source=Path(value).expanduser()
        if not source.is_absolute():source=Path(draft).resolve().parent/source if not isinstance(draft,dict)else source
        source=source.resolve()
        if not source.exists():raise CaptionError(f'Template resource missing: {source.name}')
        if not copy_resources:return str(source)
        if str(source) in cache:return cache[str(source)]
        # Hash path only for the local output location; no source path is stored.
        key=hashlib.sha256(str(source).encode('utf-8')).hexdigest()[:16]
        destination=out.parent/'resources'/key/source.name
        if source.is_dir():shutil.copytree(source,destination,dirs_exist_ok=True)
        else:destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        rel=destination.relative_to(out.parent).as_posix();cache[str(source)]=rel;return rel
    def relocate(v):
        if isinstance(v,dict):
            for k,vv in list(v.items()):
                if k=='content' and isinstance(vv,str):
                    try:inner=json.loads(vv);relocate(inner);v[k]=json.dumps(inner,ensure_ascii=False)
                    except ValueError:pass
                elif k.endswith('path') and isinstance(vv,str) and vv:v[k]=portable_path(vv)
                else:relocate(vv)
        elif isinstance(v,list):
            for vv in v:relocate(vv)
    relocate(component)
    # Fail closed if any original user sentence remains in metadata fields.
    encoded=json.dumps(component,ensure_ascii=False,indent=2)
    for original in set(originals):
        if len(original)>=2 and original in encoded:
            raise CaptionError('Historical text remains outside editable slots; manual sanitization required')
    out.write_text(encoded,'utf-8')
    return {'component_path':str(out),'verified':False,'text_slot_count':len(texts),
            'material_count':len(selected),'resources_copied':len(cache),'supported_roles':['primary','mirror']}


def load_component(path):
    """Load a component and resolve its portable resource paths locally."""
    p=Path(path).resolve();component=json.loads(p.read_text('utf-8-sig'))
    def walk(v):
        if isinstance(v,dict):
            for k,vv in list(v.items()):
                if k=='content'and isinstance(vv,str):
                    try:inner=json.loads(vv);walk(inner);v[k]=json.dumps(inner,ensure_ascii=False)
                    except ValueError:pass
                elif k.endswith('path')and isinstance(vv,str)and vv:
                    q=Path(vv);v[k]=str(q if q.is_absolute() else (p.parent/q).resolve())
                else:walk(vv)
        elif isinstance(v,list):
            for vv in v:walk(vv)
    walk(component)
    return component


def validate_component(path):
    component=load_component(path)
    if 'text_styles' in component or 'text_animations' in component:
        from native_styles import recipe_styles,text_animation
        for recipe in component.get('text_styles',[]):
            recipe_styles('样式预检',recipe,lambda p:p)
            for animation in recipe.get('animations',[]):text_animation(animation,1000000,lambda p:p)
        for animation in component.get('text_animations',[]):text_animation(animation,1000000,lambda p:p)
        return {'valid':True,'verified':False,'verification_note':'Structural validation only; native preview required.'}
    if 'flowers'in component or 'caption_animations'in component:
        count=0
        for item in component.get('flowers',[]):
            style=item.get('style',{})
            _effect_style(item,{'size':14,'font':style.get('font',{})},lambda p:p)
            if not style.get('font',{}).get('path')or not Path(style['font']['path']).is_file():
                raise CaptionError('Inline flower font resource is missing')
            count+=1
        for item in component.get('caption_animations',[]):
            _native_animation(item,1000000,lambda p:p);count+=1
        if not count:raise CaptionError('Empty resource catalog')
        return {'valid':True,'verified':False,'resource_count':count,'catalog_path':str(Path(path).resolve()),
                'verification_note':'Structure/resources validated only; native appearance is not verified.'}
    errors=_validate_dependency_refs(component,_material_ids(component.get('materials',{})))
    if errors:raise CaptionError('; '.join(errors))
    # Identity staging reads existing files, without publishing or changing them.
    preview={'text':'组件预览','start_ms':0,'end_ms':1000,'words_timebase':'absolute_ms','words':[{'text':'组件预览','start_ms':0,'end_ms':1000}]}
    clone_component(component,'组件预览',0,1000,lambda p:p,cue=preview)
    return {'valid':True,'verified':component.get('verified')is True,'component_path':str(Path(path).resolve())}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    ex=subs.add_parser('extract');ex.add_argument('--draft',required=True);ex.add_argument('--segment-id',required=True);ex.add_argument('--out',required=True)
    ex.add_argument('--reference-resources',action='store_true',help='Reference local cache instead of copying a portable resource directory')
    fl=subs.add_parser('extract-flower');fl.add_argument('--draft',required=True);fl.add_argument('--segment-id',required=True);fl.add_argument('--out',required=True)
    fl.add_argument('--style-index',type=int);fl.add_argument('--text-material-id')
    an=subs.add_parser('extract-caption-animation');an.add_argument('--draft',required=True);an.add_argument('--segment-id',required=True);an.add_argument('--out',required=True)
    an.add_argument('--animation-index',type=int)
    tx=subs.add_parser('extract-text-style');tx.add_argument('--draft',required=True);tx.add_argument('--segment-id',required=True);tx.add_argument('--out',required=True)
    tx.add_argument('--role',required=True);tx.add_argument('--family',default='manual-general');tx.add_argument('--base-style-index',type=int);tx.add_argument('--accent-style-index',type=int);tx.add_argument('--panel-segment-id')
    ta=subs.add_parser('extract-text-animation');ta.add_argument('--draft',required=True);ta.add_argument('--segment-id',required=True);ta.add_argument('--out',required=True);ta.add_argument('--animation-index',type=int);ta.add_argument('--role',default='caption')
    val=subs.add_parser('validate');val.add_argument('--component',required=True)
    args=parser.parse_args(argv)
    if args.command=='extract':result=extract_component(args.draft,args.segment_id,args.out,copy_resources=not args.reference_resources)
    elif args.command=='extract-flower':result=extract_flower(args.draft,args.segment_id,args.out,style_index=args.style_index,text_material_id=args.text_material_id)
    elif args.command=='extract-caption-animation':result=extract_caption_animation(args.draft,args.segment_id,args.out,animation_index=args.animation_index)
    elif args.command=='extract-text-style':result=extract_text_style(args.draft,args.segment_id,args.out,role=args.role,family=args.family,base_style_index=args.base_style_index,accent_style_index=args.accent_style_index,panel_segment_id=args.panel_segment_id)
    elif args.command=='extract-text-animation':result=extract_text_animation(args.draft,args.segment_id,args.out,animation_index=args.animation_index,role=args.role)
    else:result=validate_component(args.component)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
