import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { decodeRemainingLabel } from "./localization/plural";
import { ru } from "./localization/ru";
import { fakeAdapter, jsonResponse, mockFetch, privacyTexts } from "./test/fakeTelegram";
import type { TelegramAdapter } from "./telegram/webapp";

const meDone = {
    onboarding_step: "done",
    consent_kind: null,
    consent_version: null,
    account_exists: true,
    consents_revoked: false,
    bot_username: "svoi_test_bot",
    decode_remaining: 5,
    max_contacts: 20,
    max_open_rules: 50,
    rule_text_max_chars: 500,
    incoming_text_max_chars: 4000,
    display_timezone: "Europe/Moscow",
};

const contact = {
    id: "c1",
    label: "Аня",
    relationship: "partner",
    pair_id: null,
    paired: false,
    created_at: "2026-10-01T12:00:00.000Z",
};

const rule = {
    id: "r1",
    category: "other",
    status: "active",
    text: "не повышать голос",
    shared: false,
    needs_my_approval: false,
    created_at: "2026-10-03T12:00:00.000Z",
    effective_since: "2026-10-03T12:00:00.000Z",
    has_pending_edit: true,
};

const proposedRule = {
    ...rule,
    id: "r2",
    status: "proposed",
    effective_since: null,
    has_pending_edit: false,
    text: "не перебивать",
};

const suggestion = {
    id: "s1",
    category: "apology",
    text: "извиняться спокойно",
    source: "decode",
    variant_kind: "softer",
    created_at: "2026-10-04T12:00:00.000Z",
};

function click(name: RegExp | string) {
    fireEvent.click(screen.getByRole("button", { name }));
}

async function openPeople() {
    fireEvent.click(await screen.findByRole("button", { name: ru.tabPeople }));
    await screen.findByRole("heading", { name: ru.contactsTitle });
}

async function chooseHomeContact(label: RegExp) {
    fireEvent.click(await screen.findByRole("button", { name: ru.contactChipNone }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: label }));
}

async function submitFromHome(mode: "write" | "decode", text: string, contactLabel?: RegExp) {
    const title = {
        write: ru.homeModeWrite,
        decode: ru.homeModeDecode,
    }[mode];
    fireEvent.click(await screen.findByRole("radio", { name: new RegExp(title) }));
    if (contactLabel !== undefined) {
        await chooseHomeContact(contactLabel);
    }
    fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
    fireEvent.click(
        screen.getByRole("button", {
            name: mode === "decode" ? ru.homeSubmitDecode : ru.homeSubmitVariants,
        }),
    );
}

type RecordedRequest = { readonly path: string; readonly body: unknown };

function recordRequests(base: typeof fetch, log: RecordedRequest[]): typeof fetch {
    return async (input, init) => {
        const isRequest = input instanceof Request;
        const url = new URL(isRequest ? input.url : String(input), "http://localhost");
        const text = isRequest
            ? await input.clone().text()
            : typeof init?.body === "string"
              ? init.body
              : "";
        log.push({
            path: url.pathname,
            body: text.length === 0 ? null : (JSON.parse(text) as unknown),
        });
        return base(input, init);
    };
}

const personalDoc = { kind: "personal_data", version: "1", text: "DOC_PERSONAL" };
const specialDoc = { kind: "special_category", version: "1", text: "DOC_SPECIAL" };

