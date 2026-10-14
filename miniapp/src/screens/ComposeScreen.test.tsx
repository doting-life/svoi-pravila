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
        { kind: "check" as const, text: "исправленный вариант" },
        { kind: "softer" as const, text: "мягкий вариант" },
        { kind: "firmer" as const, text: "твёрдый вариант" },
    ],
    applied_rules: [{ index: 0, text: "не перебивать" }],
    choice_token: "c_test_token",
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

async function renderCompose(
    fetchImpl: typeof fetch,
    options: {
        readonly title?: string;
        readonly draft?: string;
        readonly contactId?: string | null;
        readonly contacts?: readonly Contact[];
        readonly partnerUsername?: string | null;
    } = {},
) {
    const telegram = fakeAdapter();
    const contacts = options.contacts ?? [anya];
    const withContacts: typeof fetch = (input, init) => {
        const url = requestUrl(input);
        if (url.endsWith("/api/v1/contacts")) {
            return Promise.resolve(jsonResponse({ contacts }));
        }
        if (url.includes("/partner-username")) {
            return Promise.resolve(jsonResponse({ username: options.partnerUsername ?? null }));
        }
        return fetchImpl(input, init);
    };
    const client = createApiClient({
        credential: { scheme: "tma", value: telegram.initData },
        fetch: withContacts,
    });
    const handlers = {
        onLimit: vi.fn(),
        onCrisis: vi.fn(),
    };
    render(
        <ApiProvider credential={{ scheme: "tma", value: telegram.initData }} client={client}>
            <ComposeScreen
                telegram={telegram}
                {...(options.title !== undefined ? { title: options.title } : {})}
                draft={options.draft ?? ""}
                contactId={options.contactId === undefined ? "c1" : options.contactId}
                draftMax={4000}
                {...handlers}
            />
        </ApiProvider>,
    );
    const result = { telegram, fetchImpl: withContacts, ...handlers };
    if ((options.draft ?? "") === "") {
        await screen.findByRole("button", { name: ru.composeSubmit });
    }
    return result;
}

function isComposePost(input: RequestInfo | URL): boolean {
    const url = requestUrl(input);
    return url.includes("/api/v1/compose") && !url.includes("/choice");
}

function isChoicePost(input: RequestInfo | URL): boolean {
    return requestUrl(input).includes("/api/v1/compose/choice");
}

