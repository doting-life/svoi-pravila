export function cx(...parts: readonly (string | false | null | undefined)[]): string {
    return parts
        .filter((part): part is string => typeof part === "string" && part.length > 0)
        .join(" ");
}
