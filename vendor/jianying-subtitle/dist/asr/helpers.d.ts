/**
 * Generate a device ID (TDID) that mimics the official CapCut client.
 *
 * Formula: `fr = 390 + last digit of current year`, followed by a
 * MAC-based network-hash in even years or a fixed string in odd years.
 */
export declare function generateTdid(): string;
/**
 * Generate the local MD5-based request signature.
 *
 * The signing formula is:
 * `MD5("9e2c|" + urlSuffix + "|" + pf + "|" + appvr + "|" + ts + "|" + tdid + "|11ac")`
 *
 * No external signing service is required — this is computed entirely
 * offline and matches the official CapCut client behaviour.
 */
export declare function generateSignParams(url: string, tdid: string, pf?: string, appvr?: string): {
    sign: string;
    deviceTime: string;
};
/**
 * Build the minimal set of request headers required by the JianYing API.
 *
 * Includes the MD5 signature, device timestamp, platform, and app version.
 */
export declare function buildHeaders(deviceTime: string, sign: string, tdid: string): Record<string, string>;
/**
 * Parse a JSON response body.
 *
 * Throws a descriptive error when the body is not valid JSON, including
 * the first 500 characters of the raw text.
 */
export declare function parseJsonResponse<T>(text: string, label: string, status: number): T;
/**
 * Promise-based sleep. Used for polling intervals.
 */
export declare function sleep(ms: number, signal?: AbortSignal): Promise<void>;
