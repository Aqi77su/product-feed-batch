/**
 * Extract the audio track from a video file into a temporary audio file.
 *
 * Whenever the source audio codec is already accepted by the JianYing ASR API,
 * this uses ffmpeg stream copy (`-c:a copy`) so extraction is fast and lossless.
 * Unsupported codecs fall back to a temporary 16-bit PCM mono WAV.
 *
 * Requires `ffmpeg` and `ffprobe` on the system `PATH`.
 *
 * @returns Path to the temporary audio file. The caller is responsible
 *          for removing it with {@link cleanupTempFile}.
 * @throws If the input has no audio track, ffmpeg exits non-zero,
 *         or ffmpeg / ffprobe are not installed.
 */
export declare function videoToAudio(input: string, options?: {
    signal?: AbortSignal;
}): Promise<string>;
/**
 * Remove a temporary file if it exists.
 * Errors (e.g. permission denied) are silently ignored.
 */
export declare function cleanupTempFile(tmpPath: string): void;
