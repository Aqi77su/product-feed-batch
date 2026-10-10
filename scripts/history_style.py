"""Authorized local history analysis; evidence and preset suggestions stay separate."""
from __future__ import annotations
import argparse
import collections
import copy
import datetime
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor/aoguai-crypto"))

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def mode(values):
    return collections.Counter(json.dumps(x, sort_keys=True) for x in values).most_common(1)[0][0] if values else None

def main_files(directory):
    directory = Path(directory).resolve()
    if directory.is_file():
        return [directory]
    project = directory / "Timelines/project.json"
    if project.is_file():
        try:
            path = directory / "Timelines" / read(project)["main_timeline_id"] / "draft_content.json"
            if path.is_file():
                return [path]
        except (OSError, ValueError, KeyError):
            pass
    return [next((directory / n for n in ("draft_info.json", "draft_content.json") if (directory / n).is_file()), None)]

def _range(segment):
    value = segment.get("target_timerange", {})
    start = value.get("start", 0) / 1e6
    return start, start + value.get("duration", 0) / 1e6

def _merge(ranges):
    result = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if result and start <= result[-1][1] + 1e-6:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result

def _overlap(interval, active):
    return sum(max(0, min(interval[1], end) - max(interval[0], start)) for start, end in active)

def _inner(material):
    value = material.get("content", {})
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}

def _lookup(materials):
    return {m["id"]: (kind, m) for kind, group in materials.items() if isinstance(group, list)
            for m in group if isinstance(m, dict) and "id" in m}

def _main_ranges(doc, lookup):
    candidates = []
    for i, track in enumerate(doc.get("tracks", [])):
        if track.get("type") != "video":
            continue
        segments = [s for s in track.get("segments", [])
                    if lookup.get(s.get("material_id"), (None, {}))[1].get("type") == "video"]
        if segments:
            candidates.append((i, track, segments))
    if not candidates:
        return None, []
    selected = next((x for x in candidates if x[1].get("name", "").lower() == "main"), candidates[0])
    return selected[0], _merge(_range(s) for s in selected[2])

def _references(segment, lookup):
    """Resolve actual references, including nested native-template components."""
    found = {}
    queue = list(segment.get("extra_material_refs", [])) + [segment.get("material_id")]
    while queue:
        ident = queue.pop()
        if not ident or ident in found or ident not in lookup:
            continue
        kind, material = lookup[ident]
        found[ident] = {"kind": kind, "data": copy.deepcopy(material)}
        queue.extend(material.get("extra_material_refs", []))
        for component in material.get("text_info_resources", []) + material.get("non_text_info_resources", []):
            queue.append(component.get("text_material_id"))
            queue.extend(component.get("extra_material_refs", []))
    return list(found.values())

def _role(material, inner, position, template=False):
    """Use declared type/ASR structure/position; never duration < 15 seconds."""
    y = position.get("y", 0)
    if material.get("typesetting") == 1:
        return "side_benefit", "explicit_vertical_typesetting"
    if material.get("type") == "text_template_subtitle":
        return "caption_template", "explicit_caption_template_type"
    if material.get("type") == "subtitle" or material.get("recognize_task_id") or material.get("words", {}).get("text"):
        return "caption", "subtitle_type_or_asr_word_structure"
    text = inner.get("text", "")
    sizes = [s["size"] for s in inner.get("styles", []) if isinstance(s.get("size"), (int, float))]
    if y < -.55 and ("广审" in text or "注册证" in text or "产品说明书" in text or
                     ("\n" in text and sizes and max(sizes) <= 7)):
        return "disclosure", "lower_small_multiline_or_declared_product_notice"
    if template and -.6 < y < .3:
        return "caption_template", "template_in_caption_region_inferred"
    if -.6 < y < .3:
        return "caption_callout", "text_in_caption_region_inferred"
    if y >= .5 or y <= -.6:
        return "persistent_title", "outside_caption_region_inferred"
    return "unknown_text", "insufficient_semantic_evidence"

