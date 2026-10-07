/**
 * Headless screenshots of the built mini-app with a mocked API (synthetic data only).
 * Usage: node scripts/screenshots.mjs
 * Requires: pnpm build, playwright browsers installed.
 */
import { createServer } from "node:http";
import { readFileSync, mkdirSync, existsSync } from "node:fs";
import { join, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const outDir = join(root, "screenshots");

const mime = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
};

function serveDist() {
    return new Promise((resolve) => {
        const server = createServer((req, res) => {
            const url = new URL(req.url ?? "/", "http://127.0.0.1");
            let path = url.pathname === "/" ? "/index.html" : url.pathname;
            const filePath = join(dist, path);
            if (!filePath.startsWith(dist) || !existsSync(filePath)) {
                res.writeHead(404);
                res.end("missing");
                return;
            }
            res.writeHead(200, {
                "Content-Type": mime[extname(filePath)] ?? "application/octet-stream",
            });
            res.end(readFileSync(filePath));
        });
        server.listen(0, "127.0.0.1", () => {
            const address = server.address();
            resolve({ server, port: address.port });
        });
    });
}

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

const pairedContact = {
    ...contact,
    id: "c2",
    label: "Боря",
    pair_id: "p1",
    paired: true,
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

const sharedPending = {
    id: "r2",
    category: "apology",
    status: "proposed",
    text: "извиняться спокойно",
    shared: true,
    needs_my_approval: true,
    created_at: "2026-10-04T12:00:00.000Z",
    effective_since: null,
    has_pending_edit: false,
};

const suggestion = {
    id: "s1",
    category: "apology",
    text: "извиняться спокойно",
    source: "decode",
    firmness: "soft",
    created_at: "2026-10-04T12:00:00.000Z",
};

const privacyTexts = {
    export: {
        description:
            "Выгрузка содержит контакты, согласия, правила, предложения правил и историю выбора тона. Файл скачивается один раз по короткой ссылке и не хранится на сервере.",
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
        description:
            "Отзыв согласий остановит обработку сообщений. Сохранённые правила и контакты останутся, пока вы не удалите аккаунт.",
        confirm:
            "Отзыв согласий остановит обработку сообщений. Сохранённые правила и контакты останутся, пока вы не удалите аккаунт. Подтвердите отзыв.",
    },
    delete: {
        description:
            "Удаление сотрёт ваш аккаунт, согласия, контакты и ваши правила. Общие правила, которые написал партнёр, останутся у него. Это нельзя отменить.",
        confirm:
            "Удаление сотрёт ваш аккаунт, согласия, контакты и ваши правила. Общие правила, которые написал партнёр, останутся у него. Это нельзя отменить.",
    },
    leave_pair: {
        description:
            "Выход из общего свода. Каждый сохранит только правила, сформулированные им; общий свод будет удалён.",
        confirm:
            "Выйти из общего свода? Каждый сохранит только правила, сформулированные им; общий свод будет удалён.",
    },
};

async function installTelegram(page, scheme) {
    await page.route("https://telegram.org/**", (route) => route.abort());
    await page.addInitScript((colorScheme) => {
        const theme =
            colorScheme === "dark"
                ? {
                      bg_color: "#17212b",
                      text_color: "#f5f5f5",
                      hint_color: "#708499",
                      button_color: "#2aabee",
                      button_text_color: "#ffffff",
                      secondary_bg_color: "#232e3c",
                      link_color: "#62b0e8",
                      destructive_text_color: "#e53935",
                  }
                : {
                      bg_color: "#ffffff",
                      text_color: "#1a1a1a",
                      hint_color: "#6b6b6b",
                      button_color: "#2481cc",
                      button_text_color: "#ffffff",
                      secondary_bg_color: "#f4f4f5",
                      link_color: "#2481cc",
                      destructive_text_color: "#d32f2f",
                  };
        Object.defineProperty(window, "Telegram", {
            configurable: true,
            value: {
                WebApp: {
                    initData: "query_id=shot",
                    initDataUnsafe: {},
                    colorScheme,
                    themeParams: theme,
                    BackButton: {
                        show() {},
                        hide() {},
                        onClick() {},
                        offClick() {},
                    },
                    HapticFeedback: { notificationOccurred() {} },
                    ready() {},
                    expand() {},
                    close() {},
                    downloadFile(_opts, cb) {
                        cb?.(true);
                    },
                    showConfirm(_m, cb) {
                        cb?.(false);
                    },
                    onEvent() {},
                    offEvent() {},
                },
            },
        });
    }, scheme);
}

async function installTelegramWithStartParam(page, scheme, startParam) {
    await installTelegram(page, scheme);
    await page.addInitScript((param) => {
        const webApp = window.Telegram?.WebApp;
        if (webApp) {
            webApp.initDataUnsafe = { start_param: param };
        }
    }, startParam);
}

async function mockApi(page, mode) {
    await page.route("**/api/v1/**", async (route) => {
        const url = new URL(route.request().url());
        const path = url.pathname;
        if (mode === "onboarding-age" && path === "/api/v1/me") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    ...meDone,
                    onboarding_step: "age",
                    account_exists: false,
                    active_contact_id: null,
                }),
            });
            return;
        }
        if (mode === "onboarding-consent" && path === "/api/v1/me") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    ...meDone,
                    onboarding_step: "consent",
                    consent_kind: "personal_data",
                    consent_version: "1",
                    account_exists: true,
                    active_contact_id: null,
                }),
            });
            return;
        }
        if (path.startsWith("/api/v1/consents/") && path.endsWith("/document")) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    kind: "personal_data",
                    version: "1",
                    text: "Текст согласия на обработку персональных данных (синтетический).",
                }),
            });
            return;
        }
        if (path === "/api/v1/invites/resolve" && route.request().method() === "POST") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({ expires_at: "2026-10-14T12:00:00.000Z" }),
            });
            return;
        }
        if (mode === "gate-unauthorized" && path === "/api/v1/me") {
            await route.fulfill({
                status: 401,
                contentType: "application/json",
                body: JSON.stringify({ code: "unauthorized", message: "no" }),
            });
            return;
        }
        if (path === "/api/v1/me") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify(meDone),
            });
            return;
        }
        if (path === "/api/v1/privacy/texts") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify(privacyTexts),
            });
            return;
        }
        if (path === "/api/v1/contacts") {
            const contacts = mode === "paired" ? [contact, pairedContact] : [contact];
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({ contacts }),
            });
            return;
        }
        if (path.endsWith("/rules")) {
            const rules = mode === "paired" ? [rule, sharedPending] : [rule];
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({ rules }),
            });
            return;
        }
        if (path.endsWith("/suggestions")) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({ suggestions: [suggestion] }),
            });
            return;
        }
        if (path.endsWith("/invite") && route.request().method() === "POST") {
            await route.fulfill({
                status: 201,
                contentType: "application/json",
                body: JSON.stringify({
                    link: "https://t.me/test_bot?startapp=inv_demo",
                    expires_at: "2026-10-08T12:00:00.000Z",
                }),
            });
            return;
        }
        if (path === "/api/v1/me/export") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    download_url: "https://miniapp.test/api/v1/downloads/tok",
                    expires_at: "2026-10-07T12:00:00.000Z",
                }),
            });
            return;
        }
        if (path === "/api/v1/me/consents/revoke" || path === "/api/v1/me/delete") {
            await route.fulfill({ status: 204, body: "" });
            return;
        }
        if (path === "/api/v1/decode") {
            if (mode === "decode-streaming") {
                const body =
                    'event: analysis\ndata: {"chunk":"Собеседник звучит раздражённо, "}\n\n' +
                    'event: analysis\ndata: {"chunk":"но суть просьбы можно сохранить."}\n\n';
                await route.fulfill({
                    status: 200,
                    contentType: "text/event-stream",
                    headers: { "Cache-Control": "no-store" },
                    body,
                });
                return;
            }
            if (mode === "decode-crisis") {
                await route.fulfill({
                    status: 200,
                    contentType: "text/event-stream",
                    headers: { "Cache-Control": "no-store" },
                    body: 'event: crisis\ndata: {"lead":"Сейчас важнее живой человек рядом, а не текст. Если вам или кому-то рядом угрожает опасность — обратитесь за помощью. Ниже — контакты служб.","resources":["Телефон доверия: 8-800-2000-122"]}\n\n',
                });
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-store" },
                body:
                    'event: analysis\ndata: {"chunk":"Собеседник звучит раздражённо, но суть просьбы можно сохранить."}\n\n' +
                    'event: completed\ndata: {"safety":"ok","variants":[{"firmness":"gentle","text":"Давай спокойно разберём, что важно каждому.","insert_query":"p_demo"},{"firmness":"firm","text":"Мне важно продолжить разговор без повышения тона.","insert_query":null}],"applied_rules":[{"category":"other","text":"не повышать голос","effective_since":"2026-10-03T12:00:00.000Z"}],"applied_rule_template":"Учтено правило от {date}: «{text}»","rule_source_token":"tok"}\n\n',
            });
            return;
        }
        await route.fulfill({
            status: 404,
            contentType: "application/json",
            body: JSON.stringify({ code: "not_found", message: "x" }),
        });
    });
}

