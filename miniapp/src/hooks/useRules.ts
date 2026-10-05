import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type Rule = components["schemas"]["RuleItem"];
export type RuleCategory = components["schemas"]["CreateRuleRequest"]["category"];

export function useRules(contactId: string): ResourceState<readonly Rule[]> & {
    refetch: () => void;
    createRule: (input: {
        category: RuleCategory;
        text: string;
    }) => Promise<{ data?: Rule; error?: ApiError }>;
    archiveRule: (ruleId: string) => Promise<{ data?: Rule; error?: ApiError }>;
} {
    const client = useApiClient();
    const loader = useCallback(async () => {
        const result = await client.GET("/api/v1/contacts/{contact_id}/rules", {
            params: { path: { contact_id: contactId } },
        });
        const unwrapped = unwrapApiResult(result);
        if (unwrapped.error !== undefined) {
            return { error: unwrapped.error };
        }
        return { data: unwrapped.data?.rules ?? [] };
    }, [client, contactId]);
    const { refetch, ...resource } = useAsyncResource(loader, `rules:${contactId}`);

    const createRule = useCallback(
        async (input: { category: RuleCategory; text: string }) => {
            const result = await client.POST("/api/v1/contacts/{contact_id}/rules", {
                params: { path: { contact_id: contactId } },
                body: input,
            });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, contactId, refetch],
    );

    const archiveRule = useCallback(
        async (ruleId: string) => {
            const result = await client.POST("/api/v1/rules/{rule_id}/archive", {
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

    return { ...resource, refetch, createRule, archiveRule };
}
