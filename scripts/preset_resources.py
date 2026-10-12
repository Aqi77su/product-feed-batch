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
    path=Path(catalog_path).resolve()
    catalog=resolve_catalog_paths(json.loads(path.read_text('utf-8-sig')),path.parent)
    spec=json.loads(Path(recipes_path or ROOT/'presets/general-conversion/native-style-recipes.json').read_text('utf-8-sig'))
    found={};fonts={}
    for f in catalog.get('fonts',[]):
        rid=str(f.get('resource_id',f.get('id','')))
        if rid and Path(f.get('path','')).is_file():fonts[rid]=f['path']
    for kind in ('flowers','caption_animations','text_animations'):
        for e in catalog.get(kind,[]):
            rid=str(e.get('resource_id',e.get('effect_id','')))
            if rid and Path(e.get('cache_path','')).is_dir():found[rid]=e['cache_path']
            f=e.get('style',{}).get('font',{})
            if f.get('id') and Path(f.get('path','')).is_file():fonts[str(f['id'])]=f['path']
    complete=[];missing=[]
    for original in spec['text_styles']:
        r=copy.deepcopy(original);needs=[]
        for style in r['styles']:
            f=style.get('font',{});rid=str(f.get('id',''))
            if rid in fonts:f['path']=fonts[rid]
            else:needs.append({'kind':'font','resource_id':rid})
            fx=style.get('effectStyle')
            if fx:
                rid=str(fx['id'])
                if rid in found:fx['path']=found[rid]
                else:needs.append({'kind':'flower','resource_id':rid})
        for a in r.get('animations',[]):
            rid=str(a['resource_id'])
            if rid in found:a['cache_path']=found[rid];a['animation']['path']=found[rid]
            else:needs.append({'kind':'text_animation','resource_id':rid})
        if needs:missing.append({'recipe':r['name'],'resources':needs});continue
        r.update(verified=False,available=True,binding_required=False,
                 verification_note='Paths bound only. Preview this complete recipe in target Jianying before setting verified=true.')
        complete.append(r)
    existing={r.get('name')for r in catalog.get('text_styles',[])}
    catalog.setdefault('text_styles',[]).extend(r for r in complete if r['name'] not in existing)
    write(out_path,catalog)
    report={'catalog_path':str(Path(out_path).resolve()),'bound_recipes':[r['name']for r in complete],
            'missing':missing,'native_preview_required':True}
    write(Path(out_path).with_suffix('.binding-report.json'),report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True);parser.add_argument('--out',required=True);parser.add_argument('--recipes')
    args=parser.parse_args();print(json.dumps(bind(args.catalog,args.out,args.recipes),ensure_ascii=False))


if __name__=='__main__':main()
