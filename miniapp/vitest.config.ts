import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        css: { include: [/styles\/tokens\.css\?raw$/] },
        globals: true,
        setupFiles: ["./src/test/setup.ts"],
        include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
        coverage: {
            provider: "v8",
            reportsDirectory: "./coverage",
            reporter: ["text", "text-summary"],
            include: ["src/**/*.{ts,tsx}"],
            exclude: [
                "src/api/schema.d.ts",
                "src/api/openapi.json",
                "src/test/**",
                "src/**/*.test.ts",
                "src/**/*.test.tsx",
                "src/main.tsx",
            ],
            thresholds: {
                lines: 90,
                branches: 90,
                functions: 90,
                statements: 90,
                "src/screens/InlineComposeApp.tsx": {
                    lines: 100,
                    branches: 100,
                    functions: 100,
                    statements: 100,
                },
                "src/telegram/webapp.ts": {
                    lines: 100,
                    branches: 100,
                    functions: 100,
                    statements: 100,
                },
                "src/api/client.ts": {
                    lines: 100,
                    branches: 100,
                    functions: 100,
                    statements: 100,
                },
            },
        },
    },
});
