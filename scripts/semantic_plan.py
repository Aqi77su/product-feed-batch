"""Validated semantic annotations and deterministic, speech-boundary B-roll planning.

Source documents use absolute milliseconds from the beginning of the source media.
``map_document`` changes that clock exactly once to composition-local milliseconds.
It never adds the enclosing batch offset. The draft writer adds that offset once.
"""

from __future__ import annotations

import copy
import math


ROLE_ALIASES = {
    "object_reveal": "product_intro",
    "benefit_explanation": "product_usage_benefit",
    "offer_logistics": "logistics",
    "action_reminder": "call_to_action",
}
ROLES = frozenset({
    "product_intro", "product_usage_benefit", "product_feature", "operation_demo",
    "audience_scene", "price_offer", "logistics", "call_to_action", "explanation",
    "benefit_demo",
})
PRIORITIES = {"high": 3, "medium": 2, "low": 1}
PRESENTATIONS = frozenset({"one_continuous_window", "short_sequence"})


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _tags(value):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError("preferred_asset_tags must be a non-empty list of real catalog tags")
    if len(set(value)) != len(value):
        raise ValueError("preferred_asset_tags contains duplicate tags")
    return set(value)


def _cue_index(data):
    cues = data.get("cues", [])
    by_id = {}
    aliases = {}
    for cue in cues:
        cue_id = cue.get("id")
        if not isinstance(cue_id, str) or not cue_id or cue_id in by_id:
            raise ValueError("Cue IDs must be unique non-empty strings")
        by_id[cue_id] = cue
        for field in ("parent_cue_id", "source_cue_id", "original_cue_id"):
            parent = cue.get(field)
            if parent and parent != cue_id:
                aliases.setdefault(parent, []).append(cue_id)
    for name, targets in data.get("cue_aliases", {}).items():
        if not isinstance(targets, list) or not targets or any(not isinstance(x, str) for x in targets):
            raise ValueError("cue_aliases must map names to non-empty cue ID lists")
        aliases.setdefault(name, []).extend(targets)

    def resolve(name, stack=()):
        if not isinstance(name, str) or not name:
            raise ValueError("Annotation must reference a cue ID")
        if name in stack:
            raise ValueError("Cyclic cue alias: " + name)
        found = [by_id[name]] if name in by_id else []
        for target in dict.fromkeys(aliases.get(name, [])):
            if target != name:
                found.extend(resolve(target, (*stack, name)))
        if not found:
            raise ValueError("Unknown cue anchor: " + name)
        unique = {x["id"]: x for x in found}
        return sorted(unique.values(), key=lambda x: (x["start_ms"], x["end_ms"]))

    return by_id, resolve


def _anchored_range(annotation, resolve):
    first = resolve(annotation.get("from_cue"))
    last = resolve(annotation.get("to_cue"))
    start, end = first[0]["start_ms"], last[-1]["end_ms"]
    if first[0]["start_ms"] > last[0]["start_ms"] or end <= start:
        raise ValueError("Reversed cue anchor range")
    return start, end


def _known_tags(settings):
    if "available_asset_tags" in settings:
        return set(settings["available_asset_tags"])
    if "broll_assets" in settings:
        return {tag for asset in settings["broll_assets"] for tag in asset.get("tags", [])}
    return None


