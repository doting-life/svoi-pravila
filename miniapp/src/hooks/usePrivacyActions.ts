import { useCallback } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult, unwrapEmptyResult } from "../api/request";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export function usePrivacyActions(): {
    exportData: (telegram: TelegramAdapter) => Promise<{ error?: string }>;
    revokeConsents: () => Promise<{ error?: ApiError }>;
    deleteAccount: () => Promise<{ error?: ApiError }>;
} {
    const client = useApiClient();

    const exportData = useCallback(
        async (telegram: TelegramAdapter) => {
            const result = await client.POST("/api/v1/me/export");
            const unwrapped = unwrapApiResult(result);
            if (unwrapped.error !== undefined) {
                if (unwrapped.error.code === "service_unavailable") {
                    return { error: ru.privacyExportUnavailable };
                }
                if (unwrapped.error.code === "rate_limited" || unwrapped.error.status === 429) {
                    return { error: ru.rateLimited };
                }
                return { error: unwrapped.error.message || ru.errorGeneric };
            }
            if (unwrapped.data === undefined) {
                return { error: ru.errorGeneric };
            }
            const accepted = await telegram.downloadFile(
                unwrapped.data.download_url,
                "svoi-pravila-export.json",
            );
            if (!accepted) {
                return { error: ru.privacyExportFailed };
            }
            return {};
        },
        [client],
    );

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
