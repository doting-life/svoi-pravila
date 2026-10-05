import { formatDisplayDate } from "../dates/formatDisplayDate";
import { EmptyView, ErrorView, LoadingView } from "../components/StatusViews";
import { useRules } from "../hooks/useRules";
import { useSuggestions } from "../hooks/useSuggestions";
import { ru } from "../localization/ru";
import type { TelegramAdapter } from "../telegram/webapp";

export type ContactDetailScreenProps = {
    readonly contactId: string;
    readonly displayTimezone: string;
    readonly telegram: TelegramAdapter;
    readonly onAddRule: () => void;
    readonly onRulesChanged: () => void;
};

export function ContactDetailScreen({
    contactId,
    displayTimezone,
    telegram,
    onAddRule,
    onRulesChanged,
}: ContactDetailScreenProps) {
    const suggestions = useSuggestions(contactId);
    const rules = useRules(contactId);

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

    const loading = suggestions.status === "loading" || rules.status === "loading";
    const error =
        suggestions.status === "error"
            ? suggestions.error
            : rules.status === "error"
              ? rules.error
              : undefined;

    return (
        <section className="screen" aria-labelledby="detail-title">
            <header className="screen-header">
                <h2 id="detail-title" className="screen-title">
                    {ru.contactDetailTitle}
                </h2>
                <button type="button" className="btn btn-primary" onClick={onAddRule}>
                    {ru.contactAddRule}
                </button>
            </header>

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
                <section className="block" aria-labelledby="rules-title">
                    <h3 id="rules-title" className="block-title">
                        {ru.rulesTitle}
                    </h3>
                    {rules.data.length === 0 ? <EmptyView message={ru.rulesEmpty} /> : null}
                    {rules.data.length > 0 ? (
                        <ul className="list">
                            {rules.data.map((rule) => (
                                <li key={rule.id} className="list-item">
                                    <p className="list-item-title">{rule.text}</p>
                                    <p className="list-item-meta">
                                        {ru.categories[rule.category]}
                                        {rule.status === "active" &&
                                        rule.effective_since !== null &&
                                        rule.effective_since !== undefined
                                            ? ` · ${ru.ruleEffectiveSince.replace(
                                                  "{date}",
                                                  formatDisplayDate(
                                                      rule.effective_since,
                                                      displayTimezone,
                                                  ),
                                              )}`
                                            : ""}
                                        {rule.status === "proposed" ? ` · ${ru.ruleProposed}` : ""}
                                    </p>
                                    {rule.has_pending_edit ? (
                                        <p className="badge">{ru.rulePendingEdit}</p>
                                    ) : null}
                                    {rule.status === "active" || rule.status === "proposed" ? (
                                        <div className="list-item-actions">
                                            <button
                                                type="button"
                                                className="btn btn-danger"
                                                onClick={() => {
                                                    void archive(rule.id, rule.text);
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
            ) : null}
        </section>
    );
}