def validate_annotations(data, source, settings=None):
    """Return human-readable errors; do not mutate or silently repair annotations.

    Pass settings.available_asset_tags (or settings.broll_assets) to validate against
    the actual inventory. The planner independently verifies every asset match.
    Legacy documents lacking timestamp_basis are accepted as source-media time, but
    mapping explicitly downgrades their word-precision claims.
    """
    errors = []
    settings = settings or {}
    if data.get("timestamp_basis", "source_media") != "source_media":
        errors.append("Corrected input timestamp_basis must be source_media")
    if data.get("source_id") and source.get("id") and data["source_id"] != source["id"]:
        errors.append("Annotation source_id does not match source")
    duration = source.get("source_duration_ms", source.get("duration_ms"))
    if not _number(duration) or duration <= 0:
        errors.append("Source duration_ms must be positive")
        return errors
    cues = data.get("cues", [])
    if not isinstance(cues, list) or not cues:
        return errors + ["No subtitle cues"]
    last_end = 0
    for i, cue in enumerate(cues):
        start, end = cue.get("start_ms"), cue.get("end_ms")
        if not _number(start) or not _number(end) or not 0 <= start < end <= duration + 40:
            errors.append(f"Cue {i}: invalid source-media times")
            continue
        if start < last_end:
            errors.append(f"Cue {i}: overlapping or unordered cues")
        last_end = end
        if not isinstance(cue.get("text"), str) or not cue["text"].strip():
            errors.append(f"Cue {i}: empty text")
        for word in cue.get("words", []):
            ws, we = word.get("start_ms", word.get("startMs")), word.get("end_ms", word.get("endMs"))
            if not _number(ws) or not _number(we) or not start <= ws < we <= end + 40:
                errors.append(f"Cue {i}: word times must be absolute source-media milliseconds within the cue")
        for keyword in cue.get("keywords", []):
            ks, ke = keyword.get("start_ms"), keyword.get("end_ms")
            if not _number(ks) or not _number(ke) or not start <= ks < ke <= end:
                errors.append(f"Cue {i}: keyword timing outside cue")
            ca, cb = keyword.get("char_start"), keyword.get("char_end")
            if ca is not None or cb is not None:
                text = cue.get("text", "")
                if not isinstance(ca, int) or not isinstance(cb, int) or not 0 <= ca < cb <= len(text) or text[ca:cb] != keyword.get("text"):
                    errors.append(f"Cue {i}: keyword character range mismatch")
    try:
        _, resolve = _cue_index(data)
    except (ValueError, TypeError, KeyError) as exc:
        return errors + [str(exc)]
    seen = set()
    ranges = []
    known_tags = _known_tags(settings)
    opportunities = data.get("broll_opportunities", [])
    if not isinstance(opportunities, list):
        return errors + ["broll_opportunities must be a list"]
    for i, opportunity in enumerate(opportunities):
        label = f"B-roll opportunity {i}"
        try:
            identity = opportunity.get("id")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError("id must be a unique non-empty string")
            seen.add(identity)
            start, end = _anchored_range(opportunity, resolve)
            ranges.append((start, end, identity))
            role = ROLE_ALIASES.get(opportunity.get("role"), opportunity.get("role"))
            if role not in ROLES:
                raise ValueError("Unknown semantic role")
            if opportunity.get("priority") not in PRIORITIES:
                raise ValueError("priority must be high, medium, or low")
            if opportunity.get("presentation") not in PRESENTATIONS:
                raise ValueError("Unknown presentation")
            if opportunity.get("keep_within_source_segment") is not True:
                raise ValueError("keep_within_source_segment must be true")
            tags = _tags(opportunity.get("preferred_asset_tags"))
            if known_tags is not None and not tags <= known_tags:
                raise ValueError("Unknown inventory tags: " + ", ".join(sorted(tags - known_tags)))
            minimum, maximum = opportunity.get("min_duration_s"), opportunity.get("max_duration_s")
            if any(v is not None and (not _number(v) or v <= 0) for v in (minimum, maximum)):
                raise ValueError("Duration limits must be positive seconds")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError("min_duration_s exceeds max_duration_s")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append(label + ": " + str(exc))
    for before, after in zip(sorted(ranges), sorted(ranges)[1:]):
        if after[0] < before[1]:
            errors.append(f"Overlapping B-roll opportunities: {before[2]}, {after[2]}")
    protected = data.get("talking_head_preferred", [])
    if not isinstance(protected, list):
        errors.append("talking_head_preferred must be a list")
    else:
        for i, item in enumerate(protected):
            try:
                _anchored_range(item, resolve)
                if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                    raise ValueError("reason is required")
                if item.get("priority", "soft") not in ("soft", "hard"):
                    raise ValueError("priority must be soft or hard")
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errors.append(f"Talking-head interval {i}: {exc}")
    titles = data.get("title_candidates", [])
    if not isinstance(titles, list):
        errors.append("title_candidates must be a list")
    else:
        for i, title in enumerate(titles):
            text = title if isinstance(title, str) else title.get("text") if isinstance(title, dict) else None
            if not isinstance(text, str) or not text.strip():
                errors.append(f"Title candidate {i}: non-empty text is required")
            if isinstance(title, dict) and ("from_cue" in title or "to_cue" in title):
                try:
                    _anchored_range(title, resolve)
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(f"Title candidate {i}: {exc}")
    return errors


