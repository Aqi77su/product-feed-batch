"""Headless end-to-end generation with synthetic media and isolated draft roots.

These checks exercise the real planner, bundled draft backend, caption/layout
compilers and verifier. They assert structure and clocks, not rendered appearance.
The template fixture is synthetic and never a claim of desktop verification.
"""

import contextlib
import copy
import importlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def write_json(path, value):
    pathlib.Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import imageio_ffmpeg
            import pymediainfo
            from PIL import Image
        except ImportError as exc:
            raise unittest.SkipTest("Pipeline dependencies unavailable: " + str(exc))
        cls.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if not pathlib.Path(cls.ffmpeg).is_file():
            raise unittest.SkipTest("Bundled FFmpeg executable unavailable")
        candidates = [pathlib.Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
                      for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf")]
        candidates += [pathlib.Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
        cls.font = next((p for p in candidates if p.is_file()), None)
        if cls.font is None:
            raise unittest.SkipTest("A readable local font is required for pipeline QA")
        cls.temp = tempfile.TemporaryDirectory(prefix="pfb-pipeline-")
        cls.workspace = pathlib.Path(cls.temp.name)
        cls.drafts = cls.workspace / "drafts"
        cls.drafts.mkdir()
        cls.main_paths = []
        for number, (color, tone) in enumerate((("blue", 440), ("purple", 620))):
            target = cls.workspace / f"main-{number}.mp4"
            cls._ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=180x320:r=25:d=6.6",
                        "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration=6.6",
                        "-t", "6.6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(target))
            cls.main_paths.append(target)
        cls.broll = cls.workspace / "product.mp4"
        cls._ffmpeg("-f", "lavfi", "-i", "testsrc2=s=180x320:r=25:d=8",
                    "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(cls.broll))
        cls.music = []
        for number, tone in enumerate((220, 330)):
            path = cls.workspace / f"music-{number}.wav"
            cls._ffmpeg("-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration=20",
                        "-c:a", "pcm_s16le", str(path))
            cls.music.append(path)
        cls.image = cls.workspace / "product.png"
        Image.new("RGBA", (120, 120), (255, 100, 10, 230)).save(cls.image)
        cls.old_ffmpeg = os.environ.get("PFB_FFMPEG")
        os.environ["PFB_FFMPEG"] = cls.ffmpeg
        if str(ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(ROOT / "scripts"))
        cls.batch = importlib.import_module("batch")
        # Fail real media-probing problems, but clearly skip a missing native runtime.
        try:
            cls.sources = [dict(cls.batch.probe(p), sha256=cls.batch.digest(p)) for p in cls.main_paths]
        except OSError as exc:
            cls.temp.cleanup()
            raise unittest.SkipTest("MediaInfo native runtime unavailable: " + str(exc))
        for source in cls.sources:
            source["id"] = "main_" + source["sha256"][:16]

    @classmethod
    def _ffmpeg(cls, *args):
        result = subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError("Synthetic media generation failed: " + result.stderr[-1200:])

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "old_ffmpeg", None) is None:
            os.environ.pop("PFB_FFMPEG", None)
        else:
            os.environ["PFB_FFMPEG"] = cls.old_ffmpeg
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def _component(self):
        """Minimal full template with an editable child; no third-party effects."""
        path = self.workspace / "synthetic-component.json"
        component = {
            "schema_version": "1.0",
            "segment": {"id": "SEGMENT", "material_id": "TEMPLATE",
                        "target_timerange": {"start": 0, "duration": 3000000},
                        "clip": {"scale": {"x": 1.15, "y": 1.15}, "transform": {"x": 0, "y": -.365}},
                        "extra_material_refs": []},
            "materials": {
                "text_templates": [{"id": "TEMPLATE", "type": "text_template", "name": "Synthetic structural fixture",
                                    "text_info_resources": [{"id": "SLOT", "text_material_id": "TEXT",
                                                             "attach_info": {"duration": 3000000}, "extra_material_refs": []}],
                                    "non_text_info_resources": []}],
                "texts": [{"id": "TEXT", "type": "text", "content": json.dumps({
                    "text": "{{TEXT}}", "styles": [{"range": [0, 8], "size": 14,
                        "font": {"id": "fixture-font", "path": str(self.font)},
                        "fill": {"content": {"render_type": "solid", "solid": {"color": [1, 1, 1]}}}}]
                }, ensure_ascii=False)}],
            },
            "text_slots": [{"material_id": "TEXT", "role": "primary"}],
            "verification_note": "Synthetic fixture: structural QA only; never desktop-render verified",
        }
        write_json(path, component)
        return path

    def _document(self, source, section):
        duration = source["duration_ms"]
        return {"schema_version": "1.0", "status": "corrected", "timestamp_basis": "source_media",
                "source_id": source["id"], "source_hash": source["sha256"],
                "script_id": "script-test", "section_id": section,
                "cues": [
                    {"id": "s1", "text": "开场介绍，", "start_ms": 0, "end_ms": 1100,
                     "words": [{"text": "开场", "startMs": 0, "endMs": 550},
                               {"text": "介绍", "startMs": 550, "endMs": 1100}], "keywords": []},
                    {"id": "s2", "text": "实用产品，", "start_ms": 1100, "end_ms": 4400,
                     "words": [{"text": "实用", "startMs": 1100, "endMs": 2200},
                               {"text": "产品", "startMs": 2200, "endMs": 4400}],
                     "keywords": [{"text": "实用", "char_start": 0, "char_end": 2,
                                   "start_ms": 1100, "end_ms": 2200, "timing_precision": "word"}]},
                    {"id": "s3", "text": "结束提醒。", "start_ms": 4400, "end_ms": duration,
                     "words": [{"text": "结束", "startMs": 4400, "endMs": 5500},
                               {"text": "提醒", "startMs": 5500, "endMs": duration}], "keywords": []},
                ],
                "broll_opportunities": [{"id": "o1", "from_cue": "s2", "to_cue": "s2",
                                          "role": "product_intro", "priority": "high",
                                          "preferred_asset_tags": ["product"],
                                          "presentation": "one_continuous_window", "min_duration_s": 1,
                                          "max_duration_s": 4, "keep_within_source_segment": True}],
                "talking_head_preferred": [{"from_cue": "s1", "to_cue": "s1", "reason": "开场人物"},
                                           {"from_cue": "s3", "to_cue": "s3", "reason": "收尾人物"}],
                "title_candidates": ["产品介绍"]}

    def _job(self, name, *, splice, template):
        catalog_path = self.workspace / (name + "-catalog.json")
        catalog = {"fonts": [{"id": "fixture-font", "name": self.font.name, "path": str(self.font),
                              "available": True, "verified": True}],
                   "music": [{"id": f"music-{i}", "name": p.name, "path": str(p)} for i, p in enumerate(self.music)],
                   "sfx": [], "flowers": [], "caption_animations": [], "text_templates": [],
                   "verified_jianying_version": "synthetic-structural-test-only"}
        if template:
            catalog["text_templates"] = [{"name": "Synthetic structural fixture", "role": "caption",
                                           "component_path": str(self._component()), "verified": True,
                                           "available": True, "max_chars": 13}]
        write_json(catalog_path, catalog)
        subtitles = {}
        for i, source in enumerate(self.sources):
            path = self.workspace / f"{name}-subtitle-{i}.json"
            write_json(path, self._document(source, f"section-{i}"))
            subtitles[source["id"]] = str(path)
        job = {"schema_version": "1.0", "name": name, "count": 2,
               "main_videos": [str(p) for p in self.main_paths],
               "broll_assets": [{"path": str(self.broll), "tags": ["product"],
                                  "usable_ranges": [{"start_ms": 0, "end_ms": 6000}]}],
               "subtitles": subtitles, "resource_catalog": str(catalog_path), "draft_root": str(self.drafts),
               "settings": {"main_speed": 1.1, "main_volume": 1.0, "broll_mode": "semantic",
                            "broll_count": [2, 2] if splice else [1, 1], "broll_ratio": [.9, .9],
                            "opening_no_broll_s": .5, "ending_no_broll_s": .5,
                            "music_selection": "balanced_shuffle", "music_volume": .36,
                            "music_loop": False, "music_fade_in_s": 0, "music_fade_out_s": 1.6,
                            "emphasis_probability": 1.0, "template_probability": 1.0 if template else 0.0,
                            "missing_effect_policy": "plain", "caption_animation_mode": "none", "sfx_mode": "none"},
               "layout": {"bundle": "header_panel", "headline": "产品介绍", "subheadline": "使用方便",
                          "side_benefit": {"text": "便于使用", "side": "right"},
                          "disclosure": {"text": "测试信息\n仅用于结构验证"}, "product_image": str(self.image)}}
        if splice:
            job["assembly"] = {"mode": "splice", "confirmed": True, "allow_duplicate_combinations": True,
                               "groups": [{"script_id": "script-test", "script_length": 2, "count": 2,
                                           "sections": [{"section_id": f"section-{i}", "char_start": i,
                                                         "char_end": i + 1, "sources": [source["id"]]}
                                                        for i, source in enumerate(self.sources)]}]}
        return job

    def _execute(self, job):
        work = self.workspace / (job["name"] + "-work")
        with contextlib.redirect_stdout(io.StringIO()):
            report = self.batch.run(job, work, confirmed=True)
        self.assertTrue(report["ok"], report)
        draft = pathlib.Path(report["draft_path"])
        result = self.batch.verify_draft(draft)
        self.assertTrue(result["ok"], result)
        self.assertFalse((draft / "INCOMPLETE.json").exists())
        return draft, self.batch.read(draft / "draft_info.json"), self.batch.read(draft / "batch-resolved-plan.json")

    def _assert_common(self, draft, content, resolved):
        tracks = {x.get("name"): x for x in content["tracks"]}
        self.assertNotIn("Emphasis", tracks)
        self.assertIn("Subtitles", tracks)
        self.assertIn("ImageOverlay0", tracks)
        for name in ("Layout:Headline", "Layout:Subheadline", "Layout:SideBenefit", "Layout:Disclosure"):
            self.assertIn(name, tracks)
        self.assertEqual(2, len(tracks["Music"]["segments"]))
        self.assertEqual(2, len(tracks["ImageOverlay0"]["segments"]))
        self.assertEqual(content["duration"], resolved["duration_ms"] * 1000)
        expected_subtitles = sum(len(item["captions"]) for item in resolved["items"])
        self.assertEqual(expected_subtitles, len(tracks["Subtitles"]["segments"]))
        for seg in tracks["Broll"]["segments"]:
            self.assertEqual(0, seg["volume"])
        for item in resolved["items"]:
            self.assertEqual(round(sum(x["source_duration_ms"] for x in item["segments"]) / 1.1), item["duration_ms"])
            for part in item["segments"]:
                matching = [c for c in item["captions"] if c["source_id"] == part["source_id"]]
                second = next(c for c in matching if c["source_cue_id"].startswith("s2"))
                self.assertEqual(part["start_ms"] + 1000, second["start_ms"])
                self.assertEqual(part["start_ms"] + 4000, second["end_ms"])
                self.assertEqual(part["start_ms"] + 2000, second["keywords"][0]["end_ms"])
            for clip in item["broll"]:
                part = next(x for x in item["segments"] if x["source_id"] == clip["source_id"])
                self.assertGreaterEqual(clip["start_ms"], part["start_ms"] + 1000)
                self.assertLessEqual(clip["start_ms"] + clip["duration_ms"], part["start_ms"] + 4000)
                self.assertEqual(0, clip["source_start_ms"])
            self.assertEqual(1, len(item["music_chunks"]))
            self.assertEqual(0, item["music_chunks"][0]["source_start_ms"])
            self.assertEqual(1600, item["music_chunks"][0]["fade_out_ms"])
        self.assertEqual(2, len(set(item["music"]["id"] for item in resolved["items"])))
        fades = content["materials"].get("audio_fades", [])
        self.assertEqual(2, len(fades))
        self.assertTrue(all(f["fade_in_duration"] == 0 and f["fade_out_duration"] == 1600000 for f in fades))
        font_paths = set()
        for material in content["materials"].get("texts", []):
            inner = json.loads(material["content"])
            self.assertNotIn("{{TEXT}}", inner["text"])
            for style in inner["styles"]:
                if style.get("font", {}).get("path"):
                    font_paths.add(style["font"]["path"])
        self.assertTrue(font_paths)
        self.assertTrue(all(pathlib.Path(p).is_file() and pathlib.Path(p).is_relative_to(draft) for p in font_paths))
        return tracks

    def test_inline_emphasis_pipeline_with_plain_fallback(self):
        job = self._job("synthetic-inline", splice=False, template=False)
        draft, content, resolved = self._execute(job)
        tracks = self._assert_common(draft, content, resolved)
        self.assertEqual(2, len(tracks["Main"]["segments"]))
        self.assertEqual(2, len(tracks["Broll"]["segments"]))
        self.assertEqual(0, len(content["materials"].get("text_templates", [])))
        for item in resolved["items"]:
            emphasized = [x for x in item["subtitle_events"] if x["mode"] == "inline_emphasis"]
            self.assertEqual(1, len(emphasized))
        subtitle_ids = {s["material_id"] for s in tracks["Subtitles"]["segments"]}
        for mat in content["materials"]["texts"]:
            if mat["id"] not in subtitle_ids:
                continue
            text = json.loads(mat["content"])
            self.assertNotIn("，", text["text"])
            self.assertNotIn("。", text["text"])
            ranges = sorted(style["range"] for style in text["styles"])
            self.assertEqual(0, ranges[0][0])
            self.assertEqual(len(text["text"].encode("utf-16-le")) // 2, ranges[-1][1])
            self.assertTrue(all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])))

    def test_spliced_template_replacement_pipeline(self):
        job = self._job("synthetic-splice-template", splice=True, template=True)
        draft, content, resolved = self._execute(job)
        tracks = self._assert_common(draft, content, resolved)
        self.assertEqual(4, len(tracks["Main"]["segments"]))
        self.assertEqual(4, len(tracks["Broll"]["segments"]))
        self.assertEqual(4, len(content["materials"].get("text_templates", [])))
        template_ids = {t["id"] for t in content["materials"]["text_templates"]}
        template_segments = [s for s in tracks["Subtitles"]["segments"] if s["material_id"] in template_ids]
        self.assertEqual(4, len(template_segments))
        for item in resolved["items"]:
            self.assertEqual(2, len(item["segments"]))
            replacements = [x for x in item["subtitle_events"] if x["mode"] == "template_replace"]
            self.assertEqual(2, len(replacements))
        for template in template_segments:
            a = template["target_timerange"]["start"]
            b = a + template["target_timerange"]["duration"]
            simultaneous = [s for s in tracks["Subtitles"]["segments"]
                            if min(b, s["target_timerange"]["start"] + s["target_timerange"]["duration"])
                            > max(a, s["target_timerange"]["start"])]
            self.assertEqual([template["id"]], [s["id"] for s in simultaneous])


if __name__ == "__main__":
    unittest.main()
