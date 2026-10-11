import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";
import { mapHttpError, type ApiError } from "./errors";
import { unwrapApiResult } from "./request";

/**
 * Same-origin base for openapi-fetch. OpenAPI paths already include `/api/v1/…`;
 * Caddy reverse-proxies `/api/*` to the backend.
 */
export const API_BASE_PATH = "";

export type ApiClient = ReturnType<typeof createClient<paths>>;

/** Session credentials accepted by ``ApiProvider`` (FULL / COMPOSE). */
export type ApiCredential = {
    readonly scheme: "tma" | "compose";
    readonly value: string;
};

/** One-shot handoff redeem credential (not accepted by ``ApiProvider``). */
export type HandoffCredential = {
    readonly scheme: "handoff";
    readonly value: string;
};

export type RequestCredential = ApiCredential | HandoffCredential;

export type CreateApiClientOptions = {
    readonly credential: RequestCredential;
    readonly fetch?: typeof fetch;
};

export type ComposeInlineContext = components["schemas"]["ComposeInlineContextResponse"];

function sameOriginApiBaseUrl(): string {
    return window.location.origin;
}

/** Sole builder for `Authorization` values used by the mini-app. */
export function authorizationHeader(credential: RequestCredential): string {
    return `${credential.scheme} ${credential.value}`;
}

function authMiddleware(credential: RequestCredential): Middleware {
    return {
        onRequest({ request }) {
            const headers = new Headers(request.headers);
            headers.set("Authorization", authorizationHeader(credential));
            return new Request(request, { headers });
        },
    };
}

/** Same-origin OpenAPI client; attaches `Authorization` from the credential on every request. */
export function createApiClient(options: CreateApiClientOptions): ApiClient {
    const baseUrl = sameOriginApiBaseUrl();
    const client =
        options.fetch === undefined
            ? createClient<paths>({ baseUrl })
            : createClient<paths>({ baseUrl, fetch: options.fetch });
    client.use(authMiddleware(options.credential));
    return client;
}

export function errorFromResponse(status: number | undefined, detail?: string): ApiError {
    return mapHttpError(status, detail);
}

/** One-shot handoff redeem via typed OpenAPI client with ``handoff`` credential. */
export async function redeemComposeHandoff(
    handoffToken: string,
    fetchImpl: typeof fetch = fetch,
): Promise<{ data: ComposeInlineContext } | { error: ApiError }> {
    const client = createApiClient({
        credential: { scheme: "handoff", value: handoffToken },
        fetch: fetchImpl,
    });
    const unwrapped = unwrapApiResult(await client.POST("/api/v1/compose/handoff/redeem"));
    if (unwrapped.data !== undefined) {
        return { data: unwrapped.data };
    }
    if (unwrapped.error !== undefined) {
        return { error: unwrapped.error };
    }
    return { error: mapHttpError(undefined) };
}
