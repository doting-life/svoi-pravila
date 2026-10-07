import { useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapEmptyResult } from "../api/request";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type OnboardingAgeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly onConfirmed: () => void;
};

export function OnboardingAgeScreen({ telegram, onConfirmed }: OnboardingAgeScreenProps) {
    const client = useApiClient();
    const [busy, setBusy] = useState(false);
    const [declined, setDeclined] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const confirm = async () => {
        setBusy(true);
        setError(null);
        const result = await client.POST("/api/v1/me/age-confirmation");
        const unwrapped = unwrapEmptyResult(result);
        setBusy(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
            setError(unwrapped.error.message || ru.errorGeneric);
            return;
        }
        telegram.hapticNotification("success");
        onConfirmed();
    };

    if (declined) {
        return (
            <section className="screen" aria-labelledby="age-title">
                <h2 id="age-title" className="screen-title">
                    {ru.onboardingAgeTitle}
                </h2>
                <p className="status-message">{ru.onboardingAgeDeclined}</p>
                <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => {
                        telegram.close();
                    }}
                >
                    {ru.close}
                </button>
            </section>
        );
    }

    return (
        <section className="screen" aria-labelledby="age-title">
            <h2 id="age-title" className="screen-title">
                {ru.onboardingAgeTitle}
            </h2>
            <p className="status-message">{ru.onboardingAgePrompt}</p>
            <div className="list-item-actions">
                <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy}
                    onClick={() => {
                        void confirm();
                    }}
                >
                    {busy ? ru.loading : ru.onboardingAgeYes}
                </button>
                <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() => {
                        setDeclined(true);
                    }}
                >
                    {ru.onboardingAgeNo}
                </button>
            </div>
            {error !== null ? (
                <p className="status-message" role="alert">
                    {error}
                </p>
            ) : null}
        </section>
    );
}
