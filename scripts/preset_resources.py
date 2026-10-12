"""Bind shipped native style recipes to an explicitly supplied local catalog.

No download, authentication, historical scan or assumed native verification.
Output stays in the user's task directory, never in the installed skill.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def bind(catalog_path,out_path,recipes_path=None):
    from batch import resolve_catalog_paths,write
    from font_identity import collect_font_records,merge_fonts,resolve_font
    path=Path(catalog_path).resolve()
    catalog=resolve_catalog_paths(json.loads(path.read_text('utf-8-sig')),path.parent)
    spec_path=Path(recipes_path or ROOT/'presets/general-conversion/native-style-recipes.json')
    spec=json.loads(spec_path.read_text('utf-8-sig'))
    flower_spec=json.loads((spec_path.parent/spec['caption_flowers_file']).read_text('utf-8-sig')) if spec.get('caption_flowers_file') else {}
    flower_groups=flower_spec.get('caption_flowers',spec.get('caption_flowers',{}))
    identities=json.loads((ROOT/'presets/font-identities.json').read_text('utf-8'))['fonts']
    catalog['fonts']=merge_fonts([*catalog.get('fonts',[]),*collect_font_records(catalog,'explicit_catalog')])
    found={kind:{} for kind in ('flowers','caption_animations','text_animations')}
    for kind in ('flowers','caption_animations','text_animations'):
        for e in catalog.get(kind,[]):
            rid=str(e.get('resource_id')or e.get('effect_id')or '')
            if rid and Path(e.get('cache_path','')).is_dir():found[kind][rid]=e['cache_path']
    # Raw discovery lists have no exported style. Bind only IDs whose role and
    # full styling are already defined by the selected reference recipes.
    known_flower_ids={str(s['effectStyle']['id']) for r in spec['text_styles'] for s in r['styles'] if s.get('effectStyle')}
    known_flower_ids.update(rid for group in flower_groups.values()for rid in group)
    known_animation_ids={str(a['resource_id']) for r in spec['text_styles'] for a in r.get('animations',[])}
    for e in catalog.get('effects',[]):
        rid=str(e.get('id')or e.get('resource_id')or '');p=Path(e.get('path',''))
        if not p.is_dir() or not any(p.iterdir()):continue
        category=e.get('cache_category') or ('artistEffect' if p.parent.name.casefold()=='artisteffect' else 'effect' if p.parent.name.casefold()=='effect' else None)
        if rid in known_flower_ids and category=='artistEffect':found['flowers'].setdefault(rid,str(p))
        if rid in known_animation_ids and category=='effect':found['text_animations'].setdefault(rid,str(p))
    complete=[];missing=[];bindings=[];bound_flowers=[];missing_flowers=[]
    for original in spec['text_styles']:
        r=copy.deepcopy(original);needs=[]
        for index,style in enumerate(r['styles']):
            f=style.get('font',{});rid=str(f.get('id',''))
            names=identities.get(rid,{}).get('names',[])
            try:resolved,method=resolve_font(f,catalog,names)
            except ValueError as error:
                resolved=None;method='ambiguous';needs.append({'kind':'font','resource_id':rid,'names':names,'reason':str(error)})
            if resolved:
                f['path']=resolved['path']
                observed=resolved.get('resource_ids',[]) or [resolved.get('resource_id','')]
                # Local font use does not require inventing a cloud ID. Preserve
                # the request in diagnostics; emit only a genuinely observed ID.
                f['id']=rid if rid in observed else str(resolved.get('resource_id') or '')
                bindings.append({'recipe':r['name'],'style_index':index,'requested_id':rid,'expected_names':names,
                                 'method':method,'path':f['path'],'resolved_id':f['id'],'identity_evidence':resolved.get('identity_evidence',[])})
            elif method!='ambiguous':needs.append({'kind':'font','resource_id':rid,'names':names,'reason':'No matching local font identity; not evidence of an expired ID'})
            fx=style.get('effectStyle')
            if fx:
                rid=str(fx['id'])
                if rid in found['flowers']:fx['path']=found['flowers'][rid]
                else:needs.append({'kind':'flower','resource_id':rid})
        for a in r.get('animations',[]):
            rid=str(a['resource_id'])
            if rid in found['text_animations']:a['cache_path']=found['text_animations'][rid];a['animation']['path']=found['text_animations'][rid]
            else:needs.append({'kind':'text_animation','resource_id':rid})
        if needs:missing.append({'recipe':r['name'],'resources':needs});continue
        r.update(verified=False,available=True,binding_required=False,
                 verification_note='Paths bound only. Preview this complete recipe in target Jianying before setting verified=true.')
        complete.append(r)
    existing={r.get('name'):r for r in catalog.get('text_styles',[])};preserved=[]
    for r in complete:
        if r['name'] not in existing:catalog.setdefault('text_styles',[]).append(r)
        elif existing[r['name']].get('verified') is not True:
            existing[r['name']].clear();existing[r['name']].update(r)
        else:preserved.append(r['name'])
    # Discovery entries are binding inputs, not standalone executable styles.
    if flower_groups:
        font_request=flower_spec.get('style',{}).get('font',{'id':'7399865900821647935'})
        font,font_method=resolve_font(font_request,catalog,identities.get(font_request.get('id'),{}).get('names',[]))
        ids=list(dict.fromkeys(rid for group in flower_groups.values()for rid in group))
        for rid in ids:
            if not font or rid not in found['flowers']:
                missing_flowers.append({'effect_id':rid,'reason':'subtitle_font_missing' if not font else 'effect_cache_missing'});continue
            style=copy.deepcopy(flower_spec.get('style',{}))
            if not style:raise ValueError('Caption flowers need an explicit full style definition')
            style['font']={'id':font_request['id'] if font_request['id'] in font.get('resource_ids',[]) else str(font.get('resource_id')or''),'path':font['path']}
            style['effectStyle']={'id':rid,'path':found['flowers'][rid]}
            roles=[role for role,group in flower_groups.items()if rid in group]
            entry={'name':'字幕花字-'+rid,'effect_id':rid,'resource_id':rid,'cache_path':found['flowers'][rid],
                   'style':style,'role':'caption','semantic_roles':roles,'verified':False,'binding_only':False,
                   'style_basis':'user-configured gold subtitle style','verification_note':'Bound local resource; native preview required.'}
            existing_flower=next((e for e in catalog.get('flowers',[])if str(e.get('effect_id')or e.get('resource_id'))==rid),None)
            if existing_flower and isinstance(existing_flower.get('style'),dict):
                existing_flower.setdefault('semantic_roles',roles)
                existing_flower['binding_only']=False
            elif existing_flower:existing_flower.clear();existing_flower.update(entry)
            else:catalog.setdefault('flowers',[]).append(entry)
            bound_flowers.append(rid)
        catalog['caption_flowers']=flower_groups
    for entry in catalog.get('flowers',[]):
        if not isinstance(entry.get('style'),dict):entry['binding_only']=True
    for entry in catalog.get('text_animations',[]):
        if not isinstance(entry.get('animation'),dict):entry['binding_only']=True
    write(out_path,catalog)
    report={'catalog_path':str(Path(out_path).resolve()),'bound_recipes':[r['name']for r in complete],
            'missing':missing,'bound_caption_flowers':bound_flowers,'missing_caption_flowers':missing_flowers,
            'font_bindings':bindings,'preserved_verified_recipes':preserved,'native_preview_required':True}
    write(Path(out_path).with_suffix('.binding-report.json'),report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True);parser.add_argument('--out',required=True);parser.add_argument('--recipes')
    args=parser.parse_args();print(json.dumps(bind(args.catalog,args.out,args.recipes),ensure_ascii=False))


if __name__=='__main__':main()
