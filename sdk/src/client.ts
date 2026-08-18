/** The Sentinel client: capture exceptions/messages -> envelope -> transport. */

import { randomBytes } from "node:crypto";
import { framesFromError } from "./stacktrace.js";
import type {
  Breadcrumb,
  EventEnvelope,
  ExceptionInterface,
  Level,
  SentinelOptions,
  Transport,
} from "./types.js";

const DEFAULT_MAX_BREADCRUMBS = 50;

/** Default transport: POST the envelope as JSON to `${url}/api/store` using fetch. */
export class HttpTransport implements Transport {
  constructor(private readonly url: string) {}

  async send(envelope: EventEnvelope): Promise<void> {
    const res = await fetch(`${this.url.replace(/\/+$/, "")}/api/store`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    });
    if (!res.ok) {
      throw new Error(`ingest rejected event: HTTP ${res.status}`);
    }
  }
}

/** A transport that keeps envelopes in memory — used by tests and the overhead bench. */
export class MemoryTransport implements Transport {
  public readonly sent: EventEnvelope[] = [];
  async send(envelope: EventEnvelope): Promise<void> {
    this.sent.push(envelope);
  }
}

export function generateEventId(): string {
  return randomBytes(16).toString("hex"); // 32 hex chars
}

export class SentinelClient {
  private readonly transport: Transport;
  private readonly breadcrumbs: Breadcrumb[] = [];
  private readonly maxBreadcrumbs: number;
  private installed = false;

  constructor(private readonly options: SentinelOptions = {}) {
    this.maxBreadcrumbs = options.maxBreadcrumbs ?? DEFAULT_MAX_BREADCRUMBS;
    this.transport =
      options.transport ?? new HttpTransport(options.url ?? "http://localhost:8000");
  }

  addBreadcrumb(crumb: Omit<Breadcrumb, "timestamp"> & { timestamp?: number }): void {
    this.breadcrumbs.push({ timestamp: crumb.timestamp ?? Date.now() / 1000, ...crumb });
    while (this.breadcrumbs.length > this.maxBreadcrumbs) this.breadcrumbs.shift();
  }

  /** Build a full event envelope from an Error (the hot path measured for overhead). */
  eventFromException(err: Error, level: Level = "error"): EventEnvelope {
    const exception: ExceptionInterface = {
      values: [
        {
          type: err.name || "Error",
          value: err.message || "",
          stacktrace: { frames: framesFromError(err, this.options.appRoot) },
        },
      ],
    };
    return this.baseEvent(level, { exception });
  }

  eventFromMessage(message: string, level: Level = "info"): EventEnvelope {
    return this.baseEvent(level, { message });
  }

  private baseEvent(level: Level, extra: Partial<EventEnvelope>): EventEnvelope {
    return {
      event_id: generateEventId(),
      timestamp: Date.now() / 1000,
      platform: "node",
      level,
      project: this.options.project ?? "default",
      release: this.options.release,
      environment: this.options.environment,
      server_name: this.options.serverName,
      tags: { ...(this.options.tags ?? {}) },
      breadcrumbs: this.breadcrumbs.slice(),
      extra: {},
      ...extra,
    };
  }

  async captureException(err: Error, level: Level = "error"): Promise<string> {
    const event = this.eventFromException(err, level);
    await this.transport.send(event);
    return event.event_id;
  }

  async captureMessage(message: string, level: Level = "info"): Promise<string> {
    const event = this.eventFromMessage(message, level);
    await this.transport.send(event);
    return event.event_id;
  }

  /** Install process-level handlers for uncaught exceptions + unhandled rejections. */
  installHandlers(): void {
    if (this.installed) return;
    this.installed = true;
    process.on("uncaughtException", (err: Error) => {
      void this.captureException(err, "fatal");
    });
    process.on("unhandledRejection", (reason: unknown) => {
      const err = reason instanceof Error ? reason : new Error(String(reason));
      void this.captureException(err, "error");
    });
  }
}
