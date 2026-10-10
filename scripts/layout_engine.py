"""Deterministic, editable layout components built from explicitly supplied copy.

Coordinates use Jianying's half-canvas units. A bundle is chosen once per item;
positions are never independently randomized. This module never invents copy.
"""
from __future__ import annotations

import copy
import json
import unicodedata
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = {
    "header": {
        "headline": {"x": 0, "y": .84, "size": 12, "scale": 1.15, "width": .86, "height": .10, "minimum_scale": .8},
        "subheadline": {"x": 0, "y": .69, "size": 11, "scale": 1.10, "width": .86, "height": .07, "minimum_scale": .8},
    },
    "header_panel": {
        "panel": {"x": 0, "y": .81, "width": .89, "height": .16, "color": "#FFDE00"},
        "headline": {"x": 0, "y": .88, "size": 12, "scale": 1.15, "width": .83, "height": .06, "minimum_scale": .8},
        "subheadline": {"x": 0, "y": .75, "size": 11, "scale": 1.10, "width": .83, "height": .06, "minimum_scale": .8},
    },
    "plain": {},
}


def _uid():
    return uuid.uuid4().hex.upper()


def _layout(job, settings):
    result = copy.deepcopy(settings.get("layout_options", {}))
    result.update(copy.deepcopy(job.get("layout", {})))
    return result


def resolve_overlays(job, settings):
    """Return copied legacy overlays plus the optional explicit product image.

    The caller stages/exports images with its existing VideoMaterial code. Text
    overlays are handled by append_layout, and must not be exported twice.
    """
    overlays = copy.deepcopy(job.get("overlays", []))
    layout = _layout(job, settings)
    image = layout.get("product_image")
    if image and layout.get("enabled", True):
        image = {"path": image} if isinstance(image, str) else copy.deepcopy(image)
        if not image.get("path"):
            raise ValueError("layout.product_image requires a path")
        image["kind"] = "image"
        image.setdefault("role", "product_image")
        image.setdefault("transform", {"scale_x": .2567, "scale_y": .2567,
                                       "transform_x": -.507, "transform_y": -.725})
        if not any(o.get("kind") == "image" and o.get("path") == image["path"] for o in overlays):
            overlays.append(image)
    return overlays


def _copy_value(value, item):
    if isinstance(value, dict) and "text" not in value:
        for key in (item.get("script_id"), item.get("source_id"), "default"):
            if key is not None and key in value:
                return value[key]
        return None
    return value


def _component(value):
    if not value:
        return None
    obj = {"text": value} if isinstance(value, str) else copy.deepcopy(value)
    if obj.get("enabled", True) is False or not str(obj.get("text", "")).strip():
        return None
    if not isinstance(obj.get("text"), str):
        raise ValueError("Layout text must be a string")
    return obj


def _headline(layout, job, item, rng):
    explicit = _copy_value(layout.get("headline"), item)
    if explicit:
        return _component(explicit)
    candidates = item.get("title_candidates") or layout.get("title_candidates", job.get("title_candidates", []))
    if isinstance(candidates, dict):
        candidates = _copy_value(candidates, item) or []
    if isinstance(candidates, str):
        candidates = [candidates]
    suitable = []
    for candidate in candidates:
        c = _component(candidate)
        if not c:
            continue
        if any(c.get(k) is not None and c[k] != item.get(k) for k in ("script_id", "source_id")):
            continue
        suitable.append(c)
    if suitable:
        return copy.deepcopy(rng.choice(suitable))
    return _component(_copy_value(job.get("titles", {}), item) or job.get("title"))


def _measure(text, font_path, pixels, vertical=False):
    """Measure the actual font where available; otherwise use conservative ems."""
    lines = text.split("\n") or [""]
    try:
        from PIL import ImageFont
        if not font_path:
            raise OSError("No explicit font")
        face = ImageFont.truetype(str(font_path), max(1, round(pixels)))
        if vertical:
            width = len(lines) * pixels * 1.25
            height = max(sum(1 for c in line if not c.isspace()) for line in lines) * pixels * 1.25
        else:
            width = max(float(face.getlength(line)) for line in lines)
            height = len(lines) * pixels * 1.25
        return width, height, "font_metrics"
    except (ImportError, OSError, ValueError):
        def em(line):
            return sum(1 if unicodedata.east_asian_width(c) in "WF" else .7 for c in line)
        if vertical:
            return len(lines) * pixels * 1.3, max(len(line) for line in lines) * pixels * 1.3, "conservative_estimate"
        return max(em(line) for line in lines) * pixels * 1.08, len(lines) * pixels * 1.3, "conservative_estimate"


