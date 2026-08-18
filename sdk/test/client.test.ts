import { describe, it, expect } from "vitest";
import { SentinelClient, MemoryTransport, generateEventId } from "../src/client.js";

function newClient() {
  const transport = new MemoryTransport();
  const client = new SentinelClient({
    transport,
    project: "checkout",
    release: "app@1.0.0",
    environment: "test",
    appRoot: "/app",
    tags: { runtime: "node20" },
  });
  return { client, transport };
}

describe("generateEventId", () => {
  it("is 32 lowercase hex chars", () => {
    const id = generateEventId();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
  });
  it("is unique across calls", () => {
    const ids = new Set(Array.from({ length: 1000 }, () => generateEventId()));
    expect(ids.size).toBe(1000);
  });
});

describe("eventFromException", () => {
  it("builds a well-formed envelope", () => {
    const { client } = newClient();
    const event = client.eventFromException(new TypeError("boom"));
    expect(event.event_id).toMatch(/^[0-9a-f]{32}$/);
    expect(event.timestamp).toBeGreaterThan(0);
    expect(event.platform).toBe("node");
    expect(event.level).toBe("error");
    expect(event.project).toBe("checkout");
    expect(event.release).toBe("app@1.0.0");
    expect(event.tags.runtime).toBe("node20");
    const exc = event.exception!.values[0];
    expect(exc.type).toBe("TypeError");
    expect(exc.value).toBe("boom");
    expect(exc.stacktrace!.frames.length).toBeGreaterThan(0);
  });
});

describe("captureException", () => {
  it("sends the envelope via the transport and returns the event id", async () => {
    const { client, transport } = newClient();
    const id = await client.captureException(new Error("fail"));
    expect(transport.sent.length).toBe(1);
    expect(transport.sent[0].event_id).toBe(id);
    expect(transport.sent[0].exception!.values[0].value).toBe("fail");
  });
});

describe("captureMessage", () => {
  it("builds a message event with the requested level", async () => {
    const { client, transport } = newClient();
    await client.captureMessage("cache miss", "warning");
    expect(transport.sent.length).toBe(1);
    expect(transport.sent[0].message).toBe("cache miss");
    expect(transport.sent[0].level).toBe("warning");
    expect(transport.sent[0].exception).toBeUndefined();
  });
});

describe("breadcrumbs", () => {
  it("attaches recorded breadcrumbs to the event", () => {
    const { client } = newClient();
    client.addBreadcrumb({ category: "http", message: "GET /cart", level: "info" });
    client.addBreadcrumb({ category: "db", message: "SELECT carts", level: "debug" });
    const event = client.eventFromException(new Error("x"));
    expect(event.breadcrumbs.length).toBe(2);
    expect(event.breadcrumbs[0].message).toBe("GET /cart");
  });

  it("caps breadcrumbs at maxBreadcrumbs", () => {
    const transport = new MemoryTransport();
    const client = new SentinelClient({ transport, maxBreadcrumbs: 3 });
    for (let i = 0; i < 10; i++) client.addBreadcrumb({ message: `c${i}` });
    const event = client.eventFromException(new Error("x"));
    expect(event.breadcrumbs.length).toBe(3);
    expect(event.breadcrumbs.map((b) => b.message)).toEqual(["c7", "c8", "c9"]);
  });
});

describe("same-cause variants produce identical exception shape", () => {
  it("differs only in line/message, not in function/file signature", () => {
    const { client } = newClient();
    const a = client.eventFromException(new Error("user 1 missing"));
    const b = client.eventFromException(new Error("user 2 missing"));
    const sig = (e: typeof a) =>
      e.exception!.values[0].stacktrace!.frames.map((f) => `${f.filename}:${f.function}`);
    // identical call site -> identical frame signature (grouping key)
    expect(sig(a)).toEqual(sig(b));
  });
});
