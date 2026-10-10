import type { SubtitleSegment } from "../asr/types.js";
/**
 * Options for customising the ASS [V4+ Styles] section.
 * Every field is optional — omitted values fall back to the built-in defaults.
 *
 * Colour strings use the ASS `&HAABBGGRR` hex format, e.g. `&H00FFFFFF` for white.
 */
export interface AssStyleOptions {
    /** Font name (default: `"Arial"`). */
    fontName?: string;
    /** Font size in points (default: `14`). */
    fontSize?: number;
    /** Primary (fill) colour in `&HAABBGGRR` format (default: `&H00FFFFFF` — white). */
    primaryColour?: string;
    /** Secondary colour / karaoke fill (default: `&H000000FF` — red). */
    secondaryColour?: string;
    /** Outline colour (default: `&H00000000` — black). */
    outlineColour?: string;
    /** Back/shadow colour (default: `&H80000000` — semi-transparent black). */
    backColour?: string;
    /** Bold: `-1` = true, `0` = false (default: `-1`). */
    bold?: number;
    /** Italic: `-1` = true, `0` = false (default: `0`). */
    italic?: number;
    /** Underline: `-1` = true, `0` = false (default: `0`). */
    underline?: number;
    /** StrikeOut: `-1` = true, `0` = false (default: `0`). */
    strikeOut?: number;
    /** Horizontal scale % (default: `100`). */
    scaleX?: number;
    /** Vertical scale % (default: `100`). */
    scaleY?: number;
    /** Letter spacing in pixels (default: `0`). */
    spacing?: number;
    /** Rotation angle in degrees (default: `0`). */
    angle?: number;
    /** Border style: `1` = outline + drop shadow, `3` = opaque box (default: `1`). */
    borderStyle?: number;
    /** Outline width in pixels (default: `2`). */
    outline?: number;
    /** Shadow depth in pixels (default: `2`). */
    shadow?: number;
    /**
     * Alignment: 1=bottom-left … 3=bottom-right,
     * 4=mid-left … 6=mid-right, 7=top-left … 9=top-right (default: `2` = bottom-centre).
     */
    alignment?: number;
    /** Left margin in pixels (default: `20`). */
    marginL?: number;
    /** Right margin in pixels (default: `20`). */
    marginR?: number;
    /** Vertical margin in pixels (default: `20`). */
    marginV?: number;
}
/**
 * Convert segments to ASS (Advanced SubStation Alpha) format.
 *
 * By default a built-in style is used (Arial/14pt white-on-black).
 * Pass {@link AssStyleOptions} to customise font, colours, layout, etc.
 */
export declare function toAss(segments: SubtitleSegment[], assStyle?: AssStyleOptions): string;
