/** Minimal SSE parser over a byte stream (no EventSource; supports Authorization). */

export type SseEvent = {
    readonly event: string;
    readonly data: string;
};

/**
 * Feed incremental text chunks into the parser; returns complete events.
 * Unknown / incomplete trailing data stays in the buffer until more arrives.
 */
export class SseParser {
    private buffer = "";

    push(chunk: string): SseEvent[] {
        this.buffer += chunk;
        const events: SseEvent[] = [];
        let separator = this.buffer.indexOf("\n\n");
        while (separator !== -1) {
            const raw = this.buffer.slice(0, separator);
            this.buffer = this.buffer.slice(separator + 2);
            const parsed = parseBlock(raw);
            if (parsed !== null) {
                events.push(parsed);
            }
            separator = this.buffer.indexOf("\n\n");
        }
        return events;
    }

    finish(): SseEvent[] {
        if (this.buffer.trim().length === 0) {
            return [];
        }
        const parsed = parseBlock(this.buffer);
        this.buffer = "";
        return parsed === null ? [] : [parsed];
    }
}

function parseBlock(raw: string): SseEvent | null {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith("event:")) {
            event = line.slice("event:".length).trim();
            continue;
        }
        if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).replace(/^ /, ""));
            continue;
        }
        // Ignore comments and unknown fields (including unknown event names later).
    }
    if (dataLines.length === 0) {
        return null;
    }
    return { event, data: dataLines.join("\n") };
}

export type DecodeStreamHandlers = {
    readonly onAnalysis: (chunk: string) => void;
    readonly onCompleted: (payload: DecodeCompletedPayload) => void;
    readonly onCrisis: (resources: readonly string[]) => void;
    readonly onRefused: () => void;
    readonly onError: (code: string) => void;
};

export type DecodeCompletedPayload = {
    readonly safety: string;
    readonly variants: readonly {
        readonly firmness: string;
        readonly text: string;
        readonly insert_query: string | null;
    }[];
    readonly applied_rules: readonly {
        readonly category: string;
        readonly text: string;
        readonly effective_since: string;
    }[];
    readonly rule_source_token: string | null;
};

/** Consume a fetch Response body as SSE and dispatch typed decode events. */
export async function consumeDecodeSse(
    response: Response,
    handlers: DecodeStreamHandlers,
    signal?: AbortSignal,
): Promise<void> {
    if (response.body === null) {
        handlers.onError("generation_unavailable");
        return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    try {
        for (;;) {
            if (signal !== undefined && signal.aborted) {
                await reader.cancel();
                return;
            }
            const { done, value } = await reader.read();
            if (done) {
                for (const event of parser.finish()) {
                    dispatchDecodeEvent(event, handlers);
                }
                return;
            }
            const text = decoder.decode(value, { stream: true });
            for (const event of parser.push(text)) {
                dispatchDecodeEvent(event, handlers);
            }
        }
    } finally {
        reader.releaseLock();
    }
}

function dispatchDecodeEvent(event: SseEvent, handlers: DecodeStreamHandlers): void {
    if (event.event === "analysis") {
        const body = JSON.parse(event.data) as { chunk?: string };
        handlers.onAnalysis(body.chunk ?? "");
        return;
    }
    if (event.event === "completed") {
        handlers.onCompleted(JSON.parse(event.data) as DecodeCompletedPayload);
        return;
    }
    if (event.event === "crisis") {
        const body = JSON.parse(event.data) as { resources?: string[] };
        handlers.onCrisis(body.resources ?? []);
        return;
    }
    if (event.event === "refused") {
        handlers.onRefused();
        return;
    }
    if (event.event === "error") {
        const body = JSON.parse(event.data) as { code?: string };
        handlers.onError(body.code ?? "generation_unavailable");
    }
    // Unknown events are ignored.
}
