import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ru } from "./localization/ru";
import { fakeAdapter, jsonResponse, mockFetch, privacyTexts } from "./test/fakeTelegram";
import type { TelegramAdapter } from "./telegram/webapp";

const meDone = {
    onboarding_step: "done",
    consent_kind: null,
    consent_version: null,
    active_contact_id: "c1",
    account_exists: true,
    max_contacts: 20,
    max_open_rules: 50,
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
    firmness: "soft",
    created_at: "2026-10-04T12:00:00.000Z",
};

function click(name: RegExp | string) {
    fireEvent.click(screen.getByRole("button", { name }));
}

describe("App", () => {
    it("renders the open-from-Telegram message outside Telegram", () => {
        render(<App adapter={fakeAdapter({ isInsideTelegram: false, initData: "" })} />);
        expect(screen.getByRole("heading", { name: ru.appTitle })).toBeInTheDocument();
        expect(screen.getByText(ru.openFromTelegram)).toBeInTheDocument();
    });

    it("shows loading then contacts empty state", async () => {
        const fetchImpl = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [] } },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(screen.getByText(ru.loading)).toBeInTheDocument();
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
            contactsCalls += 1;
            if (contactsCalls === 1) {
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
                return Promise.resolve(
                    jsonResponse({ kind: "personal_data", version: "1", text: "DOC_AFTER_AGE" }),
                );
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByText(ru.onboardingAgePrompt)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.onboardingAgeYes }));
        expect(await screen.findByText("DOC_AFTER_AGE")).toBeInTheDocument();
    });

    it("shows age declined copy when the user declines", async () => {
        const adapter = fakeAdapter();
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: { ...meDone, onboarding_step: "age", account_exists: false },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        fireEvent.click(await screen.findByRole("button", { name: ru.onboardingAgeNo }));
        expect(await screen.findByText(ru.onboardingAgeDeclined)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.close }));
        expect(adapter.close).toHaveBeenCalled();
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
        fireEvent.click(await screen.findByRole("button", { name: ru.onboardingAgeYes }));
        expect(await screen.findByText("age failed")).toBeInTheDocument();
    });

    it("shows the consent onboarding screen with document and rights", async () => {
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
                        }),
                    );
                }
                return Promise.resolve(jsonResponse(meDone));
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(
                    jsonResponse({ kind: "personal_data", version: "1", text: "DOC_PERSONAL" }),
                );
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
        expect(await screen.findByText("DOC_PERSONAL")).toBeInTheDocument();
        expect(screen.getByText(/Что мы храним/)).toBeInTheDocument();
        expect(
            await screen.findByRole("button", { name: ru.privacyExportAction }),
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: ru.privacyDeleteAction })).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.onboardingConsentAgree }));
        expect(await screen.findByText("Аня")).toBeInTheDocument();
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
        fireEvent.click(screen.getByRole("button", { name: ru.cancel }));
        expect(await screen.findByText("Аня")).toBeInTheDocument();
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
        expect(await screen.findByText("Аня")).toBeInTheDocument();
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

    it("declines consent and closes", async () => {
        const adapter = fakeAdapter();
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
            {
                path: "/api/v1/consents/special_category/document",
                body: { kind: "special_category", version: "1", text: "DOC_SPECIAL" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
        expect(await screen.findByText("DOC_SPECIAL")).toBeInTheDocument();
        expect(screen.queryByText(/Что мы храним/)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.onboardingConsentDecline }));
        expect(await screen.findByText(ru.onboardingConsentDeclined)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.close }));
        expect(adapter.close).toHaveBeenCalled();
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
                    }),
                );
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(
                    jsonResponse({ kind: "personal_data", version: "1", text: "DOC_STALE" }),
                );
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
        expect(await screen.findByText("DOC_STALE")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.onboardingConsentAgree }));
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
        fireEvent.change(screen.getByLabelText(ru.inviteRelationship), {
            target: { value: "friend" },
        });
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
                return Promise.resolve(
                    jsonResponse({ kind: "personal_data", version: "1", text: "DOC_RETRY" }),
                );
            }
            if (path === "/api/v1/privacy/texts") {
                return Promise.resolve(jsonResponse(privacyTexts));
            }
            return Promise.resolve(jsonResponse({ code: "not_found", message: "x" }, 404));
        }) as typeof fetch;
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByRole("alert")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.retry }));
        expect(await screen.findByText("DOC_RETRY")).toBeInTheDocument();
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
            {
                path: "/api/v1/consents/personal_data/document",
                body: { kind: "personal_data", version: "1", text: "DOC_REQUIRED" },
            },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByText("DOC_REQUIRED")).toBeInTheDocument();
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
                },
            },
            {
                path: "/api/v1/consents/personal_data/document",
                body: { kind: "personal_data", version: "1", text: "DOC" },
            },
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
                    }),
                );
            }
            if (path === "/api/v1/consents/personal_data/document") {
                return Promise.resolve(
                    jsonResponse({ kind: "personal_data", version: "1", text: "DOC" }),
                );
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
        expect(await screen.findByText("Аня")).toBeInTheDocument();
        expect(screen.getByText(new RegExp(ru.contactsActiveBadge))).toBeInTheDocument();
        click(/Аня/);
        expect(await screen.findByRole("heading", { name: "Аня" })).toBeInTheDocument();
        expect(screen.getByText(ru.relationships.partner)).toBeInTheDocument();
        expect(await screen.findByText("не повышать голос")).toBeInTheDocument();
        expect(screen.getByText(ru.rulePendingEdit)).toBeInTheDocument();
        expect(screen.getByText(new RegExp(ru.ruleProposed))).toBeInTheDocument();
        expect(screen.getByText("извиняться спокойно")).toBeInTheDocument();
        expect(adapter.BackButton.show).toHaveBeenCalled();
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
        expect(await screen.findByText(ru.contactsEmpty)).toBeInTheDocument();
        click(ru.contactsAdd);
        const dialog = screen.getByRole("dialog");
        fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Новый" } });
        fireEvent.click(within(dialog).getByRole("button", { name: ru.save }));
        expect(await screen.findByText(ru.contactsLimit)).toBeInTheDocument();
    });

    it("renames and activates a contact", async () => {
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: { ...meDone, active_contact_id: null },
            },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            {
                method: "PATCH",
                path: "/api/v1/contacts/c1",
                body: { ...contact, label: "Анна" },
            },
            { method: "POST", path: "/api/v1/contacts/c1/activate", body: null, status: 204 },
        ]);
        render(<App adapter={fakeAdapter()} fetchImpl={fetchImpl} />);
        expect(await screen.findByText("Аня")).toBeInTheDocument();
        click(ru.contactsRename);
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
        expect(
            await screen.findByRole("button", { name: ru.contactsMakeActive }),
        ).toBeInTheDocument();
        click(ru.contactsMakeActive);
        await waitFor(() => {
            expect(
                vi.mocked(fetchImpl).mock.calls.some((call) => {
                    const request = call[0];
                    const url = request instanceof Request ? request.url : "";
                    return url.includes("/activate");
                }),
            ).toBe(true);
        });
    });

    it("handles rename/activate errors and cancels the modal", async () => {
        const adapter = fakeAdapter();
        const fetchImpl = mockFetch([
            {
                path: "/api/v1/me",
                body: { ...meDone, active_contact_id: null },
            },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
            {
                method: "PATCH",
                path: "/api/v1/contacts/c1",
                status: 500,
                body: { code: "not_found", message: "rename failed" },
            },
            {
                method: "POST",
                path: "/api/v1/contacts/c1/activate",
                status: 500,
                body: { code: "not_found", message: "activate failed" },
            },
            {
                method: "POST",
                path: "/api/v1/contacts",
                status: 500,
                body: { code: "not_found", message: "create failed" },
            },
        ]);
        render(<App adapter={adapter} fetchImpl={fetchImpl} />);
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
        click(ru.contactsRename);
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
        click(ru.contactsMakeActive);
        await waitFor(() => {
            expect(vi.mocked(adapter.hapticNotification).mock.calls.length).toBeGreaterThan(2);
        });
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
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
        expect(await screen.findByText(ru.addRuleValidation)).toBeInTheDocument();
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
        await screen.findByText(ru.contactsEmpty);
        themeHandler?.("dark");
        expect(document.documentElement.dataset.colorScheme).toBe("dark");
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
            {
                path: "/api/v1/consents/personal_data/document",
                body: { kind: "personal_data", version: "1", text: "DOC_AFTER_REVOKE" },
            },
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
        expect(await screen.findByText("DOC_AFTER_REVOKE")).toBeInTheDocument();
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
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyDeleteAction }));
        expect(showConfirm).toHaveBeenCalled();
        expect(screen.queryByText(ru.privacyDeleteForever)).not.toBeInTheDocument();

        showConfirm.mockResolvedValueOnce(true);
        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteAction }));
        expect(
            await screen.findByRole("button", { name: ru.privacyDeleteForever }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.cancel }));
        expect(await screen.findByText(privacyTexts.export.description)).toBeInTheDocument();

        showConfirm.mockResolvedValueOnce(true);
        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteAction }));
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

        fireEvent.click(screen.getByRole("button", { name: ru.privacyDeleteAction }));
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyDeleteForever }));
        expect(await screen.findByText("delete failed")).toBeInTheDocument();
        expect(exportCalls).toBe(1);
    });

    it("shows BackButton on the privacy screen", async () => {
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
        fireEvent.click(await screen.findByRole("button", { name: ru.privacyTitle }));
        expect(await screen.findByText(privacyTexts.export.description)).toBeInTheDocument();
        expect(adapter.BackButton.show).toHaveBeenCalled();
        backCallback?.();
        expect(await screen.findByText("Аня")).toBeInTheDocument();
    });

    it("opens decode and prefills addRule from a suggestion edit", async () => {
        const sse =
            'event: analysis\ndata: {"chunk":"разбор"}\n\n' +
            'event: completed\ndata: {"safety":"ok","variants":[{"firmness":"gentle","text":"вариант","insert_query":null}],"applied_rules":[],"applied_rule_template":"Учтено правило от {date}: «{text}»","rule_source_token":"tok"}\n\n';
        const base = mockFetch([
            { path: "/api/v1/me", body: meDone },
            { path: "/api/v1/contacts", body: { contacts: [contact] } },
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
                        firmness: null,
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
        fireEvent.click(await screen.findByRole("button", { name: ru.decodeEntry }));
        expect(await screen.findByRole("heading", { name: ru.decodeTitle })).toBeInTheDocument();
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "входящее" } });
        fireEvent.click(screen.getByRole("button", { name: ru.decodeSubmit }));
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
        fireEvent.click(screen.getByRole("button", { name: ru.addRuleSubmit }));
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
        fireEvent.click(await screen.findByRole("button", { name: /Аня/ }));
        fireEvent.click(await screen.findByRole("button", { name: ru.contactAddRule }));
        expect(screen.queryByText(ru.addRuleScope)).not.toBeInTheDocument();
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
        await screen.findByText(ru.contactsEmpty);
        expect(adapter.ready).toHaveBeenCalledTimes(1);
        expect(adapter.expand).toHaveBeenCalledTimes(1);
    });
});
