import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult } from "../api/request";
import type { components } from "../api/schema";
import { useAsyncResource, type ResourceState } from "./useAsyncResource";

export type ConsentKind = components["schemas"]["ConsentDocumentResponse"]["kind"];
export type ConsentDocument = components["schemas"]["ConsentDocumentResponse"];

export function useConsentDocuments(
    kinds: readonly ConsentKind[],
): ResourceState<readonly ConsentDocument[]> & { refetch: () => void } {
    const client = useApiClient();
    const kindsKey = kinds.join(",");
    const loader = useCallback(async () => {
        const results = await Promise.all(
            kindsKey.split(",").map(async (kind) =>
                unwrapApiResult(
                    await client.GET("/api/v1/consents/{kind}/document", {
                        params: { path: { kind: kind as ConsentKind } },
                    }),
                ),
            ),
        );
        const documents: ConsentDocument[] = [];
        for (const result of results) {
            if (result.error !== undefined) {
                return { error: result.error } satisfies { error: ApiError };
            }
            if (result.data !== undefined) {
                documents.push(result.data);
            }
        }
        return { data: documents };
    }, [client, kindsKey]);
    return useAsyncResource(loader, `consent-docs:${kindsKey}`);
}
