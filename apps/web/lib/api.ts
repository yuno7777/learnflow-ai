/**
 * Typed API client with single-flight refresh token rotation and custom SSE streaming.
 *
 * ===============================================================================
 * WHAT THIS FILE IS RESPONSIBLE FOR:
 * ===============================================================================
 * This file is the primary HTTP client used by the Next.js frontend to talk to FastAPI:
 * 1. `tokens` Helper: Manages JWT access tokens, refresh tokens, and active organization
 *    context stored in browser `localStorage`.
 * 2. Single-Flight Token Refresh (`refreshInFlight`):
 *    - When an access token expires (401 Unauthorized), the client attempts a refresh.
 *    - Because refresh tokens are strictly single-use on the backend, multiple concurrent
 *      401 requests share a single in-flight refresh promise, preventing duplicate calls
 *      that would trigger replay detection.
 * 3. `request<T>()`: Generic fetch wrapper that auto-attaches `Authorization: Bearer <token>`,
 *    `X-Organization-Id: <uuid>`, serializes JSON bodies, and unwraps `ApiErrorBody`.
 * 4. `subscribe()`: Custom fetch-based Server-Sent Events (SSE) consumer:
 *    - Standard browser `EventSource` cannot send custom `Authorization: Bearer` headers.
 *    - `subscribe()` uses native `fetch()` with a `ReadableStream` reader, decoding text
 *      chunks and parsing `\n\n` event boundaries without exposing tokens in URL query strings.
 *
 * ===============================================================================
 * FLOW (API Request with Auto-Refresh):
 * ===============================================================================
 * Frontend Component (e.g. `CoursesView`)
 *   ──► `api.get("/api/v1/courses")`
 *   ──► If 401 returned:
 *       1. `refreshTokens()` (Acquires `refreshInFlight` lock)
 *       2. POST `/api/v1/auth/refresh`
 *       3. Save new `TokenPair` to `localStorage`
 *       4. Re-issue original request with new access token.
 */

import type { ApiErrorBody, TokenPair } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_KEY = "learnflow.access";
const REFRESH_KEY = "learnflow.refresh";
const ORG_KEY = "learnflow.org";

/**
 * Standardized client-side API error class wrapping backend error JSON envelopes.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * LocalStorage token persistence helper.
 */
export const tokens = {
  get access() {
    return typeof window === "undefined" ? null : localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return typeof window === "undefined" ? null : localStorage.getItem(REFRESH_KEY);
  },
  get organizationId() {
    return typeof window === "undefined" ? null : localStorage.getItem(ORG_KEY);
  },
  save(pair: TokenPair) {
    localStorage.setItem(ACCESS_KEY, pair.access_token);
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
    localStorage.setItem(ORG_KEY, pair.organization_id);
  },
  setOrganization(id: string) {
    localStorage.setItem(ORG_KEY, id);
  },
  clear() {
    [ACCESS_KEY, REFRESH_KEY, ORG_KEY].forEach((k) => localStorage.removeItem(k));
  },
};

// Singleton in-flight promise preventing concurrent token refresh stampedes
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Redeems the refresh token for a new access/refresh token pair.
 * Concurrent callers share a single in-flight HTTP request.
 */
async function refreshTokens(): Promise<boolean> {
  const refresh = tokens.refresh;
  if (!refresh) return false;
  
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) return false;
      tokens.save((await response.json()) as TokenPair);
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshInFlight = null), 0);
    }
  })();
  return refreshInFlight;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  raw?: BodyInit;
  skipAuth?: boolean;
  retryOn401?: boolean;
}

/**
 * Core HTTP request handler with authentication, query string formatting, and error parsing.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, raw, skipAuth, retryOn401 = true, ...init } = options;

  // 1. Format URL and query parameters
  const url = new URL(`${API_URL}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  // 2. Attach Authorization and Organization context headers
  const headers = new Headers(init.headers);
  if (!skipAuth && tokens.access) {
    headers.set("Authorization", `Bearer ${tokens.access}`);
    const org = tokens.organizationId;
    if (org) headers.set("X-Organization-Id", org);
  }
  if (body !== undefined && !raw) headers.set("Content-Type", "application/json");

  // 3. Dispatch fetch call
  const response = await fetch(url.toString(), {
    ...init,
    headers,
    body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  // 4. Handle 401 Unauthorized with automatic refresh retry
  if (response.status === 401 && retryOn401 && !skipAuth && (await refreshTokens())) {
    return request<T>(path, { ...options, retryOn401: false });
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  // 5. Throw formatted ApiError on non-2xx responses
  if (!response.ok) {
    const error = (payload ?? {}) as ApiErrorBody;
    throw new ApiError(
      response.status,
      error.error?.code ?? "http_error",
      error.error?.message ?? response.statusText,
      error.error?.details ?? {},
      error.request_id ?? null,
    );
  }
  return payload as T;
}

/**
 * Ergonomic HTTP convenience methods.
 */
export const api = {
  get: <T>(path: string, query?: RequestOptions["query"]) =>
    request<T>(path, { method: "GET", query }),
  post: <T>(path: string, body?: unknown, query?: RequestOptions["query"]) =>
    request<T>(path, { method: "POST", body, query }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File, query?: RequestOptions["query"]) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>(path, { method: "POST", raw: form, query });
  },
};

/**
 * Consumes a Server-Sent Events (SSE) stream using fetch and ReadableStream reader.
 *
 * Why fetch instead of EventSource?
 * Browser `EventSource` cannot send custom `Authorization: Bearer` headers.
 * Using fetch ensures auth tokens are never leaked into server query string access logs.
 *
 * @param path - API SSE endpoint (e.g. `/api/v1/documents/{id}/stream`)
 * @param onEvent - Callback receiving event name and parsed payload
 * @param onError - Error handler callback
 * @returns Abort function to close the stream connection
 */
export function subscribe(
  path: string,
  onEvent: (event: string, data: unknown) => void,
  onError?: (error: unknown) => void,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const headers = new Headers({ Accept: "text/event-stream" });
      if (tokens.access) headers.set("Authorization", `Bearer ${tokens.access}`);
      const org = tokens.organizationId;
      if (org) headers.set("X-Organization-Id", org);

      const response = await fetch(`${API_URL}${path}`, {
        headers,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`SSE failed: ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse double newline frame delimiters (\n\n)
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (dataLines.length) {
            try {
              onEvent(event, JSON.parse(dataLines.join("\n")));
            } catch {
              onEvent(event, dataLines.join("\n"));
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      if ((error as Error)?.name !== "AbortError") onError?.(error);
    }
  })();

  return () => controller.abort();
}
