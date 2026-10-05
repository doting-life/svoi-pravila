import { describe, expect, it } from "vitest";

import { mapHttpError } from "./errors";

describe("mapHttpError", () => {
    it("prefers the provided body message", () => {
        expect(mapHttpError(500, "boom").message).toBe("boom");
    });
});