def fit_text(text, config, font, width, height, vertical=False):
    """Fit the whole string without discarding punctuation, letters or newlines.

    size-to-pixels is a conservative 1080-wide calibration, not a claim of a
    render-verified Jianying font metric. A final scale is retained in events.
    """
    size = float(config.get("size", 12))
    scale = float(config.get("scale", 1))
    if size <= 0 or scale <= 0:
        raise ValueError("Layout size and scale must be positive")
    max_w = float(config.get("width", .86)) * width
    max_h = float(config.get("height", .10)) * height
    x, y = float(config.get("x", 0)), float(config.get("y", 0))
    # Clip the available box to safe canvas boundaries before measuring.
    max_w = min(max_w, width * max(0, .96 - abs(x)))
    max_h = min(max_h, height * max(0, .96 - abs(y)))
    if max_w <= 0 or max_h <= 0:
        raise ValueError("Layout position is outside the safe canvas")
    raw_w, raw_h, method = _measure(text, (font or {}).get("path"), size * 4 * width / 1080, vertical)
    ratio = min(1, max_w / max(1, raw_w * scale), max_h / max(1, raw_h * scale))
    fitted = scale * ratio
    if fitted < float(config.get("minimum_scale", .55)):
        raise ValueError("Layout text exceeds its readable area; shorten copy or choose a larger component")
    return size, fitted, {"method": method, "width_px": round(raw_w * fitted, 2),
                          "height_px": round(raw_h * fitted, 2), "box_width_px": max_w,
                          "box_height_px": max_h, "original_scale": scale,
                          "final_scale": fitted, "render_verified": False}


def _track(content, role, kind):
    tracks = content.setdefault("tracks", [])
    name = "Layout:" + role
    for i, track in enumerate(tracks):
        if track.get("name") == name:
            return track, i
    track = {"id": _uid(), "type": kind, "name": name, "segments": [], "is_default_name": False}
    tracks.append(track)
    return track, len(tracks) - 1


def _append_text(content, role, component, config, item, settings, font, stage):
    from caption_engine import _segment, _style
    width, height = int(settings.get("width", 1080)), int(settings.get("height", 1920))
    config = {**config, **{k: v for k, v in component.items() if k in
              {"x", "y", "size", "scale", "width", "height", "minimum_scale", "color"}}}
    if "font_size" in component:
        config["size"] = component["font_size"]
    text = component["text"]
    vertical = bool(component.get("vertical", False))
    size, scale, estimate = fit_text(text, config, font, width, height, vertical)
    color = component.get("color", config.get("color", [1, 1, 1]))
    effective = {**settings, "base_font_size": size, "subtitle_color": color,
                 "subtitle_scale": scale, "subtitle_x": config.get("x", 0), "subtitle_y": config.get("y", 0),
                 "subtitle_border_width": component.get("border_width", settings.get("subtitle_border_width", 40))}
    mid = _uid()
    raw = _segment(mid, item["start_ms"], item["start_ms"]+item["duration_ms"], effective)
    native_font = {"id": "", "path": ""}
    if font and font.get("path"):
        staged = stage(font["path"])
        resource = str(font.get("resource_id") or font.get("font_id") or font.get("id") or "")
        native_font = {"id": resource, "path": staged}
    inner = {"text": text, "styles": [_style(text, effective, native_font)]}
    mat = {"id": mid, "type": "text", "font_size": size, "font_path": native_font["path"],
           "font_resource_id": native_font["id"], "line_feed": 0, "force_apply_line_max_width": False,
           "typesetting": 1 if vertical else 0, "alignment": 3 if vertical else 0 if role == "Disclosure" else 1,
           "line_spacing": .02, "letter_spacing": 0, "check_flag": 15, "global_alpha": 1.0,
           "words": {}, "current_words": {}, "subtitle_keywords": {}}
    if role == "Disclosure":
        mat.update(line_spacing=.12, fixed_width=estimate["box_width_px"] / max(scale, .01))
    mat["content"] = json.dumps(inner, ensure_ascii=False)
    track, index = _track(content, role, "text")
    raw.update(track_render_index=index, render_index=15000 + index,
               enable_adjust=False, enable_lut=False, enable_hsl=False)
    content.setdefault("materials", {}).setdefault("texts", []).append(mat)
    track["segments"].append(raw)
    return {"component": role, "segment_id": raw["id"], "text": text, "vertical": vertical,
            "start_ms": item["start_ms"], "duration_ms": item["duration_ms"],
            "transform": raw["clip"], "fit": estimate}


