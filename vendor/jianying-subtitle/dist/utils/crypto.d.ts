/** Compute CRC32 (IEEE 802.3) checksum of a buffer. Returns 8-char hex. */
export declare function crc32(buf: Buffer): string;
/** Compute MD5 hex digest of a string. */
export declare function md5(s: string): string;
/** Compute SHA-256 hex digest of a string or buffer. */
export declare function sha256(s: string | Buffer): string;
