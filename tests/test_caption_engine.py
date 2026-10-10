"""Synthetic fixtures only: never publish a customer's text or cache paths."""
import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from caption_engine import (CaptionError, append_captions, clean_text, clone_component,
                            prepare_document, validate_caption_content, validate_requested_resources)
from component_tools import (extract_component, extract_flower, extract_caption_animation,
                             load_component, validate_component)


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.font_path=self.root/'synthetic.ttf';self.font_path.write_bytes(b'synthetic-font-fixture')
        self.cache=self.root/'effect';self.cache.mkdir();(self.cache/'resource.json').write_text('{}','utf-8')
        self.font={'path':str(self.font_path),'resource_id':'font-resource','available':True}
        self.settings={'emphasis_probability':1,'template_probability':0,'subtitle_max_cjk_chars':13,
                       'base_font_size':14,'subtitle_scale':1.15,'caption_animation_mode':'auto','inline_emphasis_mode':'border'}
    def tearDown(self):self.temp.cleanup()
    def cue(self,text='这个产品很好',words=False):
        c={'id':'c1','text':text,'start_ms':1000,'end_ms':4000,'keywords':[]}
        if words:
            unit=3000/len(text);c['words']=[{'text':ch,'startMs':1000+i*unit,'endMs':1000+(i+1)*unit}for i,ch in enumerate(text)]
        return c
    def keyword(self,cue,text):
        a=cue['text'].index(text);cue['keywords']=[{'text':text,'char_start':a,'char_end':a+len(text),
            'start_ms':cue['start_ms'],'end_ms':cue['end_ms'],'timing_precision':'cue'}]
        return cue
    def document(self,cues):return {'status':'corrected','source_id':'source','cues':cues}
    def prepared(self,cues,settings=None):return prepare_document(self.document(cues),settings or self.settings,self.font)
    def content(self):return {'tracks':[],'materials':{}}
    def component(self,text='{{TEXT}}'):
        style={'range':[0,len(text)],'size':22,'font':{'path':str(self.font_path),'id':'font-resource'},
               'fill':{'content':{'render_type':'gradient','gradient':{'color':[[1,0,0],[1,1,0]]}}},
               'effectStyle':{'id':'effect-resource','path':str(self.cache)}}
        return {'schema_version':'1.0','segment':{'id':'SEGOLD','material_id':'TPL',
            'target_timerange':{'start':5000000,'duration':2000000},'extra_material_refs':['ANIM','FX'],
            'clip':{'scale':{'x':.7,'y':.7},'transform':{'x':.1,'y':.2}}},
            'materials':{'text_templates':[{'id':'TPL','type':'text_template','name':'Synthetic template',
                'path':str(self.cache),'text_info_resources':[{'id':'CHILD','text_material_id':'TXT',
                'attach_info':{'duration':2000000,'clip':{'scale':{'x':1.6,'y':1.6},'transform':{'x':2,'y':3}}},
                'extra_material_refs':['FX','ANIM']}],'non_text_info_resources':[{'type':'sticker',
                'attach_info':{'duration':2000000},'extra_material_refs':['FX']}]}],
                'texts':[{'id':'TXT','type':'text','content':json.dumps({'text':text,'styles':[style]},ensure_ascii=False)}],
                'effects':[{'id':'FX','type':'text_effect','effect_id':'effect-resource','path':str(self.cache)}],
                'material_animations':[{'id':'ANIM','type':'sticker_animation','animations':[{'type':'in','duration':500000,'path':str(self.cache),'resource_id':'animation-resource'}]}]},
            'text_slots':[{'material_id':'TXT','role':'primary'}]}
    def test_punctuation_keeps_numeric_meaning_and_remaps_keyword(self):
        c=self.keyword(self.cue('现在，9.9元！50-90岁，30%优惠。'),'9.9元')
        p=self.prepared([c],{**self.settings,'subtitle_max_cjk_chars':99,'subtitle_max_width_ratio':3})
        got=p['cues'][0]
        self.assertEqual(got['text'],'现在9.9元50-90岁30%优惠')
        self.assertEqual((got['keywords'][0]['char_start'],got['keywords'][0]['char_end']),(2,6))
        self.assertIn('，',c['text']);self.assertFalse(got['words'])
    def test_short_sentence_stays_single_line(self):
        p=self.prepared([self.cue('五个字\n够大')]);self.assertEqual(len(p['cues']),1);self.assertNotIn('\n',p['cues'][0]['text'])
    def test_cleaning_keeps_english_words_and_signed_numbers(self):
        self.assertEqual(clean_text('Hello, world!')[0],'Hello world')
        self.assertEqual(clean_text('-10到.5')[0],'-10到.5')
        self.assertEqual(clean_text('温度 -10')[0],'温度-10')
    def test_opening_card_preserves_explicit_semantic_lines_and_style_coverage(self):
        c=self.keyword(self.cue('朋友们看这里\n操作简单又方便'),'操作简单又方便');c['layout_role']='opening_hook_card'
        p=self.prepared([c]);self.assertEqual(p['cues'][0]['text'],c['text']);self.assertEqual(len(p['cues']),1)
        out=self.content();e=append_captions(out,p['cues'],{**self.settings,'template_probability':1},{},self.font,lambda p:p,random.Random(1))
        self.assertEqual(e[0]['layout_role'],'opening_hook_card');self.assertEqual(validate_caption_content(out),[])
        self.assertEqual(out['materials']['texts'][0]['line_spacing'],.27)
    def test_opening_card_overlong_line_requires_explicit_rewrite(self):
        c=self.cue('这个第一行长得超过了整个安全区域所以需要重写\n第二行');c['layout_role']='opening_hook_card'
        with self.assertRaisesRegex(CaptionError,'opening hook line'):
            self.prepared([c])
    def test_word_split_uses_real_boundaries_and_aliases(self):
        c=self.cue('这个产品操作方便老人自己在家也能够轻松使用',True)
        p=self.prepared([c]);self.assertGreater(len(p['cues']),1)
        self.assertEqual(''.join(x['text']for x in p['cues']),c['text'])
        self.assertEqual(p['cue_aliases']['c1'],[x['id']for x in p['cues']])
        self.assertTrue(all(x['parent_cue_id']=='c1'and x['timing_precision']=='word'for x in p['cues']))
        self.assertEqual(p['cues'][0]['end_ms'],p['cues'][1]['start_ms'])
        self.assertEqual(p['cues'][1]['words'][0]['start_ms'],p['cues'][1]['start_ms'])
    def test_no_word_long_cue_fails_closed(self):
        with self.assertRaisesRegex(CaptionError,'lacks reliable words'):
            self.prepared([self.cue('这个产品操作方便老人自己在家也能够轻松使用')])
    def test_equal_split_is_explicitly_approximate(self):
        p=self.prepared([self.cue('这个产品操作方便老人自己在家也能够轻松使用')],{**self.settings,'subtitle_long_cue_policy':'approximate_equal'})
        self.assertTrue(all(c['approximate_timing']and c['timing_precision']=='cue'and not c['words']for c in p['cues']))
        self.assertTrue(any('approximate' in w for w in p['warnings']))
    def test_mismatched_words_do_not_claim_precision(self):
        c=self.cue('正确内容',True);c['words'][0]['text']='错'
        p=self.prepared([c]);self.assertFalse(p['cues'][0]['words']);self.assertTrue(p['warnings'])
    def test_inline_keyword_preserves_full_style_and_one_body(self):
        c=self.keyword(self.cue(),'产品');p=self.prepared([c]);out=self.content()
        ev=append_captions(out,p['cues'],self.settings,{},self.font,lambda p:p,random.Random(1),offset_ms=5000)
        self.assertEqual(len(out['tracks'][0]['segments']),1)
        mat=out['materials']['texts'][0];inner=json.loads(mat['content'])
        self.assertEqual([s['range']for s in inner['styles']],[[0,2],[2,4],[4,6]])
        self.assertTrue(all(s['font']['path']==str(self.font_path)for s in inner['styles']))
        self.assertEqual(validate_caption_content(out),[]);self.assertEqual(ev[0]['start_ms'],6000)
    def test_non_bmp_characters_fail_until_native_indexing_is_verified(self):
        with self.assertRaisesRegex(CaptionError,'Non-BMP'):
            self.prepared([self.keyword(self.cue('产品😀很好'),'很好')])
    def test_native_caption_animation_retains_real_relative_words(self):
        p=self.prepared([self.cue(words=True)]);out=self.content()
        cat={'caption_animations':[{'name':'Synthetic caption','verified':True,'resource_id':'anim-1','cache_path':str(self.cache)}]}
        e=append_captions(out,p['cues'],self.settings,cat,self.font,lambda p:p,random.Random(1),5000)
        words=out['materials']['texts'][0]['words'];self.assertEqual(words['start_time'][0],0);self.assertEqual(words['end_time'][-1],3000)
        self.assertEqual(out['materials']['material_animations'][0]['animations'][0]['type'],'caption')
        self.assertEqual(e[0]['caption_animation'],'Synthetic caption')
    def test_accelerated_word_times_round_without_false_segment_overflow(self):
        p=self.prepared([self.cue(words=True)])
        for c in p['cues']:
            c['start_ms']/=1.1;c['end_ms']/=1.1
            for w in c['words']:w['start_ms']/=1.1;w['end_ms']/=1.1
        out=self.content();cat={'caption_animations':[{'verified':True,'resource_id':'anim','cache_path':str(self.cache)}]}
        append_captions(out,p['cues'],self.settings,cat,self.font,lambda p:p,random.Random(1))
        self.assertEqual(validate_caption_content(out),[])
    def test_cue_precision_skips_native_word_animation(self):
        p=self.prepared([self.cue()]);out=self.content();cat={'caption_animations':[{'verified':True,'resource_id':'a','cache_path':str(self.cache)}]}
        e=append_captions(out,p['cues'],self.settings,cat,self.font,lambda p:p,random.Random(1))
        self.assertFalse(out['materials'].get('material_animations'));self.assertTrue(e[0]['warnings'])
    def test_template_replaces_body_and_clones_dependencies(self):
        comp=self.component();path=self.root/'component.json';path.write_text(json.dumps(comp),'utf-8')
        c=self.keyword(self.cue(),'产品');p=self.prepared([c]);out=self.content()
        cat={'text_templates':[{'name':'Synthetic','verified':True,'component_path':str(path),'role':'caption'}]}
        e=append_captions(out,p['cues'],{**self.settings,'template_probability':1},cat,self.font,lambda p:p,random.Random(1))
        self.assertEqual(e[0]['mode'],'template_replace');self.assertEqual(len(out['tracks'][0]['segments']),1)
        self.assertNotEqual(out['tracks'][0]['segments'][0]['material_id'],'TPL')
        self.assertEqual(len(out['materials']['texts']),1);self.assertEqual(json.loads(out['materials']['texts'][0]['content'])['text'],c['text'])
        tpl=out['materials']['text_templates'][0]
        self.assertEqual(tpl['text_info_resources'][0]['attach_info']['duration'],3000000)
        self.assertEqual(tpl['text_info_resources'][0]['attach_info']['clip']['scale']['x'],1.6)
        self.assertEqual(validate_caption_content(out),[]);self.assertEqual(comp['segment']['material_id'],'TPL')
    def test_template_adjusts_whole_component_and_rejects_tiny_text(self):
        comp=self.component();path=self.root/'wide.json';path.write_text(json.dumps(comp),'utf-8')
        c=self.keyword(self.cue('这个产品操作简单方便又实用'),'产品');p=self.prepared([c])
        cat={'text_templates':[{'verified':True,'role':'caption','component_path':str(path),'max_chars':13}]}
        out=self.content();events=append_captions(out,p['cues'],{**self.settings,'template_probability':1},cat,self.font,lambda p:p,random.Random(1))
        self.assertLess(events[0]['template_layout_estimate']['outer_scale_adjustment'],1)
        self.assertEqual(out['materials']['text_templates'][0]['text_info_resources'][0]['attach_info']['clip']['scale']['x'],1.6)
        inner=json.loads(comp['materials']['texts'][0]['content']);inner['styles'][0]['size']=3;comp['materials']['texts'][0]['content']=json.dumps(inner);path.write_text(json.dumps(comp),'utf-8')
        with self.assertRaisesRegex(CaptionError,'smaller than base'):
            append_captions(self.content(),p['cues'],{**self.settings,'template_probability':1},cat,self.font,lambda p:p,random.Random(1))
    def test_missing_template_resource_errors_or_explicit_plain(self):
        comp=self.component();comp['materials']['text_templates'][0]['path']=str(self.root/'missing')
        path=self.root/'component.json';path.write_text(json.dumps(comp),'utf-8')
        c=self.keyword(self.cue(),'产品');p=self.prepared([c]);cat={'text_templates':[{'verified':True,'component_path':str(path),'role':'caption'}]}
        with self.assertRaises(CaptionError):append_captions(self.content(),p['cues'],{**self.settings,'template_probability':1},cat,self.font,lambda p:p,random.Random(1))
        e=append_captions(self.content(),p['cues'],{**self.settings,'template_probability':1,'missing_effect_policy':'plain'},cat,self.font,lambda p:p,random.Random(1))
        self.assertTrue(any('fallback' in w for w in e[0]['warnings']))
    def test_effect_id_alone_rejected(self):
        p=self.prepared([self.keyword(self.cue(),'产品')]);cat={'flowers':[{'verified':True,'effect_id':'fx','cache_path':str(self.cache)}]}
        with self.assertRaisesRegex(CaptionError,'exported style'):
            append_captions(self.content(),p['cues'],{**self.settings,'inline_emphasis_mode':'flower'},cat,self.font,lambda p:p,random.Random(1))
    def test_required_native_animation_does_not_silently_disappear(self):
        p=self.prepared([self.cue(words=True)])
        with self.assertRaisesRegex(CaptionError,'No verified native caption'):
            append_captions(self.content(),p['cues'],self.settings,{},self.font,lambda p:p,random.Random(1))
        events=append_captions(self.content(),p['cues'],{**self.settings,'missing_effect_policy':'plain'},{},self.font,lambda p:p,random.Random(1))
        self.assertTrue(any('fallback'in w for w in events[0]['warnings']))
    def test_resource_preflight_fails_even_when_seed_would_skip_emphasis(self):
        p=self.prepared([self.keyword(self.cue(),'产品')])
        settings={**self.settings,'emphasis_probability':.65,'inline_emphasis_mode':'flower'}
        for seed in (0,1,2,3):
            rng=random.Random(seed);before=rng.getstate();content=self.content()
            with self.assertRaisesRegex(CaptionError,'No verified inline flower'):
                append_captions(content,p['cues'],settings,{},self.font,lambda p:p,rng)
            self.assertEqual(rng.getstate(),before);self.assertEqual(content,self.content())
    def test_every_selectable_animation_is_preflighted(self):
        p=self.prepared([self.cue(words=True)])
        catalog={'caption_animations':[{'verified':True,'resource_id':'good','cache_path':str(self.cache)},
            {'verified':True,'resource_id':'bad','cache_path':str(self.root/'absent-animation')}]}
        with self.assertRaisesRegex(CaptionError,'Invalid selectable caption animation bad'):
            validate_requested_resources(p['cues'],self.settings,catalog,self.font,lambda p:p)
    def test_every_selectable_flower_is_preflighted(self):
        style=json.loads(self.component()['materials']['texts'][0]['content'])['styles'][0]
        catalog={'flowers':[{'name':'good','verified':True,'effect_id':'fx1','cache_path':str(self.cache),'style':style},
            {'name':'bad','verified':True,'effect_id':'fx2','cache_path':str(self.root/'absent-flower'),'style':style}]}
        p=self.prepared([self.keyword(self.cue(),'产品')])
        with self.assertRaisesRegex(CaptionError,'Invalid selectable flower bad'):
            validate_requested_resources(p['cues'],{**self.settings,'inline_emphasis_mode':'flower'},catalog,self.font,lambda p:p)
    def test_explicitly_disabled_emphasis_does_not_require_effect_resources(self):
        p=self.prepared([self.keyword(self.cue(),'产品')]);settings={**self.settings,'emphasis_probability':0,'template_probability':1,'inline_emphasis_mode':'flower','caption_animation_mode':'none'}
        events=append_captions(self.content(),p['cues'],settings,{},self.font,lambda p:p,random.Random(0))
        self.assertEqual(events[0]['mode'],'base');self.assertFalse(events[0]['keyword_events'])
    def test_every_selectable_caption_component_is_preflighted(self):
        component=self.root/'good-template.json';component.write_text(json.dumps(self.component()),'utf-8')
        p=self.prepared([self.keyword(self.cue(),'产品')]);catalog={'text_templates':[
            {'verified':True,'role':'caption','name':'good','component_path':str(component)},
            {'verified':True,'role':'caption','name':'bad','component_path':str(self.root/'missing-template.json')}]}
        with self.assertRaisesRegex(CaptionError,'Invalid selectable caption component bad'):
            validate_requested_resources(p['cues'],{**self.settings,'template_probability':.01},catalog,self.font,lambda p:p)
    def test_preflight_plain_policy_reports_all_bad_pool_candidates(self):
        p=self.prepared([self.cue(words=True)]);catalog={'caption_animations':[
            {'verified':True,'resource_id':'bad-one','cache_path':str(self.root/'absent-one')},
            {'verified':True,'resource_id':'bad-two','cache_path':str(self.root/'absent-two')}]}
        warnings=validate_requested_resources(p['cues'],{**self.settings,'missing_effect_policy':'plain'},catalog,self.font,lambda p:p)
        self.assertEqual(len([w for w in warnings if'Invalid selectable'in w]),2)
    def test_title_template_cannot_be_used_as_caption(self):
        c=self.keyword(self.cue(),'产品');p=self.prepared([c]);cat={'text_templates':[{'name':'Title only','role':'title','verified':True,'component_path':'unused.json'}]}
        with self.assertRaisesRegex(CaptionError,'caption-role'):
            append_captions(self.content(),p['cues'],{**self.settings,'template_probability':1},cat,self.font,lambda p:p,random.Random(1))
    def test_extract_sanitizes_and_portably_copies_resources(self):
        comp=self.component('样例旧文案');draft={'tracks':[{'segments':[comp['segment']]}],'materials':comp['materials']}
        out=self.root/'portable'/'component.json'
        result=extract_component(draft,'SEGOLD',out);self.assertFalse(result['verified'])
        text=out.read_text('utf-8');self.assertNotIn('样例旧文案',text);self.assertNotIn(str(self.font_path),text)
        self.assertTrue(validate_component(out)['valid']);self.assertTrue(load_component(out)['materials']['texts'][0]['content'])
    def test_complex_child_labels_are_rejected(self):
        comp=self.component('标题内容');second=copy.deepcopy(comp['materials']['texts'][0]);second['id']='TXT2'
        inner=json.loads(second['content']);inner['text']='另一个标签';inner['styles'][0]['range']=[0,6];second['content']=json.dumps(inner)
        comp['materials']['texts'].append(second);comp['materials']['text_templates'][0]['text_info_resources'].append({'text_material_id':'TXT2'})
        with self.assertRaisesRegex(CaptionError,'different historical text'):
            extract_component({'tracks':[{'segments':[comp['segment']]}],'materials':comp['materials']},'SEGOLD',self.root/'x.json')
    def test_flower_export_load_and_compile_without_original_copy(self):
        comp=self.component('旧产品名称');draft={'tracks':[{'segments':[comp['segment']]}],'materials':comp['materials']}
        out=self.root/'flower'/'catalog.json';extract_flower(draft,'SEGOLD',out)
        self.assertNotIn('旧产品名称',out.read_text('utf-8'));self.assertTrue(validate_component(out)['valid'])
        cat=load_component(out);self.assertFalse(cat['flowers'][0]['verified']);cat['flowers'][0]['verified']=True
        p=self.prepared([self.keyword(self.cue(),'产品')]);content=self.content()
        events=append_captions(content,p['cues'],{**self.settings,'inline_emphasis_mode':'flower'},cat,self.font,lambda p:p,random.Random(1))
        self.assertEqual(events[0]['keyword_events'][0]['kind'],'inline_flower')
        self.assertEqual(len(content['tracks'][0]['segments']),1);self.assertEqual(validate_caption_content(content),[])
    def test_native_animation_export_preserves_type_and_requires_unique_choice(self):
        comp=self.component();comp['materials']['material_animations'][0]['animations'][0]['type']='caption'
        draft={'tracks':[{'segments':[comp['segment']]}],'materials':comp['materials']}
        out=self.root/'caption'/'catalog.json';extract_caption_animation(draft,'SEGOLD',out)
        cat=load_component(out);self.assertFalse(cat['caption_animations'][0]['verified']);self.assertTrue(validate_component(out)['valid'])
        draft['materials']['material_animations'][0]['animations'].append(copy.deepcopy(draft['materials']['material_animations'][0]['animations'][0]))
        with self.assertRaisesRegex(CaptionError,'multiple native'):
            extract_caption_animation(draft,'SEGOLD',self.root/'ambiguous.json')
    def test_intro_cannot_masquerade_as_caption_animation(self):
        p=self.prepared([self.cue(words=True)]);cat={'caption_animations':[{'name':'Wrong type','verified':True,'resource_id':'x','cache_path':str(self.cache),'animation':{'type':'in'}}]}
        with self.assertRaisesRegex(CaptionError,'cannot be relabeled'):
            append_captions(self.content(),p['cues'],self.settings,cat,self.font,lambda p:p,random.Random(1))
    def test_duplicate_caption_bodies_and_gaps_are_detected(self):
        p=self.prepared([self.cue()]);out=self.content();append_captions(out,p['cues'],self.settings,{},self.font,lambda p:p,random.Random(1))
        out['tracks'][0]['segments'].append(copy.deepcopy(out['tracks'][0]['segments'][0]))
        inner=json.loads(out['materials']['texts'][0]['content']);inner['styles'][0]['range']=[2,6];out['materials']['texts'][0]['content']=json.dumps(inner)
        errors=validate_caption_content(out);self.assertTrue(any('simultaneous' in e for e in errors));self.assertTrue(any('coverage'in e for e in errors))


if __name__=='__main__':unittest.main()
