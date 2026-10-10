import copy
import importlib.util
import pathlib
import random
import unittest


SPEC = importlib.util.spec_from_file_location(
    "semantic_plan", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "semantic_plan.py"
)
semantic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(semantic)


def document(source_id="a", duration=11000):
    return {
        "source_id": source_id, "timestamp_basis": "source_media",
        "cues": [
            {"id": "s1", "text": "开头", "start_ms": 0, "end_ms": 2200, "keywords": []},
            {"id": "s2", "text": "产品好用", "start_ms": 2200, "end_ms": 5500,
             "words": [{"text": "产品", "startMs": 2200, "endMs": 3300},
                       {"text": "好用", "startMs": 3300, "endMs": 5500}],
             "keywords": [{"text": "好用", "char_start": 2, "char_end": 4,
                           "start_ms": 3300, "end_ms": 5500, "timing_precision": "word"}]},
            {"id": "s3", "text": "操作方便", "start_ms": 5500, "end_ms": 8800, "keywords": []},
            {"id": "s4", "text": "结尾", "start_ms": 8800, "end_ms": duration, "keywords": []},
        ],
        "broll_opportunities": [{"id": "o1", "from_cue": "s2", "to_cue": "s3",
                                  "role": "operation_demo", "priority": "high",
                                  "preferred_asset_tags": ["product"],
                                  "presentation": "one_continuous_window",
                                  "min_duration_s": 1, "max_duration_s": 10,
                                  "keep_within_source_segment": True}],
        "talking_head_preferred": [{"from_cue": "s1", "to_cue": "s1", "reason": "开场钩子"}],
        "title_candidates": ["操作简单"],
    }


def part(source_id="a", start=0):
    return {"source_id": source_id, "start_ms": start, "duration_ms": 10000,
            "source_duration_ms": 11000, "speed": 1.1}


ASSETS = [{"id": "p", "path": "product.mp4", "duration_ms": 20000, "tags": ["product"]}]
SETTINGS = {"broll_count": [1, 1], "broll_duration_s": [1, 32],
            "opening_no_broll_s": 0, "ending_no_broll_s": 0}


class SemanticValidationTests(unittest.TestCase):
    def test_valid_tags_and_role_alias(self):
        data = document()
        data["broll_opportunities"][0]["role"] = "object_reveal"
        self.assertEqual([], semantic.validate_annotations(data, {"id": "a", "duration_ms": 11000},
                                                         {"available_asset_tags": ["product"]}))

    def test_reject_bad_anchors_roles_tags_and_overlaps(self):
        source = {"id": "a", "duration_ms": 11000}
        for field, bad in [("from_cue", "missing"), ("role", "invented"),
                           ("priority", "highest"), ("keep_within_source_segment", False),
                           ("preferred_asset_tags", ["unknown"]), ("presentation", "anything")]:
            data = document()
            data["broll_opportunities"][0][field] = bad
            self.assertTrue(semantic.validate_annotations(data, source, {"available_asset_tags": ["product"]}), field)
        data = document()
        second = copy.deepcopy(data["broll_opportunities"][0])
        second["id"] = "o2"
        data["broll_opportunities"].append(second)
        self.assertTrue(any("Overlapping" in x for x in semantic.validate_annotations(data, source)))

    def test_words_must_not_be_cue_relative(self):
        data = document()
        data["cues"][1]["words"][0].update(startMs=0, endMs=1100)
        self.assertTrue(any("absolute source-media" in x for x in semantic.validate_annotations(data, {"duration_ms": 11000})))


class TimestampMappingTests(unittest.TestCase):
    def test_speed_offset_words_and_idempotence(self):
        original = document()
        data = semantic.map_document(original, part(start=10000), 1.1)
        cue = data["cues"][1]
        self.assertEqual((12000, 15000), (cue["start_ms"], cue["end_ms"]))
        self.assertEqual((13000, 15000), (cue["keywords"][0]["start_ms"], cue["keywords"][0]["end_ms"]))
        self.assertEqual((12000, 13000), (cue["words"][0]["start_ms"], cue["words"][0]["end_ms"]))
        self.assertNotIn("startMs", cue["words"][0])
        self.assertEqual(2200, original["cues"][1]["start_ms"])
        self.assertEqual(data, semantic.map_document(data, part(start=10000), 1.1))
        with self.assertRaisesRegex(ValueError, "different composition"):
            semantic.map_document(data, part(start=20000), 1.1)

    def test_legacy_does_not_claim_word_precision(self):
        data = document()
        del data["timestamp_basis"]
        mapped = semantic.map_document(data, part())
        self.assertEqual("cue", mapped["cues"][1]["keywords"][0]["timing_precision"])
        self.assertEqual("estimated", mapped["cues"][1]["words"][0]["timing_precision"])
        self.assertTrue(mapped["timing_warnings"])

    def test_duration_clock_mismatch_rejected(self):
        invalid = part()
        invalid["duration_ms"] = 11000
        with self.assertRaisesRegex(ValueError, "duration_ms"):
            semantic.map_document(document(), invalid)


