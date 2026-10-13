import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ru } from "../localization/ru";
import { fakeAdapter, jsonResponse } from "../test/fakeTelegram";
import { InlineComposeApp } from "./InlineComposeApp";

afterEach(() => {
    vi.restoreAllMocks();
});

const sessionToken = "cs_" + "b".repeat(64);

function redeemOk(overrides: Record<string, unknown> = {}) {
    return {
        draft: "черновик из inline",
        limits: { incoming_text_max_chars: 4000 },
        session: { token: sessionToken, expires_at: "2026-06-01T12:15:00Z" },
        ...overrides,
    };
}

function composeOk(overrides: Record<string, unknown> = {}) {
    return {
        safety: "ok",
        variants: [
            { kind: "check", text: "проверенный" },
            { kind: "softer", text: "мягкий" },
            { kind: "firmer", text: "твёрдый" },
        ],
        applied_rules: [],
        choice_token: "choice-1",
        ...overrides,
    };
}

function requestUrl(input: RequestInfo | URL): string {
    if (typeof input === "string") {
        return input;
    }
    if (input instanceof URL) {
        return input.href;
    }
    return input.url;
}

function inlineTelegram(token = "h") {
    return fakeAdapter({
        launch: { kind: "inline_compose", handoffToken: token },
        initData: "",
    });
}

function fetchRouter(routes: (request: Request) => Promise<Response> | Response): typeof fetch {
    const impl: typeof fetch = (input, init) => {
        const request = input instanceof Request ? input : new Request(requestUrl(input), init);
        return Promise.resolve(routes(request));
    };
    return vi.fn(impl);
}

