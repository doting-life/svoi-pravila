import { useCallback, useState } from "react";

import { useApiClient } from "../api/ApiContext";
import type { ApiError } from "../api/errors";
import { unwrapApiResult, unwrapEmptyResult } from "../api/request";
import { LoadingView } from "../components/StatusViews";
import { formatDisplayDate } from "../dates/formatDisplayDate";
import { useAsyncResource } from "../hooks/useAsyncResource";
import { ru, type RelationshipKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

const RELATIONSHIPS = Object.keys(ru.relationships) as RelationshipKey[];

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
        if (trimmed.length === 0 || trimmed.length > 32) {
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
            <section className="screen" aria-labelledby="invite-title">
                <h2 id="invite-title" className="screen-title">
                    {ru.inviteTitle}
                </h2>
                <p className="status-message" role="alert">
                    {inviteErrorMessage(resolved.error)}
                </p>
                <button type="button" className="btn btn-primary" onClick={onDismiss}>
                    {ru.continue}
                </button>
            </section>
        );
    }

    return (
        <section className="screen" aria-labelledby="invite-title">
            <h2 id="invite-title" className="screen-title">
                {ru.inviteTitle}
            </h2>
            <p className="status-message">{ru.inviteExplain}</p>
            <p className="status-message">
                {ru.inviteExpires.replace(
                    "{date}",
                    formatDisplayDate(resolved.data.expires_at, displayTimezone),
                )}
            </p>
            <label className="field">
                <span className="field-label">{ru.inviteLabel}</span>
                <input
                    className="field-input"
                    aria-label={ru.inviteLabel}
                    value={label}
                    maxLength={32}
                    onChange={(event) => {
                        setLabel(event.target.value);
                    }}
                />
            </label>
            <label className="field">
                <span className="field-label">{ru.inviteRelationship}</span>
                <select
                    className="field-input"
                    aria-label={ru.inviteRelationship}
                    value={relationship}
                    onChange={(event) => {
                        setRelationship(event.target.value as RelationshipKey);
                    }}
                >
                    {RELATIONSHIPS.map((key) => (
                        <option key={key} value={key}>
                            {ru.relationships[key]}
                        </option>
                    ))}
                </select>
            </label>
            {formError !== null ? (
                <p className="status-message" role="alert">
                    {formError}
                </p>
            ) : null}
            <div className="list-item-actions">
                <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy}
                    onClick={() => {
                        void accept();
                    }}
                >
                    {busy ? ru.loading : ru.inviteAccept}
                </button>
                <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={onDismiss}
                >
                    {ru.cancel}
                </button>
            </div>
        </section>
    );
}