def map_document(data, part, speed=None):
    """Deep-copy and convert every cue/word/keyword to composition-local time once."""
    speed = part.get("speed", 1.0) if speed is None else speed
    if not _number(speed) or speed <= 0:
        raise ValueError("Playback speed must be positive")
    if "speed" in part and abs(part["speed"] - speed) > 1e-9:
        raise ValueError("Conflicting part speed and requested speed")
    start, duration = part.get("start_ms"), part.get("duration_ms")
    source_duration = part.get("source_duration_ms")
    if not all(_number(v) for v in (start, duration, source_duration)) or start < 0 or min(duration, source_duration) <= 0:
        raise ValueError("Part requires start_ms, duration_ms and source_duration_ms")
    if abs(duration - round(source_duration / speed)) > 1:
        raise ValueError("Part duration_ms must equal source_duration_ms / speed")
    mapping = {"source_id": part["source_id"], "start_ms": start, "duration_ms": duration,
               "source_duration_ms": source_duration, "speed": speed}
    basis = data.get("timestamp_basis", "source_media")
    if basis == "composition_local":
        if data.get("timing_mapping") != mapping:
            raise ValueError("Document already mapped for a different composition part")
        return copy.deepcopy(data)
    if basis != "source_media":
        raise ValueError("Unsupported timestamp_basis: " + str(basis))
    if data.get("source_id") and data["source_id"] != part["source_id"]:
        raise ValueError("Document source_id does not match composition part")
    result = copy.deepcopy(data)
    legacy = "timestamp_basis" not in data
    limit = start + duration

    def convert(value):
        if not _number(value) or not 0 <= value <= source_duration + 40:
            raise ValueError("Timestamp outside source media")
        return min(limit, start + round(value / speed))

    for cue in result.get("cues", []):
        cue["start_ms"], cue["end_ms"] = convert(cue["start_ms"]), convert(cue["end_ms"])
        if cue["end_ms"] <= cue["start_ms"]:
            raise ValueError("Cue has zero duration after time mapping")
        for keyword in cue.get("keywords", []):
            keyword["start_ms"], keyword["end_ms"] = convert(keyword["start_ms"]), min(cue["end_ms"], convert(keyword["end_ms"]))
            if keyword["start_ms"] < cue["start_ms"] or keyword["end_ms"] <= keyword["start_ms"]:
                raise ValueError("Invalid mapped keyword timing")
            if legacy and keyword.get("timing_precision") == "word":
                keyword["timing_precision"] = "cue"
        for word in cue.get("words", []):
            ws, we = word.get("start_ms", word.get("startMs")), word.get("end_ms", word.get("endMs"))
            word["start_ms"], word["end_ms"] = convert(ws), min(cue["end_ms"], convert(we))
            word.pop("startMs", None)
            word.pop("endMs", None)
            if word["start_ms"] < cue["start_ms"] or word["end_ms"] <= word["start_ms"]:
                raise ValueError("Invalid mapped word timing")
            if legacy:
                word["timing_precision"] = "estimated"
        if cue.get("words"):
            cue["words_timebase"] = "absolute_ms"
        if legacy and cue.get("timing_precision") == "word":
            cue["timing_precision"] = "cue"
    result["timestamp_basis"] = "composition_local"
    result["timing_mapping"] = mapping
    result["source_timestamp_basis"] = "source_media" if not legacy else "legacy_assumed_source_media"
    if legacy:
        result.setdefault("timing_warnings", []).append("Legacy timestamps assumed source-media absolute; word precision is unverified")
    return result


