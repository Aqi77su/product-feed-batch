/**
 * Convert segments to plain text, one utterance per line.
 */
export function toTxt(segments) {
    return segments.map((s) => s.text).join("\n");
}
//# sourceMappingURL=txt.js.map