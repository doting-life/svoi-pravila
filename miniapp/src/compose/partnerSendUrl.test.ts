import { describe, expect, it } from "vitest";

import { partnerSendUrl } from "./partnerSendUrl";

describe("partnerSendUrl", () => {
    it("encodes spaces, emoji and line breaks", () => {
        expect(partnerSendUrl("alice", "hello world")).toBe(
            "https://t.me/alice?text=hello%20world",
        );
        expect(partnerSendUrl("bob", "прив 👋")).toBe(
            `https://t.me/bob?text=${encodeURIComponent("прив 👋")}`,
        );
        expect(partnerSendUrl("carol", "line1\nline2")).toBe(
            `https://t.me/carol?text=${encodeURIComponent("line1\nline2")}`,
        );
    });

    it("strips a leading @ from the username", () => {
        expect(partnerSendUrl("@dave", "hi")).toBe("https://t.me/dave?text=hi");
    });
});
