import { describe, it, expect } from "vitest";
import { parseStackString, framesFromError } from "../src/stacktrace.js";

const SAMPLE = `TypeError: Cannot read properties of undefined (reading 'name')
    at renderUserCard (/app/src/components/UserCard.js:48:17)
    at renderProfile (/app/src/components/Profile.js:112:9)
    at handleRequest (/app/src/server/handler.js:30:5)
    at Layer.handle (/app/node_modules/express/lib/router/layer.js:95:5)
    at processTicksAndRejections (node:internal/process/task_queues:95:5)`;

describe("parseStackString", () => {
  it("parses function/file/lineno/colno for each frame", () => {
    const frames = parseStackString(SAMPLE, "/app");
    // 5 frames parsed (header line ignored)
    expect(frames.length).toBe(5);
    // reversed -> crashing frame (renderUserCard) is LAST
    const top = frames[frames.length - 1];
    expect(top.function).toBe("renderUserCard");
    expect(top.filename).toBe("src/components/UserCard.js");
    expect(top.lineno).toBe(48);
    expect(top.colno).toBe(17);
    expect(top.in_app).toBe(true);
  });

  it("strips the app root to a relative filename", () => {
    const frames = parseStackString(SAMPLE, "/app");
    const handler = frames.find((f) => f.function === "handleRequest")!;
    expect(handler.filename).toBe("src/server/handler.js");
  });

  it("marks node_modules frames as not in_app", () => {
    const frames = parseStackString(SAMPLE, "/app");
    const lib = frames.find((f) => f.filename?.includes("node_modules"))!;
    expect(lib.in_app).toBe(false);
  });

  it("marks node: internal frames as not in_app", () => {
    const frames = parseStackString(SAMPLE, "/app");
    const internal = frames.find((f) => f.filename?.startsWith("node:"))!;
    expect(internal).toBeTruthy();
    expect(internal.in_app).toBe(false);
  });

  it("handles anonymous frames without a function name", () => {
    const stack = `Error: boom
    at /app/src/a.js:5:1
    at Object.<anonymous> (/app/src/b.js:1:1)`;
    const frames = parseStackString(stack, "/app");
    expect(frames.length).toBe(2);
    const anon = frames.find((f) => f.filename === "src/a.js")!;
    expect(anon.function).toBe("<anonymous>");
    const objAnon = frames.find((f) => f.filename === "src/b.js")!;
    expect(objAnon.function).toBe("<anonymous>");
  });

  it("parses async frame markers", () => {
    const stack = `Error: x
    at async processPayment (/app/src/pay.js:8:3)`;
    const frames = parseStackString(stack, "/app");
    expect(frames[0].function).toBe("async processPayment");
    expect(frames[0].filename).toBe("src/pay.js");
  });

  it("returns [] for an error with no stack", () => {
    const e = new Error("no stack");
    // @ts-expect-error deliberately remove the stack
    e.stack = undefined;
    expect(framesFromError(e)).toEqual([]);
  });

  it("parses a real runtime Error stack into non-empty frames", () => {
    function boom() {
      throw new TypeError("kaboom");
    }
    try {
      boom();
    } catch (e) {
      const frames = framesFromError(e as Error);
      expect(frames.length).toBeGreaterThan(0);
      const last = frames[frames.length - 1];
      expect(last.lineno).toBeGreaterThan(0);
      expect(typeof last.function).toBe("string");
    }
  });
});
