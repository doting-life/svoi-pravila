import { describe, expect, it } from "vitest";

import { mapHttpError, parseErrorBody } from "./errors";

describe("mapHttpError", () => {
    it("prefers the provided body message and code", () => {
        const error = mapHttpError(409, "limit", "contact_limit");
        expect(error.message).toBe("limit");
        expect(error.kind).toBe("conflict");
        expect(error.code).toBe("contact_limit");
    });

    it("maps unauthorized and validation", () => {
        expect(mapHttpError(401).kind).toBe("unauthorized");
        expect(mapHttpError(422).kind).toBe("validation");
        expect(mapHttpError(undefined).kind).toBe("network");
    });

    it("maps bot_chat_unavailable 409, rate_limited 429, and 202", () => {
        const unavailable = mapHttpError(409, "start", "bot_chat_unavailable");
        expect(unavailable.kind).toBe("conflict");
        expect(unavailable.code).toBe("bot_chat_unavailable");
        const limited = mapHttpError(429, "slow", "rate_limited");
        expect(limited.status).toBe(429);
        expect(limited.code).toBe("rate_limited");
        expect(mapHttpError(202).kind).toBe("unknown");
    });
});

describe("parseErrorBody", () => {
    it("reads code and message", () => {
        expect(parseErrorBody({ code: "contact_limit", message: "x" })).toEqual({
            code: "contact_limit",
            message: "x",
        });
        expect(parseErrorBody(null)).toEqual({ code: undefined, message: undefined });
    });
});
