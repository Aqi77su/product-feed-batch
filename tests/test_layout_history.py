import copy
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from layout_engine import append_layout, resolve_overlays, fit_text
from history_style import analyze_document, analyze, create, _suggest


def draft():
    return {"tracks": [], "materials": {"texts": [], "speeds": []}, "canvas_config": {"width": 1080, "height": 1920}}


ITEM = {"number": 1, "script_id": "s1", "source_id": "v1", "start_ms": 7000, "duration_ms": 12000}
SETTINGS = {"width": 1080, "height": 1920, "subtitle_border_width": 40}


class LayoutTests(unittest.TestCase):
    def append(self, content, job, item=None, seed=3, font=None):
        return append_layout(content, item or ITEM, job, SETTINGS, {}, font, lambda p: "staged/"+Path(p).name, random.Random(seed))

    def test_no_copy_no_invented_components(self):
        content = draft()
        self.append(content, {})
        self.assertEqual(content["materials"]["texts"], [])
        self.assertNotIn("shapes", content["materials"])

    def test_disabled_layout_preserves_explicit_legacy_notice(self):
        content = draft()
        self.append(content, {"title": "不自动显示", "layout": {"enabled": False},
            "overlays": [{"kind": "text", "text": "说明：按原文保留。"}]})
        self.assertEqual([json.loads(t["content"])["text"] for t in content["materials"]["texts"]], ["说明：按原文保留。"])

    def test_native_panel_vertical_and_formal_text(self):
        content = draft()
        notice = "说明：请阅读\n型号：A-1，售价9.9元。"
        job = {"layout": {"bundle": "header_panel", "headline": "示例产品介绍", "subheadline": "了解使用方法",
                          "side_benefit": {"text": "便捷操作", "side": "left"}, "disclosure": notice}}
        events = self.append(content, job, font={"path": "demo.ttf", "resource_id": "font-1"})
        self.assertEqual(len(content["materials"]["shapes"]), 1)
        shape = content["materials"]["shapes"][0]
        self.assertEqual(shape["type"], "shape")
        self.assertEqual(shape["shape_type"], 4)
        self.assertEqual(len(shape["custom_points"]), 8)
        self.assertEqual(shape["name"], "rect_item")
        native = {json.loads(t["content"])["text"]: t for t in content["materials"]["texts"]}
        self.assertEqual(native["便捷操作"]["typesetting"], 1)
        self.assertEqual(native["便捷操作"]["alignment"], 3)
        self.assertIn(notice, native)
        for material in native.values():
            inner = json.loads(material["content"])
            self.assertEqual(inner["styles"][0]["font"], {"id": "font-1", "path": "staged/demo.ttf"})
        for event in events:
            if "fit" in event:
                self.assertLessEqual(event["fit"]["width_px"], event["fit"]["box_width_px"] + .02)
                self.assertLessEqual(event["fit"]["height_px"], event["fit"]["box_height_px"] + .02)

    def test_item_candidates_are_scoped_and_not_invented(self):
        item = {**ITEM, "title_candidates": [{"text": "不应选到", "script_id": "other"}, {"text": "指定候选", "script_id": "s1"}]}
        content = draft()
        self.append(content, {"layout": {"bundle": "header"}}, item)
        self.assertEqual([json.loads(x["content"])["text"] for x in content["materials"]["texts"]], ["指定候选"])

    def test_panel_track_precedes_existing_heading_across_items(self):
        content = draft()
        self.append(content, {"layout": {"bundle": "header", "headline": "第一个标题"}})
        self.append(content, {"layout": {"bundle": "header_panel", "headline": "第二个标题"}}, {**ITEM, "start_ms": 19000})
        names = [t["name"] for t in content["tracks"]]
        self.assertLess(names.index("Layout:HeaderPanel"), names.index("Layout:Headline"))
        heading = next(t for t in content["tracks"] if t["name"] == "Layout:Headline")
        self.assertEqual([s["target_timerange"]["start"] for s in heading["segments"]], [7000000, 19000000])
        speeds = {m["id"] for m in content["materials"]["speeds"]}
        for segment in heading["segments"]:
            self.assertTrue(set(segment["extra_material_refs"]).issubset(speeds))

    def test_extreme_title_fails_instead_of_invisible_text(self):
        with self.assertRaisesRegex(ValueError, "readable area"):
            self.append(draft(), {"layout": {"bundle": "header", "headline": "超长标题"*30}})

    def test_unverified_unicode_rejected_and_no_input_mutation(self):
        job = {"layout": {"bundle": "header", "headline": "标题😀"}}
        original = copy.deepcopy(job)
        content = draft()
        with self.assertRaisesRegex(ValueError, "Non-BMP"):
            self.append(content, job)
        self.assertEqual(job, original)

    def test_image_config_is_separate_and_preserves_overrides(self):
        job = {"overlays": [{"kind": "text", "text": "声明：保留。"}], "layout": {"product_image": "product.png"}}
        result = resolve_overlays(job, SETTINGS)
        self.assertEqual(result[-1]["transform"]["transform_x"], -.507)
        self.assertEqual(result[-1]["role"], "product_image")
        self.assertEqual(len(job["overlays"]), 1)
        job["overlays"].append({"kind": "image", "path": "product.png", "transform": {"transform_x": .1}})
        self.assertEqual(len(resolve_overlays(job, SETTINGS)), 2)

    def test_canvas_calibration_and_seeded_bundle(self):
        config = {"size": 12, "scale": 1.15, "width": .86, "height": .10, "y": .84}
        fit720 = fit_text("同一段标题", config, None, 720, 1280)[2]
        fit1080 = fit_text("同一段标题", config, None, 1080, 1920)[2]
        self.assertAlmostEqual(fit1080["width_px"] / fit720["width_px"], 1.5, places=3)
        a = self.append(draft(), {"title": "选择布局"}, seed=42)[0]
        b = self.append(draft(), {"title": "选择布局"}, seed=42)[0]
        self.assertEqual(a, b)


