import { useState } from "react";

import { useApiClient } from "../api/ApiContext";
import { unwrapEmptyResult } from "../api/request";
import { Button } from "../components/Button";
import { BookmarkIcon, ChatOutlineIcon, LogoBubbles, ShieldIcon } from "../components/icons";
import { ProgressSteps } from "../components/ProgressSteps";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type OnboardingAgeScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly onConfirmed: () => void;
};

export function OnboardingAgeScreen({ telegram, onConfirmed }: OnboardingAgeScreenProps) {
    const client = useApiClient();
    const [adult, setAdult] = useState(false);
    const [busy, setBusy] = useState(false);
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

    return (
        <section className="screen screen--onboarding" aria-labelledby="age-title">
            <ProgressSteps current={1} total={2} />

            <div className="onboarding-hero">
                <LogoBubbles />
                <h1 id="age-title" className="onboarding-hero__title">
                    {ru.appTitle}
                </h1>
                <p className="onboarding-hero__tagline">{ru.onboardingTagline}</p>
            </div>

            <ul className="feature-list">
                <li className="feature">
                    <span className="feature__icon">
                        <ChatOutlineIcon size={20} />
                    </span>
                    <span>
                        <strong>{ru.onboardingFeatureDecodeLead}</strong>{" "}
                        {ru.onboardingFeatureDecode}
                    </span>
                </li>
                <li className="feature">
                    <span className="feature__icon">
                        <BookmarkIcon size={20} />
                    </span>
                    <span>
                        <strong>{ru.onboardingFeatureRulesLead}</strong> {ru.onboardingFeatureRules}
                    </span>
                </li>
                <li className="feature">
                    <span className="feature__icon">
                        <ShieldIcon size={20} />
                    </span>
                    <span>
                        <strong>{ru.onboardingFeaturePrivateLead}</strong>{" "}
                        {ru.onboardingFeaturePrivate}
                    </span>
                </li>
            </ul>

            <div className="screen-footer">
                <label className="check-card">
                    <input
                        type="checkbox"
                        checked={adult}
                        disabled={busy}
                        onChange={(event) => {
                            setAdult(event.target.checked);
                        }}
                    />
                    {ru.onboardingAgeCheckbox}
                </label>
                {error !== null ? (
                    <p className="form-error" role="alert">
                        {error}
                    </p>
                ) : null}
                <Button
                    size="lg"
                    block
                    disabled={!adult || busy}
                    onClick={() => {
                        void confirm();
                    }}
                >
                    {ru.continue}
                </Button>
            </div>
        </section>
    );
}
