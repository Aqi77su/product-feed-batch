import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
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
export async function videoToAudio(input, options = {}) {
    const audio = await probeAudioTrack(input);
    if (!audio) {
        throw new Error(`Input video has no audio track: ${input}`);
    }
    const copyPlan = getAudioCopyPlan(audio.codecName);
    if (copyPlan) {
        try {
            return await extractAudioCopy(input, copyPlan.ext, options.signal);
        }
        catch (err) {
            if (isCancellation(err))
                throw err;
            // Some containers/codecs have edge cases where remuxing fails. Fall back
            // to WAV so transcription still works instead of surfacing a copy-only
            // failure to the user.
        }
    }
    return extractAudioWav(input, options.signal);
}
function isCancellation(err) {
    return err instanceof Error && err.message === "Cancelled";
}
function getAudioCopyPlan(codecName) {
    switch (codecName.toLowerCase()) {
        case "aac":
            return { ext: "m4a" };
        case "mp3":
            return { ext: "mp3" };
        case "flac":
            return { ext: "flac" };
        case "pcm_s16le":
        case "pcm_s24le":
        case "pcm_s32le":
        case "pcm_u8":
            return { ext: "wav" };
        default:
            return undefined;
    }
}
function tempAudioPath(ext) {
    return path.join(os.tmpdir(), `jianying-subtitle-${Date.now()}-${Math.random().toString(36).slice(2)}.${ext}`);
}
async function extractAudioCopy(input, ext, signal) {
    const tmpPath = tempAudioPath(ext);
    try {
        await runFfmpeg([
            "-y",
            "-i",
            input,
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "copy",
            tmpPath,
        ], tmpPath, signal);
        return tmpPath;
    }
    catch (err) {
        cleanupTempFile(tmpPath);
        throw err;
    }
}
async function extractAudioWav(input, signal) {
    const tmpPath = tempAudioPath("wav");
    try {
        await runFfmpeg([
            "-y",
            "-i",
            input,
            "-map",
            "0:a:0",
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "1",
            tmpPath,
        ], tmpPath, signal);
        return tmpPath;
    }
    catch (err) {
        cleanupTempFile(tmpPath);
        throw err;
    }
}
function runFfmpeg(args, outputPath, signal) {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) {
            reject(new Error("Cancelled"));
            return;
        }
        const proc = spawn("ffmpeg", args, { stdio: ["ignore", "pipe", "pipe"] });
        const onAbort = () => proc.kill("SIGTERM");
        signal?.addEventListener("abort", onAbort, { once: true });
        let stderr = "";
        proc.stderr.on("data", (chunk) => {
            stderr += chunk.toString();
        });
        proc.on("close", (code) => {
            signal?.removeEventListener("abort", onAbort);
            if (signal?.aborted) {
                reject(new Error("Cancelled"));
                return;
            }
            if (code !== 0 ||
                !fs.existsSync(outputPath) ||
                fs.statSync(outputPath).size === 0) {
                reject(new Error(`ffmpeg failed (exit ${code}): ${stderr.slice(-200)}`.trim()));
                return;
            }
            resolve();
        });
        proc.on("error", (err) => {
            signal?.removeEventListener("abort", onAbort);
            reject(new Error(`ffmpeg not found: ${err.message}`));
        });
    });
}
/** Probe the first audio stream in a media file. */
async function probeAudioTrack(input) {
    return new Promise((resolve) => {
        const proc = spawn("ffprobe", [
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nw=1:nk=1",
            input,
        ], { stdio: ["ignore", "pipe", "pipe"] });
        let stdout = "";
        proc.stdout.on("data", (chunk) => {
            stdout += chunk.toString();
        });
        proc.on("close", (code) => {
            const codecName = stdout.trim().split(/\s+/)[0];
            resolve(code === 0 && codecName ? { codecName } : undefined);
        });
        proc.on("error", () => resolve(undefined));
    });
}
/**
 * Remove a temporary file if it exists.
 * Errors (e.g. permission denied) are silently ignored.
 */
export function cleanupTempFile(tmpPath) {
    try {
        if (fs.existsSync(tmpPath))
            fs.unlinkSync(tmpPath);
    }
    catch {
        /* best-effort */
    }
}
//# sourceMappingURL=media.js.map