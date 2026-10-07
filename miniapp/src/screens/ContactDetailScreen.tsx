import { useState } from "react";

import { Avatar } from "../components/Avatar";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { PencilIcon, PlusIcon } from "../components/icons";
import { EmptyView, ErrorView, LoadingView } from "../components/StatusViews";
import { formatDisplayDate } from "../dates/formatDisplayDate";
import { useContacts } from "../hooks/useContacts";
import { usePrivacyTexts } from "../hooks/usePrivacyTexts";
import { useRules, type Rule } from "../hooks/useRules";
import { useSuggestions } from "../hooks/useSuggestions";
import { ru, type RelationshipKey } from "../localization/ru";
import { ContactFormSheet } from "../sheets/ContactFormSheet";
import type { TelegramAdapter } from "../telegram/webapp";

export type ContactDetailScreenProps = {
    readonly contactId: string;
    readonly label: string;
    readonly relationship: string;
    readonly paired: boolean;
    readonly activeContactId: string | null | undefined;
    readonly displayTimezone: string;
    readonly telegram: TelegramAdapter;
    readonly onAddRule: () => void;
    readonly onPairingChanged: (paired: boolean) => void;
    readonly onRenamed: (label: string) => void;
    readonly onActivated: (contactId: string) => void;
};

type RuleActions = {
    readonly displayTimezone: string;
    readonly partnerLabel: string;
    readonly onArchive: (ruleId: string, text: string) => void;
    readonly onApprove: (ruleId: string) => void;
    readonly onReject: (ruleId: string) => void;
};

function ruleMeta(rule: Rule, displayTimezone: string): string {
    const parts: string[] = [ru.categories[rule.category]];
    if (
        rule.status === "active" &&
        rule.effective_since !== null &&
        rule.effective_since !== undefined
    ) {
        parts.push(
            ru.ruleEffectiveSince.replace(
                "{date}",
                formatDisplayDate(rule.effective_since, displayTimezone),
            ),
        );
    }
    if (rule.status === "proposed" && !rule.needs_my_approval) {
        parts.push(rule.shared ? ru.ruleAwaitingPartner : ru.ruleProposed);
    }
    return parts.join(" · ");
}

function RuleCard({ rule, actions }: { readonly rule: Rule; readonly actions: RuleActions }) {
    const needsApproval = rule.needs_my_approval;
    return (
        <li>
            <Card as="article" tone={needsApproval ? "warm" : "outlined"} className="rule-card">
                {needsApproval ? (
                    <Badge tone="warm">
                        {ru.ruleNeedsApproval.replace("{name}", actions.partnerLabel)}
                    </Badge>
                ) : null}
                <p className="rule-text">{rule.text}</p>
                <p className="rule-card__meta">{ruleMeta(rule, actions.displayTimezone)}</p>
                {rule.has_pending_edit ? <Badge tone="warm">{ru.rulePendingEdit}</Badge> : null}
                {needsApproval ? (
                    <div className="rule-card__footer">
                        <Button
                            onClick={() => {
                                actions.onApprove(rule.id);
                            }}
                        >
                            {ru.ruleApprove}
                        </Button>
                        <Button
                            variant="ghost"
                            tone="warm"
                            onClick={() => {
                                actions.onReject(rule.id);
                            }}
                        >
                            {ru.ruleReject}
                        </Button>
                    </div>
                ) : null}
                {!needsApproval && (rule.status === "active" || rule.status === "proposed") ? (
                    <Button
                        variant="ghost"
                        tone="danger"
                        bare
                        className="rule-card__archive"
                        onClick={() => {
                            actions.onArchive(rule.id, rule.text);
                        }}
                    >
                        {ru.ruleArchive}
                    </Button>
                ) : null}
            </Card>
        </li>
    );
}