def analyze_document(doc):
    materials = doc["materials"]
    lookup = _lookup(materials)
    main_track, active = _main_ranges(doc, lookup)
    texts, text_segments, audios, videos, visuals, effects = [], [], [], [], [], []
    effect_seen = set()
    for track_index, track in enumerate(doc["tracks"]):
        for segment in track.get("segments", []):
            kind, material = lookup.get(segment.get("material_id"), (None, {}))
            start, end = _range(segment)
            overlap = _overlap((start, end), active) if active else None
            excluded = overlap is not None and overlap <= 1e-6
            position = segment.get("clip", {}).get("transform", {})
            scale = segment.get("clip", {}).get("scale", {})
            refs = _references(segment, lookup)
            animations = [a for ref in refs if ref["kind"] == "material_animations"
                          for a in ref["data"].get("animations", [])]
            base = {"segment_id": segment.get("id"), "material_id": segment.get("material_id"),
                    "track_index": track_index, "start_s": start, "end_s": end, "duration_s": end-start,
                    "overlap_main_s": overlap, "excluded_from_defaults": excluded,
                    "clip": copy.deepcopy(segment.get("clip", {})),
                    "render_index": segment.get("render_index"), "track_render_index": segment.get("track_render_index")}
            for ref in refs:
                if ref["kind"] in ("effects", "material_animations", "video_effects", "transitions"):
                    key = (ref["kind"], ref["data"].get("id"))
                    if key not in effect_seen:
                        effects.append({"type": ref["kind"], "data": ref["data"]})
                        effect_seen.add(key)
            if track.get("type") == "text":
                is_template = kind == "text_templates"
                inner = _inner(material)
                role, reason = _role(material, inner, position, is_template)
                components = [(material, None)]
                if is_template:
                    components = [(lookup.get(c.get("text_material_id"), (None, {}))[1], c)
                                  for c in material.get("text_info_resources", [])]
                entry = {**base, "role": role, "role_evidence": reason,
                         "animations": copy.deepcopy(animations), "text_components": [],
                         "native_template": copy.deepcopy(material) if is_template else None,
                         "referenced_materials": refs if is_template else []}
                for text_material, attachment in components:
                    content = _inner(text_material)
                    entry["text_components"].append({"material": copy.deepcopy(text_material),
                        "content": content, "attach_info": copy.deepcopy(attachment),
                        "words": copy.deepcopy(text_material.get("words", {})),
                        "current_words": copy.deepcopy(text_material.get("current_words", {})),
                        "caption_template_info": copy.deepcopy(text_material.get("caption_template_info", {}))})
                    for style in content.get("styles", []):
                        strokes = style.get("strokes", [])
                        body_candidate = role == "caption" and not is_template and not excluded
                        texts.append({**base, "role": "subtitle_candidate" if body_candidate else role,
                            "semantic_role": role, "text": content.get("text", ""), "range": style.get("range"),
                            "size": style.get("size"), "color": style.get("fill", {}).get("content", {}).get("solid", {}).get("color"),
                            "y": position.get("y"), "scale_x": scale.get("x", 1), "scale_y": scale.get("y", 1),
                            "border_json_width": strokes[0].get("width") if strokes else None,
                            "font_path": style.get("font", {}).get("path") or text_material.get("font_path"),
                            "font_id": style.get("font", {}).get("id") or text_material.get("font_resource_id"),
                            "effect_style": copy.deepcopy(style.get("effectStyle")), "fill": copy.deepcopy(style.get("fill")),
                            "is_template_child": is_template})
                text_segments.append(entry)
            elif track.get("type") in ("audio", "video"):
                source = segment.get("source_timerange") or {}
                speed_refs = [x["data"] for x in refs if x["kind"] == "speeds"]
                speed = segment.get("speed", next((x["speed"] for x in speed_refs if "speed" in x), None))
                speed_evidence = "explicit_segment_or_speed_material" if speed is not None else "unknown"
                if speed is None and source.get("duration") and end > start:
                    speed = source["duration"] / (1e6 * (end-start))
                    speed_evidence = "source_to_target_duration_ratio"
                volume = segment.get("volume")
                fades = [x["data"] for x in refs if x["kind"] == "audio_fades"]
                values = {**base, "material_type": material.get("type"), "name": material.get("name") or material.get("material_name"),
                    "path": material.get("path"), "resource_id": material.get("resource_id"),
                    "volume": volume, "volume_explicit": "volume" in segment,
                    "muted": volume == 0 if volume is not None else None, "speed": speed,
                    "speed_evidence": speed_evidence,
                    "source_start_s": source.get("start", 0)/1e6, "source_duration_s": source.get("duration", 0)/1e6,
                    "fade_refs": [x.get("id") for x in fades], "fades": [{**x,
                        "fade_in_s": x.get("fade_in_duration", 0)/1e6,
                        "fade_out_s": x.get("fade_out_duration", 0)/1e6,
                        "has_nonzero_fade": bool(x.get("fade_in_duration", 0) or x.get("fade_out_duration", 0))} for x in fades],
                    "animations": copy.deepcopy(animations)}
                if track.get("type") == "audio":
                    audio_type = material.get("type", "")
                    values["role"] = "music" if audio_type == "music" else "sound_effect" if audio_type in ("sound", "sound_effect") else "unclassified_audio"
                    values["role_evidence"] = "material.type" if values["role"] != "unclassified_audio" else "requires_user_classification"
                    audios.append(values)
                else:
                    values.update(role="photo_overlay" if material.get("type") == "photo" else "main_video" if track_index == main_track else "video_overlay",
                                  width=material.get("width"), height=material.get("height"), crop=copy.deepcopy(material.get("crop", {})),
                                  matting=copy.deepcopy(material.get("matting", {})))
                    videos.append(values)
                    if material.get("type") == "photo":
                        visuals.append(values)
            else:
                visuals.append({**base, "role": "shape" if kind == "shapes" else kind or "unresolved_visual",
                                "material": copy.deepcopy(material), "referenced_materials": refs})
    return {"canvas": copy.deepcopy(doc.get("canvas_config", {})), "duration_s": doc.get("duration", 0)/1e6,
            "main_track_index": main_track, "effective_main_ranges_s": active,
            "evidence_warnings": [] if active else ["No main video range established; roles are evidence only"],
            "texts": texts, "text_segments": text_segments, "audios": audios, "effects": effects,
            "video_segments": videos, "visual_components": visuals,
            "keyframes": copy.deepcopy(doc.get("keyframes", {})),
            "render_index_track_mode_on": doc.get("render_index_track_mode_on")}