def segment(mid, start, duration, y=0, refs=None, **extra):
    return {"id": "segment-"+mid, "material_id": mid, "target_timerange": {"start": int(start*1e6), "duration": int(duration*1e6)},
            "clip": {"scale": {"x": 1.15, "y": 1.15}, "transform": {"x": 0, "y": y}},
            "extra_material_refs": refs or [], **extra}


def text(mid, value, size, kind="text", **extra):
    return {"id": mid, "type": kind, "content": json.dumps({"text": value, "styles": [{"range": [0, len(value)], "size": size,
            "font": {"id": "font-demo", "path": "inner-font.ttf"}, "strokes": [{"width": .08}],
            "fill": {"content": {"solid": {"color": [1, 1, 1]}}}}]}), **extra}


def history_fixture():
    return {"canvas_config": {"width": 1080, "height": 1920}, "duration": 100000000,
        "tracks": [
            {"type": "video", "name": "Main", "segments": [segment("video", 0, 60, volume=0, speed=1.2)]},
            {"type": "text", "segments": [segment("caption", 0, 20, -.365, ["anim"]), segment("title", 2, 3, .84),
                segment("orphan", 80, 2, -.365), segment("template", 2, 2, -.365), segment("vertical", 0, 60, .1), segment("notice", 0, 60, -.72)]},
            {"type": "audio", "segments": [segment("music", 0, 5, refs=["fade-empty", "fade-real"]), segment("sound", 1, 25), segment("unknown", 2, 2)]}],
        "materials": {"videos": [{"id": "video", "type": "video", "path": "main.mp4"}],
            "texts": [text("caption", "长句仍是字幕", 13, "subtitle", words={"text": ["长句"], "start_time": [0], "end_time": [900]}),
                text("title", "三秒标题", 44), text("orphan", "孤立字幕", 88, "subtitle"), text("child", "模板文字", 55),
                text("vertical", "侧边信息", 17, typesetting=1), text("notice", "说明：请阅读\n注册证：A1", 5)],
            "text_templates": [{"id": "template", "type": "text_template", "name": "demo", "resource_id": "native-resource",
                "text_info_resources": [{"text_material_id": "child", "attach_info": {"original_size_width": 600, "original_size_height": 100}, "extra_material_refs": ["anim"]}],
                "non_text_info_resources": [{"type": "sticker", "name": "ornament"}], "resources": [{"panel": "flower", "resource_id": "decor"}]}],
            "material_animations": [{"id": "anim", "type": "sticker_animation", "animations": [{"type": "caption", "name": "word-color", "resource_id": "animation-demo"}]}],
            "audios": [{"id": "music", "type": "music", "name": "short-track", "path": "Cache/music/a.mp3"},
                       {"id": "sound", "type": "sound", "name": "long-sound", "path": "Cache/music/b.mp3"},
                       {"id": "unknown", "type": "audio", "name": "unknown", "path": "Cache/music/c.mp3"}],
            "audio_fades": [{"id": "fade-empty", "type": "audio_fade"}, {"id": "fade-real", "type": "audio_fade", "fade_out_duration": 1600000}]}}