describe("InlineComposeApp", () => {
    it("redeems once, generates with compose auth, records choice and copies", async () => {
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/compose/handoff/redeem")) {
                expect(request.headers.get("Authorization")).toBe("handoff h_one");
                return jsonResponse(redeemOk());
            }
            if (url.pathname.endsWith("/compose") && request.method === "POST") {
                expect(request.headers.get("Authorization")).toBe(`compose ${sessionToken}`);
                return jsonResponse(composeOk());
            }
            if (url.pathname.endsWith("/compose/choice")) {
                expect(request.headers.get("Authorization")).toBe(`compose ${sessionToken}`);
                return jsonResponse({ outcome: "recorded" });
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram("h_one");
        render(<InlineComposeApp telegram={telegram} handoffToken="h_one" fetchImpl={fetchImpl} />);
        expect(await screen.findByDisplayValue("черновик из inline")).toBeInTheDocument();
        expect(telegram.BackButton.hide).toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        expect(await screen.findByDisplayValue("мягкий")).toBeInTheDocument();
        const copyButtons = screen.getAllByRole("button", { name: ru.composeCopyAndReturn });
        const firstCopy = copyButtons[0];
        expect(firstCopy).toBeDefined();
        if (firstCopy === undefined) {
            throw new Error("missing copy button");
        }
        fireEvent.click(firstCopy);
        await waitFor(() => {
            expect(telegram.copyText).toHaveBeenCalled();
            expect(telegram.close).toHaveBeenCalled();
        });
        const redeemCalls = vi
            .mocked(fetchImpl)
            .mock.calls.filter((call) => requestUrl(call[0]).includes("/handoff/redeem"));
        expect(redeemCalls).toHaveLength(1);
    });

    it("generates without a contact and offers no partner send", async () => {
        const captured: { body?: Record<string, unknown> } = {};
        const fetchImpl = fetchRouter(async (request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            if (url.pathname.endsWith("/compose") && request.method === "POST") {
                captured.body = (await request.json()) as Record<string, unknown>;
                return jsonResponse(composeOk());
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        render(
            <InlineComposeApp telegram={inlineTelegram()} handoffToken="h" fetchImpl={fetchImpl} />,
        );
        await screen.findByDisplayValue("черновик из inline");
        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        await screen.findByDisplayValue("мягкий");
        expect(screen.queryByText(ru.composeYourDraft)).not.toBeInTheDocument();
        expect(captured.body).toBeDefined();
        expect(captured.body?.["contact_id"] ?? null).toBeNull();
        expect(
            screen.queryByRole("button", { name: ru.composeSend.replace("{label}", "Аня") }),
        ).not.toBeInTheDocument();
    });

    it("shows expired handoff error and closes", async () => {
        const fetchImpl = fetchRouter(() =>
            jsonResponse({ code: "handoff_expired", message: "expired" }, 401),
        );
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        expect(
            await screen.findByRole("heading", { name: ru.composeInlineHandoffExpired }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.composeInlineClose }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("shows consent required error and closes", async () => {
        const fetchImpl = fetchRouter(() =>
            jsonResponse({ code: "consent_required", message: "consent" }, 403),
        );
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        expect(
            await screen.findByRole("heading", { name: ru.composeInlineConsent }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.composeInlineClose }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("maps redeem rate_limited to rateLimited copy and closes", async () => {
        const fetchImpl = fetchRouter(() =>
            jsonResponse({ code: "rate_limited", message: "slow" }, 429),
        );
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        expect(await screen.findByRole("heading", { name: ru.rateLimited })).toBeInTheDocument();
        expect(screen.queryByText(ru.errorGeneric)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.composeInlineClose }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("shows onboarding and generic redeem errors", async () => {
        const onboarding = fetchRouter(() =>
            jsonResponse({ code: "onboarding_required", message: "onboard" }, 403),
        );
        const telegram = inlineTelegram();
        const { unmount } = render(
            <InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={onboarding} />,
        );
        expect(
            await screen.findByRole("heading", { name: ru.composeInlineOnboarding }),
        ).toBeInTheDocument();
        unmount();

        const generic = fetchRouter(() => jsonResponse({ code: "not_found", message: "x" }, 404));
        render(
            <InlineComposeApp telegram={inlineTelegram()} handoffToken="h" fetchImpl={generic} />,
        );
        expect(await screen.findByRole("heading", { name: ru.errorGeneric })).toBeInTheDocument();
    });

    it("shows limit screen then closes", async () => {
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            if (url.pathname.endsWith("/compose") && request.method === "POST") {
                return jsonResponse({ code: "quota_exhausted", message: "quota" }, 429);
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        await screen.findByDisplayValue("черновик из inline");
        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        expect(
            await screen.findByRole("heading", { name: ru.limitTitleQuota }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.limitOk }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("opens crisis from limit help and closes from crisis", async () => {
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            if (url.pathname.endsWith("/compose") && request.method === "POST") {
                return jsonResponse({ code: "service_budget_exhausted", message: "budget" }, 503);
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        await screen.findByDisplayValue("черновик из inline");
        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        expect(
            await screen.findByRole("heading", { name: ru.limitTitleBudget }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.limitHelpLink }));
        fireEvent.click(await screen.findByRole("button", { name: ru.crisisBack }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("shows crisis screen from compose safety and closes", async () => {
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            if (url.pathname.endsWith("/compose") && request.method === "POST") {
                return jsonResponse({
                    safety: "crisis",
                    lead: "help",
                    resources: ["https://example.test"],
                    variants: [],
                    applied_rules: [],
                    choice_token: null,
                });
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        await screen.findByDisplayValue("черновик из inline");
        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        fireEvent.click(await screen.findByRole("button", { name: ru.crisisBack }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("shows expired screen when compose session expires on POST", async () => {
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            if (url.pathname.endsWith("/compose") && request.method === "POST") {
                return jsonResponse({ code: "compose_session_expired", message: "gone" }, 401);
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        await screen.findByDisplayValue("черновик из inline");
        fireEvent.click(screen.getByRole("button", { name: ru.composeSubmit }));
        expect(
            await screen.findByRole("heading", { name: ru.composeInlineSessionExpired }),
        ).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: ru.composeInlineClose }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("closes via editor close button in ready phase", async () => {
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram();
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        await screen.findByDisplayValue("черновик из inline");
        fireEvent.click(screen.getByRole("button", { name: ru.composeInlineClose }));
        expect(telegram.close).toHaveBeenCalled();
    });

    it("maps redeem compose_session_expired and closes via BackButton", async () => {
        const fetchImpl = fetchRouter(() =>
            jsonResponse({ code: "compose_session_expired", message: "gone" }, 401),
        );
        const onClick = vi.fn((callback: () => void) => {
            callback();
            return () => undefined;
        });
        const telegram = fakeAdapter({
            launch: { kind: "inline_compose", handoffToken: "h" },
            initData: "",
            BackButton: {
                show: vi.fn(),
                hide: vi.fn(),
                onClick,
            },
        });
        render(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />);
        expect(
            await screen.findByRole("heading", { name: ru.composeInlineSessionExpired }),
        ).toBeInTheDocument();
        expect(telegram.close).toHaveBeenCalled();
    });

    it("skips a second redeem when effect deps change after start", async () => {
        let redeemCount = 0;
        const fetchImpl = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                redeemCount += 1;
                return jsonResponse(redeemOk());
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        const telegram = inlineTelegram();
        const { rerender } = render(
            <InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={fetchImpl} />,
        );
        await screen.findByDisplayValue("черновик из inline");
        expect(redeemCount).toBe(1);
        const other = fetchRouter(() => jsonResponse(redeemOk()));
        rerender(<InlineComposeApp telegram={telegram} handoffToken="h" fetchImpl={other} />);
        await waitFor(() => {
            expect(redeemCount).toBe(1);
        });
    });

    it("uses global fetch when fetchImpl is omitted", async () => {
        const fetchMock = fetchRouter((request) => {
            const url = new URL(request.url);
            if (url.pathname.endsWith("/handoff/redeem")) {
                return jsonResponse(redeemOk());
            }
            return jsonResponse({ code: "not_found", message: "x" }, 404);
        });
        vi.stubGlobal("fetch", fetchMock);
        try {
            render(<InlineComposeApp telegram={inlineTelegram()} handoffToken="h" />);
            expect(await screen.findByDisplayValue("черновик из inline")).toBeInTheDocument();
        } finally {
            vi.unstubAllGlobals();
        }
    });
});
