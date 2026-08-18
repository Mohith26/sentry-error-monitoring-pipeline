/**
 * Stack-trace parsing + frame normalization.
 *
 * Parses a V8 `Error.stack` string into structured frames (function/file/lineno/colno),
 * reverses them to Sentry's oldest-first order (crashing frame last), and computes
 * `in_app` + an app-relative filename against a configured app root so the server's
 * grouping is stable across machines.
 */

import type { Frame } from "./types.js";

// `    at FUNC (FILE:LINE:COL)`  or  `    at FILE:LINE:COL`
const FRAME_RE = /^\s*at\s+(?:(.+?)\s+\()?(.+?):(\d+):(\d+)\)?\s*$/;

const NODE_INTERNAL_RE = /^(node:|internal\/|events\.js|timers\.js)/;

/** Parse a raw stack string into frames, oldest-first (V8 is newest-first -> reversed). */
export function parseStackString(stack: string, appRoot?: string): Frame[] {
  const root = normalizeRoot(appRoot ?? safeCwd());
  const lines = stack.split("\n");
  const frames: Frame[] = [];
  for (const line of lines) {
    const m = FRAME_RE.exec(line);
    if (!m) continue; // header line ("Error: message") or an unparseable frame
    const rawFn = m[1];
    const rawFile = m[2];
    const lineno = parseInt(m[3], 10);
    const colno = parseInt(m[4], 10);
    frames.push(makeFrame(rawFn, rawFile, lineno, colno, root));
  }
  frames.reverse(); // oldest call first, crashing frame last
  return frames;
}

/** Parse frames from an Error. */
export function framesFromError(err: Error, appRoot?: string): Frame[] {
  if (!err || typeof err.stack !== "string") return [];
  return parseStackString(err.stack, appRoot);
}

function makeFrame(
  rawFn: string | undefined,
  rawFile: string,
  lineno: number,
  colno: number,
  root: string,
): Frame {
  let file = rawFile.trim();
  // strip file:// scheme (URLs on some Node/loader paths)
  file = file.replace(/^file:\/\//, "");

  const isNodeInternal = NODE_INTERNAL_RE.test(file);
  const isNodeModules = file.includes("node_modules");

  let relative = file;
  if (!isNodeInternal && root && file.startsWith(root)) {
    relative = file.slice(root.length).replace(/^[\\/]+/, "");
  }

  const inApp = !isNodeInternal && !isNodeModules && (file.startsWith(root) || !isAbsolute(file));

  return {
    function: normalizeFunction(rawFn),
    filename: relative,
    module: null,
    lineno,
    colno,
    in_app: inApp,
  };
}

function normalizeFunction(fn: string | undefined): string {
  if (!fn) return "<anonymous>";
  const f = fn.trim();
  if (f === "" || f === "<anonymous>" || f === "Object.<anonymous>") return "<anonymous>";
  return f;
}

function isAbsolute(p: string): boolean {
  return p.startsWith("/") || /^[A-Za-z]:[\\/]/.test(p);
}

function normalizeRoot(root: string): string {
  return root.replace(/[\\/]+$/, "");
}

function safeCwd(): string {
  try {
    return process.cwd();
  } catch {
    return "";
  }
}
