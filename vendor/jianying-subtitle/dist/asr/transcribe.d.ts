import type { JianYingResponse, SubtitleSegment } from "./types.js";
import type { TranscribeOptions } from "./types.js";
/**
 * Submit a transcription task to the JianYing API.
 *
 * Returns the task ID used to poll for results.
 */
export declare function submitTask(storeUri: string, tdid: string, checkedFetch: (url: string, init: RequestInit) => Promise<Response>): Promise<string>;
/**
 * Poll the query endpoint until the transcription is ready.
 *
 * When `data` is `null` the task is still processing and we retry
 * after `queryIntervalMs`. If the timeout (`queryTimeoutMs`) is
 * reached before data arrives an error is thrown.
 */
export declare function pollForResult(taskId: string, tdid: string, checkedFetch: (url: string, init: RequestInit) => Promise<Response>, options: Pick<TranscribeOptions, "queryTimeoutMs" | "queryIntervalMs" | "signal">): Promise<JianYingResponse>;
/**
 * Parse a completed ASR response into {@link SubtitleSegment} objects.
 *
 * Each segment carries the utterance text, start/end time in
 * milliseconds, and word-level timestamps when the API provides them.
 */
export declare function parseSegments(resp: JianYingResponse): SubtitleSegment[];
