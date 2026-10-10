import * as fs from "node:fs";
import * as path from "node:path";
import { toSrt } from "./srt.js";
import { toAss } from "./ass.js";
import { toJson } from "./json.js";
import { toTxt } from "./txt.js";
/**
 * Resolve the output format.
 *
 * If an explicit format is provided it is returned directly.
 * Otherwise the output file extension is matched against the known
 * set of subtitle extensions, falling back to `"srt"`.
 */
function resolveFormat(output, explicit) {
    if (explicit)
        return explicit;
    const ext = path.extname(output).slice(1).toLowerCase();
    const valid = new Set(["srt", "ass", "json", "txt"]);
    return valid.has(ext) ? ext : "srt";
}
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
export function writeSubtitle(opts) {
    const fmt = resolveFormat(opts.output, opts.format);
    let content;
    switch (fmt) {
        case "ass":
            content = toAss(opts.segments, opts.assStyle);
            break;
        case "json":
            content = toJson(opts.segments);
            break;
        case "txt":
            content = toTxt(opts.segments);
            break;
        case "srt":
        default:
            content = toSrt(opts.segments);
            break;
    }
    fs.mkdirSync(path.dirname(path.resolve(opts.output)), { recursive: true });
    fs.writeFileSync(opts.output, content, "utf-8");
}
export function writeSubtitles(segments, output, formatOrExtras, extras) {
    // legacy — format inferred from extension
    if (formatOrExtras == null || typeof formatOrExtras === "object") {
        const legacyExtras = formatOrExtras;
        writeSubtitle({ segments, output, assStyle: legacyExtras?.assStyle });
        if (legacyExtras?.also) {
            const base = output.replace(/\.[^.]+$/, "");
            const primaryExt = path.extname(output).slice(1).toLowerCase();
            for (const fmt of legacyExtras.also) {
                if (fmt === primaryExt)
                    continue;
                writeSubtitle({ segments, output: `${base}.${fmt}`, format: fmt, assStyle: legacyExtras.assStyle });
            }
        }
        return;
    }
    // explicit primary format
    const primaryFormat = formatOrExtras;
    writeSubtitle({
        segments,
        output: `${output}.${primaryFormat}`,
        format: primaryFormat,
        assStyle: extras?.assStyle,
    });
    if (extras?.also) {
        for (const fmt of extras.also) {
            if (fmt === primaryFormat)
                continue;
            writeSubtitle({ segments, output: `${output}.${fmt}`, format: fmt, assStyle: extras.assStyle });
        }
    }
}
//# sourceMappingURL=write.js.map