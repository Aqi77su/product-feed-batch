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
    spec=json.loads(Path(recipes_path or ROOT/'presets/general-conversion/native-style-recipes.json').read_text('utf-8-sig'))
    identities=json.loads((ROOT/'presets/font-identities.json').read_text('utf-8'))['fonts']
    catalog['fonts']=merge_fonts([*catalog.get('fonts',[]),*collect_font_records(catalog,'explicit_catalog')])
    found={kind:{} for kind in ('flowers','caption_animations','text_animations')}
    for kind in ('flowers','caption_animations','text_animations'):
        for e in catalog.get(kind,[]):
            rid=str(e.get('resource_id')or e.get('effect_id')or '')
            if rid and Path(e.get('cache_path','')).is_dir():found[kind][rid]=e['cache_path']
    complete=[];missing=[];bindings=[]
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
    write(out_path,catalog)
    report={'catalog_path':str(Path(out_path).resolve()),'bound_recipes':[r['name']for r in complete],
            'missing':missing,'font_bindings':bindings,'preserved_verified_recipes':preserved,'native_preview_required':True}
    write(Path(out_path).with_suffix('.binding-report.json'),report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True);parser.add_argument('--out',required=True);parser.add_argument('--recipes')
    args=parser.parse_args();print(json.dumps(bind(args.catalog,args.out,args.recipes),ensure_ascii=False))


if __name__=='__main__':main()
