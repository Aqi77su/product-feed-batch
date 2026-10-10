import type { SubtitleSegment } from "../asr/types.js";
/**
 * Convert segments to plain text, one utterance per line.
 */
export declare function toTxt(segments: SubtitleSegment[]): string;