describe("App", () => {
    it("renders the open-from-Telegram message outside Telegram", () => {
        render(<App adapter={fakeAdapter({ launch: { kind: "outside" }, initData: "" })} />);
        expect(screen.getByRole("heading", { name: ru.openFromTelegram })).toBeInTheDocument();
    });

    it("shows loading then contacts empty state", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(screen.getByText(ru.loading)).toBeInTheDocument();
        await openPeople();
        expect(await screen.findByText(ru.contactsEmpty)).toBeInTheDocument();
    });

    it("shows contacts error state with retry", async () => {
        let contactsCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL) => {
            const request = input instanceof Request ? input : new Request(String(input));
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(
                    new Response(JSON.stringify(meDone), {
                        status: 200,
                        headers: { "Content-Type": "application/json" },
                    }),
                );
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            if (path === "/api/v1/rules/pending") {
                return Promise.resolve(jsonResponse({ items: [] }));
            }
            contactsCalls += 1;
            if (contactsCalls <= 2) {
                return Promise.resolve(
                    new Response(JSON.stringify({ code: "not_found", message: "boom" }), {
                        status: 500,
                        headers: { "Content-Type": "application/json" },
                    }),
                );
            }
            return Promise.resolve(
                new Response(JSON.stringify({ contacts: [] }), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            );
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        click(ru.retry);
        expect(await screen.findByText(ru.contactsEmpty)).toBeInTheDocument();
    });

    it("shows the age onboarding screen and advances after confirmation", async () => {
        let meCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                meCalls += 1;
                if (meCalls === 1) {
                    return Promise.resolve(
                        jsonResponse({ ...meDone, onboarding_step: "age", account_exists: false }),
                    );
                }
                return Promise.resolve(
                    jsonResponse({
                        ...meDone,
                        onboarding_step: "consent",
                        consent_kind: "personal_data",
                        consent_version: "1",
                    }),
                );
            }
            if (path === "/api/v1/me/age-confirmation" && request.method === "POST") {
                return Promise.resolve(jsonResponse(null, 204));
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(jsonResponse(personalDoc));
            }
            if (path === "/api/v1/consents/special_category/document") {
                return Promise.resolve(jsonResponse(specialDoc));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByText(ru.onboardingTagline)).toBeInTheDocument();
        fireEvent.click(screen.getByLabelText(ru.onboardingAgeCheckbox));
        click(ru.continue);
        expect(
            await screen.findByRole("heading", { name: ru.onboardingConsentTitle }),
        ).toBeInTheDocument();
    });

    it("keeps the age continue button disabled until 18+ is confirmed", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: { ...meDone, onboarding_step: "age", account_exists: false },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        const next = await screen.findByRole("button", { name: ru.continue });
        expect(next).toBeDisabled();
        fireEvent.click(screen.getByLabelText(ru.onboardingAgeCheckbox));
        expect(next).toBeEnabled();
    });

    it("shows age confirmation API errors", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: { ...meDone, onboarding_step: "age", account_exists: false },
            },
            {
                method: "POST",
                path: "/api/v1/me/age-confirmation",
                status: 500,
                body: { code: "not_found", message: "age failed" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByLabelText(ru.onboardingAgeCheckbox));
        click(ru.continue);
        expect(await screen.findByText("age failed")).toBeInTheDocument();
    });

    it("hides export/delete on first-run consent and shows them after revoke", async () => {
        let meCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                meCalls += 1;
                if (meCalls === 1) {
                    return Promise.resolve(
                        jsonResponse({
                            ...meDone,
                            onboarding_step: "consent",
                            consent_kind: "personal_data",
                            consent_version: "1",
                            consents_revoked: false,
                        }),
                    );
                }
                return Promise.resolve(jsonResponse(meDone));
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(jsonResponse(personalDoc));
            }
            if (path === "/api/v1/consents/special_category/document") {
                return Promise.resolve(jsonResponse(specialDoc));
            }
            if (path === "/api/v1/me/consents" && request.method === "POST") {
                return Promise.resolve(jsonResponse(null, 204));
            }
            if (path === "/api/v1/contacts") {
                return Promise.resolve(jsonResponse({ contacts: [contact] }));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(
            await screen.findByRole("heading", { name: ru.onboardingConsentTitle }),
        ).toBeInTheDocument();
        expect(
            screen.getByText(ru.onboardingViaBot.replace("{bot}", "svoi_test_bot")),
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: ru.privacyExportAction }),
        ).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: ru.privacyDeleteAction }),
        ).not.toBeInTheDocument();

        const accept = screen.getByRole("button", { name: ru.onboardingAccept });
        expect(accept).toBeDisabled();
        fireEvent.click(
            screen.getAllByRole("button", {
                name: new RegExp(ru.consentDocLink),
            })[0] as HTMLElement,
        );
        expect(within(screen.getByRole("dialog")).getByText("DOC_PERSONAL")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.consentDocsClose }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

        fireEvent.click(screen.getByLabelText(ru.consentKinds.personal_data));
        expect(accept).toBeDisabled();
        fireEvent.click(screen.getByLabelText(ru.consentKinds.special_category));
        expect(accept).toBeEnabled();
        fireEvent.click(accept);
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        const grants = vi
            .mocked(fetchImpl)
            .mock.calls.filter(
                (call) =>
                    call[0] instanceof Request &&
                    new URL(call[0].url).pathname === "/api/v1/me/consents",
            );
        expect(grants).toHaveLength(2);
    });

    it("shows export/delete on consent after prior revoke", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: {
                    ...meDone,
                    onboarding_step: "consent",
                    consent_kind: "personal_data",
                    consent_version: "1",
                    consents_revoked: true,
                },
            },
            { path: "/api/v1/consents/personal_data/document", body: personalDoc },
            { path: "/api/v1/consents/special_category/document", body: specialDoc },
            { path: "/api/v1/privacy/texts", body: privacyTexts },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(
            await screen.findByRole("heading", { name: ru.onboardingConsentTitle }),
        ).toBeInTheDocument();
        expect(
            await screen.findByRole("button", { name: ru.privacyExportAction }),
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.privacyDeleteAction })).toBeInTheDocument();
    });

    it("shows the unauthorized gate on 401 without rights actions", async () => {
        const adapter = fakeAdapter();
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                status: 401,
                body: { code: "unauthorized", message: "no" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        expect(await screen.findByText(ru.gateUnauthorized)).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: ru.privacyExportAction }),
        ).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: ru.privacyDeleteAction }),
        ).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.close }));
        expect(adapter.close).toHaveBeenCalled();
    });

    it("shows invite screen from startParam and accepts", async () => {
        const adapter = fakeAdapter({ startParam: "inv_token123" });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                body: { expires_at: "2026-10-14T12:00:00.000Z" },
            },
            { method: "POST", path: "/api/v1/invites/accept", status: 204, body: null },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        expect(await screen.findByText(ru.inviteTitle)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.inviteAccept }));
        expect(await screen.findByText(ru.addRuleValidation)).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText(ru.inviteLabel), {
            target: { value: "Партнёр" },
        });
        fireEvent.click(screen.getByRole("button", { name: ru.inviteAccept }));
        expect(await screen.findByText("Аня")).toBeInTheDocument();
    });

    it("cancels invite screen without accepting", async () => {
        const adapter = fakeAdapter({ startParam: "inv_token123" });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                body: { expires_at: "2026-10-14T12:00:00.000Z" },
            },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        expect(await screen.findByText(ru.inviteTitle)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.inviteLater }));
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
    });

    it("shows accept errors on invite screen", async () => {
        const adapter = fakeAdapter({ startParam: "inv_token123" });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                body: { expires_at: "2026-10-14T12:00:00.000Z" },
            },
            {
                method: "POST",
                path: "/api/v1/invites/accept",
                status: 409,
                body: { code: "contact_limit", message: "limit" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.change(await screen.findByLabelText(ru.inviteLabel), {
            target: { value: "Партнёр" },
        });
        fireEvent.click(screen.getByRole("button", { name: ru.inviteAccept }));
        expect(await screen.findByText(ru.contactsLimit)).toBeInTheDocument();
    });

    it("dismisses invite screen on resolve error", async () => {
        const adapter = fakeAdapter({ startParam: "inv_bad" });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                status: 404,
                body: { code: "not_found", message: "missing" },
            },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        expect(await screen.findByText(ru.inviteInvalid)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.continue }));
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
    });

    it("maps invite expired and own errors", async () => {
        const expired = fakeAdapter({ startParam: "inv_expired" });
        const fetchExpired = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                status: 409,
                body: { code: "invite_expired", message: "gone" },
            },
        ]);
        const { unmount } = render(<App adapter={expired} fetchImpl={fetchExpired} />);
        expect(await screen.findByText(ru.inviteExpired)).toBeInTheDocument();
        unmount();

        const own = fakeAdapter({ startParam: "inv_own" });
        const fetchOwn = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                status: 409,
                body: { code: "invite_own", message: "self" },
            },
        ]);
        render(<App adapter={own} fetchImpl={fetchOwn} />);
        expect(await screen.findByText(ru.inviteOwn)).toBeInTheDocument();
    });

    it("asks only for the outstanding consent", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: {
                    ...meDone,
                    onboarding_step: "consent",
                    consent_kind: "special_category",
                    consent_version: "1",
                },
            },
            { path: "/api/v1/consents/special_category/document", body: specialDoc },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByLabelText(ru.consentKinds.special_category)).toBeInTheDocument();
        expect(screen.queryByLabelText(ru.consentKinds.personal_data)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.onboardingAccept })).toBeDisabled();
    });

    it("handles stale consent grant and delete failure on consent screen", async () => {
        const showConfirm = vi.fn(() => Promise.resolve(true));
        const adapter = fakeAdapter({ showConfirm });
        let grantCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(
                    jsonResponse({
                        ...meDone,
                        onboarding_step: "consent",
                        consent_kind: "personal_data",
                        consent_version: "1",
                        consents_revoked: true,
                    }),
                );
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(jsonResponse(personalDoc));
            }
            if (path === "/api/v1/consents/special_category/document") {
                return Promise.resolve(jsonResponse(specialDoc));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            if (path === "/api/v1/me/consents" && request.method === "POST") {
                grantCalls += 1;
                return Promise.resolve(
                    jsonResponse({ code: "consent_stale", message: "stale now" }, 409),
                );
            }
            if (path === "/api/v1/me/delete" && request.method === "POST") {
                return Promise.resolve(
                    jsonResponse({ code: "not_found", message: "delete failed" }, 500),
                );
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByLabelText(ru.consentKinds.personal_data));
        fireEvent.click(screen.getByLabelText(ru.consentKinds.special_category));
        fireEvent.click(screen.getByRole("button", { name: ru.onboardingAccept }));
        expect(await screen.findByText("stale now")).toBeInTheDocument();
        expect(grantCalls).toBe(1);
        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteAction }));
        expect(await screen.findByText("delete failed")).toBeInTheDocument();
    });

    it("maps invite_invalid on accept", async () => {
        const adapter = fakeAdapter({ startParam: "inv_token123" });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                body: { expires_at: "2026-10-14T12:00:00.000Z" },
            },
            {
                method: "POST",
                path: "/api/v1/invites/accept",
                status: 409,
                body: { code: "invite_invalid", message: "used" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.change(await screen.findByLabelText(ru.inviteLabel), {
            target: { value: "Партнёр" },
        });
        fireEvent.click(screen.getByLabelText(ru.relationships.friend));
        fireEvent.click(screen.getByRole("button", { name: ru.inviteAccept }));
        expect(await screen.findByText(ru.inviteInvalid)).toBeInTheDocument();
    });

    it("maps invite_expired on accept", async () => {
        const adapter = fakeAdapter({ startParam: "inv_token123" });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            {
                method: "POST",
                path: "/api/v1/invites/resolve",
                body: { expires_at: "2026-10-14T12:00:00.000Z" },
            },
            {
                method: "POST",
                path: "/api/v1/invites/accept",
                status: 409,
                body: { code: "invite_expired", message: "late" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.change(await screen.findByLabelText(ru.inviteLabel), {
            target: { value: "Партнёр" },
        });
        fireEvent.click(screen.getByRole("button", { name: ru.inviteAccept }));
        expect(await screen.findByText(ru.inviteExpired)).toBeInTheDocument();
    });

    it("shows consent document load error with retry", async () => {
        let docCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(
                    jsonResponse({
                        ...meDone,
                        onboarding_step: "consent",
                        consent_kind: "personal_data",
                        consent_version: "1",
                    }),
                );
            }
            if (path === "/api/v1/consents/personal_data/document") {
                docCalls += 1;
                if (docCalls === 1) {
                    return Promise.resolve(
                        jsonResponse({ code: "not_found", message: "doc missing" }, 404),
                    );
                }
                return Promise.resolve(jsonResponse(personalDoc));
            }
            if (path === "/api/v1/consents/special_category/document") {
                return Promise.resolve(jsonResponse(specialDoc));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.retry }));
        expect(
            await screen.findByRole("heading", { name: ru.onboardingConsentTitle }),
        ).toBeInTheDocument();
    });

    it("shows export failed when downloadFile is rejected", async () => {
        const adapter = fakeAdapter({
            downloadFile: vi.fn(() => Promise.resolve(false)),
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            {
                method: "POST",
                path: "/api/v1/me/export",
                status: 200,
                body: {
                    download_url: "https://miniapp.test/api/v1/downloads/t",
                    expires_at: "2026-10-07T12:00:00.000Z",
                },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyTitle }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.privacyExportFailed)).toBeInTheDocument();
    });

    it("shows consent screen when /me returns consent_required", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                status: 403,
                body: { code: "consent_required", message: "need consent" },
            },
            { path: "/api/v1/consents/personal_data/document", body: personalDoc },
            { path: "/api/v1/consents/special_category/document", body: specialDoc },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(
            await screen.findByRole("heading", { name: ru.onboardingConsentTitle }),
        ).toBeInTheDocument();
        expect(
            await screen.findByRole("button", { name: ru.privacyExportAction }),
        ).toBeInTheDocument();
    });

    it("exports from consent screen via downloadFile and deletes account", async () => {
        const showConfirm = vi.fn(() => Promise.resolve(true));
        const adapter = fakeAdapter({ showConfirm });
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: {
                    ...meDone,
                    onboarding_step: "consent",
                    consent_kind: "personal_data",
                    consent_version: "1",
                    consents_revoked: true,
                },
            },
            { path: "/api/v1/consents/personal_data/document", body: personalDoc },
            { path: "/api/v1/consents/special_category/document", body: specialDoc },
            {
                method: "POST",
                path: "/api/v1/me/export",
                status: 200,
                body: {
                    download_url: "https://miniapp.test/api/v1/downloads/tok",
                    expires_at: "2026-10-07T12:00:00.000Z",
                },
            },
            { method: "POST", path: "/api/v1/me/delete", status: 204, body: null },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.privacyExportDone)).toBeInTheDocument();
        expect(adapter.downloadFile).toHaveBeenCalledWith(
            "https://miniapp.test/api/v1/downloads/tok",
            "svoi-pravila-export.json",
        );
        expect(adapter.hapticNotification).toHaveBeenCalledWith("success");

        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteAction }));
        expect(showConfirm).toHaveBeenCalled();
        expect(await screen.findByText(ru.privacyDeleted)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.close }));
        expect(adapter.close).toHaveBeenCalled();
    });

    it("handles consent-screen export errors", async () => {
        const adapter = fakeAdapter();
        let exportCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(
                    jsonResponse({
                        ...meDone,
                        onboarding_step: "consent",
                        consent_kind: "personal_data",
                        consent_version: "1",
                        consents_revoked: true,
                    }),
                );
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(jsonResponse(personalDoc));
            }
            if (path === "/api/v1/consents/special_category/document") {
                return Promise.resolve(jsonResponse(specialDoc));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            if (path === "/api/v1/me/export" && request.method === "POST") {
                exportCalls += 1;
                if (exportCalls === 1) {
                    return Promise.resolve(
                        jsonResponse({ code: "service_unavailable", message: "down" }, 503),
                    );
                }
                if (exportCalls === 2) {
                    return Promise.resolve(
                        jsonResponse({ code: "rate_limited", message: "slow" }, 429),
                    );
                }
                return Promise.resolve(
                    jsonResponse({ code: "not_found", message: "export failed" }, 500),
                );
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.privacyExportUnavailable)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.rateLimited)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText("export failed")).toBeInTheDocument();
    });

    it("shows a generic me error with retry", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                status: 500,
                body: { code: "not_found", message: "server" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByText("server")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.retry })).toBeInTheDocument();
    });

    it("loads contacts and opens detail with rules and suggestions", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [rule, proposedRule] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [suggestion] } },
        ]);
        const adapter = fakeAdapter();
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByText("Аня")).toBeInTheDocument();
        click(/Аня/);
        expect(await screen.findByRole("heading", { name: "Аня" })).toBeInTheDocument();
        expect(screen.getByText(ru.relationships.partner)).toBeInTheDocument();
        expect(await screen.findByText("не повышать голос")).toBeInTheDocument();
        expect(screen.getByText(ru.rulePendingEdit)).toBeInTheDocument();
        expect(screen.getByText(new RegExp(ru.ruleProposed))).toBeInTheDocument();
        expect(screen.getByText("извиняться спокойно")).toBeInTheDocument();
        expect(adapter.BackButton.show).toHaveBeenCalled();
        expect(screen.queryByRole("button", { name: /активн/i })).not.toBeInTheDocument();
    });

    it("supports back navigation via BackButton", async () => {
        let backCallback: (() => void) | undefined;
        const adapter = fakeAdapter({
            BackButton: {
                show: vi.fn(),
                hide: vi.fn(),
                onClick: (callback: () => void) => {
                    backCallback = callback;
                    return () => {
                        backCallback = undefined;
                    };
                },
            },
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByText("Аня")).toBeInTheDocument();
        click(/Аня/);
        expect(await screen.findByText(ru.rulesEmpty)).toBeInTheDocument();
        expect(backCallback).toBeTypeOf("function");
        act(() => {
            backCallback?.();
        });
        expect(await screen.findByRole("heading", { name: ru.contactsTitle })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Аня/ })).toBeInTheDocument();
    });

    it("archives only after confirm accepts", async () => {
        const showConfirm = vi.fn(() => Promise.resolve(false));
        const adapter = fakeAdapter({ showConfirm });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [rule] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/rules/r1/archive",
                body: { ...rule, status: "archived" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleArchive }));
        expect(showConfirm).toHaveBeenCalled();
        expect(
            vi.mocked(fetchImpl).mock.calls.some((call) => {
                const request = call[0];
                const url = request instanceof Request ? request.url : String(request);
                return url.includes("/archive");
            }),
        ).toBe(false);

        showConfirm.mockResolvedValueOnce(true);
        fireEvent.click(screen.getByRole("button", { name: ru.ruleArchive }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : String(request);
                    return url.includes("/archive");
                }),
            ).toBe(true);
        });
    });

    it("creates a contact and maps the contact limit", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts",
                status: 409,
                body: { code: "contact_limit", message: "limit" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByText(ru.contactsEmpty)).toBeInTheDocument();
        click(ru.contactsAdd);
        const dialog = screen.getByRole("dialog");
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Новый" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        expect(await screen.findByText(ru.contactsLimit)).toBeInTheDocument();
    });

    it("renames a contact and never calls activate", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "PATCH",
                path: "/api/v1/contacts/c1",
                body: { ...contact, label: "Анна" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByText("Аня"));
        expect(await screen.findByRole("button", { name: ru.contactEdit })).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.contactEdit }));
        const dialog = await screen.findByRole("dialog");
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Анна" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    return request instanceof Request && request.method === "PATCH";
                }),
            ).toBe(true);
        });
        expect(await screen.findByRole("heading", { name: "Анна" })).toBeInTheDocument();
        expect(
            vi.mocked(fetchImpl).mock.calls.some((call) => {
                const request = call[0];
                const url = request instanceof Request ? request.url : "";
                return url.includes("/activate");
            }),
        ).toBe(false);
    });

    it("handles rename and create errors and cancels the modal", async () => {
        const adapter = fakeAdapter();
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "PATCH",
                path: "/api/v1/contacts/c1",
                status: 500,
                body: { code: "not_found", message: "rename failed" },
            },
            {
                method: "POST",
                path: "/api/v1/contacts",
                status: 500,
                body: { code: "not_found", message: "create failed" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByText("Аня")).toBeInTheDocument();
        click(ru.contactsAdd);
        const addDialog = screen.getByRole("dialog");
        fireEvent.click(within(addDialog).getByLabelText(ru.relationships.family));
        fireEvent.change(within(addDialog).getByRole("textbox"), { target: { value: "Боря" } });
        fireEvent.click(within(addDialog).getByRole("button", { name: ru.save }));
        await waitFor(() => {
            expect(adapter.hapticNotification).toHaveBeenCalledWith("error");
        });
        fireEvent.click(within(addDialog).getByRole("button", { name: ru.cancel }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        fireEvent.click(screen.getByText("Аня"));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactEdit }));
        const dialog = await screen.findByRole("dialog");
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        expect(await screen.findByText(ru.addRuleValidation)).toBeInTheDocument();
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Анна" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        await waitFor(() => {
            expect(vi.mocked(adapter.hapticNotification).mock.calls.length).toBeGreaterThan(1);
        });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.cancel }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("maps generic add-rule errors", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/rules",
                status: 500,
                body: { code: "not_found", message: "rule boom" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "правило" } });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(await screen.findByText("rule boom")).toBeInTheDocument();
    });

    it("accepts and dismisses suggestions", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [suggestion] } },
            {
                method: "POST",
                path: "/api/v1/suggestions/s1/accept",
                body: { outcome: "accepted", rule_id: "r9" },
            },
            {
                method: "POST",
                path: "/api/v1/suggestions/s1/dismiss",
                body: { outcome: "dismissed" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        expect(await screen.findByText("извиняться спокойно")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionAccept }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/accept");
                }),
            ).toBe(true);
        });
        fireEvent.click(await screen.findByRole("button", { name: ru.suggestionDismiss }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/dismiss");
                }),
            ).toBe(true);
        });
    });

    it("keeps add-rule input on validation error", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/rules",
                status: 422,
                body: { code: "validation_error", message: "bad" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        const textarea = screen.getByRole("textbox");
        fireEvent.change(textarea, { target: { value: "оставить как есть" } });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(await screen.findByText(ru.addRuleValidation)).toBeInTheDocument();
        expect(textarea).toHaveValue("оставить как есть");
    });

    it("maps open rule limit on add rule", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/rules",
                status: 409,
                body: { code: "open_rule_limit", message: "limit" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "правило" } });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(await screen.findByText(ru.addRuleOpenLimit)).toBeInTheDocument();
    });

    it("creates a rule successfully and pops back", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/rules",
                status: 201,
                body: { ...rule, id: "r-new", text: "новое правило" },
            },
        ]);
        const adapter = fakeAdapter();
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.click(screen.getByLabelText(ru.categories.taboo_topic));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "новое правило" } });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        await waitFor(() => {
            expect(adapter.hapticNotification).toHaveBeenCalledWith("success");
        });
        expect(await screen.findByText(ru.rulesEmpty)).toBeInTheDocument();
    });

    it("rejects empty add-rule text locally", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(await screen.findByText(ru.addRuleEmpty)).toBeInTheDocument();
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "ok" } });
        expect(screen.queryByText(ru.addRuleEmpty)).not.toBeInTheDocument();
    });

    it("rejects invalid chars and too-long add-rule text locally", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "a\tb" } });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(await screen.findByText(ru.addRuleInvalidChars)).toBeInTheDocument();
        fireEvent.change(screen.getByRole("textbox"), {
            target: { value: "x".repeat(501) },
        });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(
            await screen.findByText(
                ru.addRuleTooLong
                    .replace("{n}", "501")
                    .replace("{max}", "500")
                    .replace("{over}", "1"),
            ),
        ).toBeInTheDocument();
    });

    it("maps server rule-text error codes on create", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/rules",
                status: 422,
                body: {
                    code: "rule_text_too_long",
                    message: "too long",
                    max: 500,
                    actual: 501,
                },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "сервер отверг" } });
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(
            await screen.findByText(
                ru.addRuleTooLong
                    .replace("{n}", "501")
                    .replace("{max}", "500")
                    .replace("{over}", "1"),
            ),
        ).toBeInTheDocument();
    });

    it("shows the pending block at the top of People with the tab badge", async () => {
        const pendingItem = {
            id: "r-pending",
            contact_id: "c1",
            contact_label: "Аня",
            category: "other",
            status: "proposed",
            text: "общее\nдля двоих",
            shared: true,
            has_pending_edit: false,
        };
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/rules/pending", body: { items: [pendingItem] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [rule] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/rules/r-pending/approve",
                body: { ...rule, id: "r-pending", status: "active", shared: true },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(screen.queryByText(ru.pendingTitle)).not.toBeInTheDocument();
        await openPeople();
        expect(await screen.findByText(ru.pendingTitle)).toBeInTheDocument();
        expect(screen.getByText(/общее/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.tabPeople })).toHaveAttribute(
            "title",
            `${ru.tabPeople}: 1`,
        );
        const block = screen.getByTestId("people-pending");
        const list = await screen.findByRole("list");
        expect(block.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleApprove }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/approve");
                }),
            ).toBe(true);
        });
    });

    it("rejects a pending rule from People", async () => {
        const pendingItem = {
            id: "r-pending",
            contact_id: "c1",
            contact_label: "Аня",
            category: "other",
            status: "proposed",
            text: "отклонить это",
            shared: true,
            has_pending_edit: false,
        };
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/rules/pending", body: { items: [pendingItem] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/rules/r-pending/reject",
                body: { ...rule, id: "r-pending", status: "rejected", shared: true },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByTestId("people-pending")).toBeInTheDocument();
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleReject }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/reject");
                }),
            ).toBe(true);
        });
    });

    it("keeps pending when approve or reject fails", async () => {
        const pendingItem = {
            id: "r-pending",
            contact_id: "c1",
            contact_label: "Аня",
            category: "other",
            status: "proposed",
            text: "ошибка решения",
            shared: true,
            has_pending_edit: false,
        };
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/rules/pending", body: { items: [pendingItem] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/rules/r-pending/approve",
                status: 404,
                body: { code: "not_found", message: "gone" },
            },
            {
                method: "POST",
                path: "/api/v1/rules/r-pending/reject",
                status: 404,
                body: { code: "not_found", message: "gone" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByTestId("people-pending")).toBeInTheDocument();
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleApprove }));
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleReject }));
        expect(await screen.findByTestId("people-pending")).toBeInTheDocument();
    });

    it("lands on the pending block of People from ?view=pending with Telegram hash on boot", async () => {
        const telegramHash = "#tgWebAppData=stub&tgWebAppVersion=8.0";
        window.history.replaceState(null, "", `/?view=pending${telegramHash}`);
        const pendingItem = {
            id: "r-pending",
            contact_id: "c1",
            contact_label: "Аня",
            category: "other",
            status: "proposed",
            text: "ждёт ответа",
            shared: true,
            has_pending_edit: false,
        };
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/rules/pending", body: { items: [pendingItem] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        const scrollIntoView = vi.fn();
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
            configurable: true,
            value: scrollIntoView,
        });
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByRole("heading", { name: ru.contactsTitle })).toBeInTheDocument();
        expect(await screen.findByTestId("people-pending")).toBeInTheDocument();
        await waitFor(() => {
            expect(scrollIntoView).toHaveBeenCalled();
        });
        expect(window.location.hash).toBe(telegramHash);
        window.history.replaceState(null, "", "/");
    });

    it("does not open compose from ?view=compose&h= under identified launch", async () => {
        const telegramHash = "#tgWebAppData=stub&tgWebAppVersion=8.0";
        window.history.replaceState(null, "", `/?view=compose&h=h_token${telegramHash}`);
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(
            <App adapter={fakeAdapter({ launch: { kind: "identified" } })} fetchImpl={fetchImpl} />,
        );
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(screen.queryByRole("heading", { name: ru.composeTitle })).not.toBeInTheDocument();
        expect(window.location.hash).toBe(telegramHash);
        window.history.replaceState(null, "", "/");
    });

    it("shows outside screen for empty initData without inline handoff even on ios platform", () => {
        window.history.replaceState(null, "", "/");
        render(<App adapter={fakeAdapter({ launch: { kind: "outside" }, initData: "" })} />);
        expect(screen.getByRole("heading", { name: ru.openFromTelegram })).toBeInTheDocument();
    });

    it("opens inline compose launch with handoff redeem", async () => {
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            if (request.url.includes("/compose/handoff/redeem")) {
                return Promise.resolve(
                    jsonResponse({
                        draft: "из inline",
                        limits: { incoming_text_max_chars: 4000 },
                        session: {
                            token: "cs_" + "a".repeat(64),
                            expires_at: "2026-06-01T12:15:00Z",
                        },
                    }),
                );
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        });
        render(
            <App
                adapter={fakeAdapter({
                    launch: { kind: "inline_compose", handoffToken: "h_token" },
                    initData: "",
                })}
                fetchImpl={fetchImpl}
            />,
        );
        expect(await screen.findByDisplayValue("из inline")).toBeInTheDocument();
        expect(fetchImpl).toHaveBeenCalled();
        const call = vi.mocked(fetchImpl).mock.calls[0];
        expect(call).toBeDefined();
        if (call === undefined) {
            return;
        }
        const [input, init] = call as [RequestInfo | URL, RequestInit?];
        const auth =
            input instanceof Request
                ? input.headers.get("Authorization")
                : new Headers(init?.headers).get("Authorization");
        expect(auth).toBe("handoff h_token");
    });

    it("opens compose from the home entry", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await submitFromHome("write", "черновик");
        expect(await screen.findByRole("heading", { name: ru.composeTitle })).toBeInTheDocument();
    });

    it("creates a contact successfully", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts",
                status: 201,
                body: contact,
            },
        ]);
        const adapter = fakeAdapter();
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        expect(await screen.findByText(ru.contactsEmpty)).toBeInTheDocument();
        click(ru.contactsAdd);
        const dialog = screen.getByRole("dialog");
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Аня" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        await waitFor(() => {
            expect(adapter.hapticNotification).toHaveBeenCalledWith("success");
        });
    });

    it("shows detail error and empty rules explanation", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            {
                path: "/api/v1/contacts/c1/rules",
                status: 500,
                body: { code: "not_found", message: "fail" },
            },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.retry }));
    });

    it("handles archive and suggestion mutation errors", async () => {
        const adapter = fakeAdapter({
            showConfirm: vi.fn(() => Promise.resolve(true)),
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [rule] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [suggestion] } },
            {
                method: "POST",
                path: "/api/v1/rules/r1/archive",
                status: 500,
                body: { code: "not_found", message: "fail" },
            },
            {
                method: "POST",
                path: "/api/v1/suggestions/s1/accept",
                status: 500,
                body: { code: "not_found", message: "fail" },
            },
            {
                method: "POST",
                path: "/api/v1/suggestions/s1/dismiss",
                status: 500,
                body: { code: "not_found", message: "fail" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleArchive }));
        await waitFor(() => {
            expect(adapter.hapticNotification).toHaveBeenCalledWith("error");
        });
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionAccept }));
        fireEvent.click(screen.getByRole("button", { name: ru.suggestionDismiss }));
        await waitFor(() => {
            expect(vi.mocked(adapter.hapticNotification).mock.calls.length).toBeGreaterThan(2);
        });
    });

    it("applies theme changes from the adapter", async () => {
        let themeHandler: ((scheme: "light" | "dark") => void) | undefined;
        const adapter = fakeAdapter({
            onColorSchemeChanged: (callback) => {
                themeHandler = callback;
                return () => {
                    themeHandler = undefined;
                };
            },
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await screen.findByRole("heading", { name: ru.homeTitle });
        expect(document.documentElement.dataset.colorScheme).toBe("light");
        document.documentElement.style.setProperty("--bg", "#1b1916");
        act(() => {
            themeHandler?.("dark");
        });
        expect(document.documentElement.dataset.colorScheme).toBe("dark");
        expect(adapter.applyChromeColor).toHaveBeenLastCalledWith("#1b1916");
    });

    it("exports data with success, 409 and 429 handling", async () => {
        const adapter = fakeAdapter();
        let exportCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(jsonResponse(meDone));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            if (path === "/api/v1/contacts") {
                return Promise.resolve(jsonResponse({ contacts: [contact] }));
            }
            if (path === "/api/v1/me/export" && request.method === "POST") {
                exportCalls += 1;
                if (exportCalls === 1) {
                    return Promise.resolve(
                        jsonResponse(
                            {
                                download_url: "https://miniapp.test/api/v1/downloads/t",
                                expires_at: "2026-10-07T12:00:00.000Z",
                            },
                            200,
                        ),
                    );
                }
                if (exportCalls === 2) {
                    return Promise.resolve(
                        jsonResponse({ code: "service_unavailable", message: "down" }, 503),
                    );
                }
                return Promise.resolve(
                    jsonResponse({ code: "rate_limited", message: "slow" }, 429),
                );
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyTitle }));
        expect(await screen.findByText(privacyTexts.export.description)).toBeInTheDocument();
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.privacyExportDone)).toBeInTheDocument();
        expect(adapter.downloadFile).toHaveBeenCalled();
        expect(adapter.hapticNotification).toHaveBeenCalledWith("success");
        fireEvent.click(screen.getByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.privacyExportUnavailable)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText(ru.rateLimited)).toBeInTheDocument();
    });

    it("revokes consents only when confirm accepts and shows consent screen", async () => {
        const showConfirm = vi.fn(() => Promise.resolve(false));
        const adapter = fakeAdapter({ showConfirm });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { method: "POST", path: "/api/v1/me/consents/revoke", status: 204, body: null },
            { path: "/api/v1/consents/personal_data/document", body: personalDoc },
            { path: "/api/v1/consents/special_category/document", body: specialDoc },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyTitle }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyRevokeAction }));
        expect(showConfirm).toHaveBeenCalled();
        expect(
            vi.mocked(fetchImpl).mock.calls.some((call) => {
                const request = call[0];
                const url = request instanceof Request ? request.url : String(request);
                return url.includes("/consents/revoke");
            }),
        ).toBe(false);

        showConfirm.mockResolvedValueOnce(true);
        fireEvent.click(screen.getByRole("button", { name: ru.privacyRevokeAction }));
        expect(
            await screen.findByRole("heading", { name: ru.onboardingConsentTitle }),
        ).toBeInTheDocument();
        expect(
            vi.mocked(fetchImpl).mock.calls.some((call) => {
                const request = call[0];
                const url = request instanceof Request ? request.url : String(request);
                return url.includes("/consents/revoke");
            }),
        ).toBe(true);
    });

    it("delete flow: step1 decline, step2 cancel, then success and close", async () => {
        const showConfirm = vi.fn(() => Promise.resolve(false));
        const adapter = fakeAdapter({ showConfirm });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { method: "POST", path: "/api/v1/me/delete", status: 204, body: null },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyTitle }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyDeleteButton }));
        expect(showConfirm).toHaveBeenCalled();
        expect(screen.queryByText(ru.privacyDeleteForever)).not.toBeInTheDocument();

        showConfirm.mockResolvedValueOnce(true);
        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteButton }));
        expect(
            await screen.findByRole("button", { name: ru.privacyDeleteForever }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.cancel }));
        expect(await screen.findByText(privacyTexts.export.description)).toBeInTheDocument();

        showConfirm.mockResolvedValueOnce(true);
        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteButton }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyDeleteForever }));
        expect(await screen.findByText(ru.privacyDeleted)).toBeInTheDocument();
        expect(screen.queryByText("Аня")).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.close }));
        expect(adapter.close).toHaveBeenCalled();
    });

    it("maps export generic errors and revoke/delete API failures", async () => {
        const showConfirm = vi.fn(() => Promise.resolve(true));
        const adapter = fakeAdapter({ showConfirm });
        let exportCalls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(jsonResponse(meDone));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            if (path === "/api/v1/contacts") {
                return Promise.resolve(jsonResponse({ contacts: [contact] }));
            }
            if (path === "/api/v1/me/export") {
                exportCalls += 1;
                return Promise.resolve(
                    jsonResponse({ code: "not_found", message: "export failed" }, 500),
                );
            }
            if (path === "/api/v1/me/consents/revoke") {
                return Promise.resolve(
                    jsonResponse({ code: "not_found", message: "revoke failed" }, 500),
                );
            }
            if (path === "/api/v1/me/delete") {
                return Promise.resolve(
                    jsonResponse({ code: "open_rule_limit", message: "delete failed" }, 409),
                );
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyTitle }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyExportAction }));
        expect(await screen.findByText("export failed")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: ru.privacyRevokeAction }));
        await waitFor(() => {
            expect(adapter.hapticNotification).toHaveBeenCalledWith("error");
        });
        expect(screen.getByText(privacyTexts.export.description)).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteButton }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyDeleteForever }));
        expect(await screen.findByText("delete failed")).toBeInTheDocument();
        expect(exportCalls).toBe(1);
    });

    it("shows BackButton on the delete confirmation and returns to privacy", async () => {
        let backCallback: (() => void) | undefined;
        const adapter = fakeAdapter({
            BackButton: {
                show: vi.fn(),
                hide: vi.fn(),
                onClick: (callback: () => void) => {
                    backCallback = callback;
                    return () => {
                        backCallback = undefined;
                    };
                },
            },
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.tabPrivacy }));
        expect(await screen.findByText(privacyTexts.export.description)).toBeInTheDocument();
        expect(adapter.BackButton.show).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteButton }));
        expect(
            await screen.findByRole("button", { name: ru.privacyDeleteForever }),
        ).toBeInTheDocument();
        expect(adapter.BackButton.show).toHaveBeenCalled();
        act(() => {
            backCallback?.();
        });
        expect(await screen.findByText(privacyTexts.export.description)).toBeInTheDocument();
    });

    it("opens decode and prefills addRule from a suggestion edit", async () => {
        const sse =
            'event: analysis\ndata: {"chunk":"разбор"}\n\n' +
            'event: completed\ndata: {"safety":"ok","variants":[{"kind":"softer","text":"вариант","insert_query":null}],"applied_rules":[],"applied_rule_template":"Учтено правило от {date}: «{text}»","rule_source_token":"tok"}\n\n';
        const base = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/suggestions/from-decode",
                body: {
                    outcome: "ok",
                    suggestion: {
                        id: "s1",
                        category: "apology",
                        text: "извиняться спокойно",
                        source: "decode",
                        variant_kind: null,
                        created_at: "2026-01-01T00:00:00Z",
                    },
                },
            },
        ]);
        const fetchImpl: typeof fetch = async (input, init) => {
            const url = input instanceof Request ? input.url : String(input);
            if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
                return new Response(sse, {
                    status: 200,
                    headers: {
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-store",
                    },
                });
            }
            return base(input, init);
        };
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await submitFromHome("decode", "входящее", /Аня/);
        expect(await screen.findByText("разбор")).toBeInTheDocument();
        fireEvent.click(await screen.findByRole("button", { name: ru.decodeMakeRule }));
        expect(await screen.findByText("извиняться спокойно")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSuggestionEdit }));
        expect(await screen.findByRole("heading", { name: ru.addRuleTitle })).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toHaveValue("извиняться спокойно");
    });

    it("invites unpaired contact and shares/copies the link", async () => {
        const adapter = fakeAdapter();
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [rule] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/invite",
                status: 201,
                body: {
                    link: "https://t.me/test_bot?start=inv_token",
                    expires_at: "2026-10-08T12:00:00.000Z",
                },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactInvite }));
        expect(
            await screen.findByText("https://t.me/test_bot?start=inv_token"),
        ).toBeInTheDocument();
        expect(screen.queryByText(ru.contactPairedBadge)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.contactInviteShare }));
        expect(adapter.openTelegramLink).toHaveBeenCalledWith(
            expect.stringContaining("https://t.me/share/url?url="),
        );
        fireEvent.click(screen.getByRole("button", { name: ru.contactInviteCopy }));
        await waitFor(() => {
            expect(adapter.copyText).toHaveBeenCalledWith("https://t.me/test_bot?start=inv_token");
        });
        expect(await screen.findByText(ru.contactInviteCopied)).toBeInTheDocument();
    });

    it("shows paired sections, approve/reject, scope switch, and leave confirm", async () => {
        const pairedContact = {
            ...contact,
            paired: true,
            pair_id: "p1",
        };
        const personal = { ...rule, id: "r-personal", shared: false };
        const pendingMine = {
            ...rule,
            id: "r-mine",
            text: "моё на согласовании",
            shared: true,
            status: "proposed",
            needs_my_approval: false,
            has_pending_edit: false,
            effective_since: null,
        };
        const pendingTheirs = {
            ...rule,
            id: "r-theirs",
            text: "чужое на согласовании",
            shared: true,
            status: "proposed",
            needs_my_approval: true,
            has_pending_edit: false,
            effective_since: null,
        };
        const adapter = fakeAdapter({
            showConfirm: vi.fn(() => Promise.resolve(true)),
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [pairedContact] } },
            {
                path: "/api/v1/contacts/c1/rules",
                body: { rules: [personal, pendingMine, pendingTheirs] },
            },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "POST",
                path: "/api/v1/rules/r-theirs/approve",
                body: { ...pendingTheirs, status: "active", needs_my_approval: false },
            },
            {
                method: "POST",
                path: "/api/v1/rules/r-theirs/reject",
                body: { ...pendingTheirs, status: "rejected", needs_my_approval: false },
            },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/leave",
                status: 204,
            },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/rules",
                status: 201,
                body: {
                    ...rule,
                    id: "r-new",
                    text: "общее новое",
                    shared: true,
                    status: "proposed",
                },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        expect(await screen.findByText(ru.contactPairedBadge)).toBeInTheDocument();
        expect(screen.getByText(ru.rulesPersonalTitle)).toBeInTheDocument();
        expect(screen.getByText(ru.rulesSharedTitle)).toBeInTheDocument();
        const leaveButton = await screen.findByRole("button", { name: ru.contactLeavePair });
        const addRule = screen.getByRole("button", { name: ru.contactAddRule });
        expect(
            addRule.compareDocumentPosition(leaveButton) & Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
        expect(
            screen.getByText(ru.rulesSharedTitle).compareDocumentPosition(leaveButton) &
                Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
        expect(screen.getByText("моё на согласовании")).toBeInTheDocument();
        expect(screen.getByText("чужое на согласовании")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.ruleApprove }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/approve");
                }),
            ).toBe(true);
        });
        fireEvent.click(await screen.findByRole("button", { name: ru.ruleReject }));
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/reject");
                }),
            ).toBe(true);
        });

        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        expect(await screen.findByText(ru.addRuleScope)).toBeInTheDocument();
        fireEvent.click(screen.getByLabelText(ru.addRuleScopeShared));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "общее новое" } });
        fireEvent.click(
            screen.getByRole("button", {
                name: ru.addRuleSubmitShared.replace("{label}", "Аня"),
            }),
        );
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    if (!(request instanceof Request) || !request.url.includes("/rules")) {
                        return false;
                    }
                    return request.method === "POST";
                }),
            ).toBe(true);
        });

        await waitFor(() => {
            expect(screen.getByRole("button", { name: ru.contactLeavePair })).toBeEnabled();
        });
        fireEvent.click(screen.getByRole("button", { name: ru.contactLeavePair }));
        await waitFor(() => {
            expect(adapter.showConfirm).toHaveBeenCalledWith("TEST_LEAVE_PAIR_CONFIRM");
        });
        await waitFor(() => {
            expect(screen.queryByText(ru.contactPairedBadge)).not.toBeInTheDocument();
        });
    });

    it("does not leave when confirm is declined", async () => {
        const pairedContact = { ...contact, paired: true, pair_id: "p1" };
        const adapter = fakeAdapter({
            showConfirm: vi.fn(() => Promise.resolve(false)),
        });
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [pairedContact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        await waitFor(() => {
            expect(screen.getByRole("button", { name: ru.contactLeavePair })).toBeEnabled();
        });
        fireEvent.click(screen.getByRole("button", { name: ru.contactLeavePair }));
        await waitFor(() => {
            expect(adapter.showConfirm).toHaveBeenCalled();
        });
        expect(screen.getByText(ru.contactPairedBadge)).toBeInTheDocument();
        expect(
            vi.mocked(fetchImpl).mock.calls.every((call) => {
                const request = call[0];
                const url = request instanceof Request ? request.url : "";
                return !url.includes("/leave");
            }),
        ).toBe(true);
    });

    it("hides scope switch when contact is unpaired", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        expect(screen.queryByText(ru.addRuleScope)).not.toBeInTheDocument();
    });
});

