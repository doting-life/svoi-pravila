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
    readonly max?: number;
    readonly actual?: number;
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
    extras?: { max?: number; actual?: number },
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
        ...(extras?.max !== undefined ? { max: extras.max } : {}),
        ...(extras?.actual !== undefined ? { actual: extras.actual } : {}),
    };
}

export function parseErrorBody(body: unknown): {
    message: string | undefined;
    code: MiniappErrorCode | undefined;
    max: number | undefined;
    actual: number | undefined;
} {
    if (typeof body !== "object" || body === null) {
        return { message: undefined, code: undefined, max: undefined, actual: undefined };
    }
    const record = body as { message?: unknown; code?: unknown; max?: unknown; actual?: unknown };
    const message = typeof record.message === "string" ? record.message : undefined;
    const code = typeof record.code === "string" ? (record.code as MiniappErrorCode) : undefined;
    const max = typeof record.max === "number" ? record.max : undefined;
    const actual = typeof record.actual === "number" ? record.actual : undefined;
    return { message, code, max, actual };
}
