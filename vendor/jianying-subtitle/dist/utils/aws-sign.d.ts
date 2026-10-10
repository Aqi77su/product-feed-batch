export interface AwsSignParams {
    secretKey: string;
    accessKey: string;
    sessionToken: string;
    requestParams: string;
    method?: string;
    payload?: string;
    region?: string;
    service?: string;
}
export interface AwsSignResult {
    authorization: string;
    amzDate: string;
    headers: Record<string, string>;
}
/** Generate an AWS V4 signature and authorization header. */
export declare function awsSign(params: AwsSignParams): AwsSignResult;