function sseFetch(base: typeof fetch, sse: string, init: ResponseInit = {}): typeof fetch {
    return async (input, requestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/api/v1/decode") && !url.includes("from-decode")) {
            return new Response(sse, {
                status: 200,
                headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
                ...init,
            });
        }
        return base(input, requestInit);
    };
}

describe("Home and navigation", () => {
    const boris = { ...contact, id: "c2", label: "Боря", relationship: "friend" };

    function homeFetch() {
        return mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact, boris] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
    }

    it("shows two modes, the contact chip and the draft field, without the old inline hint", async () => {
        render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        const modes = screen.getAllByRole("radio");
        expect(modes).toHaveLength(2);
        const write = screen.getByRole("radio", { name: new RegExp(ru.homeModeWrite) });
        expect(write).toBeChecked();
        expect(write.closest("label")).toHaveTextContent(ru.homeModeWriteHint);
        const decode = screen.getByRole("radio", { name: new RegExp(ru.homeModeDecode) });
        expect(decode).not.toBeChecked();
        expect(decode.closest("label")).toHaveTextContent(ru.homeModeDecodeHint);
        expect(screen.getByRole("button", { name: ru.contactChipNone })).toBeInTheDocument();
        expect(screen.getByLabelText(ru.homeDraftLabel)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.homeSubmitVariants })).toBeDisabled();
        expect(
            screen.queryByText(ru.introInlineHintBefore, { exact: false }),
        ).not.toBeInTheDocument();
        expect(screen.queryByText("@svoi_test_bot")).not.toBeInTheDocument();
        expect(screen.getByRole("navigation", { name: ru.tabsLabel })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.tabHome })).toHaveAttribute(
            "aria-current",
            "page",
        );
    });

    it("switches the field label for the decode mode", async () => {
        render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        fireEvent.click(await screen.findByRole("radio", { name: new RegExp(ru.homeModeDecode) }));
        expect(screen.getByLabelText(ru.homeIncomingLabel)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.homeSubmitDecode })).toBeInTheDocument();
    });

    function writeFetch() {
        return mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact, boris] } },
            {
                method: "POST",
                path: "/api/v1/compose",
                body: {
                    safety: "ok",
                    applied_rules: [],
                    choice_token: "choice",
                    variants: [
                        { kind: "firmer", text: "твёрже" },
                        { kind: "check", text: "проверенный" },
                        { kind: "softer", text: "мягче" },
                    ],
                },
            },
        ]);
    }

    it("opens the rewrite variants in the fixed order with goal chips and no contact", async () => {
        const log: RecordedRequest[] = [];
        render(<App adapter={fakeAdapter()} fetchImpl={recordRequests(writeFetch(), log)} />);
        await submitFromHome("write", "  черновик  ");
        expect(await screen.findByRole("heading", { name: ru.composeTitle })).toBeInTheDocument();
        expect(await screen.findByDisplayValue("проверенный")).toBeInTheDocument();
        expect(screen.queryByRole("navigation", { name: ru.tabsLabel })).not.toBeInTheDocument();
        expect(
            screen.getAllByRole("article").map((card) => within(card).getByRole("textbox")),
        ).toHaveLength(3);
        expect(
            screen.getAllByRole("textbox").map((box) => (box as HTMLTextAreaElement).value),
        ).toEqual(["проверенный", "мягче", "твёрже"]);
        expect(screen.getByText(ru.composeGoalLegend)).toBeInTheDocument();
        expect(
            screen.getAllByRole("radio").every((radio) => !(radio as HTMLInputElement).checked),
        ).toBe(true);
        const composeCalls = log.filter((entry) => entry.path === "/api/v1/compose");
        expect(composeCalls).toHaveLength(1);
        expect(composeCalls[0]?.body).toMatchObject({
            draft: "черновик",
            goal: null,
            contact_id: null,
        });
    });

    it("opens the rewrite variants with the chosen contact", async () => {
        const log: RecordedRequest[] = [];
        render(<App adapter={fakeAdapter()} fetchImpl={recordRequests(writeFetch(), log)} />);
        await submitFromHome("write", "черновик", /Боря/);
        expect(await screen.findByRole("heading", { name: ru.composeTitle })).toBeInTheDocument();
        await waitFor(() => {
            expect(log.filter((entry) => entry.path === "/api/v1/compose")).toHaveLength(1);
        });
        expect(log.find((entry) => entry.path === "/api/v1/compose")?.body).toMatchObject({
            draft: "черновик",
            contact_id: "c2",
        });
    });

    it("sends contact_id in decode when a contact is chosen and null otherwise", async () => {
        const withContact: RecordedRequest[] = [];
        const first = render(
            <App adapter={fakeAdapter()} fetchImpl={recordRequests(homeFetch(), withContact)} />,
        );
        await submitFromHome("decode", "входящее", /Аня/);
        await waitFor(() => {
            expect(withContact.some((entry) => entry.path === "/api/v1/decode")).toBe(true);
        });
        expect(withContact.find((entry) => entry.path === "/api/v1/decode")?.body).toMatchObject({
            text: "входящее",
            contact_id: "c1",
        });
        first.unmount();

        const without: RecordedRequest[] = [];
        render(<App adapter={fakeAdapter()} fetchImpl={recordRequests(homeFetch(), without)} />);
        await submitFromHome("decode", "входящее");
        await waitFor(() => {
            expect(without.some((entry) => entry.path === "/api/v1/decode")).toBe(true);
        });
        expect(without.find((entry) => entry.path === "/api/v1/decode")?.body).toMatchObject({
            text: "входящее",
            contact_id: null,
        });
    });

    it("does not keep the chosen contact after the app is reopened", async () => {
        const first = render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        await chooseHomeContact(/Аня/);
        expect(
            screen.getByRole("button", { name: ru.contactChipFor.replace("{label}", "Аня") }),
        ).toBeInTheDocument();
        first.unmount();
        render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        expect(await screen.findByRole("button", { name: ru.contactChipNone })).toBeInTheDocument();
    });

    it("returns to no contact from the picker", async () => {
        render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        await chooseHomeContact(/Аня/);
        fireEvent.click(
            screen.getByRole("button", { name: ru.contactChipFor.replace("{label}", "Аня") }),
        );
        fireEvent.click(
            within(screen.getByRole("dialog")).getByRole("button", { name: ru.contactNoneItem }),
        );
        expect(screen.getByRole("button", { name: ru.contactChipNone })).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.contactChipNone }));
        fireEvent.click(
            within(screen.getByRole("dialog")).getByRole("button", { name: ru.cancel }),
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("pastes the clipboard into the draft and ignores an unreadable clipboard", async () => {
        const readText = vi.fn(() => Promise.resolve("из буфера"));
        Object.defineProperty(navigator, "clipboard", {
            value: { readText },
            configurable: true,
        });
        render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.homePaste }));
        await waitFor(() => {
            expect(screen.getByLabelText(ru.homeDraftLabel)).toHaveValue("из буфера");
        });
        readText.mockImplementation(() => Promise.reject(new Error("denied")));
        fireEvent.change(screen.getByLabelText(ru.homeDraftLabel), { target: { value: "своё" } });
        fireEvent.click(screen.getByRole("button", { name: ru.homePaste }));
        await waitFor(() => {
            expect(readText).toHaveBeenCalledTimes(2);
        });
        expect(screen.getByLabelText(ru.homeDraftLabel)).toHaveValue("своё");
    });

    it("keeps the tab bar on root screens only", async () => {
        render(<App adapter={fakeAdapter()} fetchImpl={homeFetch()} />);
        await submitFromHome("decode", "текст");
        expect(await screen.findByRole("heading", { name: ru.decodeTitle })).toBeInTheDocument();
        expect(screen.queryByRole("navigation", { name: ru.tabsLabel })).not.toBeInTheDocument();
    });
});

