import { vi } from "vitest";

import type { TelegramAdapter } from "../telegram/webapp";

export function fakeAdapter(overrides: Partial<TelegramAdapter> = {}): TelegramAdapter {
    return {
        initData: "query_id=1",
        startParam: null,
        colorScheme: "light",
        isInsideTelegram: true,
        BackButton: {
            show: vi.fn(),
            hide: vi.fn(),
            onClick: () => () => undefined,
        },
        ready: vi.fn(),
        expand: vi.fn(),
        close: vi.fn(),
        showConfirm: vi.fn(() => Promise.resolve(true)),
        openTelegramLink: vi.fn(),
        downloadFile: vi.fn(() => Promise.resolve(true)),
        copyText: vi.fn(() => Promise.resolve(true)),
        hapticNotification: vi.fn(),
        applyChromeColor: vi.fn(),
        switchInlineQuery: vi.fn(),
        onColorSchemeChanged: () => () => undefined,
        ...overrides,
    };
}

export function jsonResponse(body: unknown, status = 200): Response {
    if (status === 204) {
        return new Response(null, { status });
    }
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}

export type MockRoute = {
    readonly method?: string;
    readonly path: string;
    readonly status?: number;
    readonly body?: unknown;
};

/** Synthetic API copy for RTL (not the production catalog wording). */
export const privacyTexts = {
    export: {
        description: "TEST_EXPORT_DESCRIPTION",
        sections: {
            согласия: "согласия",
            контакты: "контакты",
            правила: "правила",
            общие_правила: "правила",
            предложения: "предложения правил",
            сигналы_тона: "историю выбора тона",
        },
    },
    revoke: {
        description: "TEST_REVOKE_DESCRIPTION",
        confirm: "TEST_REVOKE_CONFIRM",
    },
    delete: {
        description: "TEST_DELETE_DESCRIPTION",
        confirm: "TEST_DELETE_CONFIRM",
    },
    leave_pair: {
        description: "TEST_LEAVE_PAIR_DESCRIPTION",
        confirm: "TEST_LEAVE_PAIR_CONFIRM",
    },
} as const;

export function mockFetch(routes: readonly MockRoute[]): typeof fetch {
    const withDefaults: MockRoute[] = [
        ...routes,
        { path: "/api/v1/privacy/texts", body: privacyTexts },
        { path: "/api/v1/rules/pending", body: { items: [] } },
    ];
    const impl: typeof fetch = (input, init) => {
        const request = input instanceof Request ? input : new Request(String(input), init);
        const url = new URL(request.url);
        const method = request.method.toUpperCase();
        const match = withDefaults.find((route) => {
            if (url.pathname !== route.path) {
                return false;
            }
            const expected = (route.method ?? "GET").toUpperCase();
            return expected === method;
        });
        if (match === undefined) {
            return Promise.resolve(
                jsonResponse({ code: "not_found", message: "missing mock" }, 404),
            );
        }
        return Promise.resolve(jsonResponse(match.body ?? {}, match.status ?? 200));
    };
    return vi.fn(impl);
}
