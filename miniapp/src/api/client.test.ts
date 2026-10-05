import { describe, expect, it, vi } from "vitest";

import { API_BASE_PATH, createApiClient, errorFromResponse } from "./client";

const meBody = {
    onboarding_step: "done",
    consent_kind: null,
    consent_version: null,
    active_contact_id: null,
    max_contacts: 20,
    max_open_rules: 50,
    display_timezone: "Europe/Moscow",
};

describe("createApiClient", () => {
    it("uses same-origin /api/v1 paths and attaches Authorization tma header", async () => {
        expect(window.location.origin.length).toBeGreaterThan(0);
        expect(API_BASE_PATH).toBe("");

        const fetchMock: typeof fetch = vi.fn((input: RequestInfo | URL) => {
            const request = input instanceof Request ? input : new Request(String(input));
            expect(new URL(request.url).pathname).toBe("/api/v1/me");
            expect(new URL(request.url).origin).toBe(window.location.origin);
            expect(request.headers.get("Authorization")).toBe("tma init-data-token");
            return Promise.resolve(
                new Response(JSON.stringify(meBody), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            );
        });

        const client = createApiClient({
            initData: "init-data-token",
            fetch: fetchMock,
        });

        const result = await client.GET("/api/v1/me");
        expect(result.error).toBeUndefined();
        expect(fetchMock).toHaveBeenCalledTimes(1);
        const called = vi.mocked(fetchMock).mock.calls[0]?.[0];
        expect(called).toBeInstanceOf(Request);
        if (called instanceof Request) {
            expect(new URL(called.url).pathname).toBe("/api/v1/me");
            expect(new URL(called.url).origin).toBe(window.location.origin);
        }
    });

    it("uses the global fetch when no fetch is injected", async () => {
        const fetchMock: typeof fetch = vi.fn(() =>
            Promise.resolve(
                new Response(JSON.stringify(meBody), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            ),
        );
        vi.stubGlobal("fetch", fetchMock);

        const client = createApiClient({ initData: "init-data-token" });
        const result = await client.GET("/api/v1/me");
        expect(result.error).toBeUndefined();
        expect(fetchMock).toHaveBeenCalledTimes(1);
        const called = vi.mocked(fetchMock).mock.calls[0]?.[0];
        expect(called).toBeInstanceOf(Request);
        if (called instanceof Request) {
            expect(new URL(called.url).origin).toBe(window.location.origin);
            expect(new URL(called.url).pathname).toBe("/api/v1/me");
            expect(called.headers.get("Authorization")).toBe("tma init-data-token");
        }

        vi.unstubAllGlobals();
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
});