describe("Onboarding intro", () => {
    function storage(value: string | Error) {
        const getItem = vi.fn(() =>
            value instanceof Error ? Promise.reject(value) : Promise.resolve(value),
        );
        const setItem = vi.fn(() => Promise.resolve(true));
        return { getItem, setItem };
    }

    function introFetch() {
        return mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
        ]);
    }

    it("is not shown without CloudStorage", async () => {
        render(<App adapter={fakeAdapter()} fetchImpl={introFetch()} />);
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(screen.queryByRole("heading", { name: ru.introTitle })).not.toBeInTheDocument();
    });

    it("is shown when the flag is unset, with the owner copy and the bot hint", async () => {
        const cloud = storage("");
        render(<App adapter={fakeAdapter({ cloudStorage: cloud })} fetchImpl={introFetch()} />);
        expect(await screen.findByRole("heading", { name: ru.introTitle })).toBeInTheDocument();
        expect(screen.getByText(ru.introSubtitle)).toBeInTheDocument();
        for (const step of ru.introSteps) {
            expect(screen.getByText(step.title)).toBeInTheDocument();
            expect(screen.getByText(step.caption)).toBeInTheDocument();
        }
        expect(screen.getByText("svoi_test_bot", { exact: false })).toBeInTheDocument();
        expect(cloud.getItem).toHaveBeenCalledWith("intro_seen");
        expect(cloud.setItem).not.toHaveBeenCalled();
    });

    it("is not shown when the flag is already set", async () => {
        const cloud = storage("1");
        render(<App adapter={fakeAdapter({ cloudStorage: cloud })} fetchImpl={introFetch()} />);
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(screen.queryByRole("heading", { name: ru.introTitle })).not.toBeInTheDocument();
    });

    it("is treated as seen when the storage read fails", async () => {
        const cloud = storage(new Error("unavailable"));
        render(<App adapter={fakeAdapter({ cloudStorage: cloud })} fetchImpl={introFetch()} />);
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(screen.queryByRole("heading", { name: ru.introTitle })).not.toBeInTheDocument();
    });

    it("writes the flag and opens Home when skipped", async () => {
        const cloud = storage("");
        render(<App adapter={fakeAdapter({ cloudStorage: cloud })} fetchImpl={introFetch()} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.introSkip }));
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(cloud.setItem).toHaveBeenCalledWith("intro_seen", "1");
    });

    it("writes the flag and opens Home when closed", async () => {
        const cloud = storage("");
        render(<App adapter={fakeAdapter({ cloudStorage: cloud })} fetchImpl={introFetch()} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.introClose }));
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
        expect(cloud.setItem).toHaveBeenCalledWith("intro_seen", "1");
    });

    it("runs the example in the fixed order with the draft block and unselected goals", async () => {
        const cloud = storage("");
        const log: RecordedRequest[] = [];
        const base = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            {
                method: "POST",
                path: "/api/v1/compose",
                body: {
                    safety: "ok",
                    applied_rules: [],
                    choice_token: "choice",
                    variants: [
                        { kind: "softer", text: "мягкий" },
                        { kind: "firmer", text: "твёрже" },
                        { kind: "check", text: "проверенный" },
                    ],
                },
            },
        ]);
        render(
            <App
                adapter={fakeAdapter({ cloudStorage: cloud })}
                fetchImpl={recordRequests(base, log)}
            />,
        );
        fireEvent.click(await screen.findByRole("button", { name: ru.introTryExample }));
        expect(
            await screen.findByRole("heading", { name: ru.introExampleTitle }),
        ).toBeInTheDocument();
        expect(cloud.setItem).toHaveBeenCalledWith("intro_seen", "1");
        expect(await screen.findByDisplayValue("проверенный")).toBeInTheDocument();
        const quote = screen.getByText(ru.composeYourDraft).closest(".quote");
        expect(quote).toHaveTextContent(ru.introExampleDraft);
        expect(
            screen.getAllByRole("textbox").map((box) => (box as HTMLTextAreaElement).value),
        ).toEqual(["проверенный", "мягкий", "твёрже"]);
        expect(screen.getByText(ru.composeGoalLegend)).toBeInTheDocument();
        expect(
            screen.getAllByRole("radio").every((radio) => !(radio as HTMLInputElement).checked),
        ).toBe(true);
        const composeCalls = log.filter((entry) => entry.path === "/api/v1/compose");
        expect(composeCalls).toHaveLength(1);
        expect(composeCalls[0]?.body).toEqual({
            draft: ru.introExampleDraft,
            goal: null,
            contact_id: null,
        });
        expect(log.some((entry) => entry.path.includes("activate"))).toBe(false);
    });
});

