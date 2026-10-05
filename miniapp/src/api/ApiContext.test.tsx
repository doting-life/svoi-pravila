import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useApiClient } from "./ApiContext";

describe("useApiClient", () => {
    it("throws outside the provider", () => {
        expect(() => renderHook(() => useApiClient())).toThrow(/ApiProvider/);
    });
});
