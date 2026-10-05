import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type Me = components["schemas"]["MeResponse"];

export function useMe(): ResourceState<Me> & { refetch: () => void } {
    const client = useApiClient();
    const loader = useCallback(async () => {
        const result = await client.GET("/api/v1/me");
        return unwrapApiResult(result); // sync unwrap after await GET
    }, [client]);
    return useAsyncResource(loader, "me");
}
