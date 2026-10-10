import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import { ApiProvider } from "../api/ApiContext";
import { createApiClient } from "../api/client";
import { ru } from "../localization/ru";
import type { Contact } from "../hooks/useContacts";
import { fakeAdapter, jsonResponse, mockFetch } from "../test/fakeTelegram";
import { ComposeScreen } from "./ComposeScreen";

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

async function requestJson(input: RequestInfo | URL, init?: RequestInit): Promise<unknown> {
    if (input instanceof Request) {
        return input.clone().json();
    }
    const body = init?.body;
    if (typeof body === "string") {
        return JSON.parse(body);
    }
    return {};
}

const anya: Contact = {
    id: "c1",
    label: "Аня",
    relationship: "partner",
    pair_id: null,
    paired: false,
    created_at: "2026-10-01T12:00:00.000Z",
};

const borya: Contact = {
    id: "c2",
    label: "Боря",
    relationship: "friend",
    pair_id: null,
    paired: false,
    created_at: "2026-10-01T12:00:00.000Z",
};

const okVariants = {
    safety: "ok" as const,
    variants: [
        { firmness: "gentle" as const, text: "мягкий вариант" },
        { firmness: "balanced" as const, text: "ровный вариант" },
        { firmness: "firm" as const, text: "твёрдый вариант" },
    ],
    applied_rules: [{ index: 0, text: "не перебивать" }],
};

function composeFetch(
    handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> | Response,
): Mock<typeof fetch> {
    const impl: typeof fetch = (input, init) => {
        if (isComposePost(input) || isChoicePost(input)) {
            return Promise.resolve(handler(input, init));
        }
        return mockFetch([])(input, init);
    };
    return vi.fn(impl);
}

function renderCompose(
    fetchImpl: typeof fetch,
    options: {
        readonly activeContactId?: string | null;
        readonly contacts?: readonly Contact[];
        readonly activateStatus?: number;
    } = {},
) {
    const telegram = fakeAdapter();
    const contacts = options.contacts ?? [anya];
    const withContacts: typeof fetch = (input, init) => {
        const url = requestUrl(input);
        if (url.endsWith("/api/v1/contacts")) {
            return Promise.resolve(jsonResponse({ contacts }));
        }
        if (url.endsWith("/activate")) {
            const status = options.activateStatus ?? 204;
            if (status === 204) {
                return Promise.resolve(jsonResponse(null, 204));
            }
            return Promise.resolve(
                jsonResponse({ code: "not_found", message: "activate failed" }, status),
            );
        }
        return fetchImpl(input, init);
    };
    const client = createApiClient({ initData: telegram.initData, fetch: withContacts });
    const handlers = {
        onLimit: vi.fn(),
        onCrisis: vi.fn(),
        onActivated: vi.fn(),
    };
    render(
        <ApiProvider initData={telegram.initData} client={client}>
            <ComposeScreen
                telegram={telegram}
                activeContactId={
                    options.activeContactId === undefined ? "c1" : options.activeContactId
                }
                {...handlers}
            />
        </ApiProvider>,
    );
    return { telegram, ...handlers };
}

function isComposePost(input: RequestInfo | URL): boolean {
    const url = requestUrl(input);
    return url.includes("/api/v1/compose") && !url.includes("/choice");
}

function isChoicePost(input: RequestInfo | URL): boolean {
    return requestUrl(input).includes("/api/v1/compose/choice");
}

function submitDraft(text: string): void {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
}

