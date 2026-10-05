import { useState } from "react";

import { formatDisplayDate } from "../dates/formatDisplayDate";
import { EmptyView, ErrorView, LoadingView } from "../components/StatusViews";
import { useContacts } from "../hooks/useContacts";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { useRules, type Rule } from "../hooks/useRules";
import { useSuggestions } from "../hooks/useSuggestions";
import { ru, type RelationshipKey } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type ContactDetailScreenProps = {
    readonly contactId: string;
    readonly label: string;
    readonly relationship: string;
    readonly paired: boolean;
    readonly displayTimezone: string;
    readonly telegram: TelegramAdapter;
    readonly onAddRule: () => void;
    readonly onRulesChanged: () => void;
    readonly onPairingChanged: (paired: boolean) => void;
};

function RuleList({
    rules,
    title,
    titleId,
    emptyMessage,
    displayTimezone,
    onArchive,
    onApprove,
    onReject,
}: {
    readonly rules: readonly Rule[];
    readonly title: string;
    readonly titleId: string;
    readonly emptyMessage: string | null;
    readonly displayTimezone: string;
    readonly onArchive: (ruleId: string, text: string) => void;
    readonly onApprove: (ruleId: string) => void;
    readonly onReject: (ruleId: string) => void;
}) {
    return (
        <section className="block" aria-labelledby={titleId}>
            <h3 id={titleId} className="block-title">
                {title}
            </h3>
            {rules.length === 0 && emptyMessage !== null ? (
                <EmptyView message={emptyMessage} />
            ) : null}
            {rules.length > 0 ? (
                <ul className="list">
                    {rules.map((rule) => (
                        <li key={rule.id} className="list-item">
                            <p className="list-item-title">{rule.text}</p>
                            <p className="list-item-meta">
                                {ru.categories[rule.category]}
                                {rule.status === "active" &&
                                rule.effective_since !== null &&
                                rule.effective_since !== undefined
                                    ? ` · ${ru.ruleEffectiveSince.replace(
                                          "{date}",
                                          formatDisplayDate(rule.effective_since, displayTimezone),
                                      )}`
                                    : ""}
                                {rule.status === "proposed" && rule.needs_my_approval
                                    ? ` · ${ru.ruleProposed}`
                                    : ""}
                                {rule.status === "proposed" &&
                                !rule.needs_my_approval &&
                                rule.shared
                                    ? ` · ${ru.ruleAwaitingPartner}`
                                    : ""}
                                {rule.status === "proposed" &&
                                !rule.needs_my_approval &&
                                !rule.shared
                                    ? ` · ${ru.ruleProposed}`
                                    : ""}
                            </p>
                            {rule.has_pending_edit ? (
                                <p className="badge">{ru.rulePendingEdit}</p>
                            ) : null}
                            {rule.needs_my_approval ? (
                                <div className="list-item-actions">
                                    <button
                                        type="button"
                                        className="btn btn-primary"
                                        onClick={() => {
                                            onApprove(rule.id);
                                        }}
                                    >
                                        {ru.ruleApprove}
                                    </button>
                                    <button
                                        type="button"
                                        className="btn btn-secondary"
                                        onClick={() => {
                                            onReject(rule.id);
                                        }}
                                    >
                                        {ru.ruleReject}
                                    </button>
                                </div>
                            ) : null}
                            {!rule.needs_my_approval &&
                            (rule.status === "active" || rule.status === "proposed") ? (
                                <div className="list-item-actions">
                                    <button
                                        type="button"
                                        className="btn btn-danger"
                                        onClick={() => {
                                            onArchive(rule.id, rule.text);
                                        }}
                                    >
                                        {ru.ruleArchive}
                                    </button>
                                </div>
                            ) : null}
                        </li>
                    ))}
                </ul>
            ) : null}
        </section>
    );
}

