import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult, unwrapEmptyResult } from "../api/request";

export function usePrivacyActions(): {
    exportData: () => Promise<{ error?: ApiError }>;
    revokeConsents: () => Promise<{ error?: ApiError }>;
    deleteAccount: () => Promise<{ error?: ApiError }>;
} {
    const client = useApiClient();

    const exportData = useCallback(async () => {
        const result = await client.POST("/api/v1/me/export");
        const unwrapped = unwrapApiResult(result);
        if (unwrapped.error !== undefined) {
            return { error: unwrapped.error };
        }
        return {};
    }, [client]);

    const revokeConsents = useCallback(async () => {
        const result = await client.POST("/api/v1/me/consents/revoke", {
            body: { confirm: true },
        });
        return unwrapEmptyResult(result);
    }, [client]);

    const deleteAccount = useCallback(async () => {
        const result = await client.POST("/api/v1/me/delete", {
            body: { confirm: true },
        });
        return unwrapEmptyResult(result);
    }, [client]);

    return { exportData, revokeConsents, deleteAccount };
}