class SemanticPlannerTests(unittest.TestCase):
    def test_valid_phrase_window_with_speed(self):
        source = part()
        doc = semantic.map_document(document(), source)
        result = semantic.plan_semantic_broll([source], {"a": doc}, ASSETS, SETTINGS, random.Random(1))
        clip = result["clips"][0]
        self.assertEqual((2000, 6000, 0), (clip["start_ms"], clip["duration_ms"], clip["source_start_ms"]))
        self.assertEqual("selected", result["opportunities"][0]["status"])

    def test_parent_cue_alias_survives_layout_split(self):
        data = document()
        old = data["cues"].pop(1)
        first = {"id": "s2a", "parent_cue_id": "s2", "text": "产品", "start_ms": 2200, "end_ms": 3300}
        second = {"id": "s2b", "parent_cue_id": "s2", "text": "好用", "start_ms": 3300, "end_ms": 5500}
        data["cues"][1:1] = [first, second]
        data["cue_aliases"] = {"s2": ["s2a", "s2b"]}
        self.assertEqual([], semantic.validate_annotations(data, {"duration_ms": 11000}))
        mapped = semantic.map_document(data, part())
        result = semantic.plan_semantic_broll([part()], {"a": mapped}, ASSETS, SETTINGS, random.Random(1))
        self.assertEqual(2000, result["clips"][0]["start_ms"])

    def test_no_crossing_speakers_and_reproducible(self):
        parts = [part(), part("b", 10000)]
        docs = {x["source_id"]: semantic.map_document(document(x["source_id"]), x) for x in parts}
        options = dict(SETTINGS, broll_count=[2, 2])
        one = semantic.plan_semantic_broll(parts, docs, ASSETS, options, random.Random(7))
        two = semantic.plan_semantic_broll(parts, docs, ASSETS, options, random.Random(7))
        self.assertEqual(one, two)
        self.assertEqual(2, len(one["clips"]))
        for clip in one["clips"]:
            matched = next(x for x in parts if x["source_id"] == clip["source_id"])
            self.assertGreaterEqual(clip["start_ms"], matched["start_ms"])
            self.assertLessEqual(clip["start_ms"] + clip["duration_ms"], matched["start_ms"] + matched["duration_ms"])

    def test_missing_annotations_block_production(self):
        doc = document()
        doc["broll_opportunities"] = []
        with self.assertRaisesRegex(ValueError, "no broll_opportunities"):
            semantic.plan_semantic_broll([part()], {"a": semantic.map_document(doc, part())}, ASSETS, SETTINGS, random.Random(1))

    def test_untagged_assets_warn_and_do_not_fill_arbitrarily(self):
        assets = copy.deepcopy(ASSETS)
        assets[0]["tags"] = []
        result = semantic.plan_semantic_broll([part()], {"a": semantic.map_document(document(), part())}, assets, SETTINGS, random.Random(1))
        self.assertEqual([], result["clips"])
        self.assertTrue(any("no usable asset matches" in x for x in result["warnings"]))

    def test_hard_limits_and_protected_cue_never_violated(self):
        doc = document()
        doc["broll_opportunities"][0].update(from_cue="s1", to_cue="s4")
        doc["talking_head_preferred"].append({"from_cue": "s3", "to_cue": "s3", "reason": "保留人物", "priority": "hard"})
        options = dict(SETTINGS, opening_no_broll_s=1, ending_no_broll_s=1,
                       broll_blocked_intervals=[{"start_ms": 8000, "end_ms": 10000}])
        result = semantic.plan_semantic_broll([part()], {"a": semantic.map_document(doc, part())}, ASSETS, options, random.Random(2))
        self.assertEqual(1, len(result["clips"]))
        self.assertEqual((2000, 3000), (result["clips"][0]["start_ms"], result["clips"][0]["duration_ms"]))

    def test_no_window_is_not_permission_to_cover_hook(self):
        doc = document()
        doc["talking_head_preferred"] = [{"from_cue": "s1", "to_cue": "s4", "reason": "整段人物优先"}]
        result = semantic.plan_semantic_broll([part()], {"a": semantic.map_document(doc, part())}, ASSETS, SETTINGS, random.Random(1))
        self.assertEqual([], result["clips"])
        self.assertEqual("protected", result["opportunities"][0]["status"])

    def test_source_usable_ranges_and_short_sequence(self):
        doc = document()
        doc["broll_opportunities"][0].update(presentation="short_sequence", min_duration_s=2, max_duration_s=3)
        assets = [{"id": "p", "path": "product.mp4", "duration_ms": 10000,
                   "tags": ["product"], "usable_ranges": [{"start_ms": 5000, "end_ms": 8000}]}]
        result = semantic.plan_semantic_broll([part()], {"a": semantic.map_document(doc, part())}, assets,
                                              dict(SETTINGS, broll_count=[2, 2]), random.Random(1))
        self.assertEqual(2, len(result["clips"]))
        for clip in result["clips"]:
            self.assertEqual(5000, clip["source_start_ms"])
            self.assertLessEqual(clip["source_start_ms"] + clip["duration_ms"], 8000)


class BalancedMusicTests(unittest.TestCase):
    def test_eligibility_and_balanced_rounds(self):
        pool = [{"id": "a", "duration_ms": 10000}, {"id": "b", "duration_ms": 10000},
                {"id": "short", "duration_ms": 100}]
        state, rng = {}, random.Random(12)
        chosen = [semantic.choose_music(pool, 5000, {}, state, rng)["id"] for _ in range(10)]
        self.assertEqual(5, chosen.count("a"))
        self.assertEqual(5, chosen.count("b"))
        self.assertNotIn("short", chosen)
        self.assertTrue(all(a != b for a, b in zip(chosen, chosen[1:])))


if __name__ == "__main__":
    unittest.main()