def _subtract(interval, blocks):
    spans = [interval]
    for lo, hi in sorted(blocks):
        next_spans = []
        for a, b in spans:
            if hi <= a or lo >= b:
                next_spans.append((a, b))
            else:
                if a < lo:
                    next_spans.append((a, lo))
                if hi < b:
                    next_spans.append((hi, b))
        spans = next_spans
    return spans


def _asset_ranges(asset):
    duration = asset.get("duration_ms")
    if not _number(duration) or duration <= 0:
        raise ValueError("Asset duration_ms must be positive: " + str(asset.get("id")))
    if "usable_ranges" not in asset:
        return [(0, round(duration))]
    if not isinstance(asset["usable_ranges"], list):
        raise ValueError("usable_ranges must be a list")
    output = []
    for item in asset["usable_ranges"]:
        if isinstance(item, dict):
            a, b = item.get("start_ms"), item.get("end_ms")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            a, b = item
        else:
            raise ValueError("usable_ranges require start_ms/end_ms")
        if not _number(a) or not _number(b) or not 0 <= a < b <= duration:
            raise ValueError("Asset usable range outside media: " + str(asset.get("id")))
        output.append((round(a), round(b)))
    output.sort()
    if any(b[0] < a[1] for a, b in zip(output, output[1:])):
        raise ValueError("Overlapping asset usable ranges")
    return output


def _blocked_intervals(settings):
    result = []
    for item in settings.get("broll_blocked_intervals", settings.get("blocked_intervals", [])):
        if isinstance(item, dict):
            a, b = item.get("start_ms"), item.get("end_ms")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            a, b = item
        else:
            raise ValueError("Blocked intervals require start_ms/end_ms")
        if not _number(a) or not _number(b) or not 0 <= a < b:
            raise ValueError("Invalid B-roll blocked interval")
        result.append((a, b))
    return result


