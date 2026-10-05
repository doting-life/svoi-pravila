export type ApiErrorKind =
    "network" | "unauthorized" | "forbidden" | "not_found" | "validation" | "server" | "unknown";

export type ApiError = {
    readonly kind: ApiErrorKind;
    readonly status: number | undefined;
    readonly message: string;
};

export function mapHttpError(status: number | undefined, bodyMessage?: string): ApiError {
    if (status === undefined) {
        return { kind: "network", status: undefined, message: bodyMessage ?? "network error" };
    }
    if (status === 401) {
        return { kind: "unauthorized", status, message: bodyMessage ?? "unauthorized" };
    }
    if (status === 403) {
        return { kind: "forbidden", status, message: bodyMessage ?? "forbidden" };
    }
    if (status === 404) {
        return { kind: "not_found", status, message: bodyMessage ?? "not found" };
    }
    if (status === 422) {
        return { kind: "validation", status, message: bodyMessage ?? "validation error" };
    }
    if (status >= 500) {
        return { kind: "server", status, message: bodyMessage ?? "server error" };
    }
    return { kind: "unknown", status, message: bodyMessage ?? "unknown error" };
}