def _suggest(records):
    texts = [x for r in records for x in r["texts"] if x["role"] == "subtitle_candidate" and not x["excluded_from_defaults"]]
    settings = {}
    for field, key in (("size", "base_font_size"), ("color", "subtitle_color"), ("y", "subtitle_y"), ("scale_x", "subtitle_scale")):
        values = [t[field] for t in texts if t.get(field) is not None]
        if values:
            settings[key] = json.loads(mode(values))
    widths = [t["border_json_width"] for t in texts if t.get("border_json_width") is not None]
    if widths:
        settings["subtitle_border_width"] = min(100, max(0, statistics.median(widths)/.002))
    music = [x for r in records for x in r["audios"] if x["role"] == "music" and not x["excluded_from_defaults"] and x.get("volume") is not None]
    if music:
        settings["music_volume"] = json.loads(mode([x["volume"] for x in music]))
    return settings

def analyze(paths, out, install=None, approved=False):
    if not approved:
        raise ValueError("Explicit user authorization required for this historical scope")
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    records, errors, seen = [], [], set()
    for path in paths:
        for file in main_files(path):
            if not file:
                errors.append({"path": str(path), "error": "No main draft"})
                continue
            try:
                raw = file.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                try:
                    value, encrypted = json.loads(raw.decode("utf-8-sig")), False
                except (ValueError, UnicodeError):
                    if not install:
                        raise ValueError("Encrypted draft needs local videoeditor.dll install directory")
                    from draft_crypto import decrypt_draft_file
                    target = out / "decrypted" / (digest + ".json")
                    decrypt_draft_file(file, target, jy_install_dir=install, isolated=True)
                    value, encrypted = read(target), True
                docs, doc_seen = [], set()
                def collect(node):
                    if isinstance(node, dict):
                        if isinstance(node.get("tracks"), list) and isinstance(node.get("materials"), dict):
                            ident = hashlib.sha256(json.dumps(node, sort_keys=True).encode()).hexdigest()
                            if ident not in doc_seen:
                                docs.append(node)
                                doc_seen.add(ident)
                        for child in node.values():
                            collect(child)
                    elif isinstance(node, list):
                        for child in node:
                            collect(child)
                collect(value)
                if not docs:
                    raise ValueError("No draft tracks/materials found")
                analyses = [analyze_document(doc) for doc in docs]
                record = {"source": str(file), "hash": digest, "encrypted": encrypted, "documents": analyses}
                for key in ("texts", "text_segments", "audios", "effects", "video_segments", "visual_components"):
                    record[key] = [x for a in analyses for x in a[key]]
                records.append(record)
            except Exception as exc:
                errors.append({"path": str(file), "error": str(exc)})
    settings = _suggest(records)
    report = {"schema_version": "1.1", "scope": [str(x) for x in paths], "records": records, "errors": errors,
              "suggested_settings": settings,
              "suggestion_evidence": {"method": "Mode of referenced in-range ASR caption styles; declared music material volume only",
                  "requires_confirmation": True, "mixed_canvas_sizes": sorted({(a["canvas"].get("width", 0), a["canvas"].get("height", 0)) for r in records for a in r["documents"]})},
              "note": "Evidence is not a render check. Role reasons are explicit. Unknown audio remains unclassified. Templates/titles/orphan layers do not set base subtitle size; confirm suggestions before preset creation."}
    write(out / "analysis.json", report)
    return {"report": str(out / "analysis.json"), "drafts": len(records), "errors": errors, "suggested_settings": settings}

