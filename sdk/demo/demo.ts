/**
 * SDK demo app: throws a mix of errors (same-cause variants + distinct causes +
 * an auto-captured uncaught exception) and ships them to the running ingest API.
 * Then prints the grouped issues the server produced.
 *
 * Usage (with the API up on :8000):
 *   cd sdk && npm run build && SENTINEL_URL=http://localhost:8000 npm run demo
 */

import { init, captureException, addBreadcrumb } from "../src/index.js";
import { SentinelClient } from "../src/client.js";

const URL = process.env.SENTINEL_URL ?? "http://localhost:8000";
const PROJECT = process.env.SENTINEL_PROJECT ?? "checkout";

const client: SentinelClient = init({
  url: URL,
  project: PROJECT,
  release: "app@1.4.2",
  environment: "production",
  appRoot: process.cwd(),
  tags: { runtime: `node${process.versions.node.split(".")[0]}` },
  installHandlers: true,
});

// --- bug #1: repeated same-cause crash with varying user data (should group to 1 issue) ---
function renderUserCard(userId: number): void {
  const user: any = undefined;
  // differing user id in the message must NOT split the group
  addBreadcrumb({ category: "ui", message: `render card ${userId}`, level: "info" });
  void user.name; // TypeError: Cannot read properties of undefined (reading 'name')
}

function handleRequest(userId: number): void {
  renderUserCard(userId);
}

// --- bug #2: a different crash in a different file (distinct issue) ---
function formatPrice(cents: any): string {
  return cents.toFixed(2); // TypeError on undefined
}

// --- bug #3: a message-only error stream with varying shard/ms (should group) ---
async function main(): Promise<void> {
  console.log(`[demo] shipping events to ${URL} (project=${PROJECT})`);

  // Yield once so EVERY iteration below runs inside the event loop (a microtask
  // continuation), exactly like a real request handler. Without this, the very first
  // synchronous throw would carry an extra module-top entry frame and — since
  // frame-signature grouping is sensitive to leading frames (see RESULTS.md) — would
  // land in its own issue. Real captured exceptions never fire during module eval.
  await Promise.resolve();

  // 15 variants of bug #1 -> 1 issue, count 15
  for (let i = 0; i < 15; i++) {
    try {
      handleRequest(1000 + i);
    } catch (e) {
      await captureException(e as Error);
    }
  }

  // 8 variants of bug #2 -> 1 issue, count 8
  for (let i = 0; i < 8; i++) {
    try {
      formatPrice(undefined);
    } catch (e) {
      await captureException(e as Error);
    }
  }

  // 6 message-only db timeouts with differing shard + ms -> 1 issue
  for (let i = 0; i < 6; i++) {
    const shard = 1 + (i % 8);
    const ms = 1000 + i * 500;
    try {
      throw new Error(`Connection to db-shard-${shard} timed out after ${ms}ms`);
    } catch (e) {
      await captureException(e as Error);
    }
  }

  // one auto-captured uncaught exception (proves the installed handler works)
  await new Promise<void>((r) => {
    process.nextTick(() => {
      // handler captures this without exiting; give it a tick to fire
      setImmediate(r);
      throw new RangeError("uncaught: array index out of range");
    });
  });
  await new Promise((r) => setTimeout(r, 150)); // let the async capture flush

  // --- read back what the server grouped ---
  const health = await (await fetch(`${URL}/health`)).json();
  const issues = await (
    await fetch(`${URL}/issues?project=${PROJECT}&sort=count&order=desc`)
  ).json();

  console.log(`\n[demo] server health:`, health);
  console.log(`[demo] grouped issues (${issues.count}):`);
  for (const issue of issues.issues) {
    console.log(
      `  #${issue.id}  x${issue.count}  [${issue.level}]  ${issue.title}  <- ${issue.culprit}`,
    );
  }
  console.log(
    `\n[demo] shipped 30 events -> ${issues.count} grouped issues ` +
      `(dedupe ${(30 / Math.max(1, issues.count)).toFixed(1)}x)`,
  );
}

void main().then(() => setTimeout(() => process.exit(0), 100));
