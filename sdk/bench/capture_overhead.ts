/**
 * Measure SDK per-capture overhead: the cost of turning a thrown Error into a
 * normalized event envelope (stack parse + frame normalization + envelope build),
 * plus the end-to-end captureException with an in-memory transport (no network).
 *
 * Writes <repo>/results/sdk_overhead.json. Run after `npm run build`.
 */

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { writeFileSync, mkdirSync } from "node:fs";
import { SentinelClient, MemoryTransport } from "../src/client.js";

const ITERATIONS = 20000;
const WARMUP = 2000;

function percentile(sorted: number[], pct: number): number {
  if (sorted.length === 0) return 0;
  const k = Math.min(sorted.length - 1, Math.max(0, Math.round((pct / 100) * (sorted.length - 1))));
  return sorted[k];
}

/** Throw from a few levels deep so the stack is realistic (not a 1-frame stack). */
function makeError(i: number): Error {
  function level3(): never {
    throw new TypeError(`Cannot read properties of undefined (reading 'id') for user ${i}`);
  }
  function level2(): void {
    level3();
  }
  function level1(): void {
    level2();
  }
  try {
    level1();
  } catch (e) {
    return e as Error;
  }
  return new Error("unreachable");
}

function measure(label: string, fn: (i: number) => void): Record<string, number> {
  for (let i = 0; i < WARMUP; i++) fn(i); // warm the JIT
  const samples = new Float64Array(ITERATIONS);
  for (let i = 0; i < ITERATIONS; i++) {
    const t0 = performance.now();
    fn(i);
    samples[i] = performance.now() - t0;
  }
  const arr = Array.from(samples).sort((a, b) => a - b);
  const mean = arr.reduce((s, v) => s + v, 0) / arr.length;
  return {
    iterations: ITERATIONS,
    mean_ms: round(mean),
    p50_ms: round(percentile(arr, 50)),
    p95_ms: round(percentile(arr, 95)),
    p99_ms: round(percentile(arr, 99)),
    max_ms: round(arr[arr.length - 1]),
  };
}

function round(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

async function main() {
  const transport = new MemoryTransport();
  const client = new SentinelClient({
    transport,
    project: "checkout",
    release: "app@1.4.2",
    environment: "production",
    appRoot: process.cwd(),
  });

  const frameCount = client.eventFromException(makeError(0)).exception!.values[0].stacktrace!
    .frames.length;

  const build = measure("eventFromException", (i) => {
    client.eventFromException(makeError(i));
  });

  // full capture path (envelope build + transport.send to memory)
  const capture = measure("captureException(memory transport)", (i) => {
    void client.captureException(makeError(i));
  });

  const result = {
    measurement:
      "per-call wall time via performance.now(); Node " +
      process.version +
      "; in-memory transport (no network). Error thrown from 3 levels deep.",
    node: process.version,
    stack_frames_per_event: frameCount,
    event_from_exception: build,
    capture_exception_memory_transport: capture,
  };

  const here = dirname(fileURLToPath(import.meta.url)); // sdk/dist/bench
  const outDir = resolve(here, "../../../results");
  mkdirSync(outDir, { recursive: true });
  const outPath = resolve(outDir, "sdk_overhead.json");
  writeFileSync(outPath, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  console.log(`\nwrote ${outPath}`);
}

void main();
