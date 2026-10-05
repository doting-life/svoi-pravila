import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiProvider } from "../api/ApiContext";
import { createApiClient } from "../api/client";
import { ru } from "../localization/ru";
import { fakeAdapter, jsonResponse, mockFetch } from "../test/fakeTelegram";
import { DecodeScreen } from "./DecodeScreen";

afterEach(() => {
    cleanup();
});

function requestUrl(input: RequestInfo | URL): string {
    if (typeof input === "string") {
        return input;
    }
    if (input instanceof URL) {
        return input.href;
    }
    return input.url;
}

function renderDecode(fetchImpl: typeof fetch) {
    const telegram = fakeAdapter();
    const client = createApiClient({ initData: telegram.initData, fetch: fetchImpl });
    const onEditSuggestion = vi.fn();
    render(
        <ApiProvider initData={telegram.initData} client={client}>
            <DecodeScreen
                telegram={telegram}
                displayTimezone="Europe/Moscow"
                activeContactId="c1"
                fetchImpl={fetchImpl}
                onEditSuggestion={onEditSuggestion}
            />
        </ApiProvider>,
    );
    return { telegram, onEditSuggestion };
}

function sseResponse(body: string): Response {
    return new Response(body, {
        status: 200,
        headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
        },
    });
}

function completedSse(options?: {
    readonly firmness?: string;
    readonly insertQuery?: string | null;
    readonly ruleSourceToken?: string | null;
    readonly appliedRules?: boolean;
    readonly category?: string;
}): string {
    const firmness = options?.firmness ?? "gentle";
    const insertQueryJson =
        options?.insertQuery === undefined ? "null" : JSON.stringify(options.insertQuery);
    const tokenJson =
        options?.ruleSourceToken === undefined
            ? '"tok"'
            : options.ruleSourceToken === null
              ? "null"
              : JSON.stringify(options.ruleSourceToken);
    const rules = options?.appliedRules
        ? '[{"category":"other","text":"правило","effective_since":"2026-01-01T00:00:00Z"}]'
        : "[]";
    return (
        'event: analysis\ndata: {"chunk":"x"}\n\n' +
        `event: completed\ndata: {"safety":"ok","variants":[{"firmness":"${firmness}","text":"вариант","insert_query":${insertQueryJson}}],"applied_rules":${rules},"rule_source_token":${tokenJson}}\n\n`
    );
}

const okSuggestion = {
    id: "s1",
    category: "other",
    text: "предложенное",
    source: "decode",
    firmness: null,
    created_at: "2026-01-01T00:00:00Z",
};

