/**
 * Convert segments to a JSON string (pretty-printed array).
 *
 * Each segment includes `text`, `startMs`, `endMs`, and
 * word-level `words` when available.
 */
export function toJson(segments) {
    return JSON.stringify(segments, null, 2);
}
//# sourceMappingURL=json.js.map