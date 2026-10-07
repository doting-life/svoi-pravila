import { useCallback, useState } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult, unwrapEmptyResult } from "../api/request";
import { Button } from "../components/Button";
import { ChipGroup } from "../components/ChipGroup";
import { TextInput } from "../components/Field";
import { PairCircles } from "../components/icons";
import { EmptyState } from "../components/EmptyState";
import { LoadingView } from "../components/StatusViews";
import { formatDisplayDate } from "../dates/formatDisplayDate";
import { useAsyncResource } from "../hooks/useAsyncResource";
import { ru, type RelationshipKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

const RELATIONSHIP_OPTIONS = (Object.keys(ru.relationships) as RelationshipKey[]).map((key) => ({
    value: key,
    label: ru.relationships[key],
}));

const LABEL_MAX = 32;

export type InviteScreenProps = {
    readonly telegram: TelegramAdapter;
    readonly displayTimezone: string;
    readonly onAccepted: () => void;
    readonly onDismiss: () => void;
};

function inviteErrorMessage(error: ApiError): string {
    if (error.code === "invite_expired") {
        return ru.inviteExpired;
    }
    if (error.code === "invite_own") {
        return ru.inviteOwn;
    }
    if (error.code === "invite_invalid" || error.kind === "not_found") {
        return ru.inviteInvalid;
    }
    return error.message || ru.errorGeneric;
}

export function InviteScreen({
    telegram,
    displayTimezone,
    onAccepted,
    onDismiss,
}: InviteScreenProps) {
    const client = useApiClient();
    const resolved = useAsyncResource(
        useCallback(async () => {
            const result = await client.POST("/api/v1/invites/resolve");
            return unwrapApiResult(result);
        }, [client]),
        "invite-resolve",
    );
    const [label, setLabel] = useState("");
    const [relationship, setRelationship] = useState<RelationshipKey>("partner");
    const [busy, setBusy] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);

    const accept = async () => {
        const trimmed = label.trim();
        if (trimmed.length === 0 || trimmed.length > LABEL_MAX) {
            setFormError(ru.addRuleValidation);
            return;
        }
        setBusy(true);
        setFormError(null);
        const result = await client.POST("/api/v1/invites/accept", {
            body: { label: trimmed, relationship },
        });
        const unwrapped = unwrapEmptyResult(result);
        setBusy(false);
        if (unwrapped.error !== undefined) {
            telegram.hapticNotification("error");
            if (unwrapped.error.code === "contact_limit") {
                setFormError(ru.contactsLimit);
            } else {
                setFormError(inviteErrorMessage(unwrapped.error));
            }
            return;
        }
        telegram.hapticNotification("success");
        onAccepted();
    };

    if (resolved.status === "loading") {
        return <LoadingView />;
    }
    if (resolved.status === "error") {
        return (
            <section className="screen screen--centered" aria-labelledby="invite-title">
                <EmptyState
                    illustration={<PairCircles />}
                    title={ru.inviteTitle}
                    titleId="invite-title"
                    message={inviteErrorMessage(resolved.error)}
                    action={<Button onClick={onDismiss}>{ru.continue}</Button>}
                />
            </section>
        );
    }

    return (
        <section className="screen" aria-labelledby="invite-title">
            <div className="invite-art">
                <PairCircles />
            </div>
            <header className="screen-header">
                <div className="screen-header__text">
                    <h1 id="invite-title" className="screen-title">
                        {ru.inviteTitle}
                    </h1>
                    <p className="screen-lead">{ru.inviteLead}</p>
                </div>
            </header>

            <ol className="invite-steps">
                <li className="invite-steps__item">
                    <span className="invite-steps__num" aria-hidden="true">
                        1
                    </span>
                    {ru.inviteStepShared}
                </li>
                <li className="invite-steps__item">
                    <span className="invite-steps__num" aria-hidden="true">
                        2
                    </span>
                    {ru.inviteStepConfirm}
                </li>
                <li className="invite-steps__item">
                    <span className="invite-steps__num" aria-hidden="true">
                        3
                    </span>
                    {ru.inviteStepLeave}
                </li>
            </ol>

            <p className="hint-text">
                {ru.inviteExpires.replace(
                    "{date}",
                    formatDisplayDate(resolved.data.expires_at, displayTimezone),
                )}
            </p>

            <TextInput
                label={ru.inviteLabel}
                value={label}
                maxLength={LABEL_MAX}
                disabled={busy}
                onChange={setLabel}
            />
            <ChipGroup
                legend={ru.inviteRelationship}
                options={RELATIONSHIP_OPTIONS}
                value={relationship}
                onChange={setRelationship}
            />

            {formError !== null ? (
                <p className="form-error" role="alert">
                    {formError}
                </p>
            ) : null}

            <div className="screen-footer">
                <Button
                    size="lg"
                    block
                    disabled={busy}
                    onClick={() => {
                        void accept();
                    }}
                >
                    {busy ? ru.loading : ru.inviteAccept}
                </Button>
                <Button variant="ghost" block disabled={busy} onClick={onDismiss}>
                    {ru.inviteLater}
                </Button>
            </div>
        </section>
    );
}