async function submitDraft(text: string): Promise<void> {
    const box = await screen.findByRole("textbox");
    fireEvent.change(box, { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
}

describe("ComposeScreen", () => {
    it("submits draft with goal and contact, shows editable variants and applied rules", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl);
        expect(
            await screen.findByText(ru.contactChipFor.replace("{label}", "Аня")),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("radio", { name: ru.composeGoals.decline }));
        await submitDraft("не могу прийти");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        expect(screen.getByDisplayValue("исправленный вариант")).toBeInTheDocument();
        expect(screen.getByDisplayValue("твёрдый вариант")).toBeInTheDocument();
        expect(
            screen.getByText(ru.composeAppliedRule.replace("{text}", "не перебивать")),
        ).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        expect(call).toBeDefined();
        expect(await requestJson(call?.[0] as RequestInfo, call?.[1])).toEqual({
            draft: "не могу прийти",
            goal: "decline",
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

    it("sends null goal by default and after the chosen goal is cleared", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl);
        const decline = await screen.findByRole("radio", { name: ru.composeGoals.decline });
        expect(decline).not.toBeChecked();
        fireEvent.click(decline);
        expect(decline).toBeChecked();
        fireEvent.click(decline);
        expect(decline).not.toBeChecked();
        await submitDraft("не могу прийти");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        expect(await requestJson(call?.[0] as RequestInfo, call?.[1])).toEqual({
            draft: "не могу прийти",
            goal: null,
            contact_id: "c1",
        });
    });

    it("allows submit without contact and sends null contact_id", async () => {
        const fetchImpl = composeFetch(() =>
            jsonResponse({
                safety: "ok",
                variants: [{ kind: "softer", text: "без правил" }],
                applied_rules: [],
                choice_token: "c_none",
            }),
        );
        await renderCompose(fetchImpl, { contactId: null });
        expect(await screen.findByText(ru.contactChipNone)).toBeInTheDocument();
        await submitDraft("черновик без контакта");
        expect(await screen.findByDisplayValue("без правил")).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        const body = (await requestJson(call?.[0] as RequestInfo, call?.[1])) as {
            contact_id: string | null;
        };
        expect(body.contact_id).toBeNull();
    });

    it("picks none contact from the picker", async () => {
        await renderCompose(mockFetch([]));
        fireEvent.click(await screen.findByText(ru.contactChipFor.replace("{label}", "Аня")));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: ru.contactNoneItem }));
        expect(screen.getByText(ru.contactChipNone)).toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("shows the submitted draft above the variants", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl);
        await submitDraft("  мой исходный текст  ");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const block = screen.getByText(ru.composeYourDraft).closest(".quote");
        expect(block).not.toBeNull();
        expect(within(block as HTMLElement).getByText("мой исходный текст")).toBeInTheDocument();
    });

    it("keeps the draft block on the example screen with the fixed order and unselected goals", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, {
            title: ru.introExampleTitle,
            draft: ru.introExampleDraft,
        });
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        expect(screen.getByRole("heading", { name: ru.introExampleTitle })).toBeInTheDocument();
        expect(screen.getByText(ru.composeYourDraft)).toBeInTheDocument();
        expect(screen.getByText(ru.introExampleDraft)).toBeInTheDocument();
        expect(screen.getByText(ru.composeGoalLegend)).toBeInTheDocument();
        expect(
            screen.getAllByRole("radio").every((radio) => !(radio as HTMLInputElement).checked),
        ).toBe(true);
        expect(
            screen.getAllByRole("textbox").map((box) => (box as HTMLTextAreaElement).value),
        ).toEqual(["исправленный вариант", "мягкий вариант", "твёрдый вариант"]);
    });

    it("shows an initial and truncates a long name in the chip", async () => {
        const long = { ...anya, label: "Очень длинное имя контакта для проверки обрезки" };
        await renderCompose(mockFetch([]), { contacts: [long] });
        const chip = await screen.findByRole("button", {
            name: ru.contactChipFor.replace("{label}", long.label),
        });
        expect(chip.querySelector(".contact-chip__label")).toHaveTextContent(long.label);
        expect(chip.querySelector(".avatar")).toHaveTextContent("О");
    });

    it("shows a dash avatar in the chip without a contact", async () => {
        await renderCompose(mockFetch([]), { contactId: null });
        const chip = await screen.findByRole("button", { name: ru.contactChipNone });
        expect(chip.querySelector(".avatar--empty")).toHaveTextContent("–");
    });

    it("selects another contact from the picker as local state only", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { contacts: [anya, borya] });
        fireEvent.click(await screen.findByText(ru.contactChipFor.replace("{label}", "Аня")));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /Боря/ }));
        expect(
            await screen.findByText(ru.contactChipFor.replace("{label}", "Боря")),
        ).toBeInTheDocument();
        await submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        expect(await requestJson(call?.[0] as RequestInfo, call?.[1])).toEqual({
            draft: "черновик",
            goal: null,
            contact_id: "c2",
        });
        expect(fetchImpl.mock.calls.every(([req]) => !requestUrl(req).includes("/activate"))).toBe(
            true,
        );
    });

    it("copies text only without choice or close", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        const { telegram } = await renderCompose(fetchImpl);
        await submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const cards = screen.getAllByRole("article");
        const softer = cards[1];
        expect(softer).toBeDefined();
        fireEvent.click(
            within(softer as HTMLElement).getByRole("button", { name: ru.composeCopy }),
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
        const { telegram } = await renderCompose(fetchImpl);
        vi.mocked(telegram.copyText).mockResolvedValue(false);
        await submitDraft("черновик");
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

    it("copy-and-return records the firmer choice then closes", async () => {
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ outcome: "recorded" });
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = await renderCompose(fetchImpl);
        fireEvent.click(screen.getByRole("radio", { name: ru.composeGoals.set_boundary }));
        await submitDraft("граница");
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
            token: "c_test_token",
            kind: "firmer",
        });
    });

    it("copy-and-return sends choice token and kind then closes", async () => {
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ outcome: "recorded" });
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = await renderCompose(fetchImpl);
        await submitDraft("смягчить это");
        expect(await screen.findByDisplayValue("исправленный вариант")).toBeInTheDocument();
        const cards = screen.getAllByRole("article");
        const check = cards[0];
        expect(check).toBeDefined();
        fireEvent.click(
            within(check as HTMLElement).getByRole("button", { name: ru.composeCopyAndReturn }),
        );
        await waitFor(() => {
            expect(telegram.close).toHaveBeenCalled();
        });
        const choiceCall = fetchImpl.mock.calls.find(([req]) => isChoicePost(req));
        expect(await requestJson(choiceCall?.[0] as RequestInfo, choiceCall?.[1])).toEqual({
            token: "c_test_token",
            kind: "check",
        });
    });

    it("closes after copy even when choice recording fails", async () => {
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ code: "validation_error", message: "bad" }, 422);
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = await renderCompose(fetchImpl);
        await submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        fireEvent.click(
            within(screen.getAllByRole("article")[0] as HTMLElement).getByRole("button", {
                name: ru.composeCopyAndReturn,
            }),
        );
        await waitFor(() => {
            expect(telegram.hapticNotification).toHaveBeenCalledWith("error");
            expect(telegram.close).toHaveBeenCalled();
        });
    });

    it("does not call choice when copy-and-return copy fails", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        const { telegram } = await renderCompose(fetchImpl);
        vi.mocked(telegram.copyText).mockResolvedValue(false);
        await submitDraft("черновик");
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
        const { onCrisis } = await renderCompose(fetchImpl);
        await submitDraft("кризисный текст");
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
        await renderCompose(fetchImpl);
        await submitDraft("манипуляция");
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
        const first = await renderCompose(fetchImpl);
        await submitDraft("текст для лимита");
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
        const second = await renderCompose(budgetFetch);
        await submitDraft("бюджет");
        await waitFor(() => {
            expect(second.onLimit).toHaveBeenCalledWith("budget", "budget");
        });
    });

    it("maps catalog error codes and empty draft", async () => {
        await renderCompose(mockFetch([]));
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
            await renderCompose(fetchImpl);
            await submitDraft("достаточно длинный черновик");
            expect(await screen.findByText(item.message)).toBeInTheDocument();
        }
    });

    it("pastes from clipboard and shows paste failure", async () => {
        Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { readText: vi.fn(() => Promise.resolve("из буфера")) },
        });
        await renderCompose(mockFetch([]));
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
        await renderCompose(mockFetch([]));
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
        await renderCompose(fetchImpl);
        await submitDraft("ждём");
        expect(await screen.findByText(ru.loading)).toBeInTheDocument();
        resolveCompose?.(jsonResponse(okVariants));
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
    });

    it("shows send button with username and opens edited text link", async () => {
        const pairedAnya: Contact = { ...anya, paired: true, pair_id: "p1" };
        const fetchImpl = composeFetch((input) => {
            if (isChoicePost(input)) {
                return jsonResponse({ outcome: "recorded" });
            }
            return jsonResponse(okVariants);
        });
        const { telegram } = await renderCompose(fetchImpl, {
            contacts: [pairedAnya],
            partnerUsername: "anya_tg",
        });
        expect(
            await screen.findByText(ru.contactChipFor.replace("{label}", "Аня")),
        ).toBeInTheDocument();
        await submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const sendLabel = ru.composeSend.replace("{label}", "Аня");
        const card = screen.getAllByRole("article")[0] as HTMLElement;
        const sendButton = await within(card).findByRole("button", { name: sendLabel });
        fireEvent.change(within(card).getByRole("textbox"), {
            target: { value: "отредактировано 👋\nвторая" },
        });
        fireEvent.click(sendButton);
        await waitFor(() => {
            expect(telegram.openTelegramLink).toHaveBeenCalledWith(
                `https://t.me/anya_tg?text=${encodeURIComponent("отредактировано 👋\nвторая")}`,
            );
            expect(telegram.close).toHaveBeenCalled();
        });
        const choiceCall = fetchImpl.mock.calls.find(([req]) => isChoicePost(req));
        expect(choiceCall).toBeDefined();
    });

    it("hides send button when partner has no username", async () => {
        const pairedAnya: Contact = { ...anya, paired: true, pair_id: "p1" };
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { contacts: [pairedAnya], partnerUsername: null });
        await submitDraft("черновик");
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: ru.composeSend.replace("{label}", "Аня") }),
        ).not.toBeInTheDocument();
        expect(
            screen.getAllByRole("button", { name: ru.composeCopyAndReturn }).length,
        ).toBeGreaterThan(0);
    });
    it("opens the variants straight away when a draft is provided", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { draft: "Ты опять опоздал", contactId: "c1" });
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const calls = fetchImpl.mock.calls.filter(([req]) => isComposePost(req));
        expect(calls).toHaveLength(1);
        expect(await requestJson(calls[0]?.[0] as RequestInfo, calls[0]?.[1])).toEqual({
            draft: "Ты опять опоздал",
            goal: null,
            contact_id: "c1",
        });
    });

    it("sends no contact when opened without one", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { draft: "Привет", contactId: null });
        expect(await screen.findByDisplayValue("мягкий вариант")).toBeInTheDocument();
        const call = fetchImpl.mock.calls.find(([req]) => isComposePost(req));
        expect(await requestJson(call?.[0] as RequestInfo, call?.[1])).toEqual({
            draft: "Привет",
            goal: null,
            contact_id: null,
        });
    });

    it("orders variants check, softer, firmer under the rewrite title with goal chips", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { draft: "текст" });
        await screen.findByDisplayValue("мягкий вариант");
        expect(
            screen.getAllByRole("article").map((card) => within(card).getByRole("textbox")),
        ).toHaveLength(3);
        expect(
            screen.getAllByRole("textbox").map((box) => (box as HTMLTextAreaElement).value),
        ).toEqual(["исправленный вариант", "мягкий вариант", "твёрдый вариант"]);
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(ru.composeTitle);
        expect(screen.getByRole("radio", { name: ru.composeGoals.decline })).toBeInTheDocument();
    });

    it("uses the given title", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { draft: "текст", title: "Пример" });
        await screen.findByDisplayValue("мягкий вариант");
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Пример");
    });

    it("regenerates with the chosen goal only after the goal changes", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { draft: "текст" });
        await screen.findByDisplayValue("мягкий вариант");
        expect(
            screen.getAllByRole("radio").every((radio) => !(radio as HTMLInputElement).checked),
        ).toBe(true);
        expect(screen.queryByRole("button", { name: ru.composeRegenerate })).toBeNull();

        fireEvent.click(screen.getByRole("radio", { name: ru.composeGoals.reconnect }));
        fireEvent.click(screen.getByRole("button", { name: ru.composeRegenerate }));
        await waitFor(() => {
            expect(fetchImpl.mock.calls.filter(([req]) => isComposePost(req))).toHaveLength(2);
        });
        const second = fetchImpl.mock.calls.filter(([req]) => isComposePost(req))[1];
        expect(await requestJson(second?.[0] as RequestInfo, second?.[1])).toEqual({
            draft: "текст",
            goal: "reconnect",
            contact_id: "c1",
        });
        await waitFor(() => {
            expect(screen.queryByRole("button", { name: ru.composeRegenerate })).toBeNull();
        });
    });

    it("shows the error and the form when the provided draft is too long", async () => {
        const fetchImpl = composeFetch(() => jsonResponse(okVariants));
        await renderCompose(fetchImpl, { draft: "я".repeat(4001) });
        expect(await screen.findByText(ru.composeErrorLong)).toBeInTheDocument();
        expect(fetchImpl.mock.calls.some(([req]) => isComposePost(req))).toBe(false);
    });
});
