import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import batch


class RuntimeContractTests(unittest.TestCase):
    def test_builtin_preset_loads_real_settings_and_respects_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'job.json'
            path.write_text(json.dumps({'settings':{'music_volume':.2,'width':720,'height':1280}}),encoding='utf-8')
            job=batch.load_job(path);settings=batch.job_settings(job)
            self.assertEqual(job['preset_name'],'通用口播转化风格')
            self.assertEqual(settings['broll_mode'],'semantic')
            self.assertEqual(settings['main_speed'],1.1)
            self.assertEqual(settings['music_volume'],.2)
            self.assertEqual(settings['base_font_size'],13)
            self.assertEqual(settings['subtitle_y'],-.390625)
            path.write_text(json.dumps({'settings':{'width':720,'height':1280,'base_font_size':12}}),encoding='utf-8')
            self.assertEqual(batch.job_settings(batch.load_job(path))['base_font_size'],12)

    def test_nested_catalog_paths_are_portable(self):
        original={'flowers':[{'cache_path':'fx','style':{'font':{'path':'fonts/body.ttf'}}}]}
        resolved=batch.resolve_catalog_paths(original,Path.cwd())
        self.assertEqual(resolved['flowers'][0]['cache_path'],str((Path.cwd()/'fx').resolve()))
        self.assertTrue(Path(resolved['flowers'][0]['style']['font']['path']).is_absolute())
        self.assertEqual(original['flowers'][0]['cache_path'],'fx')

    def test_cache_directory_does_not_define_audio_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/'user/Cache/music';cache.mkdir(parents=True)
            files={k:cache/(k+'.wav') for k in ('music','sound','unknown')}
            for path in files.values():path.write_bytes(b'fixture')
            draft=root/'drafts/demo';draft.mkdir(parents=True)
            data={'materials':{'audios':[{'type':k,'name':k,'path':str(files[k])} for k in ('music','sound')]}}
            (draft/'draft_info.json').write_text(json.dumps(data),encoding='utf-8')
            discovery={'user_data':str(root/'user'),'draft_roots':[{'path':str(root/'drafts')}]}
            with patch.object(batch,'discover',return_value=discovery),patch.dict(os.environ,{'WINDIR':str(root/'no-system-fonts')}):
                found=batch.resources()
            self.assertEqual([x['name'] for x in found['music']],['music'])
            self.assertEqual([x['name'] for x in found['sfx']],['sound'])
            self.assertEqual([x['name'] for x in found['audio_candidates']],['unknown'])

    def test_empty_music_is_blocked_unless_explicitly_disabled(self):
        inv={'errors':[],'settings':{'music_enabled':True},'catalog':{},'main':[]}
        with patch.object(batch,'inventory',return_value=copy.deepcopy(inv)):
            with self.assertRaisesRegex(ValueError,'BGM requested'):batch.validate_job({})
        inv['settings']['music_enabled']=False
        with patch.object(batch,'inventory',return_value=copy.deepcopy(inv)):
            self.assertIn('documents',batch.validate_job({}))

    def test_single_source_keeps_script_group_for_layout(self):
        source={'id':'main_example','path':'not-read.mp4','duration_ms':11000}
        doc={'source_id':'main_example','script_id':'script_group','timestamp_basis':'source_media',
             'cues':[{'id':'c1','start_ms':0,'end_ms':1100,'text':'操作简单','words':[],'keywords':[]}],
             'broll_opportunities':[],'title_candidates':[]}
        inv={'settings':batch.job_settings({'settings':{'music_enabled':False}}),
             'catalog':{'fonts':[{'path':'not-read.ttf'}]},'main':[source],'broll':[],
             'distribution':{'main_example':1},'documents':{'main_example':doc}}
        item=batch.plan(inv)['items'][0]
        self.assertEqual(item['script_id'],'script_group')
        self.assertEqual(item['duration_ms'],10000)
        self.assertEqual(item['captions'][0]['end_ms'],1000)

    def test_out_of_canvas_caption_and_non_boolean_music_rejected(self):
        with self.assertRaisesRegex(ValueError,'safe canvas'):
            batch.job_settings({'settings':{'subtitle_y':-1.2}})
        with self.assertRaisesRegex(ValueError,'music_enabled'):
            batch.job_settings({'settings':{'music_enabled':'false'}})


if __name__=='__main__':unittest.main()
