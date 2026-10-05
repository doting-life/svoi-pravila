import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";

import { resetTelegramAdapterGuardsForTests } from "../telegram/webapp";

afterEach(() => {
    resetTelegramAdapterGuardsForTests();
    document.documentElement.removeAttribute("data-color-scheme");
    document.documentElement.removeAttribute("style");
    Reflect.deleteProperty(window, "Telegram");
});
