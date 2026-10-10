import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type PendingRule = components["schemas"]["PendingRuleItem"];

export function usePendingRules(): ResourceState<readonly PendingRule[]> & {
    refetch: () => void;
    approveRule: (ruleId: string) => Promise<{ error?: ApiError }>;
    rejectRule: (ruleId: string) => Promise<{ error?: ApiError }>;
} {
    const client = useApiClient();
    const loader = useCallback(async () => {
        const result = await client.GET("/api/v1/rules/pending");
        const unwrapped = unwrapApiResult(result);
        if (unwrapped.error !== undefined) {
            return { error: unwrapped.error };
        }
        return { data: unwrapped.data?.items ?? [] };
    }, [client]);
    const { refetch, ...resource } = useAsyncResource(loader, "rules:pending");

    const approveRule = useCallback(
        async (ruleId: string) => {
            const result = await client.POST("/api/v1/rules/{rule_id}/approve", {
                params: { path: { rule_id: ruleId } },
            });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    const rejectRule = useCallback(
        async (ruleId: string) => {
            const result = await client.POST("/api/v1/rules/{rule_id}/reject", {
                params: { path: { rule_id: ruleId } },
            });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    return { ...resource, refetch, approveRule, rejectRule };
}
