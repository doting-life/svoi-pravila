import type { components } from "./schema";

export type MiniappErrorCode = components["schemas"]["MiniappErrorCode"];

export type ApiErrorKind =
    | "network"
    | "unauthorized"
    | "forbidden"
    | "not_found"
    | "validation"
    | "conflict"
    | "server"
    | "unknown";

export type ApiError = {
    readonly kind: ApiErrorKind;
    readonly status: number | undefined;
    readonly message: string;
    readonly code: MiniappErrorCode | undefined;
};

function kindFromStatus(status: number): ApiErrorKind {
    if (status === 401) {
        return "unauthorized";
    }
    if (status === 403) {
        return "forbidden";
    }
    if (status === 404) {
        return "not_found";
    }
    if (status === 409) {
        return "conflict";
    }
    if (status === 422) {
        return "validation";
    }
    if (status === 429) {
        return "unknown";
    }
    if (status === 202) {
        return "unknown";
    }
    if (status >= 500) {
        return "server";
    }
    return "unknown";
}

export function mapHttpError(
    status: number | undefined,
    bodyMessage?: string,
    code?: MiniappErrorCode,
): ApiError {
    if (status === undefined) {
        return {
            kind: "network",
            status: undefined,
            message: bodyMessage ?? "network error",
            code: undefined,
        };
    }
    return {
        kind: kindFromStatus(status),
        status,
        message: bodyMessage ?? "request failed",
        code,
    };
}

export function parseErrorBody(body: unknown): {
    message: string | undefined;
    code: MiniappErrorCode | undefined;
} {
    if (typeof body !== "object" || body === null) {
        return { message: undefined, code: undefined };
    }
    const record = body as { message?: unknown; code?: unknown };
    const message = typeof record.message === "string" ? record.message : undefined;
    const code = typeof record.code === "string" ? (record.code as MiniappErrorCode) : undefined;
    return { message, code };
}
