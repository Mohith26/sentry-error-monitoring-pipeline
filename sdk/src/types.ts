/** Event-envelope + SDK option types. Mirrors the Python ingest schema. */

export type Level = "fatal" | "error" | "warning" | "info" | "debug";

export interface Frame {
  function: string | null;
  filename: string | null;
  module?: string | null;
  lineno: number | null;
  colno: number | null;
  in_app: boolean;
}

export interface StackTrace {
  frames: Frame[];
}

export interface ExceptionValue {
  type: string | null;
  value: string | null;
  stacktrace?: StackTrace;
}

export interface ExceptionInterface {
  values: ExceptionValue[];
}

export interface Breadcrumb {
  timestamp: number;
  category?: string;
  message?: string;
  level?: Level;
  data?: Record<string, unknown>;
}

export interface EventEnvelope {
  event_id: string;
  timestamp: number;
  platform: string;
  level: Level;
  project: string;
  release?: string;
  environment?: string;
  server_name?: string;
  transaction?: string;
  message?: string;
  exception?: ExceptionInterface;
  tags: Record<string, string>;
  breadcrumbs: Breadcrumb[];
  extra: Record<string, unknown>;
}

/** A transport ships an envelope somewhere. Injectable for tests/bench (no network). */
export interface Transport {
  send(envelope: EventEnvelope): Promise<void>;
}

export interface SentinelOptions {
  /** Ingest base URL, e.g. http://localhost:8000 (POSTs to `${url}/api/store`). */
  url?: string;
  project?: string;
  release?: string;
  environment?: string;
  serverName?: string;
  /** App root used to compute in-app frames + relative filenames. Default: process.cwd(). */
  appRoot?: string;
  tags?: Record<string, string>;
  maxBreadcrumbs?: number;
  /** Override the transport (tests, bench, custom sinks). */
  transport?: Transport;
  /** Auto-install process-level uncaught handlers on init. Default: false. */
  installHandlers?: boolean;
}
