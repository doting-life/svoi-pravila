import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ru } from "./localization/ru";
import type { TelegramAdapter } from "./telegram/webapp";

function fakeAdapter(overrides: Partial<TelegramAdapter> = {}): TelegramAdapter {
    return {
        initData: "",
        colorScheme: "light",
        themeCssVariables: {},
        isInsideTelegram: false,
        ready: vi.fn(),
        expand: vi.fn(),
        ...overrides,
    };
}

describe("App", () => {
    it("renders the open-from-Telegram message outside Telegram", () => {
        render(<App adapter={fakeAdapter()} />);
        expect(screen.getByRole("heading", { name: ru.appTitle })).toBeInTheDocument();
        expect(screen.getByText(ru.openFromTelegram)).toBeInTheDocument();
    });

    it("renders the placeholder and calls ready/expand inside Telegram", () => {
        const adapter = fakeAdapter({
            initData: "query_id=1",
            isInsideTelegram: true,
            themeCssVariables: { "--tg-bg-color": "#111111" },
            colorScheme: "dark",
        });
        render(<App adapter={adapter} />);
        expect(screen.getByText(ru.placeholder)).toBeInTheDocument();
        expect(adapter.ready).toHaveBeenCalledTimes(1);
        expect(adapter.expand).toHaveBeenCalledTimes(1);
        expect(document.documentElement.dataset.colorScheme).toBe("dark");
    });
});
