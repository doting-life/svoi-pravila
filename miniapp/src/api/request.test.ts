import { describe, expect, it } from "vitest";

import { unwrapApiResult, unwrapEmptyResult } from "./request";

describe("unwrapApiResult", () => {
    it("returns data on ok responses", () => {
        const result = unwrapApiResult({
            data: { ok: true },
            response: new Response(null, { status: 200 }),
        });
        expect(result.data).toEqual({ ok: true });
    });

    it("maps error bodies", () => {
        const result = unwrapApiResult({
            error: { code: "contact_limit", message: "limit" },
            response: new Response(null, { status: 409 }),
        });
        expect(result.error?.code).toBe("contact_limit");
        expect(result.error?.kind).toBe("conflict");
    });
});

describe("unwrapEmptyResult", () => {
    it("returns empty on success", () => {
        const result = unwrapEmptyResult({
            response: new Response(null, { status: 204 }),
        });
        expect(result.error).toBeUndefined();
    });

    it("maps failures", () => {
        const result = unwrapEmptyResult({
            error: { code: "not_found", message: "x" },
            response: new Response(null, { status: 404 }),
        });
        expect(result.error?.kind).toBe("not_found");
    });
});
