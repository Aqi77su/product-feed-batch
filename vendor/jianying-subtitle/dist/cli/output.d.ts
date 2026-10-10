import type { SubtitleFormat } from "../subtitle/index.js";
/**
 * Determine the output file path and format.
 *
 * - No `-o` → `<input-dir>/<stem>.json`
 * - `-o out.srt` → `out.srt`, format `srt`
 * - `-o out` (no extension) → `out.json`
 * - Unsupported extension → error
 * - Directory paths → error
 */
export declare function resolveOutput(input: string, output?: string): {
    outputPath: string;
    format: SubtitleFormat;
};
