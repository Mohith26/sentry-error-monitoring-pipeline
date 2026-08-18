/** Public SDK surface: a Sentry-style functional API over a module-global client. */

import { SentinelClient } from "./client.js";
import type { Breadcrumb, Level, SentinelOptions } from "./types.js";

export { SentinelClient, HttpTransport, MemoryTransport, generateEventId } from "./client.js";
export { parseStackString, framesFromError } from "./stacktrace.js";
export type * from "./types.js";

let globalClient: SentinelClient | null = null;

/** Initialize the global client (like Sentry.init). Installs handlers if requested. */
export function init(options: SentinelOptions = {}): SentinelClient {
  globalClient = new SentinelClient(options);
  if (options.installHandlers) globalClient.installHandlers();
  return globalClient;
}

function requireClient(): SentinelClient {
  if (!globalClient) {
    throw new Error("Sentinel not initialized — call init() first");
  }
  return globalClient;
}

export function captureException(err: Error, level: Level = "error"): Promise<string> {
  return requireClient().captureException(err, level);
}

export function captureMessage(message: string, level: Level = "info"): Promise<string> {
  return requireClient().captureMessage(message, level);
}

export function addBreadcrumb(
  crumb: Omit<Breadcrumb, "timestamp"> & { timestamp?: number },
): void {
  requireClient().addBreadcrumb(crumb);
}
