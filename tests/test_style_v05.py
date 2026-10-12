"""Regression tests for audited manual semantics. Fixtures are synthetic."""
import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from caption_engine import prepare_document,append_captions,clone_component,CaptionError,estimate_width,validate_caption_content
from caption_policy import slice_cue,_select_emphasis,policy,profile_for
from native_styles import render_recipe,measure_styles
from layout_engine import append_layout
from component_tools import extract_text_style,load_component,validate_component
from semantic_plan import map_document
import batch


class StyleV05Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.fp=self.root/'synthetic.ttf';self.fp.write_bytes(b'not-a-font: structural fixture')
        self.fx=self.root/'effect';self.fx.mkdir();(self.fx/'resource.json').write_text('{}')
        self.font={'path':str(self.fp),'resource_id':'font','name':'fixture','available':True,'verified':True}
        self.settings=batch.job_settings({'settings':{'music_enabled':False,'caption_animation_mode':'none','caption_profile':'conversion',
             'missing_effect_policy':'plain','emphasis_probability':1,'template_probability':1}})
    def tearDown(self):self.temp.cleanup()
    def cue(self,text,tokens=None,keyword=None,mode=None,start=1000):
        tokens=tokens or list(text);words=[];cursor=0
        for token in tokens:
            words.append({'text':token,'start_ms':start+cursor*200,'end_ms':start+(cursor+len(token))*200});cursor+=len(token)
        out={'id':'c'+str(start),'text':text,'start_ms':start,'end_ms':start+len(text)*200,'words':words,'keywords':[]}
        if keyword:
            a=text.index(keyword);k={'text':keyword,'char_start':a,'char_end':a+len(keyword),'start_ms':start+a*200,
                                     'end_ms':start+(a+len(keyword))*200,'timing_precision':'word','role':'core_object'}
            if mode:k['presentation']=mode
            out['keywords']=[k]
        return prepare_document({'timestamp_basis':'source_media','source_id':'v','cues':[out]},self.settings,self.font)['cues'][0]
    def style(self,size=14,color=None):
        return {'size':size,'font':{'id':'font','path':str(self.fp)},'range':[0,8],
                'fill':{'content':{'render_type':'solid','solid':{'color':color or [1,1,1]}}},
                'strokes':[{'width':.08,'content':{'solid':{'color':[0,0,0]}}}]}
    def component(self,caption=False):
        comp={'schema_version':'1.0','segment':{'id':'SEG','material_id':'T','target_timerange':{'start':0,'duration':10000000},
            'clip':{'scale':{'x':1.15,'y':1.15},'transform':{'x':0,'y':-.3024}},'extra_material_refs':['A'] if caption else []},
            'materials':{'text_templates':[{'id':'T','type':'text_template_subtitle' if caption else 'text_template',
              'text_info_resources':[{'text_material_id':'C','attach_info':{'clip':{'scale':{'x':1,'y':1}}}}]}],
              'texts':[{'id':'C','type':'subtitle','content':json.dumps({'text':'{{TEXT}}','styles':[self.style()]})}]},
            'text_slots':[{'material_id':'C','role':'primary'}]}
        if caption:comp['materials']['material_animations']=[{'id':'A','type':'sticker_animation','animations':[{'type':'caption','resource_id':'cap','path':str(self.fx),'duration':10000000}]}]
        return comp
    def catalog(self,caption=False,role='caption'):
        comp=self.component(caption)
        if role=='price_badge':comp['segment']['clip']['transform']={'x':-.34,'y':-.08}
        path=self.root/('component-'+role+'.json');path.write_text(json.dumps(comp),encoding='utf-8')
        return {'fonts':[self.font],'text_templates':[{'name':'Synthetic','role':role,'verified':True,'component_path':str(path)}]}
    def compile(self,cues,catalog=None,settings=None):
        content={'tracks':[],'materials':{}}
        events=append_captions(content,cues,settings or self.settings,catalog or {},self.font,lambda p:p,random.Random(7))
        return content,events
    def test_template_word_times_use_new_cue_not_old_duration(self):
        cue=self.cue('产品好用',tokens=['产品','好用'])
        seg,mats=clone_component(self.component(True),cue['text'],9000,9800,lambda p:p,self.settings,cue=cue)
        self.assertEqual(mats['texts'][0]['words'],{'text':['产品','好用'],'start_time':[0,400],'end_time':[400,800]})
        self.assertEqual(seg['target_timerange']['start'],9000000)
        with self.assertRaisesRegex(CaptionError,'current, reliable'):
            clone_component(self.component(True),'新句子',0,1000,lambda p:p)
    def test_keyword_template_preserves_prefix_suffix_and_word_clock(self):
        cue=self.cue('现在看电子血压计很好用',tokens=['现在','看','电子','血压计','很','好用'],keyword='电子血压计',mode='template')
        content,ev=self.compile([cue],self.catalog(True))
        segs=next(t for t in content['tracks'] if t['name']=='Subtitles')['segments']
        self.assertEqual(len(segs),3)
        self.assertEqual([x['text']for x in ev],['现在看','电子血压计','很好用'])
        self.assertEqual([x['mode']for x in ev],['base','template_replace','base'])
        self.assertEqual([s['target_timerange']for s in segs],[{'start':1000000,'duration':600000},{'start':1600000,'duration':1000000},{'start':2600000,'duration':600000}])
        template_text=next(m for m in content['materials']['texts']if json.loads(m['content'])['text']=='电子血压计')
        self.assertEqual(template_text['words']['text'],['电子','血压计'])
        self.assertEqual(template_text['words']['start_time'],[0,400])
    def test_long_caption_does_not_cut_keyword(self):
        cue=self.cue('今天给大家介绍一款全新电子血压计',tokens=['今天','给','大家','介绍','一款','全新','电子','血压计'],keyword='电子血压计',mode='border')
        content,ev=self.compile([cue])
        texts=[x['text']for x in ev]
        self.assertEqual(''.join(texts),cue['text'])
        self.assertTrue(any('电子血压计'in text for text in texts))
        self.assertFalse(any(t.endswith('电子')for t in texts))
    def test_observed_long_line_is_not_rejected_by_13_character_cap(self):
        text='面向1945-1980年的朋友演示'
        self.assertTrue(estimate_width(text,self.settings)['fits'])
        content,ev=self.compile([self.cue(text)],settings={**self.settings,'emphasis_probability':0})
        self.assertEqual([e['text']for e in ev],[text])
    def test_template_uses_own_typography_before_base_width(self):
        cue=self.cue('这款方便日常操作的电子测试仪',keyword='这款方便日常操作的电子测试仪',mode='template')
        _,events=self.compile([cue],self.catalog())
        self.assertEqual(len(events),1);self.assertEqual(events[0]['mode'],'template_replace')
    def test_border_keeps_white_fill_and_full_style_coverage(self):
        cue=self.cue('的电子血压计好不好',keyword='电子血压计',mode='border')
        content,_=self.compile([cue]);inner=json.loads(content['materials']['texts'][0]['content'])
        self.assertEqual([s['range']for s in inner['styles']],[[0,1],[1,6],[6,9]])
        self.assertEqual(inner['styles'][1]['fill']['content']['solid']['color'],[1,1,1])
        self.assertGreater(inner['styles'][1]['strokes'][0]['content']['solid']['color'][0],.9)
    def test_price_badge_is_separate_short_phrase(self):
        cue=self.cue('福利价仅需九元',keyword='九元',mode='badge');cue['keywords'][0]['role']='price'
        content,events=self.compile([cue],self.catalog(role='price_badge'))
        self.assertEqual({t['name']for t in content['tracks']},{'Subtitles','PriceBadges'})
        badge=next(e for e in events if e['mode']=='price_badge');self.assertEqual(badge['text'],'九元')
        self.assertLess(badge['end_ms']-badge['start_ms'],cue['end_ms']-cue['start_ms'])
    def test_same_profile_seed_and_spacing_are_reproducible(self):
        cues=[self.cue('便捷操作',keyword='便捷操作',mode='border',start=1000+i*1800)for i in range(12)]
        p=policy(self.settings);a=_select_emphasis(cues,self.settings,random.Random(3),p)
        b=_select_emphasis(cues,self.settings,random.Random(3),p);self.assertEqual(a,b)
        times=[cues[i]['start_ms'] for i in sorted(a)]
        self.assertTrue(all(b-a>=8000 for a,b in zip(times,times[1:])))
        self.assertEqual(profile_for(cues,{'caption_profile':'auto'}),'explainer')
        cues[0]['keywords'][0]['role']='price';self.assertEqual(profile_for(cues,{'caption_profile':'auto'}),'conversion')
    def recipe(self,role='headline'):
        side=role=='side_benefit'
        st=self.style(15 if side else 13);st.pop('range');red=copy.deepcopy(st);red['strokes'][0]['content']['solid']['color']=[1,.094,.216]
        return {'name':'Synthetic native '+role,'roles':[role],'verified':True,'styles':[st,red],'base_style':0,'accent_style':1,
          'clip':{'scale':{'x':.72 if side else 1.15,'y':.72 if side else 1.15},'transform':{'x':.843 if side else 0,'y':.124 if side else .89}},
          'vertical':side,'animations':[{'resource_id':'loop','cache_path':str(self.fx),'animation':{'type':'loop','duration':1000000}}]}
    def test_native_header_side_are_default_and_keep_style_animation(self):
        catalog={'text_styles':[self.recipe(),self.recipe('side_benefit')]}
        item={'start_ms':9000,'duration_ms':10000,'title_candidates':['便捷操作'],'captions':[], 'side_candidates':['仅需9.9元']}
        content={'tracks':[],'materials':{}}
        events=append_layout(content,item,{},self.settings,catalog,self.font,lambda p:p,random.Random(3))
        self.assertEqual([e['component']for e in events],['headline','side_benefit'])
        side=next(m for m in content['materials']['texts']if m['typesetting']==1)
        inner=json.loads(side['content']);self.assertTrue(any(s['strokes'][0]['content']['solid']['color'][0]==1 for s in inner['styles']))
        self.assertEqual(inner['styles'][0]['size'],15)
        self.assertEqual(len(content['materials']['material_animations']),2)
    def test_default_missing_copy_is_explicit_and_opt_out_works(self):
        item={'start_ms':0,'duration_ms':10000,'captions':[]}
        with self.assertRaisesRegex(CaptionError,'headline'):
            append_layout({},item,{},self.settings,{},self.font,lambda p:p,random.Random(1))
        self.assertEqual(append_layout({},item,{'layout':{'headline_enabled':False,'side_enabled':False}},self.settings,{},self.font,lambda p:p,random.Random(1)),[])
    def test_opening_has_independent_multiline_font_and_style(self):
        recipe=self.recipe('opening_hook');recipe['clip']={'scale':{'x':1.245,'y':1.245},'transform':{'x':0,'y':-.3201}}
        for st in recipe['styles']:st['size']=12
        recipe['line_styles']=[1,0,1];recipe['animations']=[]
        cue={'id':'open','text':'开场第一句\n中间一句\n最后一句','start_ms':0,'end_ms':3000,'layout_role':'opening_hook_card','keywords':[]}
        cue=prepare_document({'cues':[cue]},self.settings)['cues'][0]
        content,ev=self.compile([cue],{'text_styles':[recipe]})
        self.assertEqual(ev[0]['mode'],'opening_hook');mat=content['materials']['texts'][0]
        self.assertEqual(json.loads(mat['content'])['styles'][0]['size'],12)
        self.assertAlmostEqual(content['tracks'][0]['segments'][0]['clip']['transform']['y'],-.3201)
    def test_extract_native_style_removes_copy_and_exports_line_selectors(self):
        st=self.style();st['range']=[0,2];st2=copy.deepcopy(st);st2['range']=[2,5]
        doc={'materials':{'texts':[{'id':'m','content':json.dumps({'text':'甲乙\n丙丁','styles':[st,st2]}),'typesetting':0}]},
             'tracks':[{'segments':[{'id':'s','material_id':'m','clip':{'scale':{'x':1,'y':1},'transform':{'x':0,'y':-.32}},'extra_material_refs':[]}]}]}
        path=self.root/'pack.json';extract_text_style(doc,'s',path,role='opening_hook')
        raw=path.read_text(encoding='utf-8');self.assertNotIn('甲乙',raw)
        data=load_component(path);self.assertEqual(data['text_styles'][0]['line_styles'],[0,1])
        self.assertTrue(validate_component(path)['valid']);self.assertFalse(data['text_styles'][0]['verified'])
    def test_speed_and_offset_do_not_rescale_template_words_twice(self):
        cue=self.cue('电子产品',tokens=['电子','产品'],keyword='电子产品',mode='template',start=0)
        doc={'source_id':'v','timestamp_basis':'source_media','cues':[cue]}
        mapped=map_document(doc,{'source_id':'v','start_ms':2000,'duration_ms':400,'source_duration_ms':800,'speed':2})
        _,mats=clone_component(self.component(True),'电子产品',12000,12400,lambda p:p,self.settings,cue=mapped['cues'][0])
        self.assertEqual(mats['texts'][0]['words']['start_time'],[0,200]);self.assertEqual(mats['texts'][0]['words']['end_time'],[200,400])

    def test_shipped_style_binding_is_local_complete_and_never_self_verified(self):
        from preset_resources import bind
        root=Path(__file__).resolve().parents[1]
        definitions=json.loads((root/'presets/general-conversion/native-style-recipes.json').read_text('utf-8'))
        catalog={'fonts':[],'flowers':[],'text_animations':[]}
        for r in definitions['text_styles']:
            for s in r['styles']:
                catalog['fonts'].append({'resource_id':s['font']['id'],'path':str(self.fp)})
                if s.get('effectStyle'):catalog['flowers'].append({'resource_id':s['effectStyle']['id'],'cache_path':str(self.fx)})
            for a in r.get('animations',[]):catalog['text_animations'].append({'resource_id':a['resource_id'],'cache_path':str(self.fx)})
        cp=self.root/'bank.json';cp.write_text(json.dumps(catalog),encoding='utf-8');out=self.root/'bound.json'
        report=bind(cp,out);bound=json.loads(out.read_text('utf-8'))
        self.assertEqual(len(report['bound_recipes']),7);self.assertEqual(report['missing'],[])
        self.assertTrue(all(r['verified'] is False and r['binding_required'] is False for r in bound['text_styles']))
        for a,b in zip(definitions['text_styles'],bound['text_styles']):
            self.assertEqual(a['clip'],b['clip'])
            self.assertEqual([s['size']for s in a['styles']],[s['size']for s in b['styles']])
            self.assertTrue(all(Path(s['font']['path']).is_file()for s in b['styles']))
        catalog['fonts']=[];cp.write_text(json.dumps(catalog),encoding='utf-8')
        self.assertTrue(bind(cp,self.root/'missing.json')['missing'])

    def test_template_missing_or_wrong_words_is_rejected_by_final_validator(self):
        cue=self.cue('新产品',keyword='新产品',mode='template');content,_=self.compile([cue],self.catalog(True))
        child=content['materials']['texts'][0];child['words']={}
        self.assertTrue(any('missing current words'in e for e in validate_caption_content(content)))
        child['words']={'text':['旧产品'],'start_time':[0],'end_time':[600]}
        self.assertTrue(any('differ from current text'in e for e in validate_caption_content(content)))

    def test_native_panel_stays_below_title_and_starts_after_hook(self):
        head=self.recipe();head['panel']={'x':0,'y':.84,'width':.85,'height':.1,'color':'#0023ff'}
        catalog={'text_styles':[head,self.recipe('side_benefit')]}
        item={'start_ms':9000,'duration_ms':10000,'title_candidates':['当前标题'],'side_candidates':['当前利益'],
              'captions':[{'layout_role':'opening_hook_card','start_ms':0,'end_ms':2500}]}
        content={'tracks':[],'materials':{}}
        events=append_layout(content,item,{'layout':{'side':'left'}},self.settings,catalog,self.font,lambda p:p,random.Random(3))
        self.assertTrue(all(e['start_ms']==11500 for e in events))
        tracks={t['name']:t for t in content['tracks']}
        self.assertLess(tracks['Layout:HeaderPanel']['segments'][0]['render_index'],tracks['Layout:Headline']['segments'][0]['render_index'])
        self.assertLess(tracks['Layout:SideBenefit']['segments'][0]['clip']['transform']['x'],0)

    def test_headline_chooses_panel_recipe_and_text_renders_above_panel(self):
        plain=self.recipe();plain['name']='manual-headline-1'
        panel=self.recipe();panel['name']='manual-headline-2';panel['panel']={'x':0,'y':.84,'width':.85,'height':.1,'color':'#b9f5ca'}
        catalog={'text_styles':[plain,panel]}
        item={'start_ms':0,'duration_ms':10000,'title_candidates':['统一标题'],'side_candidates':['利益']}
        content={'tracks':[],'materials':{}}
        append_layout(content,item,{},self.settings,catalog,self.font,lambda p:p,random.Random(10))
        names={t['name']:t for t in content['tracks']}
        self.assertTrue(names['Layout:HeaderPanel']['segments'])
        tracks=content['tracks'];panel_track=names['Layout:HeaderPanel'];tracks.remove(panel_track);tracks.append(panel_track)
        append_layout(content,{**item,'start_ms':10000},{},self.settings,catalog,self.font,lambda p:p,random.Random(11))
        names={t['name']:t for t in content['tracks']}
        self.assertLess(content['tracks'].index(names['Layout:HeaderPanel']),content['tracks'].index(names['Layout:Headline']))
        self.assertTrue(all(s['render_index']<names['Layout:Headline']['segments'][0]['render_index']for s in names['Layout:HeaderPanel']['segments']))
        self.assertGreater(names['Layout:Headline']['segments'][0]['render_index'],names['Layout:HeaderPanel']['segments'][0]['render_index'])
        self.assertEqual(json.loads(next(m for m in content['materials']['texts']if m['id']==names['Layout:Headline']['segments'][0]['material_id'])['content'])['text'],'统一标题')

    def test_base_color_animation_scheme_is_stable_within_composition(self):
        cues=[self.cue('当前口播字幕',start=1000+i*2000)for i in range(6)]
        catalog={'caption_animations':[{'name':n,'resource_id':n,'cache_path':str(self.fx),'verified':True,
                  'role':'caption','animation':{'type':'caption'}} for n in ('逐词显色','下划线')]}
        content,events=self.compile(cues,catalog,{**self.settings,'caption_animation_mode':'auto'})
        colors={tuple(json.loads(m['content'])['styles'][0]['fill']['content']['solid']['color'])for m in content['materials']['texts']}
        self.assertEqual(len(colors),1)
        animations=content['materials'].get('material_animations',[])
        self.assertIn(len(animations),(0,len(cues)))
        self.assertEqual(len({json.dumps(e['base_scheme'],sort_keys=True)for e in events}),1)


if __name__=='__main__':unittest.main()