class HistoryTests(unittest.TestCase):
    def test_roles_use_structure_not_duration_or_cache_folder(self):
        result = analyze_document(history_fixture())
        roles = {x["material_id"]: x["role"] for x in result["text_segments"]}
        self.assertEqual(roles["caption"], "caption")
        self.assertEqual(roles["title"], "persistent_title")
        self.assertEqual(roles["vertical"], "side_benefit")
        self.assertEqual(roles["notice"], "disclosure")
        self.assertEqual([x["role"] for x in result["audios"]], ["music", "sound_effect", "unclassified_audio"])

    def test_orphans_templates_and_titles_do_not_change_body_settings(self):
        result = analyze_document(history_fixture())
        self.assertEqual(result["effective_main_ranges_s"], [[0, 60]])
        orphan = next(x for x in result["text_segments"] if x["material_id"] == "orphan")
        self.assertTrue(orphan["excluded_from_defaults"])
        settings = _suggest([result])
        self.assertEqual(settings["base_font_size"], 13)
        self.assertEqual(settings["subtitle_y"], -.365)
        self.assertAlmostEqual(settings["subtitle_border_width"], 40)

    def test_native_components_words_and_caption_animation_retained(self):
        result = analyze_document(history_fixture())
        template = next(x for x in result["text_segments"] if x["material_id"] == "template")
        self.assertEqual(template["native_template"]["non_text_info_resources"][0]["name"], "ornament")
        self.assertEqual(template["text_components"][0]["attach_info"]["attach_info"]["original_size_width"], 600)
        self.assertTrue(template["referenced_materials"])
        caption = next(x for x in result["text_segments"] if x["material_id"] == "caption")
        self.assertEqual(caption["text_components"][0]["words"]["text"], ["长句"])
        self.assertEqual(caption["animations"][0]["type"], "caption")

    def test_fade_values_and_video_mute_speed_are_explicit(self):
        result = analyze_document(history_fixture())
        music = result["audios"][0]
        self.assertIsNone(music["volume"])
        self.assertIsNone(music["muted"])
        self.assertFalse(music["volume_explicit"])
        fades = {f["id"]: f for f in music["fades"]}
        self.assertFalse(fades["fade-empty"]["has_nonzero_fade"])
        self.assertEqual(fades["fade-real"]["fade_out_s"], 1.6)
        self.assertTrue(result["video_segments"][0]["muted"])
        self.assertEqual(result["video_segments"][0]["speed"], 1.2)

    def test_main_gaps_exclude_floating_objects(self):
        doc = history_fixture()
        doc["tracks"][0]["segments"] = [segment("video", 0, 5), segment("video", 15, 5)]
        doc["tracks"][1]["segments"].append(segment("orphan", 7, 2, -.365))
        result = analyze_document(doc)
        self.assertEqual(result["effective_main_ranges_s"], [[0, 5], [15, 20]])
        floating = [x for x in result["text_segments"] if x["start_s"] == 7][0]
        self.assertTrue(floating["excluded_from_defaults"])

    def test_authorization_precedes_file_scan_and_compatible_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "draft.json"
            source.write_text(json.dumps(history_fixture()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "authorization"):
                analyze([source], Path(tmp)/"denied")
            self.assertFalse((Path(tmp)/"denied").exists())
            output = analyze([source], Path(tmp)/"approved", approved=True)
            self.assertEqual(output["drafts"], 1)
            self.assertEqual(output["errors"], [])
            report = json.loads(Path(output["report"]).read_text(encoding="utf-8"))
            self.assertIn("texts", report["records"][0])
            self.assertIn("documents", report["records"][0])
            self.assertTrue(report["suggestion_evidence"]["requires_confirmation"])

    def test_created_preset_uses_runtime_layout_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)/"spec.json"
            spec.write_text(json.dumps({"confirmed": True, "name": "custom", "layout": {"bundle": "header_panel"}}), encoding="utf-8")
            result = create(spec, Path(tmp)/"presets")
            preset = json.loads(Path(result["preset"]).read_text(encoding="utf-8"))
            self.assertEqual(preset["layout_defaults"], {"bundle": "header_panel"})


if __name__ == "__main__":
    unittest.main()
