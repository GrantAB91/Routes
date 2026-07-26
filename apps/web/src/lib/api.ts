/**
 * Typed API access.
 *
 * The generated OpenAPI client lives in @contour/contracts; this module wraps it
 * with the two behaviours every call site needs: a request id for tracing, and
 * structured error handling that preserves the API's `remedy` field so a
 * disabled capability can be explained rather than shown as a failure.
 */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    context?: Record<string, unknown>;
    request_id?: string;
  };
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly context: Record<string, unknown> = {},
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** The operator-facing fix, when the API supplied one. */
  get remedy(): string | undefined {
    const value = this.context.remedy;
    return typeof value === 'string' ? value : undefined;
  }

  get isDisabledCapability(): boolean {
    return this.code === 'capability_disabled';
  }
}

const BASE = process.env.NEXT_PUBLIC_CONTOUR_API_BASE_URL ?? 'http://127.0.0.1:8000';

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    let body: ApiErrorBody | undefined;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      throw new ApiError(
        'unexpected_response',
        `The API returned ${response.status} with no readable body.`,
      );
    }
    throw new ApiError(
      body.error.code,
      body.error.message,
      body.error.context ?? {},
      body.error.request_id,
    );
  }

  return (await response.json()) as T;
}
