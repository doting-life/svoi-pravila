/** Russian UI catalog for the mini-app (single source of user-facing strings). */
export const ru = {
    appTitle: "Свои Правила",
    openFromTelegram: "Откройте приложение из Telegram",
    loading: "Загрузка…",
    errorGeneric: "Не удалось загрузить. Попробуйте ещё раз.",
    retry: "Повторить",
    close: "Закрыть",
    cancel: "Отмена",
    save: "Сохранить",
    add: "Добавить",
    gateIncomplete: "Завершите настройку в чате с ботом",
    gateConsentExtra: "Нужно подтвердить согласия в боте.",
    gateUnauthorized: "Откройте приложение заново из Telegram",
    contactsTitle: "Контакты",
    contactsEmpty: "Пока нет контактов. Добавьте человека, с которым хотите договориться.",
    contactsAdd: "Добавить контакт",
    contactsRename: "Переименовать",
    contactsMakeActive: "Сделать активным",
    contactsActiveBadge: "активный",
    contactsLabel: "Как называть",
    contactsRelationship: "Отношения",
    contactsLimit: "Достигнут лимит контактов.",
    contactAddRule: "Добавить правило",
    privacyTitle: "Приватность",
    privacyExportBlurb:
        "Выгрузка содержит контакты, согласия, правила, предложения правил и историю выбора тона. Файл придёт в чат с ботом и не хранится на сервере.",
    privacyExportAction: "Выгрузить данные",
    privacyExportDone: "Файл отправлен в чат с ботом",
    privacyExportUnavailable: "Напишите боту /start, затем повторите",
    privacyRevokeBlurb:
        "Отзыв согласий остановит обработку сообщений. Сохранённые правила и контакты останутся, пока вы не удалите аккаунт.",
    privacyRevokeAction: "Отозвать согласия",
    privacyRevokeConfirm:
        "Отзыв согласий остановит обработку сообщений. Сохранённые правила и контакты останутся, пока вы не удалите аккаунт. Подтвердите отзыв.",
    privacyDeleteBlurb:
        "Удаление сотрёт ваш аккаунт, согласия, контакты и ваши правила. Общие правила, которые написал партнёр, останутся у него. Это нельзя отменить.",
    privacyDeleteAction: "Удалить аккаунт",
    privacyDeleteConfirm:
        "Удаление сотрёт ваш аккаунт, согласия, контакты и ваши правила. Общие правила, которые написал партнёр, останутся у него. Это нельзя отменить.",
    privacyDeleteForever: "Удалить навсегда",
    privacyDeleted: "Аккаунт удалён",
    rateLimited: "Слишком много запросов. Подождите немного.",
    suggestionsTitle: "Предложения",
    suggestionAccept: "Добавить",
    suggestionDismiss: "Не надо",
    rulesTitle: "Правила",
    rulesEmpty: "Правило — короткая договорённость, которую вы хотите помнить в разговоре.",
    ruleEffectiveSince: "действует с {date}",
    rulePendingEdit: "есть правка на согласовании",
    ruleProposed: "ждёт согласования",
    ruleArchive: "Архивировать",
    ruleArchiveConfirm: "Архивировать правило «{text}»?",
    addRuleTitle: "Новое правило",
    addRuleCategory: "Категория",
    addRuleText: "Текст",
    addRuleSubmit: "Сохранить правило",
    addRuleValidation: "Проверьте текст правила.",
    addRuleOpenLimit: "Слишком много открытых правил.",
    relationships: {
        partner: "Партнёр",
        family: "Семья",
        friend: "Друг",
        work: "Работа",
        other: "Другое",
    },
    categories: {
        taboo_topic: "Запретная тема",
        how_to_ask: "Как спрашивать",
        apology: "Извинения",
        conflict_protocol: "Конфликт",
        other: "Другое",
    },
} as const;

export type RuStrings = typeof ru;

export type RelationshipKey = keyof typeof ru.relationships;
export type CategoryKey = keyof typeof ru.categories;
