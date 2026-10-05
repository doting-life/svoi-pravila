import type { ApiError } from "./errors";
import { mapHttpError, parseErrorBody } from "./errors";

type OpenApiResult<T> = {
    readonly data?: T;
    readonly error?: unknown;
    readonly response: Response;
};

export function unwrapApiResult<T>(result: OpenApiResult<T>): {
    data?: T;
    error?: ApiError;
} {
    if (result.data !== undefined && result.response.ok) {
        return { data: result.data };
    }
    const parsed = parseErrorBody(result.error);
    return {
        error: mapHttpError(result.response.status, parsed.message, parsed.code),
    };
}

export function unwrapEmptyResult(result: {
    readonly error?: unknown;
    readonly response: Response;
}): { error?: ApiError } {
    if (result.response.ok) {
        return {};
    }
    const parsed = parseErrorBody(result.error);
    return {
        error: mapHttpError(result.response.status, parsed.message, parsed.code),
    };
}
