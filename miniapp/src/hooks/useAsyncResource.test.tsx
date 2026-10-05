import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useAsyncResource } from "./useAsyncResource";

describe("useAsyncResource", () => {
    it("surfaces an error when the loader returns empty data", async () => {
        const { result } = renderHook(() => useAsyncResource(() => Promise.resolve({}), "empty"));
        await waitFor(() => {
            expect(result.current.status).toBe("error");
        });
        if (result.current.status === "error") {
            expect(result.current.error.message).toBe("empty response");
        }
    });
});