def create(spec, out):
    spec = read(spec)
    if spec.get("confirmed") is not True:
        raise ValueError("User must confirm preset name, settings and resource scope")
    name = spec.get("name", "")
    if not name or name in (".", "..") or any(c in name for c in '/\\<>:"|?*'):
        raise ValueError("Invalid preset name")
    directory = Path(out).resolve() / name
    if directory.exists():
        raise ValueError("Preset exists; choose versioned name")
    catalog = spec.get("catalog", {})
    for key in ("fonts", "music", "sfx"):
        for item in catalog.get(key, []):
            if not Path(item["path"]).is_file():
                raise ValueError("Resource missing: " + item["path"])
    write(directory / "resources.json", catalog)
    write(directory / "preset.json", {"schema_version": "1.0", "name": name, "description": spec.get("description", ""),
        "settings": spec.get("settings", {}), "layout_defaults": spec.get("layout_defaults", spec.get("layout", {})), "resource_catalog": "resources.json",
        "evidence": spec.get("evidence", []), "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "local_only": True})
    return {"preset": str(directory / "preset.json")}

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    command = sub.add_parser("analyze")
    command.add_argument("--paths", nargs="+")
    command.add_argument("--root")
    command.add_argument("--days", type=int)
    command.add_argument("--install")
    command.add_argument("--out", required=True)
    command.add_argument("--authorized", action="store_true")
    command = sub.add_parser("create")
    command.add_argument("--spec", required=True)
    command.add_argument("--out", required=True)
    command = sub.add_parser("list")
    command.add_argument("--root", required=True)
    args = parser.parse_args()
    if args.cmd == "analyze":
        # Permission must precede even enumeration of historical folders.
        if not args.authorized:
            raise ValueError("Explicit user authorization required for this historical scope")
        paths = args.paths or []
        if args.root:
            if not args.days or args.days < 1:
                raise ValueError("Positive --days required for historical root scan")
            cutoff = datetime.datetime.now().timestamp() - args.days*86400
            paths += [str(d) for d in Path(args.root).iterdir() if d.is_dir()
                      and any(f and f.stat().st_mtime >= cutoff for f in main_files(d))]
        if not paths:
            raise ValueError("No drafts in authorized scope")
        result = analyze(paths, args.out, args.install, args.authorized)
    elif args.cmd == "create":
        result = create(args.spec, args.out)
    else:
        result = [{"path": str(f), **read(f)} for f in Path(args.root).glob("*/preset.json")]
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
