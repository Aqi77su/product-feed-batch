import type { SubtitleSegment } from "../asr/types.js";
import { type AssStyleOptions } from "./ass.js";
/** Supported subtitle output formats. */
export type SubtitleFormat = "srt" | "ass" | "json" | "txt";
/** Options for {@link writeSubtitle}. */
export type WriteSubtitleOptions = {
    segments: SubtitleSegment[];
    output: string;
    /**
     * Explicit format override.
     *
     * When provided it is used instead of inferring the format from
     * the output file extension. Useful when the output path is an
     * extensionless base.
     */
    format?: SubtitleFormat;
    /**
     * ASS style overrides.
     *
     * Only used when writing `ass` format — ignored for other formats.
     * Omitted fields fall back to built-in defaults (Arial/14pt white-on-black).
     */
    assStyle?: AssStyleOptions;
};
/**
 * Write a single subtitle file.
 *
 * The format defaults to the output file extension. Pass {@link
 * WriteSubtitleOptions.format} to override (for example when the
 * output path is an extensionless base that will have the extension
 * appended later).
 *
 * Parent directories are created automatically.
 */
export declare function writeSubtitle(opts: WriteSubtitleOptions): void;
/** Additional formats requested alongside the primary output. */
export type WriteSubtitlesExtras = {
    also?: SubtitleFormat[];
    /** ASS style overrides (only used when writing `.ass` files). */
    assStyle?: AssStyleOptions;
};
/**
 * Write segments to multiple subtitle formats at once.
 *
 * Two calling conventions are supported:
 *
 * **Legacy** — format is inferred from the output file extension.
 * ```ts
 * writeSubtitles(segments, "/tmp/out.srt", { also: ["json", "txt"] });
 * // → out.srt, out.json, out.txt
 * ```
 *
 * **Explicit primary format** — output is an extensionless base path.
 * ```ts
 * writeSubtitles(segments, "/tmp/out", "srt", { also: ["json"] });
 * // → out.srt, out.json
 * ```
 */
export declare function writeSubtitles(segments: SubtitleSegment[], output: string, extras?: WriteSubtitlesExtras): void;
export declare function writeSubtitles(segments: SubtitleSegment[], output: string, primaryFormat: SubtitleFormat, extras?: WriteSubtitlesExtras): void;