describe("Decode limits and crisis", () => {
    function decodeBase() {
        return mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
    }

    async function startDecode(fetchImpl: typeof fetch) {
        const adapter = fakeAdapter();
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await submitFromHome("decode", "входящее");
        return adapter;
    }

    it("shows the limit screen for an exhausted daily quota and returns home", async () => {
        const base = decodeBase();
        const fetchImpl: typeof fetch = async (input, init) => {
            const url = input instanceof Request ? input.url : String(input);
            if (url.includes("/api/v1/decode")) {
                return jsonResponse({ code: "quota_exhausted", message: "Лимит до 00:00." }, 429);
            }
            return base(input, init);
        };
        await startDecode(fetchImpl);
        expect(
            await screen.findByRole("heading", { name: ru.limitTitleQuota }),
        ).toBeInTheDocument();
        expect(screen.getByText("Лимит до 00:00.")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.limitOk }));
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
    });

    it("opens the crisis screen from the limit screen and comes back", async () => {
        const base = decodeBase();
        const fetchImpl: typeof fetch = async (input, init) => {
            const url = input instanceof Request ? input.url : String(input);
            if (url.includes("/api/v1/decode")) {
                return jsonResponse(
                    { code: "service_budget_exhausted", message: "Перегрузка." },
                    503,
                );
            }
            return base(input, init);
        };
        await startDecode(fetchImpl);
        expect(
            await screen.findByRole("heading", { name: ru.limitTitleBudget }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.limitHelpLink }));
        expect(await screen.findByRole("heading", { name: ru.crisisTitle })).toBeInTheDocument();
        expect(
            screen.getByRole("link", { name: new RegExp(ru.crisisEmergencyTitle) }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.crisisBack }));
        expect(
            await screen.findByRole("heading", { name: ru.limitTitleBudget }),
        ).toBeInTheDocument();
    });

    it("replaces the decode screen with crisis resources", async () => {
        const crisis =
            'event: crisis\ndata: {"lead":"SERVER_CRISIS_LEAD","resources":["112 — единый номер"]}\n\n';
        await startDecode(sseFetch(decodeBase(), crisis));
        expect(await screen.findByRole("heading", { name: ru.crisisTitle })).toBeInTheDocument();
        expect(screen.getByText("SERVER_CRISIS_LEAD")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.crisisBack }));
        expect(await screen.findByRole("heading", { name: ru.homeTitle })).toBeInTheDocument();
    });

    it("counts down the remaining decodes locally after a completed decode", async () => {
        const completed =
            'event: analysis\ndata: {"chunk":"разбор"}\n\n' +
            'event: completed\ndata: {"safety":"ok","variants":[{"kind":"softer","text":"вариант","insert_query":null}],"applied_rules":[],"applied_rule_template":"Учтено правило от {date}: «{text}»","rule_source_token":null}\n\n';
        const base = decodeBase();
        const fetchImpl = sseFetch(base, completed);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await submitFromHome("decode", "входящее");
        expect(await screen.findByText("вариант")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.decodeAgain }));
        expect(await screen.findByText(decodeRemainingLabel(4))).toBeInTheDocument();
    });

    it("keeps the typed message when the contact changes inside decode", async () => {
        const boris = { ...contact, id: "c2", label: "Боря", relationship: "friend" };
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact, boris] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await submitFromHome("decode", "не потерять", /Аня/);
        fireEvent.click(
            await screen.findByRole("button", { name: ru.decodeFrom.replace("{name}", "Аня") }),
        );
        fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /Боря/ }));
        expect(
            await screen.findByRole("button", { name: ru.decodeFrom.replace("{name}", "Боря") }),
        ).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toHaveValue("не потерять");
    });
});