def _append_panel(content, config, item, settings):
    width = float(config["width"]) * settings.get("width", 1080)
    height = float(config["height"]) * settings.get("height", 1920)
    if width <= 0 or height <= 0 or width > settings.get("width", 1080):
        raise ValueError("Invalid panel dimensions")
    ident, segid = _uid(), _uid()
    points = [-width/2, height/2, width/2, height/2, width/2, -height/2, -width/2, -height/2]
    mat = {"id": ident, "type": "shape", "shape_type": 4, "name": "rect_item",
           "custom_points": points, "custom_points_in": [0.0]*8, "custom_points_out": [0.0]*8,
           "shape_size": [width, height], "border_width": 0, "border_alpha": 0,
           "border_color": "#000000", "shadow_color": "#000000", "shadow_alpha": 0,
           "shadow_distance": 0, "shadow_angle": 0, "check_flag": 17, "combo_info": {},
           "fill_render_style": {"color": {"solid": {"color": config.get("color", "#FFDE00")}}}}
    track, index = _track(content, "HeaderPanel", "sticker")
    seg = {"id": segid, "material_id": ident, "target_timerange": {
               "start": round(item["start_ms"]*1000), "duration": round(item["duration_ms"]*1000)},
           "render_timerange": {}, "uniform_scale": {}, "clip": {"scale": {"x": 1., "y": 1.},
               "transform": {"x": config.get("x", 0), "y": config.get("y", .81)}, "flip": {}},
           "render_index": 15000+index, "track_render_index": index, "enable_lut": False,
           "enable_adjust": False, "enable_hsl": False, "responsive_layout": {}, "source": "segmentsourcenormal"}
    content.setdefault("materials", {}).setdefault("shapes", []).append(mat)
    track["segments"].append(seg)
    return {"component": "HeaderPanel", "segment_id": segid, "color": config.get("color", "#FFDE00")}


def append_layout(content, item, job, settings, catalog, font, stage, rng):
    """Append one item's explicit persistent components to a saved raw draft."""
    layout = _layout(job, settings)
    enabled = layout.get("enabled", True) is not False
    if not enabled:
        # Explicit legacy text overlays retain their own lifecycle even when
        # automatic header/side/product layout components are disabled.
        layout = {"bundle": "plain"}
    headline = _headline(layout, job, item, rng) if enabled else None
    subheadline = _component(_copy_value(layout.get("subheadline"), item))
    bundle_name = layout.get("bundle", "auto")
    if bundle_name == "auto":
        allowed = layout.get("bundle_candidates", ["header", "header_panel"])
        if not allowed or any(x not in BUNDLES for x in allowed):
            raise ValueError("Unknown/empty layout bundle candidates")
        bundle_name = rng.choice(allowed) if headline or subheadline else "plain"
    if bundle_name not in BUNDLES:
        raise ValueError("Unknown layout bundle: " + str(bundle_name))
    bundle = copy.deepcopy(BUNDLES[bundle_name])
    events = [{"component": "Bundle", "bundle": bundle_name}]
    if bundle_name != "plain" and (headline or subheadline):
        # Reserve the background track before title tracks even when this item
        # chooses the plain header; later items may use a panel in this batch.
        _track(content, "HeaderPanel", "sticker")
        if bundle.get("panel"):
            if layout.get("panel_color"):
                bundle["panel"]["color"] = layout["panel_color"]
            events.append(_append_panel(content, bundle["panel"], item, settings))
        for role, value in (("headline", headline), ("subheadline", subheadline)):
            if value:
                config = bundle[role]
                if bundle.get("panel"):
                    config["color"] = [0.08, .08, .08]
                events.append(_append_text(content, role.title(), value, config, item, settings, font, stage))
    side = _component(_copy_value(layout.get("side_benefit"), item))
    if side:
        side["vertical"] = True
        which = side.get("side", layout.get("side", "right"))
        if which not in ("left", "right"):
            raise ValueError("side_benefit.side must be left or right")
        config = {"x": -.83 if which == "left" else .83, "y": .16, "size": 11,
                  "scale": .85, "width": .09, "height": .48}
        events.append(_append_text(content, "SideBenefit", side, config, item, settings, font, stage))
    disclosure = _component(_copy_value(layout.get("disclosure"), item))
    if disclosure:
        config = {"x": .246, "y": -.719, "size": 5, "scale": .873, "width": .56, "height": .20}
        events.append(_append_text(content, "Disclosure", disclosure, config, item, settings, font, stage))
    for i, overlay in enumerate(job.get("overlays", [])):
        if overlay.get("kind") != "text":
            continue
        obj = _component(overlay)
        if not obj:
            continue
        config = {"x": 0, "y": -.80, "size": 5, "scale": 1, "width": .88, "height": .14}
        config.update({k: overlay[k] for k in ("x", "y", "scale", "width", "height") if k in overlay})
        events.append(_append_text(content, "TextOverlay"+str(i), obj, config, item, settings, font, stage))
    return events