export function ContactDetailScreen({
    contactId,
    label,
    relationship,
    paired,
    displayTimezone,
    telegram,
    onAddRule,
    onRulesChanged,
    onPairingChanged,
}: ContactDetailScreenProps) {
    const suggestions = useSuggestions(contactId);
    const rules = useRules(contactId);
    const contacts = useContacts();
    const privacyTexts = usePrivacyTexts();
    const [inviteLink, setInviteLink] = useState<string | null>(null);
    const [inviteBusy, setInviteBusy] = useState(false);
    const [copyHint, setCopyHint] = useState(false);

    const archive = async (ruleId: string, text: string) => {
        const confirmed = await telegram.showConfirm(ru.ruleArchiveConfirm.replace("{text}", text));
        if (!confirmed) {
            return;
        }
        const result = await rules.archiveRule(ruleId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onRulesChanged();
    };

    const approve = async (ruleId: string) => {
        const result = await rules.approveRule(ruleId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onRulesChanged();
    };

    const reject = async (ruleId: string) => {
        const result = await rules.rejectRule(ruleId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onRulesChanged();
    };

    const accept = async (suggestionId: string) => {
        const result = await suggestions.acceptSuggestion(suggestionId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        suggestions.refetch();
        rules.refetch();
        onRulesChanged();
    };

    const dismiss = async (suggestionId: string) => {
        const result = await suggestions.dismissSuggestion(suggestionId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        suggestions.refetch();
    };

    const invite = async () => {
        setInviteBusy(true);
        const result = await contacts.inviteContact(contactId);
        setInviteBusy(false);
        if (result.error !== undefined || result.data === undefined) {
            telegram.hapticNotification("error");
            return;
        }
        setInviteLink(result.data.link);
        telegram.hapticNotification("success");
    };

    const shareInvite = (link: string) => {
        const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(link)}`;
        telegram.openTelegramLink(shareUrl);
    };

    const copyInvite = async (link: string) => {
        const ok = await telegram.copyText(link);
        if (!ok) {
            telegram.hapticNotification("error");
            return;
        }
        setCopyHint(true);
        telegram.hapticNotification("success");
    };

    const leave = async () => {
        if (privacyTexts.status !== "success") {
            return;
        }
        const confirmed = await telegram.showConfirm(privacyTexts.data.leave_pair.confirm);
        if (!confirmed) {
            return;
        }
        const result = await contacts.leavePair(contactId);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        setInviteLink(null);
        telegram.hapticNotification("success");
        onPairingChanged(false);
        rules.refetch();
        onRulesChanged();
    };

    const loading = suggestions.status === "loading" || rules.status === "loading";
    const error =
        suggestions.status === "error"
            ? suggestions.error
            : rules.status === "error"
              ? rules.error
              : undefined;

    const allRules = rules.status === "success" ? rules.data : [];
    const personalRules = allRules.filter((rule) => !rule.shared);
    const sharedRules = allRules.filter((rule) => rule.shared);

    return (
        <section className="screen" aria-labelledby="detail-title">
            <header className="screen-header">
                <div>
                    <h2 id="detail-title" className="screen-title">
                        {label}
                    </h2>
                    <p className="screen-subtitle">
                        {relationship in ru.relationships
                            ? ru.relationships[relationship as RelationshipKey]
                            : relationship}
                    </p>
                    {paired ? <p className="badge">{ru.contactPairedBadge}</p> : null}
                </div>
                <button type="button" className="btn btn-primary" onClick={onAddRule}>
                    {ru.contactAddRule}
                </button>
            </header>

            {!paired ? (
                <section className="block" aria-labelledby="invite-title">
                    <h3 id="invite-title" className="block-title">
                        {ru.contactInvite}
                    </h3>
                    {inviteLink === null ? (
                        <button
                            type="button"
                            className="btn btn-secondary"
                            disabled={inviteBusy}
                            onClick={() => {
                                void invite();
                            }}
                        >
                            {ru.contactInvite}
                        </button>
                    ) : (
                        <>
                            <p className="screen-subtitle">{ru.contactInviteExplain}</p>
                            <p className="list-item-meta">{inviteLink}</p>
                            <div className="list-item-actions">
                                <button
                                    type="button"
                                    className="btn btn-primary"
                                    onClick={() => {
                                        shareInvite(inviteLink);
                                    }}
                                >
                                    {ru.contactInviteShare}
                                </button>
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={() => {
                                        void copyInvite(inviteLink);
                                    }}
                                >
                                    {ru.contactInviteCopy}
                                </button>
                            </div>
                            {copyHint ? (
                                <p className="status-message" role="status">
                                    {ru.contactInviteCopied}
                                </p>
                            ) : null}
                        </>
                    )}
                </section>
            ) : (
                <div className="list-item-actions">
                    <button
                        type="button"
                        className="btn btn-danger"
                        onClick={() => {
                            void leave();
                        }}
                    >
                        {ru.contactLeavePair}
                    </button>
                </div>
            )}

            {loading ? <LoadingView /> : null}
            {error !== undefined ? (
                <ErrorView
                    message={error.message}
                    onRetry={() => {
                        suggestions.refetch();
                        rules.refetch();
                    }}
                />
            ) : null}

            {!loading && error === undefined && suggestions.status === "success" ? (
                <section className="block" aria-labelledby="suggestions-title">
                    <h3 id="suggestions-title" className="block-title">
                        {ru.suggestionsTitle}
                    </h3>
                    {suggestions.data.length === 0 ? null : (
                        <ul className="list">
                            {suggestions.data.map((item) => (
                                <li key={item.id} className="list-item">
                                    <p className="list-item-title">{item.text}</p>
                                    <p className="list-item-meta">{ru.categories[item.category]}</p>
                                    <div className="list-item-actions">
                                        <button
                                            type="button"
                                            className="btn btn-primary"
                                            onClick={() => {
                                                void accept(item.id);
                                            }}
                                        >
                                            {ru.suggestionAccept}
                                        </button>
                                        <button
                                            type="button"
                                            className="btn btn-secondary"
                                            onClick={() => {
                                                void dismiss(item.id);
                                            }}
                                        >
                                            {ru.suggestionDismiss}
                                        </button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </section>
            ) : null}

            {!loading && error === undefined && rules.status === "success" ? (
                paired ? (
                    <>
                        <RuleList
                            rules={personalRules}
                            title={ru.rulesPersonalTitle}
                            titleId="rules-personal-title"
                            emptyMessage={null}
                            displayTimezone={displayTimezone}
                            onArchive={(ruleId, text) => {
                                void archive(ruleId, text);
                            }}
                            onApprove={(ruleId) => {
                                void approve(ruleId);
                            }}
                            onReject={(ruleId) => {
                                void reject(ruleId);
                            }}
                        />
                        <RuleList
                            rules={sharedRules}
                            title={ru.rulesSharedTitle}
                            titleId="rules-shared-title"
                            emptyMessage={null}
                            displayTimezone={displayTimezone}
                            onArchive={(ruleId, text) => {
                                void archive(ruleId, text);
                            }}
                            onApprove={(ruleId) => {
                                void approve(ruleId);
                            }}
                            onReject={(ruleId) => {
                                void reject(ruleId);
                            }}
                        />
                        {allRules.length === 0 ? <EmptyView message={ru.rulesEmpty} /> : null}
                    </>
                ) : (
                    <RuleList
                        rules={allRules}
                        title={ru.rulesTitle}
                        titleId="rules-title"
                        emptyMessage={ru.rulesEmpty}
                        displayTimezone={displayTimezone}
                        onArchive={(ruleId, text) => {
                            void archive(ruleId, text);
                        }}
                        onApprove={(ruleId) => {
                            void approve(ruleId);
                        }}
                        onReject={(ruleId) => {
                            void reject(ruleId);
                        }}
                    />
                )
            ) : null}
        </section>
    );
}
