import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";
import { mapHttpError, type ApiError } from "./errors";

/** Path prefix on the same origin (Caddy reverse-proxies `/api/*`). */
export const API_BASE_PATH = "/api";

export type ApiClient = ReturnType<typeof createClient<paths>>;

export type CreateApiClientOptions = {
    readonly initData: string;
    readonly fetch?: typeof fetch;
    /** Override for tests; defaults to same-origin `/api`. */
    readonly baseUrl?: string;
};

function defaultBaseUrl(): string {
    if (typeof window !== "undefined") {
        return `${window.location.origin}${API_BASE_PATH}`;
    }
    return API_BASE_PATH;
}

function authMiddleware(initData: string): Middleware {
    return {
        onRequest({ request }) {
            const headers = new Headers(request.headers);
            headers.set("Authorization", `tma ${initData}`);
            return new Request(request, { headers });
        },
    };
}

/** Same-origin OpenAPI client; attaches `Authorization: tma <initData>` on every request. */
export function createApiClient(options: CreateApiClientOptions): ApiClient {
    const baseUrl = options.baseUrl ?? defaultBaseUrl();
    const client =
        options.fetch === undefined
            ? createClient<paths>({ baseUrl })
            : createClient<paths>({ baseUrl, fetch: options.fetch });
    client.use(authMiddleware(options.initData));
    return client;
}

export function errorFromResponse(status: number | undefined, detail?: string): ApiError {
    return mapHttpError(status, detail);
}
