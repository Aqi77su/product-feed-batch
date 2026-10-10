import type { SubtitleSegment } from "../asr/types.js";
/**
 * Convert segments to SRT (SubRip) format.
 *
 * Canonical format with sequential numbering,
 * `HH:MM:SS,mmm --> HH:MM:SS,mmm` timestamps, and UTF-8 text.
 */
export declare function toSrt(segments: SubtitleSegment[]): string;
