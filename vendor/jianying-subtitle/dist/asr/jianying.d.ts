import type { SubtitleSegment, TranscribeOptions } from "./types.js";
export type { JianYingResponse, SubtitleSegment, SubtitleWord, TranscribeOptions, } from "./types.js";
/**
 * JianYing (CapCut) ASR client.
 *
 * Orchestrates the full pipeline:
 * upload → submit → poll → parse.
 */
export declare class JianYingASR {
    private options;
    private tdid;
    private crc32Hex;
    private fileBuffer;
    private fileSize;
    constructor(options: TranscribeOptions);
    /**
     * Wrapped `fetch` that checks the HTTP status code.
     * Throws with a snippet of the response body on non-2xx responses.
     */
    private checkedFetch;
    /** Read the audio file from disk and compute its CRC32 checksum. */
    private loadFile;
    private get ctx();
    /**
     * Run the full ASR pipeline and return timestamped segments.
     */
    transcribe(): Promise<SubtitleSegment[]>;
}
/**
 * Convenience function: transcribe an audio file and return segments.
 */
export declare function transcribe(options: TranscribeOptions): Promise<SubtitleSegment[]>;
