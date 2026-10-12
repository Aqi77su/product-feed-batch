"""Resource identity regressions; miniature SFNTs test names, never rendering."""
import copy
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from font_identity import font_names,collect_font_records,resolve_font,merge_fonts,cache_font_id
from preset_resources import bind
from component_tools import referenced_text_animations,extract_text_animation,audit_resources
import batch


def mini_font(path,names,salt=b''):
    records=[];storage=b''
    for kind,name in names:
        raw=name.encode('utf-16-be');records.append(struct.pack('>HHHHHH',3,1,0x804,kind,len(raw),len(storage)));storage+=raw
    table=struct.pack('>HHH',0,len(records),6+12*len(records))+b''.join(records)+storage
    blob=b'\x00\x01\x00\x00'+struct.pack('>HHHH',1,16,0,0)+struct.pack('>4sIII',b'name',0,28,len(table))+table+salt
    Path(path).write_bytes(blob)


class ResourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.font=self.root/('a'*64+'.ttf');mini_font(self.font,[(1,'俪金黑'),(4,'俪金黑 Regular'),(6,'FixtureLijinHei')])
        self.cache=self.root/'effect';self.cache.mkdir();(self.cache/'resource.json').write_text('{}')
    def tearDown(self):self.temp.cleanup()
    def recipe(self,**font):
        return {'schema_version':'1.0','text_styles':[{'name':'demo','roles':['headline'],'styles':[{'size':14,'font':{'id':'6740499317733200388',**font}}],'animations':[],'verified':False}]}
    def run_bind(self,catalog,spec=None):
        cp=self.root/'catalog.json';rp=self.root/'recipe.json';out=self.root/'bound.json'
        cp.write_text(json.dumps(catalog),encoding='utf-8');rp.write_text(json.dumps(spec or self.recipe()),encoding='utf-8')
        result=bind(cp,out,rp);return result,json.loads(out.read_text('utf-8'))
    def test_hashed_font_names_are_read_from_localized_name_table(self):
        names=font_names(self.font)
        self.assertIn('俪金黑',names);self.assertIn('FixtureLijinHei',names)
        self.assertNotIn('Regular',names)
    def test_name_binding_succeeds_without_cloud_id_and_does_not_invent_one(self):
        result,out=self.run_bind({'fonts':[{'path':str(self.font),'name':self.font.stem}]})
        self.assertEqual(result['missing'],[]);self.assertEqual(result['font_bindings'][0]['method'],'exact_font_name')
        bound=out['text_styles'][0];self.assertEqual(bound['styles'][0]['font']['id'],'');self.assertFalse(bound['verified'])
        self.assertTrue(Path(bound['styles'][0]['font']['path']).samefile(self.font))
    def test_name_match_can_use_an_observed_different_id(self):
        result,out=self.run_bind({'fonts':[{'path':str(self.font),'resource_id':'current-observed-font'}]})
        self.assertEqual(out['text_styles'][0]['styles'][0]['font']['id'],'current-observed-font')
        self.assertEqual(result['font_bindings'][0]['requested_id'],'6740499317733200388')
    def test_missing_different_font_does_not_silently_use_available_body_font(self):
        result,out=self.run_bind({'fonts':[{'path':str(self.font)}]},self.recipe(id='7290457161887388219'))
        self.assertTrue(result['missing']);self.assertEqual(out.get('text_styles',[]),[])
        self.assertIn('阳华体',result['missing'][0]['resources'][0]['names'])
    def test_font_id_in_content_json_is_recovered_without_material_uuid(self):
        doc={'materials':{'texts':[{'id':'TEXT-MATERIAL-ID','font_path':str(self.font),'content':json.dumps({'styles':[{'font':{'id':'FONT-CLOUD-ID','path':str(self.font)}}]})}]}}
        records=collect_font_records(doc);self.assertEqual(len(records),1)
        self.assertEqual(records[0]['resource_ids'],['FONT-CLOUD-ID'])
        self.assertNotIn('TEXT-MATERIAL-ID',records[0]['resource_ids'])
    def test_merge_does_not_erase_id_when_file_scan_is_last(self):
        rows=[{'path':str(self.font),'resource_id':'observed'},{'path':str(self.font),'name':'hash-only'}]
        self.assertEqual(merge_fonts(rows)[0]['resource_id'],'observed')
    def test_different_same_named_files_require_explicit_choice(self):
        second=self.root/'other.ttf';mini_font(second,[(1,'俪金黑')],b'different-font-bytes')
        catalog={'fonts':[{'path':str(self.font)},{'path':str(second)}]}
        result,_=self.run_bind(catalog);self.assertIn('Ambiguous',result['missing'][0]['resources'][0]['reason'])
        catalog['font_bindings']={'6740499317733200388':{'path':str(self.font)}}
        result,_=self.run_bind(catalog);self.assertEqual(result['font_bindings'][0]['method'],'explicit_path')
    def test_identical_font_copies_are_not_ambiguous(self):
        second=self.root/'copy.ttf';second.write_bytes(self.font.read_bytes())
        result,_=self.run_bind({'fonts':[{'path':str(self.font)},{'path':str(second)}]})
        self.assertEqual(result['missing'],[])
    def test_resources_recovers_nested_font_id_and_metadata(self):
        root=self.root/'user';directory=self.root/'drafts/demo';directory.mkdir(parents=True)
        doc={'materials':{'texts':[{'id':'TEXT','font_path':str(self.font),'content':json.dumps({'styles':[{'font':{'path':str(self.font),'id':'REAL-ID'}}]})}]}}
        (directory/'draft_info.json').write_text(json.dumps(doc),encoding='utf-8')
        with patch.object(batch,'discover',return_value={'user_data':str(root),'draft_roots':[{'path':str(directory.parent)}]}),patch.dict(os.environ,{'WINDIR':str(self.root/'none')}):
            found=batch.resources()
        self.assertEqual(len(found['fonts']),1);self.assertEqual(found['fonts'][0]['resource_id'],'REAL-ID')
        self.assertIn('俪金黑',found['fonts'][0]['font_names'])
    def animation_doc(self):
        return {'tracks':[{'type':'text','segments':[{'id':'s','material_id':'tpl','extra_material_refs':[]}]}],
          'materials':{'text_templates':[{'id':'tpl','text_info_resources':[{'text_material_id':'txt','extra_material_refs':['a']}]}],
                       'texts':[{'id':'txt','content':json.dumps({'text':'新内容','styles':[{'range':[0,3],'size':14,'font':{'id':'font','path':str(self.font)}}]})}],
                       'material_animations':[{'id':'a','type':'sticker_animation','animations':[{'type':'loop','resource_id':'zoom','path':str(self.cache),'name':'逐字放大'}]}]}}
    def test_text_animation_is_resolved_through_template_child_reference(self):
        doc=self.animation_doc();seg=doc['tracks'][0]['segments'][0]
        evidence=referenced_text_animations(doc,seg)
        self.assertEqual(evidence[0]['container_type'],'sticker_animation')
        self.assertEqual(evidence[0]['animation']['type'],'loop')
        result=extract_text_animation(doc,'s',self.root/'animation.json')
        self.assertFalse(result['verified'])
    def test_audit_records_source_hash_and_reference_chain_without_copy(self):
        p=self.root/'draft.json';p.write_text(json.dumps(self.animation_doc()),encoding='utf-8')
        out=self.root/'audit.json';result=audit_resources(p,out);report=json.loads(out.read_text('utf-8'))
        self.assertEqual(len(result['source_sha256']),64)
        self.assertEqual(report['text_animation_references'][0]['via_material_id'],'a')
        self.assertNotIn('新内容',out.read_text('utf-8'))
    def test_effect_category_cannot_masquerade_as_text_animation(self):
        spec=self.recipe();spec['text_styles'][0]['animations']=[{'resource_id':'same-id','animation':{'type':'loop'}}]
        result,_=self.run_bind({'fonts':[{'path':str(self.font)}],'flowers':[{'resource_id':'same-id','cache_path':str(self.cache)}]},spec)
        self.assertTrue(any(x['kind']=='text_animation'for x in result['missing'][0]['resources']))


    def test_cache_path_id_is_immediate_directory_only(self):
        self.assertEqual(cache_font_id(r'C:\User Data\Cache\effect\7410300778990015027\hash\font.ttf'),'7410300778990015027')
        self.assertEqual(cache_font_id('D:/Cache/effect/not-an-id/7410300778990015027/font.ttf'),'')
        self.assertEqual(cache_font_id('D:/materials/7410300778990015027.ttf'),'')

    def test_discovery_and_raw_cache_bind_confirmed_reference_recipe(self):
        user=self.root/'user';font=user/'Cache/effect/7410300778990015027/hash/三极榜楷简体.ttf'
        font.parent.mkdir(parents=True);mini_font(font,[(1,'三极榜楷简体')])
        fx=user/'Cache/artistEffect/7543157780769803582';fx.mkdir(parents=True);(fx/'data').write_text('fixture')
        animation=user/'Cache/effect/6908592686781960717';animation.mkdir(parents=True);(animation/'data').write_text('fixture')
        with patch.object(batch,'discover',return_value={'user_data':str(user),'draft_roots':[]}),patch.dict(os.environ,{'WINDIR':str(self.root/'none')}):catalog=batch.resources()
        self.assertEqual(catalog['fonts'][0]['resource_id'],'7410300778990015027')
        self.assertEqual(catalog['fonts'][0]['identity_evidence'][0]['kind'],'cache_directory_id')
        spec=self.recipe(id='7410300778990015027');r=spec['text_styles'][0]
        r['styles'][0]['effectStyle']={'id':'7543157780769803582'}
        r['animations']=[{'resource_id':'6908592686781960717','animation':{'type':'loop'}}]
        result,out=self.run_bind(catalog,spec)
        self.assertEqual(result['missing'],[])
        self.assertEqual(result['font_bindings'][0]['method'],'observed_resource_id')
        self.assertFalse(out['text_styles'][0]['verified'])

    def test_fonts_are_scanned_even_after_twenty_thousand_effect_entries(self):
        user=self.root/'user';base=user/'Cache/effect';base.mkdir(parents=True)
        font=base/'7290457161887388219/阳华体.otf';font.parent.mkdir();mini_font(font,[(1,'阳华体')])
        original_walk=os.walk
        def walk(root,*args,**kwargs):
            if Path(root)==base:
                yield str(base),[],['noise'+str(i)+'.json'for i in range(20002)]
            yield from original_walk(root,*args,**kwargs)
        with patch.object(batch,'discover',return_value={'user_data':str(user),'draft_roots':[]}),patch.dict(os.environ,{'WINDIR':str(self.root/'none')}),patch('os.walk',side_effect=walk):
            catalog=batch.resources()
        self.assertEqual(catalog['fonts'][0]['resource_id'],'7290457161887388219')
        self.assertEqual(catalog['scan_errors'],[])
        self.assertIsNone(catalog['font_scan'][0]['entry_limit'])
        row=next(r for r in catalog['preset_font_inventory']if r['resource_id']=='7290457161887388219')
        self.assertTrue(row['found_paths']);self.assertTrue(row['cache_directories'])

    def test_all_reference_font_dirs_direct_and_nested_are_collected(self):
        user=self.root/'user';fonts={'7410300778990015027':'三极榜楷简体','7290457161887388219':'阳华体','6740499317733200388':'俪金黑','7035914068053463565':'喜鹊招牌体'}
        for i,(rid,name)in enumerate(fonts.items()):
            p=user/'Cache/effect'/rid/('hash/'+name+'.ttf'if i%2 else name+'.otf');p.parent.mkdir(parents=True);mini_font(p,[(1,name)])
        with patch.object(batch,'discover',return_value={'user_data':str(user),'draft_roots':[]}),patch.dict(os.environ,{'WINDIR':str(self.root/'none')}):catalog=batch.resources()
        self.assertEqual({f['resource_id']for f in catalog['fonts']},set(fonts));self.assertEqual(len(catalog['fonts']),4)

    def test_discovery_binds_all_seven_recipes_and_reference_only_entries_validate(self):
        user=self.root/'user';root=Path(__file__).resolve().parents[1]
        spec=json.loads((root/'presets/general-conversion/native-style-recipes.json').read_text('utf-8'))
        for recipe in spec['text_styles']:
            for style in recipe['styles']:
                rid=style['font']['id'];p=user/'Cache/effect'/rid/'font.ttf';p.parent.mkdir(parents=True,exist_ok=True)
                if not p.exists():mini_font(p,[(1,'Fixture-'+rid)])
                if style.get('effectStyle'):
                    p=user/'Cache/artistEffect'/style['effectStyle']['id'];p.mkdir(parents=True,exist_ok=True);(p/'data').write_text('fixture')
            for a in recipe.get('animations',[]):
                p=user/'Cache/effect'/a['resource_id'];p.mkdir(parents=True,exist_ok=True);(p/'data').write_text('fixture')
        with patch.object(batch,'discover',return_value={'user_data':str(user),'draft_roots':[]}),patch.dict(os.environ,{'WINDIR':str(self.root/'none')}):catalog=batch.resources()
        self.assertEqual(len(catalog['flowers']),6);self.assertEqual(len(catalog['text_animations']),1)
        cp=self.root/'all.json';out=self.root/'all-bound.json';cp.write_text(json.dumps(catalog),encoding='utf-8')
        report=bind(cp,out);bound=json.loads(out.read_text('utf-8'))
        self.assertEqual(len(report['bound_recipes']),7);self.assertEqual(report['missing'],[])
        bound['verified_jianying_version']='fixture'
        for r in bound['text_styles']:r['verified']=True
        batch.verify_catalog(bound)  # Bare discovered flowers are not executable styles.

    def test_unverified_execution_is_explicit_and_does_not_mutate_source_catalog(self):
        inv={'errors':[],'settings':{'music_enabled':False},'catalog':{'verified_jianying_version':None,
             'text_styles':[{'name':'recipe','verified':False}],'flowers':[{'binding_only':True,'verified':False}]},'main':[],'warnings':[]}
        with patch.object(batch,'inventory',return_value=copy.deepcopy(inv)):
            with self.assertRaises(ValueError):batch.validate_job({})
        with patch.object(batch,'inventory',return_value=copy.deepcopy(inv)):
            result=batch.validate_job({'allow_unverified':True})
        self.assertFalse(inv['catalog']['text_styles'][0]['verified'])
        self.assertEqual(result['catalog']['text_styles'][0]['verification_basis'],'explicit_unverified_execution')
        self.assertFalse(result['catalog']['flowers'][0]['verified'])
        self.assertTrue(any('preview not verified'in w for w in result['warnings']))

if __name__=='__main__':unittest.main()
