import { describe, expect, it, vi } from "vitest";

import { consumeDecodeSse, SseParser } from "./sse";

describe("SseParser", () => {
    it("splits events across chunk boundaries", () => {
        const parser = new SseParser();
        expect(parser.push('event: analysis\ndata: {"chu')).toEqual([]);
        const mid = parser.push('nk":"a"}\n\nevent: analysis\ndata: {"chunk":"b"}\n\n');
        expect(mid).toEqual([
            { event: "analysis", data: '{"chunk":"a"}' },
            { event: "analysis", data: '{"chunk":"b"}' },
        ]);
    });

    it("joins multi-line data fields", () => {
        const parser = new SseParser();
        const events = parser.push("event: note\ndata: line1\ndata: line2\n\n");
        expect(events).toEqual([{ event: "note", data: "line1\nline2" }]);
    });

    it("ignores unknown fields and comment lines inside a block", () => {
        const parser = new SseParser();
        const events = parser.push(': comment\nid: 1\nevent: analysis\ndata: {"chunk":"x"}\n\n');
        expect(events).toEqual([{ event: "analysis", data: '{"chunk":"x"}' }]);
    });
});

describe("consumeDecodeSse", () => {
    it("dispatches typed events and ignores unknown event names", async () => {
        const body = [
            'event: analysis\ndata: {"chunk":"a"}\n\n',
            "event: weird\ndata: {}\n\n",
            'event: completed\ndata: {"safety":"ok","variants":[],"applied_rules":[],"applied_rule_template":"Учтено правило от {date}: «{text}»","rule_source_token":null}\n\n',
        ].join("");
        const stream = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(new TextEncoder().encode(body));
                controller.close();
            },
        });
        const onAnalysis = vi.fn();
        const onCompleted = vi.fn();
        const onCrisis = vi.fn();
        const onRefused = vi.fn();
        const onError = vi.fn();
        await consumeDecodeSse(
            new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
            { onAnalysis, onCompleted, onCrisis, onRefused, onError },
        );
        expect(onAnalysis).toHaveBeenCalledWith("a");
        expect(onCompleted).toHaveBeenCalledTimes(1);
        expect(onCrisis).not.toHaveBeenCalled();
        expect(onError).not.toHaveBeenCalled();
    });

    it("errors when the response body is null", async () => {
        const onError = vi.fn();
        await consumeDecodeSse(new Response(null, { status: 200 }), {
            onAnalysis: vi.fn(),
            onCompleted: vi.fn(),
            onCrisis: vi.fn(),
            onRefused: vi.fn(),
            onError,
        });
        expect(onError).toHaveBeenCalledWith("generation_unavailable");
    });

    it("cancels the reader when the signal is already aborted", async () => {
        const stream = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(
                    new TextEncoder().encode('event: analysis\ndata: {"chunk":"x"}\n\n'),
                );
                controller.close();
            },
        });
        const onAnalysis = vi.fn();
        const controller = new AbortController();
        controller.abort();
        await consumeDecodeSse(
            new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
            {
                onAnalysis,
                onCompleted: vi.fn(),
                onCrisis: vi.fn(),
                onRefused: vi.fn(),
                onError: vi.fn(),
            },
            controller.signal,
        );
        expect(onAnalysis).not.toHaveBeenCalled();
    });

    it("flushes a trailing unterminated event on stream end", async () => {
        const stream = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(new TextEncoder().encode("event: refused\ndata: {}"));
                controller.close();
            },
        });
        const onRefused = vi.fn();
        await consumeDecodeSse(
            new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
            {
                onAnalysis: vi.fn(),
                onCompleted: vi.fn(),
                onCrisis: vi.fn(),
                onRefused,
                onError: vi.fn(),
            },
        );
        expect(onRefused).toHaveBeenCalledTimes(1);
    });

    it("defaults missing chunk, resources and error code fields", async () => {
        const body = [
            "event: analysis\ndata: {}\n\n",
            "event: crisis\ndata: {}\n\n",
            "event: error\ndata: {}\n\n",
        ].join("");
        const stream = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(new TextEncoder().encode(body));
                controller.close();
            },
        });
        const onAnalysis = vi.fn();
        const onCrisis = vi.fn();
        const onError = vi.fn();
        await consumeDecodeSse(
            new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
            {
                onAnalysis,
                onCompleted: vi.fn(),
                onCrisis,
                onRefused: vi.fn(),
                onError,
            },
        );
        expect(onAnalysis).toHaveBeenCalledWith("");
        expect(onCrisis).toHaveBeenCalledWith({ lead: "", resources: [] });
        expect(onError).toHaveBeenCalledWith("generation_unavailable");
    });
});

describe("SseParser.finish", () => {
    it("returns empty when the buffer is only whitespace", () => {
        const parser = new SseParser();
        parser.push("   ");
        expect(parser.finish()).toEqual([]);
    });

    it("returns null-parsed empty for data-less trailing buffer", () => {
        const parser = new SseParser();
        parser.push(": only-comment");
        expect(parser.finish()).toEqual([]);
    });
});
