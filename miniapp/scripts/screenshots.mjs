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
    created_at: "2026-10-01T12:00:00.000Z",
};

const rule = {
    id: "r1",
    category: "other",
    status: "active",
    text: "не повышать голос",
    shared: false,
    created_at: "2026-10-03T12:00:00.000Z",
    effective_since: "2026-10-03T12:00:00.000Z",
    has_pending_edit: true,
};

const suggestion = {
    id: "s1",
    category: "apology",
    text: "извиняться спокойно",
    source: "decode",
    firmness: "soft",
    created_at: "2026-10-04T12:00:00.000Z",
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

async function mockApi(page, mode) {
    await page.route("**/api/v1/**", async (route) => {
        const url = new URL(route.request().url());
        const path = url.pathname;
        if (mode === "gate-incomplete" && path === "/api/v1/me") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    ...meDone,
                    onboarding_step: "age",
                    account_exists: false,
                }),
            });
            return;
        }
        if (mode === "gate-consent" && path === "/api/v1/me") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    ...meDone,
                    onboarding_step: "consent",
                    account_exists: true,
                }),
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
        if (path === "/api/v1/contacts") {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({ contacts: [contact] }),
            });
            return;
        }
        if (path.endsWith("/rules")) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({ rules: [rule] }),
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
        if (path === "/api/v1/me/export") {
            await route.fulfill({
                status: 202,
                contentType: "application/json",
                body: JSON.stringify({ delivered_to: "bot_chat" }),
            });
            return;
        }
        if (path === "/api/v1/me/consents/revoke" || path === "/api/v1/me/delete") {
            await route.fulfill({ status: 204, body: "" });
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
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(page, scheme);
    await mockApi(page, "gate-incomplete");
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.waitForSelector("text=Завершите настройку");
    await shot(page, `${scheme}-gate-incomplete`);
    await page.close();

    const consent = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(consent, scheme);
    await mockApi(consent, "gate-consent");
    await consent.goto(baseUrl, { waitUntil: "networkidle" });
    await consent.waitForSelector("text=Нужно подтвердить согласия");
    await consent.waitForSelector("text=Выгрузить данные");
    await consent.waitForSelector("text=Удалить аккаунт");
    await shot(consent, `${scheme}-gate-consent`);
    await consent.close();

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

    const privacy = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await installTelegram(privacy, scheme);
    await mockApi(privacy, "app");
    await privacy.goto(baseUrl, { waitUntil: "networkidle" });
    await privacy.waitForSelector("text=Аня");
    await privacy.getByRole("button", { name: "Приватность" }).click();
    await privacy.waitForSelector("text=Выгрузить данные");
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