describe("ComposeScreen", () => {
    it("submits draft with intent and contact, shows editable variants and applied rules", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        renderCompose(fetchImpl);
        expect(
            await screen.findByText(ru.composeFor.replace("{label}", "Аня")),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("radio", { name: ru.composeIntents.decline }));
        submitDraft("не могу прийти");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        expect(screen.getByDisplayValue("ровный вариант")).toBeInTheDocument();
        expect(screen.getByDisplayValue("твёрдый вариант")).toBeInTheDocument();
        expect(
            screen.getByText(ru.composeAppliedRule.replace("{text}", "не перебивать")),
        ).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        expect(call).toBeDefined();
        expect(await requestJson(call?.[0] as RequestInfo, call?.[1])).toEqual({
            draft: "не могу прийти",
            intent: "decline",
            contact_id: "c1",
        });
        fireEvent.change(screen.getByDisplayValue("мягкий вариант"), {
            target: { value: "отредактировано" },
        });
        expect(screen.getByDisplayValue("отредактировано")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.composeAgain }));
        expect(await screen.findByRole("button", { name: ru.composeSubmit })).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toHaveValue("");
    });

    it("allows submit without contact and sends null contact_id", async () => {
        const fetchImpl = composeFetch(() =>
            jsonResponse({
                safety: "ok",
                variants: [{ firmness: "gentle", text: "без правил" }],
                applied_rules: [],
            }),
        );
        renderCompose(fetchImpl, { activeContactId: null });
        expect(await screen.findByText(ru.pickerNone)).toBeInTheDocument();
        submitDraft("черновик без контакта");
        expect(await screen.findByDisplayValue("без правил")).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        const body = (await requestJson(call?.[0] as RequestInfo, call?.[1])) as {
            contact_id: string | null;
        };
        expect(body.contact_id).toBeNull();
    });

    it("picks none contact from the picker", async () => {
        renderCompose(mockFetch([]));
        fireEvent.click(await screen.findByText(ru.composeFor.replace("{label}", "Аня")));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: ru.pickerNone }));
        expect(screen.getByText(ru.pickerNone)).toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("activates another contact from the picker", async () => {
        const { onActivated } = renderCompose(mockFetch([]), {
            contacts: [anya, borya],
        });
        fireEvent.click(await screen.findByText(ru.composeFor.replace("{label}", "Аня")));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /Боря/ }));
        await waitFor(() => {
            expect(onActivated).toHaveBeenCalledWith("c2");
        });
        expect(
            await screen.findByText(ru.composeFor.replace("{label}", "Боря")),
        ).toBeInTheDocument();
    });

    it("skips activate when the already-active contact is chosen", async () => {
        const { onActivated, telegram } = renderCompose(mockFetch([]));
        fireEvent.click(await screen.findByText(ru.composeFor.replace("{label}", "Аня")));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /Аня/ }));
        await waitFor(() => {
            expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        });
        expect(onActivated).not.toHaveBeenCalled();
        expect(telegram.hapticNotification).not.toHaveBeenCalledWith("error");
    });

    it("reports activate failure with haptic error", async () => {
        const { onActivated, telegram } = renderCompose(mockFetch([]), {
            contacts: [anya, borya],
            activateStatus: 404,
        });
        fireEvent.click(await screen.findByText(ru.composeFor.replace("{label}", "Аня")));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /Боря/ }));
        await waitFor(() => {
            expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
        });
        expect(onActivated).not.toHaveBeenCalled();
    });

    it("copies text only without choice or close", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        const { telegram } = renderCompose(fetchImpl);
        submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const cards = screen.getAllByRole("article");
        const gentle = cards[0];
        expect(gentle).toBeDefined();
        fireEvent.click(
            within(gentle as HTMLElement).getByRole("button", { name: ru.composeCopy }),
        );
        await waitFor(() => {
            expect(telegram.copyText).toHaveBeenCalledWith("мягкий вариант");
        });
        expect(telegram.close).not.toHaveBeenCalled();
        expect(fetchImpl.mock.calls.some(([req]) => isChoicePost(req))).toBe(false);
        expect(screen.getByText(ru.composePasteHint)).toBeInTheDocument();
    });

    it("skips paste hint when copy fails", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        const { telegram } = renderCompose(fetchImpl);
        vi.mocked(telegram.copyText).mockResolvedValue(false);
        submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const cards = screen.getAllByRole("article");
        fireEvent.click(
            within(cards[0] as HTMLElement).getByRole("button", { name: ru.composeCopy }),
        );
        await waitFor(() => {
            expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
        });
        expect(screen.queryByText(ru.composePasteHint)).not.toBeInTheDocument();
    });

    it("copy-and-return records help_say choice then closes", async () => {
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ outcome: "recorded" });
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = renderCompose(fetchImpl);
        fireEvent.click(screen.getByRole("radio", { name: ru.composeIntents.set_boundary }));
        submitDraft("граница");
        expect(await screen.findByDisplayValue("твёрдый вариант")).toBeInTheDocument();
        const cards = screen.getAllByRole("article");
        const firm = cards[2];
        expect(firm).toBeDefined();
        fireEvent.click(
            within(firm as HTMLElement).getByRole("button", { name: ru.composeCopyAndReturn }),
        );
        await waitFor(() => {
            expect(telegram.copyText).toHaveBeenCalledWith("твёрдый вариант");
            expect(telegram.close).toHaveBeenCalled();
        });
        const choiceCall = fetchImpl.mock.calls.find(([req]) => isChoicePost(req));
        expect(choiceCall).toBeDefined();
        expect(await requestJson(choiceCall?.[0] as RequestInfo, choiceCall?.[1])).toEqual({
            scenario: "help_say",
            firmness: "firm",
        });
    });

    it("copy-and-return records soften scenario for soften intent", async () => {
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ outcome: "recorded" });
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = renderCompose(fetchImpl);
        submitDraft("смягчить это");
        expect(await screen.findByDisplayValue("ровный вариант")).toBeInTheDocument();
        const cards = screen.getAllByRole("article");
        const balanced = cards[1];
        expect(balanced).toBeDefined();
        fireEvent.click(
            within(balanced as HTMLElement).getByRole("button", { name: ru.composeCopyAndReturn }),
        );
        await waitFor(() => {
            expect(telegram.close).toHaveBeenCalled();
        });
        const choiceCall = fetchImpl.mock.calls.find(([req]) => isChoicePost(req));
        expect(await requestJson(choiceCall?.[0] as RequestInfo, choiceCall?.[1])).toEqual({
            scenario: "soften",
            firmness: "balanced",
        });
    });

    it("does not close when choice recording fails", async () => {
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ code: "validation_error", message: "bad" }, 422);
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = renderCompose(fetchImpl);
        submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        fireEvent.click(
            within(screen.getAllByRole("article")[0] as HTMLElement).getByRole("button", {
                name: ru.composeCopyAndReturn,
            }),
        );
        await waitFor(() => {
            expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
        });
        expect(telegram.close).not.toHaveBeenCalled();
    });

    it("does not call choice when copy-and-return copy fails", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        const { telegram } = renderCompose(fetchImpl);
        vi.mocked(telegram.copyText).mockResolvedValue(false);
        submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        fireEvent.click(
            within(screen.getAllByRole("article")[0] as HTMLElement).getByRole("button", {
                name: ru.composeCopyAndReturn,
            }),
        );
        await waitFor(() => {
            expect(telegram.copyText).toHaveBeenCalled();
        });
        expect(fetchImpl.mock.calls.some(([req]) => isChoicePost(req))).toBe(false);
        expect(telegram.close).not.toHaveBeenCalled();
    });

    it("hands crisis to onCrisis including missing lead/resources", async () => {
        const fetchImpl = composeFetch(() =>
            jsonResponse({
                safety: "crisis",
                variants: [],
                applied_rules: [],
            }),
        );
        const { onCrisis } = renderCompose(fetchImpl);
        submitDraft("кризисный текст");
        await waitFor(() => {
            expect(onCrisis).toHaveBeenCalledWith(null, []);
        });
    });

    it("shows refuse copy inline", async () => {
        const fetchImpl = composeFetch(() =>
            jsonResponse({
                safety: "refuse_manipulation",
                variants: [],
                applied_rules: [],
            }),
        );
        renderCompose(fetchImpl);
        submitDraft("манипуляция");
        expect(await screen.findByText(ru.composeRefused)).toBeInTheDocument();
    });

    it("routes quota and budget limits to onLimit", async () => {
        const fetchImpl = composeFetch(() =>
            jsonResponse(
                {
                    code: "quota_exhausted",
                    message: "quota",
                    retry_at: "2026-03-16T21:00:00Z",
                },
                429,
            ),
        );
        const first = renderCompose(fetchImpl);
        submitDraft("текст для лимита");
        await waitFor(() => {
            expect(first.onLimit).toHaveBeenCalledWith("quota", "quota");
        });
        cleanup();

        const budgetFetch = composeFetch(() =>
            jsonResponse(
                {
                    code: "service_budget_exhausted",
                    message: "budget",
                    retry_at: "2026-03-16T21:00:00Z",
                },
                503,
            ),
        );
        const second = renderCompose(budgetFetch);
        submitDraft("бюджет");
        await waitFor(() => {
            expect(second.onLimit).toHaveBeenCalledWith("budget", "budget");
        });
    });

    it("maps catalog error codes and empty draft", async () => {
        renderCompose(mockFetch([]));
        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        expect(await screen.findByText(ru.composeErrorShort)).toBeInTheDocument();

        const cases: readonly { code: string; message: string }[] = [
            { code: "busy", message: ru.composeErrorBusy },
            { code: "quota_exceeded", message: ru.composeErrorQuota },
            { code: "text_too_short", message: ru.composeErrorShort },
            { code: "text_too_long", message: ru.composeErrorLong },
            { code: "invalid_output", message: ru.composeErrorInvalid },
            { code: "generation_unavailable", message: ru.composeErrorUnavailable },
        ];
        for (const item of cases) {
            cleanup();
            const fetchImpl = composeFetch(() =>
                jsonResponse({ code: item.code, message: item.code }, 422),
            );
            renderCompose(fetchImpl);
            submitDraft("достаточно длинный черновик");
            expect(await screen.findByText(item.message)).toBeInTheDocument();
        }
    });

    it("pastes from clipboard and shows paste failure", async () => {
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { readText: vi.fn(() => Promise.resolve("из буфера")) },
        });
        renderCompose(mockFetch([]));
        fireEvent.click(screen.getByRole("button", { name: ru.composePaste }));
        await waitFor(() => {
            expect(screen.getByRole("textbox")).toHaveValue("из буфера");
        });
        cleanup();
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: {
                readText: vi.fn(() => Promise.reject(new Error("denied"))),
            },
        });
        renderCompose(mockFetch([]));
        fireEvent.click(screen.getByRole("button", { name: ru.composePaste }));
        expect(await screen.findByText(ru.composePasteFailed)).toBeInTheDocument();
    });

    it("shows loading skeleton while waiting", async () => {
        let resolveCompose: ((value: Response) => void) | undefined;
        const fetchImpl = composeFetch(
            () =>
                new Promise<Response>((resolve) => {
                    resolveCompose = resolve;
                }),
        );
        renderCompose(fetchImpl);
        submitDraft("ждём");
        expect(await screen.findByText(ru.loading)).toBeInTheDocument();
        resolveCompose?.(jsonResponse(okVariants));
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
    });
});