def plan_semantic_broll(segments, documents, assets, settings, rng):
    """Plan tagged footage only inside annotated speech windows; never fill blindly.

    ``documents`` is keyed by source_id and already mapped by ``map_document``.
    Both soft talking-head preferences and hard exclusions are avoided. Soft count
    and ratio targets never force a protected/untagged placement.
    """
    if not segments:
        raise ValueError("No composition segments")
    parts = sorted(segments, key=lambda x: x["start_ms"])
    if any(a["start_ms"] + a["duration_ms"] > b["start_ms"] for a, b in zip(parts, parts[1:])):
        raise ValueError("Overlapping composition segments")
    total = max(p["start_ms"] + p["duration_ms"] for p in parts)
    left = max(0, round(settings.get("opening_no_broll_s", 0) * 1000))
    right = total - max(0, round(settings.get("ending_no_broll_s", 0) * 1000))
    blocked = _blocked_intervals(settings)
    warnings, opportunities = [], []
    asset_ids = set()
    usable_assets = []
    for asset in assets:
        if not asset.get("id") or asset["id"] in asset_ids or not asset.get("path"):
            raise ValueError("Assets require unique id and path")
        asset_ids.add(asset["id"])
        tags = asset.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(x, str) or not x.strip() for x in tags):
            raise ValueError("Asset tags must be strings from the inventory")
        usable_assets.append((asset, set(tags), _asset_ranges(asset)))
    for part in parts:
        source_id = part["source_id"]
        if source_id not in documents:
            raise ValueError("Missing annotated document: " + source_id)
        doc = documents[source_id]
        if doc.get("timestamp_basis") != "composition_local":
            raise ValueError("Planner requires composition_local documents")
        mapping = doc.get("timing_mapping", {})
        if mapping.get("source_id") != source_id or mapping.get("start_ms") != part["start_ms"] or mapping.get("duration_ms") != part["duration_ms"]:
            raise ValueError("Document mapping does not match composition part: " + source_id)
        _, resolve = _cue_index(doc)
        cues = doc.get("cues", [])
        part_end = part["start_ms"] + part["duration_ms"]
        if any(c["start_ms"] < part["start_ms"] or c["end_ms"] > part_end for c in cues):
            raise ValueError("Mapped cue crosses source segment")
        protected = [_anchored_range(x, resolve) for x in doc.get("talking_head_preferred", [])]
        previous_ranges = []
        for annotation in doc.get("broll_opportunities", []):
            role = ROLE_ALIASES.get(annotation.get("role"), annotation.get("role"))
            if role not in ROLES or annotation.get("priority") not in PRIORITIES or annotation.get("presentation") not in PRESENTATIONS:
                raise ValueError("Invalid semantic annotation: " + str(annotation.get("id")))
            if annotation.get("keep_within_source_segment") is not True:
                raise ValueError("Semantic B-roll must stay within its source segment")
            wanted = _tags(annotation.get("preferred_asset_tags"))
            start, end = _anchored_range(annotation, resolve)
            if any(start < b and end > a for a, b in previous_ranges):
                raise ValueError("Overlapping B-roll opportunities in " + source_id)
            previous_ranges.append((start, end))
            start, end = max(start, part["start_ms"], left), min(end, part_end, right)
            spans = _subtract((start, end), blocked + protected) if end > start else []
            opportunities.append({"id": annotation["id"], "source_id": source_id,
                                  "start_ms": start, "end_ms": end, "role": role,
                                  "priority": annotation["priority"], "presentation": annotation["presentation"],
                                  "status": "pending", "selected_clip_count": 0,
                                  "_spans": spans, "_wanted": wanted, "_annotation": annotation,
                                  "_cues": cues})
    if assets and not opportunities:
        raise ValueError("Semantic B-roll requested with footage but no broll_opportunities; annotate source subtitles before production")
    if not assets:
        warnings.append("No B-roll assets; retained talking-head footage")
    count = settings.get("broll_count", [1, 5])
    if not isinstance(count, (list, tuple)) or len(count) != 2 or any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in count) or count[0] > count[1]:
        raise ValueError("broll_count must be a non-negative integer range")
    goal = rng.randint(count[0], count[1])
    ratio = settings.get("broll_ratio")
    ratio_target = None
    if ratio is not None:
        if not isinstance(ratio, (list, tuple)) or len(ratio) != 2 or any(not _number(x) for x in ratio) or not 0 <= ratio[0] <= ratio[1] <= 1:
            raise ValueError("broll_ratio must be a range within 0..1")
        ratio_target = round(total * rng.uniform(*ratio))
    ranked = [(o, rng.random()) for o in opportunities]
    ranked.sort(key=lambda x: (-PRIORITIES[x[0]["priority"]], x[1]))
    clips = []
    selected_intervals = []
    for opportunity, _ in ranked:
        annotation = opportunity["_annotation"]
        matched = [(a, t, r) for a, t, r in usable_assets if t & opportunity["_wanted"] and r]
        if not matched:
            opportunity["status"] = "no_matching_asset"
            warnings.append(f"{opportunity['source_id']}/{opportunity['id']}: no usable asset matches tags {sorted(opportunity['_wanted'])}; retained talking head")
            continue
        if not opportunity["_spans"]:
            opportunity["status"] = "protected"
            continue
        if len(clips) >= goal or (ratio_target is not None and sum(c["duration_ms"] for c in clips) >= ratio_target):
            opportunity["status"] = "not_selected"
            continue
        limits = settings.get("broll_duration_s", [2, 32])
        minimum = round(annotation.get("min_duration_s", limits[0]) * 1000)
        maximum = round(annotation.get("max_duration_s", 6 if annotation["presentation"] == "short_sequence" else limits[1]) * 1000)
        if minimum <= 0 or maximum < minimum:
            raise ValueError("Invalid semantic duration limits")
        sequence = annotation["presentation"] == "short_sequence"
        capacity = goal - len(clips) if sequence else 1
        for _ in range(capacity):
            if ratio_target is not None and sum(c["duration_ms"] for c in clips) >= ratio_target:
                break
            gap = 0 if sequence else round(settings.get("broll_gap_s", [0, 0])[0] * 1000)
            occupied = [(a - gap, b + gap) for a, b in selected_intervals]
            spans = [piece for span in opportunity["_spans"] for piece in _subtract(span, occupied)]
            candidates = []
            for asset, tags, ranges in matched:
                longest = max(b - a for a, b in ranges)
                cap = min(maximum, longest)
                for a, b in spans:
                    contained = [c for c in opportunity["_cues"] if c["start_ms"] >= a and c["end_ms"] <= b]
                    for i, first in enumerate(contained):
                        for last in contained[i:]:
                            length = last["end_ms"] - first["start_ms"]
                            if length > cap:
                                break
                            if length >= minimum:
                                candidates.append((first["start_ms"], last["end_ms"], asset, tags, ranges))
            if not candidates:
                break
            # Prefer the beginning of the semantic explanation and a long enough
            # complete phrase. Randomness stays within these feasible candidates.
            earliest = min(c[0] for c in candidates)
            candidates = [c for c in candidates if c[0] == earliest]
            longest = max(c[1] - c[0] for c in candidates)
            candidates = [c for c in candidates if c[1] - c[0] >= max(minimum, longest * .8)]
            best_match = max(len(c[3] & opportunity["_wanted"]) for c in candidates)
            candidates = [c for c in candidates if len(c[3] & opportunity["_wanted"]) == best_match]
            if clips:
                alternatives = [c for c in candidates if c[2]["id"] != clips[-1]["asset_id"]]
                if alternatives:
                    candidates = alternatives
            start, end, asset, tags, ranges = rng.choice(candidates)
            length = end - start
            ranges = [(a, b) for a, b in ranges if b - a >= length]
            zero = [r for r in ranges if r[0] == 0]
            source_range = zero[0] if zero else ranges[0]
            mode = settings.get("broll_source_offset_mode", "start")
            if mode not in ("start", "random"):
                raise ValueError("broll_source_offset_mode must be start or random")
            source_start = rng.randint(source_range[0], source_range[1] - length) if mode == "random" else source_range[0]
            clips.append({"asset_id": asset["id"], "path": asset["path"], "start_ms": start,
                          "duration_ms": length, "source_start_ms": source_start, "intro": None, "outro": None,
                          "opportunity_id": opportunity["id"], "source_id": opportunity["source_id"],
                          "role": opportunity["role"], "matched_tags": sorted(tags & opportunity["_wanted"])})
            selected_intervals.append((start, end))
            opportunity["selected_clip_count"] += 1
        opportunity["status"] = "selected" if opportunity["selected_clip_count"] else "no_fitting_window"
        if opportunity["status"] == "no_fitting_window":
            warnings.append(f"{opportunity['source_id']}/{opportunity['id']}: no complete subtitle window fits duration, asset and protection limits")
    clips.sort(key=lambda x: x["start_ms"])
    if len(clips) < goal:
        warnings.append(f"Selected {len(clips)} semantic clips against soft count target {goal}; no arbitrary filler inserted")
    return {"clips": clips, "warnings": warnings,
            "opportunities": [{k: v for k, v in o.items() if not k.startswith("_")} for o in opportunities]}


def choose_music(pool, itemduration, settings, state, rng):
    """Choose a least-used eligible track; state is batch-local and reproducible."""
    if not pool:
        return None
    start = round(settings.get("music_source_start_s", 0) * 1000)
    eligible = list(pool)
    if not settings.get("music_loop", False) and settings.get("music_short_policy", "reject") == "reject":
        eligible = [x for x in pool if x.get("duration_ms", 0) - start >= itemduration]
    if not eligible:
        raise ValueError("No sufficiently long BGM; select longer music or an explicit short-music policy")
    usage = state.setdefault("music_usage", {})
    def key(asset):
        return str(asset.get("id") or asset.get("path"))
    least = min(usage.get(key(x), 0) for x in eligible)
    choices = [x for x in eligible if usage.get(key(x), 0) == least]
    if len(choices) > 1:
        choices = [x for x in choices if key(x) != state.get("last_music_id")] or choices
    chosen = rng.choice(choices)
    usage[key(chosen)] = usage.get(key(chosen), 0) + 1
    state["last_music_id"] = key(chosen)
    return chosen