async function shot(page, name) {
    await page.screenshot({ path: join(outDir, `${name}.png`), fullPage: true });
}

async function captureScheme(browser, baseUrl, scheme) {
    const age = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(age, scheme);
    await mockApi(age, "onboarding-age");
    await age.goto(baseUrl, { waitUntil: "networkidle" });
    await age.waitForSelector("text=Подтверждение возраста");
    await age.waitForSelector("text=Мне есть 18");
    await shot(age, `${scheme}-onboarding-age`);
    await age.close();

    const consent = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(consent, scheme);
    await mockApi(consent, "onboarding-consent");
    await consent.goto(baseUrl, { waitUntil: "networkidle" });
    await consent.waitForSelector("text=Согласия");
    await consent.waitForSelector("text=Что мы храним");
    await consent.waitForSelector("text=via @бот");
    await shot(consent, `${scheme}-onboarding-consent`);
    await consent.close();

    const invite = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegramWithStartParam(invite, scheme, "inv_demo");
    await mockApi(invite, "app");
    await invite.goto(baseUrl, { waitUntil: "networkidle" });
    await invite.waitForSelector("text=Приглашение в общий свод");
    await invite.waitForSelector("text=Принять приглашение");
    await shot(invite, `${scheme}-invite`);
    await invite.close();

    const unauthorized = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(unauthorized, scheme);
    await mockApi(unauthorized, "gate-unauthorized");
    await unauthorized.goto(baseUrl, { waitUntil: "networkidle" });
    await unauthorized.waitForSelector("text=Откройте приложение заново");
    await shot(unauthorized, `${scheme}-gate-unauthorized`);
    await unauthorized.close();

    const app = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(app, scheme);
    await mockApi(app, "app");
    await app.goto(baseUrl, { waitUntil: "networkidle" });
    await app.waitForSelector("text=Аня");
    await shot(app, `${scheme}-contacts`);
    await app.getByRole("button", { name: /Аня/ }).click();
    await app.waitForSelector("text=не повышать голос");
    await shot(app, `${scheme}-contact-detail`);
    await app.getByRole("button", { name: "Добавить правило" }).click();
    await app.waitForSelector("text=Новое правило");
    await shot(app, `${scheme}-add-rule`);
    await app.close();

    const unpairedInvite = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(unpairedInvite, scheme);
    await mockApi(unpairedInvite, "app");
    await unpairedInvite.goto(baseUrl, { waitUntil: "networkidle" });
    await unpairedInvite.getByRole("button", { name: /Аня/ }).click();
    await unpairedInvite.waitForSelector("text=Пригласить в общий свод");
    await unpairedInvite.getByRole("button", { name: "Пригласить в общий свод" }).click();
    await unpairedInvite.waitForSelector("text=https://t.me/test_bot?startapp=inv_demo");
    await shot(unpairedInvite, `${scheme}-contact-invite`);
    await unpairedInvite.close();

    const paired = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(paired, scheme);
    await mockApi(paired, "paired");
    await paired.goto(baseUrl, { waitUntil: "networkidle" });
    await paired.getByRole("button", { name: /Боря/ }).click();
    await paired.waitForSelector("text=Общий свод");
    await paired.waitForSelector("text=Личные правила");
    await paired.waitForSelector("text=Общие правила");
    await paired.waitForSelector("text=Подтвердить");
    await paired.waitForSelector("text=Выйти из общего свода");
    await shot(paired, `${scheme}-contact-paired`);
    await paired.close();

    const privacy = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(privacy, scheme);
    await mockApi(privacy, "app");
    await privacy.goto(baseUrl, { waitUntil: "networkidle" });
    await privacy.waitForSelector("text=Аня");
    await privacy.getByRole("button", { name: "Приватность" }).click();
    await privacy.waitForSelector("text=Выгрузить данные");
    await privacy.waitForSelector("text=Отозвать согласия");
    await privacy.waitForSelector("text=Удалить аккаунт");
    await shot(privacy, `${scheme}-privacy`);
    await privacy.evaluate(() => {
        window.Telegram.WebApp.showConfirm = (_m, cb) => {
            cb?.(true);
        };
    });
    await privacy.getByRole("button", { name: "Удалить аккаунт" }).click();
    await privacy.waitForSelector("text=Удалить навсегда");
    await shot(privacy, `${scheme}-delete-confirm`);
    await privacy.getByRole("button", { name: "Удалить навсегда" }).click();
    await privacy.waitForSelector("text=Аккаунт удалён");
    await shot(privacy, `${scheme}-deleted`);
    await privacy.close();

    for (const [mode, name, waitText, fillAndSubmit] of [
        ["decode-streaming", "decode-streaming", "Собеседник звучит раздражённо", true],
        ["decode-completed", "decode-completed", "Давай спокойно разберём", true],
        ["decode-crisis", "decode-crisis", "Телефон доверия", true],
    ]) {
        const decode = await browser.newPage({ viewport: { width: 390, height: 844 } });
        await installTelegram(decode, scheme);
        await mockApi(decode, mode);
        await decode.goto(baseUrl, { waitUntil: "networkidle" });
        await decode.waitForSelector("text=Аня");
        await decode.getByRole("button", { name: "Расшифровать" }).click();
        await decode.waitForSelector("text=Вставьте сообщение");
        if (fillAndSubmit) {
            await decode.locator("textarea").fill("синтетическое входящее сообщение для скриншота");
            await decode.getByRole("button", { name: "Расшифровать" }).click();
        }
        await decode.waitForSelector(`text=${waitText}`);
        await shot(decode, `${scheme}-${name}`);
        await decode.close();
    }
}

async function main() {
    if (!existsSync(join(dist, "index.html"))) {
        throw new Error("dist/ missing — run pnpm build first");
    }
    mkdirSync(outDir, { recursive: true });
    const { server, port } = await serveDist();
    const baseUrl = `http://127.0.0.1:${port}/`;
    const browser = await chromium.launch();
    try {
        await captureScheme(browser, baseUrl, "light");
        await captureScheme(browser, baseUrl, "dark");
        console.log(`Wrote screenshots to ${outDir}`);
    } finally {
        await browser.close();
        server.close();
    }
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