describe("Contact detail editing", () => {
    it("renames the contact from the detail header", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            { method: "PATCH", path: "/api/v1/contacts/c1", body: { ...contact, label: "Анна" } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactEdit }));
        const dialog = screen.getByRole("dialog", { name: ru.contactsRename });
        expect(within(dialog).queryByLabelText(ru.relationships.family)).not.toBeInTheDocument();
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Анна" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        expect(await screen.findByRole("heading", { name: "Анна" })).toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("keeps the sheet open and shows the error when renaming fails", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/contacts/c1/rules", body: { rules: [] } },
            { path: "/api/v1/contacts/c1/suggestions", body: { suggestions: [] } },
            {
                method: "PATCH",
                path: "/api/v1/contacts/c1",
                status: 500,
                body: { code: "not_found", message: "rename boom" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        await openPeople();
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactEdit }));
        fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: ru.save }));
        expect(await screen.findByText("rename boom")).toBeInTheDocument();
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("shows the consent texts from the privacy tab", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            { path: "/api/v1/consents/personal_data/document", body: personalDoc },
            { path: "/api/v1/consents/special_category/document", body: specialDoc },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.tabPrivacy }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyConsentsAction }));
        const dialog = await screen.findByRole("dialog", { name: ru.privacyConsentsAction });
        expect(await within(dialog).findByText("DOC_PERSONAL")).toBeInTheDocument();
        expect(within(dialog).getByText("DOC_SPECIAL")).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole("button", { name: ru.consentDocsClose }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("offers a retry when the consent texts fail to load", async () => {
        let calls = 0;
        const fetchImpl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
            const request = input instanceof Request ? input : new Request(String(input), init);
            const path = new URL(request.url).pathname;
            if (path === "/api/v1/me") {
                return Promise.resolve(jsonResponse(meDone));
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            if (path === "/api/v1/contacts") {
                return Promise.resolve(jsonResponse({ contacts: [contact] }));
            }
            if (path === "/api/v1/consents/personal_data/document") {
                calls += 1;
                return Promise.resolve(
                    calls === 1
                        ? jsonResponse({ code: "not_found", message: "no doc" }, 500)
                        : jsonResponse(personalDoc),
                );
            }
            return Promise.resolve(jsonResponse(specialDoc));
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.tabPrivacy }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyConsentsAction }));
        const dialog = await screen.findByRole("dialog");
        fireEvent.click(await within(dialog).findByRole("button", { name: ru.retry }));
        expect(await within(dialog).findByText("DOC_PERSONAL")).toBeInTheDocument();
    });
});

describe("Telegram adapter wiring", () => {
    it("calls ready and expand inside Telegram", async () => {
        const adapter: TelegramAdapter = fakeAdapter();
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [] } },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        await screen.findByRole("heading", { name: ru.homeTitle });
        expect(adapter.ready).toHaveBeenCalledTimes(1);
        expect(adapter.expand).toHaveBeenCalledTimes(1);
    });
});
