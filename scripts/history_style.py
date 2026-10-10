"""Authorized local draft analysis and reusable preset creation."""
import argparse, collections, datetime, hashlib, json, statistics, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor/aoguai-crypto'))

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,o):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8')
def mode(values):return collections.Counter(json.dumps(x,sort_keys=True) for x in values).most_common(1)[0][0] if values else None
def main_files(directory):
 directory=Path(directory).resolve()
 if directory.is_file():return [directory]
 project=directory/'Timelines/project.json'
 if project.is_file():
  try:
   i=read(project)['main_timeline_id'];p=directory/'Timelines'/i/'draft_content.json'
   if p.is_file():return [p]
  except Exception:pass
 return [next((directory/n for n in ('draft_info.json','draft_content.json') if (directory/n).is_file()),None)]
def analyze(paths,out,install=None,approved=False):
 if not approved:raise ValueError('Explicit user authorization required for this historical scope')
 out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
 records=[];errors=[];seen=set()
 for path in paths:
  for f in main_files(path):
   if not f:errors.append({'path':str(path),'error':'No main draft'});continue
   raw=f.read_bytes();h=hashlib.sha256(raw).hexdigest()
   if h in seen:continue
   seen.add(h)
   try:
    try:o=json.loads(raw.decode('utf-8-sig'));encrypted=False
    except (ValueError,UnicodeError):
     if not install:raise ValueError('Encrypted draft needs local videoeditor.dll install directory')
     from draft_crypto import decrypt_draft_file
     dest=out/'decrypted'/(h+'.json');decrypt_draft_file(f,dest,jy_install_dir=install,isolated=True);o=read(dest);encrypted=True
    docs=[];doc_seen=set()
    def collect(v):
     if isinstance(v,dict):
      if isinstance(v.get('tracks'),list) and isinstance(v.get('materials'),dict):
       key=hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
       if key not in doc_seen:docs.append(v);doc_seen.add(key)
      for x in v.values():collect(x)
     elif isinstance(v,list):
      for x in v:collect(x)
    collect(o)
    texts=[];audios=[];effects=[];video=[]
    for doc in docs:
     mats=doc['materials'];lookup={m['id']:m for group in mats.values() if isinstance(group,list) for m in group if isinstance(m,dict) and 'id'in m}
     for track in doc['tracks']:
      for seg in track.get('segments',[]):
       mat=lookup.get(seg.get('material_id'),{});duration=seg.get('target_timerange',{}).get('duration',0)/1e6
       if track.get('type')=='text':
        try:inner=json.loads(mat.get('content','{}'))
        except ValueError:continue
        clip=seg.get('clip',{});position=clip.get('transform',{})
        for style in inner.get('styles',[]):
         color=style.get('fill',{}).get('content',{}).get('solid',{}).get('color')
         stroke=style.get('strokes',[])
         texts.append({'size':style.get('size'),'color':color,'y':position.get('y'),
                       'border_json_width':stroke[0].get('width') if stroke else None,
                       'font_path':mat.get('font_path') or style.get('font',{}).get('path'),
                       'font_id':style.get('font',{}).get('id'),'duration_s':duration,
                       'role':'subtitle_candidate' if duration<15 else 'persistent_candidate'})
       elif track.get('type')=='audio':
        source=seg.get('source_timerange',{});audios.append({'name':mat.get('name'),'path':mat.get('path'),
          'volume':seg.get('volume'),'duration_s':duration,'source_start_s':source.get('start',0)/1e6,
          'material_id':seg.get('material_id'),'fade_refs':[r for r in seg.get('extra_material_refs',[]) if any(x.get('id')==r for x in mats.get('audio_fades',[]))]})
       elif track.get('type')=='video':video.append({'duration_s':duration,'render_index':seg.get('render_index'),'volume':seg.get('volume')})
     for key in ('effects','material_animations','video_effects','transitions'):
      effects.extend({'type':key,'data':x} for x in mats.get(key,[]))
    records.append({'source':str(f),'hash':h,'encrypted':encrypted,'texts':texts,'audios':audios,'effects':effects,'video_segments':video})
   except Exception as e:errors.append({'path':str(f),'error':str(e)})
 texts=[x for r in records for x in r['texts'] if x['role']=='subtitle_candidate'];settings={}
 for field,key in [('size','base_font_size'),('color','subtitle_color'),('y','subtitle_y')]:
  values=[t[field] for t in texts if t.get(field)is not None]
  if values:settings[key]=json.loads(mode(values))
 widths=[t['border_json_width'] for t in texts if t.get('border_json_width')is not None]
 if widths:settings['subtitle_border_width']=min(100,max(0,statistics.median(widths)/.002))
 report={'schema_version':'1.0','scope':[str(x) for x in paths],'records':records,'errors':errors,
         'suggested_settings':settings,'note':'Text/audio roles are heuristic. AI/user must distinguish captions, titles, voiceover, music and SFX before preset creation.'}
 write(out/'analysis.json',report);return {'report':str(out/'analysis.json'),'drafts':len(records),'errors':errors,'suggested_settings':settings}
def create(spec,out):
 spec=read(spec)
 if spec.get('confirmed')is not True:raise ValueError('User must confirm preset name, settings and resource scope')
 name=spec.get('name','')
 if not name or any(c in name for c in '/\\'):raise ValueError('Invalid preset name')
 directory=Path(out).resolve()/name
 if directory.exists():raise ValueError('Preset exists; choose versioned name')
 catalog=spec.get('catalog',{})
 for key in ('fonts','music','sfx'):
  for item in catalog.get(key,[]):
   if not Path(item['path']).is_file():raise ValueError('Resource missing: '+item['path'])
 write(directory/'resources.json',catalog)
 write(directory/'preset.json',{'schema_version':'1.0','name':name,'description':spec.get('description',''),
   'settings':spec.get('settings',{}),'resource_catalog':'resources.json','evidence':spec.get('evidence',[]),
   'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'local_only':True})
 return {'preset':str(directory/'preset.json')}
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True)
 a=sub.add_parser('analyze');a.add_argument('--paths',nargs='+');a.add_argument('--root');a.add_argument('--days',type=int);a.add_argument('--install');a.add_argument('--out',required=True);a.add_argument('--authorized',action='store_true')
 a=sub.add_parser('create');a.add_argument('--spec',required=True);a.add_argument('--out',required=True)
 a=sub.add_parser('list');a.add_argument('--root',required=True)
 ns=p.parse_args()
 if ns.cmd=='analyze':
  paths=ns.paths or []
  if ns.root:
   if not ns.days or ns.days<1:raise ValueError('Positive --days required for historical root scan')
   cutoff=datetime.datetime.now().timestamp()-ns.days*86400
   paths += [str(d) for d in Path(ns.root).iterdir() if d.is_dir() and any(f and f.stat().st_mtime>=cutoff for f in main_files(d))]
  if not paths:raise ValueError('No drafts in authorized scope')
  result=analyze(paths,ns.out,ns.install,ns.authorized)
 elif ns.cmd=='create':result=create(ns.spec,ns.out)
 else:result=[{'path':str(f),**read(f)} for f in Path(ns.root).glob('*/preset.json')]
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
