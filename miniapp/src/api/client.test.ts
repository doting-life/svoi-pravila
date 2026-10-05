import { describe, expect, it, vi } from "vitest";

import { API_BASE_PATH, createApiClient, errorFromResponse } from "./client";

describe("createApiClient", () => {
    it("uses /api base path and attaches Authorization tma header", async () => {
        const fetchMock: typeof fetch = vi.fn((input: RequestInfo | URL) => {
            const request = input instanceof Request ? input : new Request(String(input));
            expect(new URL(request.url).pathname.startsWith(API_BASE_PATH)).toBe(true);
            expect(request.headers.get("Authorization")).toBe("tma init-data-token");
            return Promise.resolve(
                new Response(JSON.stringify({ status: "ok" }), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            );
        });

        const client = createApiClient({
            initData: "init-data-token",
            fetch: fetchMock,
            baseUrl: "http://localhost/api",
        });

        const result = await client.GET("/healthz");
        expect(result.error).toBeUndefined();
        expect(fetchMock).toHaveBeenCalledTimes(1);
        const called = vi.mocked(fetchMock).mock.calls[0]?.[0];
        expect(called).toBeInstanceOf(Request);
        if (called instanceof Request) {
            expect(new URL(called.url).pathname).toBe("/api/healthz");
        }
    });

    it("maps HTTP errors", () => {
        expect(errorFromResponse(401).kind).toBe("unauthorized");
        expect(errorFromResponse(403).kind).toBe("forbidden");
        expect(errorFromResponse(404).kind).toBe("not_found");
        expect(errorFromResponse(422).kind).toBe("validation");
        expect(errorFromResponse(500).kind).toBe("server");
        expect(errorFromResponse(undefined).kind).toBe("network");
        expect(errorFromResponse(418).kind).toBe("unknown");
    });

    it("defaults to same-origin /api when window is present", async () => {
        const fetchMock: typeof fetch = vi.fn(() =>
            Promise.resolve(
                new Response(JSON.stringify({ status: "ok" }), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            ),
        );
        const client = createApiClient({
            initData: "token",
            fetch: fetchMock,
        });
        await client.GET("/healthz");
        const called = vi.mocked(fetchMock).mock.calls[0]?.[0];
        expect(called).toBeInstanceOf(Request);
        if (called instanceof Request) {
            expect(new URL(called.url).pathname).toBe("/api/healthz");
        }
    });
});
