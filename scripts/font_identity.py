"""Local font identity from SFNT names and explicit draft references.

Cloud IDs are optional evidence, never inferred from a text material UUID.
The name-table reader handles localized TTF/OTF/TTC names without new packages.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
import struct
import unicodedata
from functools import lru_cache
from pathlib import Path

EXTENSIONS={'.ttf','.otf','.ttc'}


def cache_font_id(path):
    parts=str(path).replace('\\','/').split('/')
    for i in range(len(parts)-3):
        if parts[i].casefold()=='cache' and parts[i+1].casefold() in ('effect','font'):
            value=parts[i+2]
            if re.fullmatch(r'\d{16,22}',value):return value
    return ''


def name_key(value):
    value=unicodedata.normalize('NFKC',str(value)).strip()
    if Path(value).suffix.lower() in EXTENSIONS:value=Path(value).stem
    return ''.join(c.casefold() for c in value if c.isalnum())


def font_names(path):
    """Read family/full/PostScript names; never confuse SFNT name ID with cloud ID."""
    try:
        p=Path(path).resolve();stat=p.stat()
        return list(_font_names(str(p),stat.st_size,stat.st_mtime_ns))
    except OSError:return []


@lru_cache(maxsize=512)
def _font_names(path,file_size,modified_ns):
    names=[]
    try:
        with Path(path).open('rb') as stream:
            def read_at(offset,length):
                if offset<0 or length<0 or length>4*1024*1024:raise ValueError('Invalid font table bounds')
                stream.seek(offset);b=stream.read(length)
                if len(b)!=length:raise ValueError('Truncated font table')
                return b
            head=read_at(0,12)
            offsets=[0]
            if head[:4]==b'ttcf':
                count=struct.unpack('>I',head[8:12])[0]
                if not 0<count<=256:raise ValueError('Invalid font collection')
                # The writer has no TTC face-index field. Do not advertise a
                # secondary face by name and then accidentally render face 0.
                offsets=list(struct.unpack('>'+str(count)+'I',read_at(12,count*4)))[:1]
            for face in offsets:
                header=read_at(face,12);count=struct.unpack('>H',header[4:6])[0]
                if not 0<count<=512:continue
                for i in range(count):
                    tag,_,offset,length=struct.unpack('>4sIII',read_at(face+12+16*i,16))
                    if tag!=b'name':continue
                    data=read_at(offset,length)
                    if len(data)<6:continue
                    _,records,storage=struct.unpack('>HHH',data[:6])
                    if records>4096 or 6+records*12>len(data):continue
                    for j in range(records):
                        platform,encoding,language,kind,n,start=struct.unpack('>HHHHHH',data[6+j*12:18+j*12])
                        if kind not in (1,4,6,16) or storage+start+n>len(data):continue
                        codec='utf-16-be' if platform in (0,3) else 'mac_roman' if platform==1 and encoding==0 else None
                        if codec is None:continue
                        try:value=data[storage+start:storage+start+n].decode(codec).strip('\0 ').strip()
                        except UnicodeError:continue
                        if value and value not in names:names.append(value)
    except (OSError,ValueError,struct.error):pass
    return tuple(names)


def font_record(path,*,resource_id=None,source='local',names=()):
    p=Path(path).expanduser().resolve();metadata=font_names(p) if p.is_file() else []
    aliases=list(dict.fromkeys([*metadata,*[str(n) for n in names if n],p.stem]))
    cached=cache_font_id(p)
    rid=str(resource_id or cached)
    evidence=[]
    if resource_id:evidence.append({'kind':'draft_font_id','resource_id':str(resource_id),'source':source})
    if cached:evidence.append({'kind':'cache_directory_id','resource_id':cached,'source':source})
    return {'name':metadata[0] if metadata else next((str(n)for n in names if n),p.stem),
            'path':str(p),'available':p.is_file(),'source':source,'font_names':metadata,
            'aliases':aliases,'resource_id':rid,'resource_ids':list(dict.fromkeys([x for x in (rid,cached) if x])),
            'identity_evidence':evidence,
            'classification':'font_metadata' if metadata else 'font_extension'}


def collect_font_records(document,source='explicit_document',base=None):
    out=[];seen=set()
    def emit(path,rid=None,names=()):
        if not isinstance(path,str) or Path(path).suffix.lower()not in EXTENSIONS:return
        p=Path(path)
        if not p.is_absolute() and base:p=Path(base)/p
        key=(str(p),str(rid or ''),tuple(names))
        if key in seen:return
        seen.add(key)
        out.append(font_record(p,resource_id=rid,source=source,names=names))
    def walk(v,parent=''):
        if isinstance(v,dict):
            if v.get('font_path'):
                emit(v['font_path'],v.get('font_resource_id'),[v.get('font_name')])
            if v.get('path') and Path(str(v['path'])).suffix.lower()in EXTENSIONS:
                rid=v.get('resource_id') or (v.get('id') if parent in ('font','fonts') or v.get('type')=='font' else None)
                emit(v['path'],rid,[v.get('name'),*v.get('font_names',[]),*v.get('aliases',[])])
            for k,x in v.items():
                if k in ('content','base_content') and isinstance(x,str):
                    try:walk(json.loads(x),k)
                    except (ValueError,TypeError):pass
                elif isinstance(x,(dict,list)):walk(x,k)
        elif isinstance(v,list):
            for x in v:walk(x,parent)
    walk(document)
    return merge_fonts(out)


def merge_fonts(records):
    merged={}
    for original in records:
        if not original.get('path'):continue
        r=copy.deepcopy(original);key=str(Path(r['path']).resolve()).casefold()
        target=merged.setdefault(key,r)
        for field in ('font_names','aliases','resource_ids','identity_evidence'):
            values=target.setdefault(field,[])
            for value in r.get(field,[]):
                if value not in values:values.append(value)
        for field in ('resource_id','id'):
            rid=str(r.get(field)or'')
            if rid and rid not in target['resource_ids']:target['resource_ids'].append(rid)
        if len(target['resource_ids'])==1:target['resource_id']=target['resource_ids'][0]
        elif len(target['resource_ids'])>1:target['resource_id']=''
    return list(merged.values())


def enrich_font(record):
    r=copy.deepcopy(record);metadata=font_names(r['path'])
    r['file_name']=Path(r['path']).stem
    r['font_names']=list(dict.fromkeys([*r.get('font_names',[]),*metadata]))
    r['aliases']=list(dict.fromkeys([*r.get('aliases',[]),r.get('name',''),r['file_name'],*r['font_names']]))
    if metadata:r['name']=metadata[0];r['classification']='font_metadata'
    r.setdefault('resource_id','');r.setdefault('resource_ids',[])
    return r


def _unique_file(items):
    hashes={}
    for item in items:
        p=Path(item['path']);digest=hashlib.sha256(p.read_bytes()).hexdigest()
        hashes.setdefault(digest,item)
    if len(hashes)==1:return next(iter(hashes.values()))
    raise ValueError('Ambiguous font identity: different files match; choose an explicit font_binding')


def resolve_font(request,catalog,expected_names=()):
    requested=str(request.get('id')or'');pool=merge_fonts([enrich_font(f)for f in catalog.get('fonts',[]) if f.get('available',True) and Path(f.get('path','')).is_file()])
    overrides=catalog.get('font_bindings',{})
    expected=list(dict.fromkeys([*expected_names,*request.get('names',[]),request.get('name','')]))
    override=overrides.get(requested) or next((overrides[n]for n in expected if n in overrides),None)
    if override:
        value={'path':override} if isinstance(override,str) else override
        path=Path(value.get('path',''))
        if not path.is_file():raise ValueError('Explicit font_binding file is missing')
        result=font_record(path,resource_id=value.get('resource_id'),source='explicit_font_binding')
        return result,'explicit_path'
    if request.get('path') and Path(request['path']).is_file():
        return font_record(request['path'],resource_id=requested,source='explicit_recipe_path'),'recipe_path'
    exact=[f for f in pool if requested and requested in {str(f.get('resource_id')or''),*map(str,f.get('resource_ids',[]))}]
    if exact:return _unique_file(exact),'observed_resource_id'
    keys={name_key(n)for n in expected if name_key(n)}
    matches=[f for f in pool if keys.intersection(name_key(n)for n in [*f.get('font_names',[]),*f.get('aliases',[]),f.get('name','')])]
    if matches:return _unique_file(matches),'exact_font_name'
    return None,'missing'
