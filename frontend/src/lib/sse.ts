import type { SseEvent } from "./types";

/** Incremental SSE parser: feed text chunks, get complete events. */
export class SseParser {
  private buf = "";
  push(chunk: string): SseEvent[] {
    this.buf += chunk.replace(/\r\n/g, "\n");
    const out: SseEvent[] = [];
    let i: number;
    while ((i = this.buf.indexOf("\n\n")) >= 0) {
      const block = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 2);
      const ev = parseBlock(block);
      if (ev) out.push(ev);
    }
    return out;
  }
}

function parseBlock(block: string): SseEvent | null {
  let event = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!event) return null;
  try {
    return { event, data: data.length ? JSON.parse(data.join("\n")) : {} } as SseEvent;
  } catch {
    return null;
  }
}

/** Read an SSE response body, calling onEvent for each event. */
export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (e: SseEvent) => void): Promise<void> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  const parser = new SseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const e of parser.push(dec.decode(value, { stream: true }))) onEvent(e);
  }
  for (const e of parser.push("\n\n")) onEvent(e);
}