function RuleSection({
    title,
    titleId,
    note,
    rules,
    actions,
    emptyMessage,
}: {
    readonly title: string;
    readonly titleId: string;
    readonly note?: string;
    readonly rules: readonly Rule[];
    readonly actions: RuleActions;
    readonly emptyMessage: string | null;
}) {
    if (rules.length === 0 && emptyMessage === null) {
        return null;
    }
    return (
        <section className="section" aria-labelledby={titleId}>
            <h2 id={titleId} className="section-title">
                {title}
                {note !== undefined ? <span className="section-title__note"> {note}</span> : null}
            </h2>
            {rules.length === 0 && emptyMessage !== null ? (
                <EmptyView message={emptyMessage} />
            ) : null}
            {rules.length > 0 ? (
                <ul className="rule-list">
                    {rules.map((rule) => (
                        <RuleCard key={rule.id} rule={rule} actions={actions} />
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
    activeContactId,
    displayTimezone,
    telegram,
    onAddRule,
    onPairingChanged,
    onRenamed,
    onActivated,
}: ContactDetailScreenProps) {
    const suggestions = useSuggestions(contactId);
    const rules = useRules(contactId);
    const contacts = useContacts();
    const privacyTexts = usePrivacyTexts();
    const [inviteLink, setInviteLink] = useState<string | null>(null);
    const [inviteBusy, setInviteBusy] = useState(false);
    const [copyHint, setCopyHint] = useState(false);
    const [renaming, setRenaming] = useState(false);
    const [activateBusy, setActivateBusy] = useState(false);
    const isActive = contactId === activeContactId;

    const settle = (error: unknown): boolean => {
        telegram.hapticNotification(error === undefined ? "success" : "error");
        return error === undefined;
    };

    const archive = async (ruleId: string, text: string) => {
        const confirmed = await telegram.showConfirm(ru.ruleArchiveConfirm.replace("{text}", text));
        if (!confirmed) {
            return;
        }
        settle((await rules.archiveRule(ruleId)).error);
    };

    const approve = async (ruleId: string) => {
        settle((await rules.approveRule(ruleId)).error);
    };

    const reject = async (ruleId: string) => {
        settle((await rules.rejectRule(ruleId)).error);
    };

    const accept = async (suggestionId: string) => {
        if (settle((await suggestions.acceptSuggestion(suggestionId)).error)) {
            suggestions.refetch();
            rules.refetch();
        }
    };

    const dismiss = async (suggestionId: string) => {
        if (settle((await suggestions.dismissSuggestion(suggestionId)).error)) {
            suggestions.refetch();
        }
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
        telegram.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}`);
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
        if (!settle(result.error)) {
            return;
        }
        setInviteLink(null);
        onPairingChanged(false);
        rules.refetch();
    };

    const rename = async (input: { readonly label: string }): Promise<{ error?: string }> => {
        const result = await contacts.renameContact(contactId, input.label);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return { error: result.error.message || ru.errorGeneric };
        }
        telegram.hapticNotification("success");
        setRenaming(false);
        onRenamed(input.label);
        return {};
    };

    const activate = async () => {
        setActivateBusy(true);
        const result = await contacts.activateContact(contactId);
        setActivateBusy(false);
        if (result.error !== undefined) {
            telegram.hapticNotification("error");
            return;
        }
        telegram.hapticNotification("success");
        onActivated(contactId);
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
    const actions: RuleActions = {
        displayTimezone,
        partnerLabel: label,
        onArchive: (ruleId, text) => {
            void archive(ruleId, text);
        },
        onApprove: (ruleId) => {
            void approve(ruleId);
        },
        onReject: (ruleId) => {
            void reject(ruleId);
        },
    };
    const relationshipLabel =
        relationship in ru.relationships
            ? ru.relationships[relationship as RelationshipKey]
            : relationship;

    return (
        <section className="screen" aria-labelledby="detail-title">
            <header className="detail-header">
                <Avatar name={label} size="lg" />
                <div className="detail-header__text">
                    <h1 id="detail-title" className="screen-title">
                        {label}
                    </h1>
                    <p className="detail-header__meta">
                        {relationshipLabel}
                        {paired ? <Badge tone="warm">{ru.contactPairedBadge}</Badge> : null}
                    </p>
                </div>
                <Button
                    variant="ghost"
                    className="btn--icon"
                    aria-label={ru.contactEdit}
                    onClick={() => {
                        setRenaming(true);
                    }}
                >
                    <PencilIcon size={20} />
                </Button>
            </header>

            {!isActive ? (
                <div className="detail-actions">
                    <Button
                        variant="ghost"
                        tone="accent"
                        disabled={activateBusy}
                        onClick={() => {
                            void activate();
                        }}
                    >
                        {ru.contactsMakeActive}
                    </Button>
                </div>
            ) : null}

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

            {!loading &&
            error === undefined &&
            suggestions.status === "success" &&
            suggestions.data.length > 0 ? (
                <section className="section" aria-labelledby="suggestions-title">
                    <h2 id="suggestions-title" className="section-title">
                        {ru.suggestionsTitle}
                    </h2>
                    <ul className="rule-list">
                        {suggestions.data.map((item) => (
                            <li key={item.id}>
                                <Card as="article" tone="warm" className="rule-card">
                                    <p className="rule-text">{item.text}</p>
                                    <p className="rule-card__meta">
                                        {ru.categories[item.category]}
                                    </p>
                                    <div className="rule-card__footer">
                                        <Button
                                            onClick={() => {
                                                void accept(item.id);
                                            }}
                                        >
                                            {ru.suggestionAccept}
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            tone="warm"
                                            onClick={() => {
                                                void dismiss(item.id);
                                            }}
                                        >
                                            {ru.suggestionDismiss}
                                        </Button>
                                    </div>
                                </Card>
                            </li>
                        ))}
                    </ul>
                </section>
            ) : null}

            {!loading && error === undefined && rules.status === "success" ? (
                paired ? (
                    <>
                        <RuleSection
                            title={ru.rulesSharedTitle}
                            titleId="rules-shared-title"
                            rules={sharedRules}
                            actions={actions}
                            emptyMessage={null}
                        />
                        <RuleSection
                            title={ru.rulesPersonalTitle}
                            titleId="rules-personal-title"
                            note={ru.rulesPersonalNote}
                            rules={personalRules}
                            actions={actions}
                            emptyMessage={null}
                        />
                        {allRules.length === 0 ? <EmptyView message={ru.rulesEmpty} /> : null}
                    </>
                ) : (
                    <RuleSection
                        title={ru.rulesTitle}
                        titleId="rules-title"
                        rules={allRules}
                        actions={actions}
                        emptyMessage={ru.rulesEmpty}
                    />
                )
            ) : null}

            <Button variant="outline" block onClick={onAddRule}>
                <PlusIcon size={20} />
                {ru.contactAddRule}
            </Button>

            {!paired ? (
                <Card tone="dashed" aria-labelledby="invite-title">
                    <h2 id="invite-title" className="hint-title">
                        {ru.contactInvite}
                    </h2>
                    {inviteLink === null ? (
                        <Button
                            variant="outline"
                            disabled={inviteBusy}
                            onClick={() => {
                                void invite();
                            }}
                        >
                            {ru.contactInvite}
                        </Button>
                    ) : (
                        <>
                            <p className="hint-text">{ru.contactInviteExplain}</p>
                            <p className="invite-link">{inviteLink}</p>
                            <div className="row">
                                <Button
                                    className="row__grow"
                                    onClick={() => {
                                        shareInvite(inviteLink);
                                    }}
                                >
                                    {ru.contactInviteShare}
                                </Button>
                                <Button
                                    className="row__grow"
                                    variant="outline"
                                    onClick={() => {
                                        void copyInvite(inviteLink);
                                    }}
                                >
                                    {ru.contactInviteCopy}
                                </Button>
                            </div>
                            {copyHint ? (
                                <p className="form-status" role="status">
                                    {ru.contactInviteCopied}
                                </p>
                            ) : null}
                        </>
                    )}
                </Card>
            ) : null}

            {paired ? (
                <section className="leave-zone" aria-label={ru.contactLeavePair}>
                    <p className="hint-text">{ru.contactLeaveNote}</p>
                    <Button
                        variant="ghost"
                        tone="danger"
                        disabled={privacyTexts.status !== "success"}
                        onClick={() => {
                            void leave();
                        }}
                    >
                        {ru.contactLeavePair}
                    </Button>
                </section>
            ) : null}

            {renaming ? (
                <ContactFormSheet
                    mode="rename"
                    initialLabel={label}
                    onSubmit={rename}
                    onClose={() => {
                        setRenaming(false);
                    }}
                />
            ) : null}
        </section>
    );
}
