/**
 * Options parsed from CLI arguments.
 * @internal CLI interface — not part of the public programmatic API.
 */
export interface CliOptions {
    input?: string;
    output?: string;
    verbose: boolean;
    help: boolean;
    version: boolean;
    /** JSON string with ASS style overrides (only used for `.ass` output). */
    assStyle?: string;
}
/**
 * Read the next positional value for a flag.
 * @throws If the value is missing or looks like another flag.
 */
export declare function readArgValue(raw: string[], i: number, flag: string): string;
/**
 * Parse command-line arguments into a typed options object.
 *
 * Supported flags: `-o`, `--output`, `-v`, `--verbose`,
 * `--version`, `-h`, `--help`. Unknown flags cause an error.
 * The first non-flag argument is treated as the input file.
 */
export declare function parseArgs(raw: string[]): CliOptions;
