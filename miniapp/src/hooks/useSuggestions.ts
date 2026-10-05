import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type Suggestion = components["schemas"]["SuggestionItem"];

export function useSuggestions(contactId: string): ResourceState<readonly Suggestion[]> & {
    refetch: () => void;
    acceptSuggestion: (
        suggestionId: string,
    ) => Promise<{ data?: components["schemas"]["AcceptSuggestionResponse"]; error?: ApiError }>;
    dismissSuggestion: (
        suggestionId: string,
    ) => Promise<{ data?: components["schemas"]["DismissSuggestionResponse"]; error?: ApiError }>;
} {
    const client = useApiClient();
    const loader = useCallback(async () => {
        const result = await client.GET("/api/v1/contacts/{contact_id}/suggestions", {
            params: { path: { contact_id: contactId } },
        });
        const unwrapped = unwrapApiResult(result);
        if (unwrapped.error !== undefined) {
            return { error: unwrapped.error };
        }
        return { data: unwrapped.data?.suggestions ?? [] };
    }, [client, contactId]);
    const { refetch, ...resource } = useAsyncResource(loader, `suggestions:${contactId}`);

    const acceptSuggestion = useCallback(
        async (suggestionId: string) => {
            const result = await client.POST("/api/v1/suggestions/{suggestion_id}/accept", {
                params: { path: { suggestion_id: suggestionId } },
            });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    const dismissSuggestion = useCallback(
        async (suggestionId: string) => {
            const result = await client.POST("/api/v1/suggestions/{suggestion_id}/dismiss", {
                params: { path: { suggestion_id: suggestionId } },
            });
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error === undefined) {
                refetch();
            }
            return unwrapped;
        },
        [client, refetch],
    );

    return { ...resource, refetch, acceptSuggestion, dismissSuggestion };
}
