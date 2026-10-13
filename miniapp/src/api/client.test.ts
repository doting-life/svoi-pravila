import { describe, expect, it, vi } from "vitest";

import { API_BASE_PATH, createApiClient, errorFromResponse, redeemComposeHandoff } from "./client";
import * as request from "./request";

const meBody = {
    onboarding_step: "done",
    consent_kind: null,
    consent_version: null,
    active_contact_id: null,
    max_contacts: 20,
    max_open_rules: 50,
    rule_text_max_chars: 500,
    incoming_text_max_chars: 4000,
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
            credential: { scheme: "tma", value: "init-data-token" },
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

        const client = createApiClient({ credential: { scheme: "tma", value: "init-data-token" } });
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

    it("redeems handoff via typed client with handoff Authorization", async () => {
        const body = {
            draft: "d",
            limits: { incoming_text_max_chars: 4000 },
            session: { token: "cs_" + "a".repeat(64), expires_at: "2026-06-01T12:15:00Z" },
        };
        const fetchMock: typeof fetch = vi.fn((input: RequestInfo | URL) => {
            const request = input instanceof Request ? input : new Request(String(input));
            expect(new URL(request.url).pathname).toBe("/api/v1/compose/handoff/redeem");
            expect(request.method).toBe("POST");
            expect(request.headers.get("Authorization")).toBe("handoff h_tok");
            return Promise.resolve(
                new Response(JSON.stringify(body), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            );
        });
        const result = await redeemComposeHandoff("h_tok", fetchMock);
        expect("data" in result).toBe(true);
        if ("data" in result) {
            expect(result.data.draft).toBe("d");
        }
    });

    it("maps redeem HTTP errors without casting body", async () => {
        const fetchMock: typeof fetch = vi.fn(() =>
            Promise.resolve(
                new Response(JSON.stringify({ code: "handoff_expired", message: "gone" }), {
                    status: 401,
                    headers: { "Content-Type": "application/json" },
                }),
            ),
        );
        const result = await redeemComposeHandoff("h_bad", fetchMock);
        expect("error" in result).toBe(true);
        if ("error" in result) {
            expect(result.error.code).toBe("handoff_expired");
        }
    });

    it("attaches compose Authorization from credential", async () => {
        const fetchMock: typeof fetch = vi.fn((input: RequestInfo | URL) => {
            const request = input instanceof Request ? input : new Request(String(input));
            expect(request.headers.get("Authorization")).toBe("compose cs_token");
            return Promise.resolve(
                new Response(JSON.stringify(meBody), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            );
        });
        const client = createApiClient({
            credential: { scheme: "compose", value: "cs_token" },
            fetch: fetchMock,
        });
        await client.GET("/api/v1/me");
        expect(fetchMock).toHaveBeenCalled();
    });

    it("maps redeem ok response without data to a network error", async () => {
        const spy = vi.spyOn(request, "unwrapApiResult").mockReturnValueOnce({});
        const fetchMock: typeof fetch = vi.fn(() =>
            Promise.resolve(
                new Response(JSON.stringify({}), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            ),
        );
        try {
            const result = await redeemComposeHandoff("h_empty", fetchMock);
            expect("error" in result).toBe(true);
            if ("error" in result) {
                expect(result.error.kind).toBe("network");
            }
        } finally {
            spy.mockRestore();
        }
    });
});
