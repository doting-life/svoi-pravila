import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type PrivacyTexts = components["schemas"]["PrivacyTextsResponse"];

export function usePrivacyTexts(): ResourceState<PrivacyTexts> & { refetch: () => void } {
    const client = useApiClient();
    const loader = useCallback(async () => {
        const result = await client.GET("/api/v1/privacy/texts");
        return unwrapApiResult(result);
    }, [client]);
    return useAsyncResource(loader, "privacy-texts");
}
