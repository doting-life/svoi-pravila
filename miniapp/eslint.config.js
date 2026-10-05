import eslint from "@eslint/js";
import eslintConfigPrettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const storageBanMessage = "Browser storage APIs are forbidden in the mini-app (architecture §6).";

const storageBanRules = {
    "no-restricted-globals": [
        "error",
        {
            name: "localStorage",
            message: storageBanMessage,
        },
        {
            name: "sessionStorage",
            message: storageBanMessage,
        },
        {
            name: "indexedDB",
            message: storageBanMessage,
        },
    ],
    "no-restricted-properties": [
        "error",
        {
            object: "window",
            property: "localStorage",
            message: storageBanMessage,
        },
        {
            object: "window",
            property: "sessionStorage",
            message: storageBanMessage,
        },
        {
            object: "window",
            property: "indexedDB",
            message: storageBanMessage,
        },
        {
            object: "document",
            property: "cookie",
            message: storageBanMessage,
        },
        {
            object: "globalThis",
            property: "localStorage",
            message: storageBanMessage,
        },
        {
            object: "globalThis",
            property: "sessionStorage",
            message: storageBanMessage,
        },
        {
            object: "globalThis",
            property: "indexedDB",
            message: storageBanMessage,
        },
    ],
};

export default tseslint.config(
    {
        ignores: [
            "dist/**",
            "coverage/**",
            "src/api/schema.d.ts",
            "src/api/openapi.json",
            "eslint.config.js",
            "prettier.config.js",
            "vite.config.ts",
            "vitest.config.ts",
            "eslint-fixtures/**",
            "scripts/**",
        ],
    },
    eslint.configs.recommended,
    {
        files: ["src/**/*.{ts,tsx}"],
        extends: [...tseslint.configs.strictTypeChecked],
        languageOptions: {
            ecmaVersion: 2023,
            globals: {
                ...globals.browser,
            },
            parserOptions: {
                projectService: true,
                tsconfigRootDir: import.meta.dirname,
            },
        },
        plugins: {
            "react-hooks": reactHooks,
        },
        rules: {
            ...reactHooks.configs.recommended.rules,
            ...storageBanRules,
        },
    },
    {
        files: ["eslint-fixtures/**/*.{js,ts}"],
        languageOptions: {
            ecmaVersion: 2023,
            sourceType: "module",
            globals: {
                ...globals.browser,
            },
        },
        rules: {
            ...storageBanRules,
        },
    },
    eslintConfigPrettier,
);