describe("DecodeScreen", () => {
    it("streams analysis then shows variants, copy and insert", async () => {
        const writeText = vi.fn(() => Promise.resolve());
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText },
        });
        const sse = [
            'event: analysis\ndata: {"chunk":"часть "}\n\n',
            'event: analysis\ndata: {"chunk":"анализа"}\n\n',
            'event: completed\ndata: {"safety":"ok","variants":[{"firmness":"gentle","text":"вариант","insert_query":"p_token"}],"applied_rules":[{"category":"other","text":"правило","effective_since":"2026-01-01T00:00:00Z"}],"rule_source_token":"sn-token"}\n\n',
        ].join("");
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode") && init?.method === "POST") {
                return Promise.resolve(sseResponse(sse));
            }
            return mockFetch([{ path: "/api/v1/me", body: { onboarding_step: "done" } }])(
                input,
                init,
            );
        }) as typeof fetch;
        const { telegram } = renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), {
            target: { value: "входящее сообщение" },
        });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        await waitFor(() => {
            expect(screen.getByText(/часть анализа/)).toBeInTheDocument();
        });
        expect(await screen.findByText("вариант")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeCopy }));
        await waitFor(() => {
            expect(writeText).toHaveBeenCalledWith("вариант");
        });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeInsert }));
        expect(telegram.switchInlineQuery).toHaveBeenCalledWith("p_token", [
            "users",
            "groups",
            "channels",
        ]);
    });

    it("shows crisis resources and keeps text on error", async () => {
        const crisis = 'event: crisis\ndata: {"resources":["линия помощи"]}\n\n';
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode")) {
                return Promise.resolve(sseResponse(crisis));
            }
            return Promise.resolve(jsonResponse({}));
        }) as typeof fetch;
        renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "кризисный текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText("линия помощи")).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toHaveValue("кризисный текст");
    });

    it("aborts in-flight decode on unmount", async () => {
        let aborted = false;
        const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
            const signal = init?.signal;
            if (signal != null) {
                signal.addEventListener("abort", () => {
                    aborted = true;
                });
            }
            return new Promise<Response>(() => undefined);
        }) as typeof fetch;
        const telegram = fakeAdapter();
        const client = createApiClient({ initData: telegram.initData, fetch: fetchImpl });
        const view = render(
            <ApiProvider initData={telegram.initData} client={client}>
                <DecodeScreen
                    telegram={telegram}
                    displayTimezone="Europe/Moscow"
                    activeContactId="c1"
                    fetchImpl={fetchImpl}
                    onEditSuggestion={() => undefined}
                />
            </ApiProvider>,
        );
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        view.unmount();
        await waitFor(() => {
            expect(aborted).toBe(true);
        });
    });

    it("shows refused and catalog error codes", async () => {
        const refused = "event: refused\ndata: {}\n\n";
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode")) {
                return Promise.resolve(sseResponse(refused));
            }
            return Promise.resolve(jsonResponse({}, 404));
        }) as typeof fetch;
        renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeRefused)).toBeInTheDocument();

        const err = 'event: error\ndata: {"code":"quota_exceeded"}\n\n';
        const fetchErr = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode")) {
                return Promise.resolve(sseResponse(err));
            }
            return Promise.resolve(jsonResponse({}, 404));
        }) as typeof fetch;
        cleanup();
        renderDecode(fetchErr);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "ещё текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeErrorQuota)).toBeInTheDocument();
    });

    it("make-rule opens suggestion card and edit callback", async () => {
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
                return Promise.resolve(
                    sseResponse(completedSse({ firmness: "firm", insertQuery: null })),
                );
            }
            if (url.includes("/suggestions/from-decode")) {
                return Promise.resolve(
                    jsonResponse({
                        outcome: "ok",
                        suggestion: okSuggestion,
                    }),
                );
            }
            return Promise.resolve(
                jsonResponse({ code: "not_found", message: "missing mock" }, 404),
            );
        }) as typeof fetch;
        const { onEditSuggestion } = renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText("вариант")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText("предложенное")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSuggestionEdit }));
        expect(onEditSuggestion).toHaveBeenCalledWith("other", "предложенное");
    });

    it("maps pre-stream HTTP errors and empty text", async () => {
        const fetchImpl = vi.fn(() =>
            Promise.resolve(jsonResponse({ code: "busy", message: "busy" }, 409)),
        ) as typeof fetch;
        renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeErrorBusy)).toBeInTheDocument();

        cleanup();
        const emptyFetch: typeof fetch = vi.fn(() => Promise.resolve(sseResponse("")));
        renderDecode(emptyFetch);
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeErrorShort)).toBeInTheDocument();
    });

    it("dismisses a decode suggestion", async () => {
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
                return Promise.resolve(sseResponse(completedSse({ insertQuery: null })));
            }
            if (url.includes("/suggestions/from-decode")) {
                return Promise.resolve(jsonResponse({ outcome: "ok", suggestion: okSuggestion }));
            }
            if (url.includes("/dismiss")) {
                return Promise.resolve(jsonResponse({ outcome: "dismissed", suggestion_id: "s1" }));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "missing" }, 404));
        }) as typeof fetch;
        renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        fireEvent.click(await screen.findByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText("предложенное")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionDismiss }));
        await waitFor(() => {
            expect(screen.queryByText("предложенное")).not.toBeInTheDocument();
        });
    });

    it("accepts a decode suggestion", async () => {
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
                return Promise.resolve(
                    sseResponse(completedSse({ firmness: "balanced", insertQuery: null })),
                );
            }
            if (url.includes("/suggestions/from-decode")) {
                return Promise.resolve(
                    jsonResponse({
                        outcome: "ok",
                        suggestion: { ...okSuggestion, category: "custom-cat" },
                    }),
                );
            }
            if (url.includes("/accept")) {
                return Promise.resolve(
                    jsonResponse({ outcome: "accepted", suggestion_id: "s1", rule_id: "r1" }),
                );
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "missing" }, 404));
        }) as typeof fetch;
        const { telegram } = renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        fireEvent.click(await screen.findByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText("предложенное")).toBeInTheDocument();
        expect(screen.getByText("custom-cat")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionAccept }));
        await waitFor(() => {
            expect(screen.queryByText("предложенное")).not.toBeInTheDocument();
        });
        expect(telegram.hapticNotification).toHaveBeenCalledWith("success");
    });

    it("maps suggest non-ok outcomes and accept/dismiss failures", async () => {
        const sse =
            'event: completed\ndata: {"safety":"ok","variants":[{"firmness":"weird","text":"v","insert_query":""}],"applied_rules":[],"rule_source_token":"tok"}\n\n';
        let suggestCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
                return Promise.resolve(sseResponse(sse));
            }
            if (url.includes("/suggestions/from-decode")) {
                suggestCalls += 1;
                if (suggestCalls === 1) {
                    return Promise.resolve(jsonResponse({ outcome: "none", suggestion: null }));
                }
                if (suggestCalls === 2) {
                    return Promise.resolve(
                        jsonResponse({ outcome: "pending_exists", suggestion: null }),
                    );
                }
                return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "missing" }, 404));
        }) as typeof fetch;
        const { telegram } = renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText("weird")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeInsert }));
        expect(telegram.switchInlineQuery).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText(ru.decodeSuggestNone)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText(ru.decodeSuggestPending)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText(ru.decodeErrorUnavailable)).toBeInTheDocument();
    });

    it("maps remaining catalog error codes and network failures", async () => {
        for (const [code, message] of [
            ["text_too_short", ru.decodeErrorShort],
            ["text_too_long", ru.decodeErrorLong],
            ["invalid_output", ru.decodeErrorInvalid],
            ["generation_unavailable", ru.decodeErrorUnavailable],
        ] as const) {
            cleanup();
            const fetchImpl = vi.fn(() =>
                Promise.resolve(sseResponse(`event: error\ndata: {"code":"${code}"}\n\n`)),
            ) as typeof fetch;
            renderDecode(fetchImpl);
            fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
            fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
            expect(await screen.findByText(message)).toBeInTheDocument();
        }

        cleanup();
        const fetchFail = vi.fn(() => Promise.reject(new TypeError("network"))) as typeof fetch;
        const { telegram } = renderDecode(fetchFail);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeErrorUnavailable)).toBeInTheDocument();
        expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
    });

    it("rejects overlong text and non-json pre-stream bodies", async () => {
        const emptyFetch: typeof fetch = vi.fn(() => Promise.resolve(sseResponse("")));
        renderDecode(emptyFetch);
        fireEvent.change(screen.getByRole("textbox"), {
            target: { value: "x".repeat(4001) },
        });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeErrorLong)).toBeInTheDocument();

        cleanup();
        const fetchImpl = vi.fn(() =>
            Promise.resolve(
                new Response("not-json", {
                    status: 503,
                    headers: { "Content-Type": "text/plain" },
                }),
            ),
        ) as typeof fetch;
        renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText(ru.decodeErrorUnavailable)).toBeInTheDocument();
    });

    it("reports clipboard failures", async () => {
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: {
                writeText: vi.fn(() => Promise.reject(new Error("denied"))),
            },
        });
        const sse =
            'event: completed\ndata: {"safety":"ok","variants":[{"firmness":"gentle","text":"вариант","insert_query":null}],"applied_rules":[],"rule_source_token":null}\n\n';
        const fetchImpl: typeof fetch = vi.fn(() => Promise.resolve(sseResponse(sse)));
        const { telegram } = renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        expect(await screen.findByText("вариант")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: ru.decodeMakeRule })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeCopy }));
        await waitFor(() => {
            expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
        });
    });

    it("haptics error when accept or dismiss fails", async () => {
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const url = requestUrl(input);
            if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
                return Promise.resolve(sseResponse(completedSse({ insertQuery: null })));
            }
            if (url.includes("/suggestions/from-decode")) {
                return Promise.resolve(jsonResponse({ outcome: "ok", suggestion: okSuggestion }));
            }
            return Promise.resolve(jsonResponse({ code: "conflict", message: "nope" }, 409));
        }) as typeof fetch;
        const { telegram } = renderDecode(fetchImpl);
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "текст" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
        fireEvent.click(await screen.findByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText("предложенное")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionAccept }));
        await waitFor(() => {
            expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
        });
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionDismiss }));
        await waitFor(() => {
            const haptic = vi.mocked(telegram.hapticNotification);
            expect(
                haptic.mock.calls.filter((call) => call[0] === "error").length,
            ).toBeGreaterThanOrEqual(2);
        });
    });
});
