/**
 * Typed API client.
 *
 * Tokens live in localStorage on the client. A 401 triggers exactly one refresh attempt,
 * and concurrent 401s share that single in-flight refresh instead of stampeding the
 * endpoint (which would burn the rotating refresh token several times over).
 */

import type { ApiErrorBody, TokenPair } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_KEY = "learnflow.access";
const REFRESH_KEY = "learnflow.refresh";
const ORG_KEY = "learnflow.org";

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

let refreshInFlight: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh = tokens.refresh;
  if (!refresh) return false;
  // Single-flight: the refresh token is single-use server-side, so a second concurrent
  // call would present an already-revoked token and log the user out spuriously.
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

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, raw, skipAuth, retryOn401 = true, ...init } = options;

  const url = new URL(`${API_URL}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  const headers = new Headers(init.headers);
  if (!skipAuth && tokens.access) {
    headers.set("Authorization", `Bearer ${tokens.access}`);
    const org = tokens.organizationId;
    if (org) headers.set("X-Organization-Id", org);
  }
  if (body !== undefined && !raw) headers.set("Content-Type", "application/json");

  const response = await fetch(url.toString(), {
    ...init,
    headers,
    body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (response.status === 401 && retryOn401 && !skipAuth && (await refreshTokens())) {
    return request<T>(path, { ...options, retryOn401: false });
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

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
 * Consume a Server-Sent Events stream.
 *
 * `EventSource` cannot send an Authorization header, and putting a bearer token in the query
 * string leaks it into access logs — so this reads the stream with fetch instead and parses
 * the SSE framing directly. Returns an abort function.
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
