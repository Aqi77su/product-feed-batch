/**
 * Shared context threaded through every upload step.
 * Callers create this once and pass it to each pipeline function.
 */
export interface UploadPipelineContext {
    tdid: string;
    crc32Hex: string;
    fileBuffer: Buffer;
    fileSize: number;
    checkedFetch: (url: string, init: RequestInit) => Promise<Response>;
}
/**
 * Mutable state accumulated during the upload pipeline.
 * Fields are populated step by step and must all be present
 * before calling the final commit.
 */
export interface UploadState {
    accessKey: string;
    secretKey: string;
    sessionToken: string;
    storeUri: string;
    authToken: string;
    uploadId: string;
    uploadHost: string;
}
/**
 * Step 1 — Request temporary AWS credentials.
 *
 * Posts a `biz: "pc-recognition"` payload to the JianYing upload-sign
 * endpoint and receives an S3-compatible access key, secret, and
 * session token.
 */
export declare function uploadSign(ctx: UploadPipelineContext): Promise<Pick<UploadState, "accessKey" | "secretKey" | "sessionToken">>;
/**
 * Step 2 — Obtain an upload destination from ByteDance VOD.
 *
 * Signs an `ApplyUploadInner` request with an AWS V4 signature using
 * the credentials from step 1. The response contains a `StoreUri`,
 * `UploadID`, and an auth token needed for the subsequent PUT.
 */
export declare function uploadAuth(ctx: UploadPipelineContext, creds: Pick<UploadState, "accessKey" | "secretKey" | "sessionToken">): Promise<Pick<UploadState, "storeUri" | "authToken" | "uploadId" | "uploadHost">>;
/**
 * Step 3 — PUT the raw audio bytes to the ByteDance VOD endpoint.
 */
export declare function uploadFile(ctx: UploadPipelineContext, st: UploadState): Promise<void>;
/**
 * Step 4 — POST a CRC32 check to verify the upload was received intact.
 */
export declare function uploadCheck(ctx: UploadPipelineContext, st: UploadState): Promise<void>;
/**
 * Step 5 — Finalize (commit) the upload.
 *
 * The Python reference implementation does not check the response,
 * and the file is already accessible after step 3, so failures here
 * are silently ignored.
 */
export declare function uploadCommit(ctx: UploadPipelineContext, st: UploadState): Promise<void>;
