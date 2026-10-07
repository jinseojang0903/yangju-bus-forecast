import type { ApiErrorCode, ApiErrorDetail } from "./types";

export const API_BASE = "/api/v1";

/** 클라이언트에서만 쓰는 코드. 서버로 보내지 않는다. */
export type ClientErrorCode = "NETWORK_ERROR" | "INVALID_RESPONSE";
export type ErrorCode = ApiErrorCode | ClientErrorCode;

const SERVER_ERROR_CODES: readonly ApiErrorCode[] = [
  "VALIDATION_FAILED",
  "NOT_FOUND",
  "METHOD_NOT_ALLOWED",
  "RATE_LIMITED",
  "INTERNAL_ERROR",
];

interface ApiErrorInit {
  code: ErrorCode;
  status: number;
  message: string;
  details?: ApiErrorDetail[];
  retryAfterSec?: number | null;
  cause?: unknown;
}

/**
 * API 호출 실패. 화면 문구는 `components/ErrorMessage` 에서 code 로 정한다.
 * message 는 디버깅용(서버 고정 문자열 또는 클라이언트 설명)이며 화면에 보여 주지 않는다.
 */
export class ApiError extends Error {
  readonly code: ErrorCode;
  /** HTTP 상태. 네트워크 오류는 0 */
  readonly status: number;
  readonly details: ApiErrorDetail[];
  /** 429 의 Retry-After(초). 그 밖에는 null */
  readonly retryAfterSec: number | null;

  constructor(init: ApiErrorInit) {
    super(init.message, { cause: init.cause });
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.details = init.details ?? [];
    this.retryAfterSec = init.retryAfterSec ?? null;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

type QueryValue = string | number | null | undefined;

export interface RequestOptions {
  signal?: AbortSignal;
  /** 응답 최상위에서 배열이어야 하는 필드. 아니면 INVALID_RESPONSE */
  arrayFields?: readonly string[];
}

/** 값이 비어 있는(null·undefined·"") 쿼리는 넣지 않는다. */
export function buildUrl(path: string, params?: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === null || value === undefined || value === "") continue;
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return `${API_BASE}${path}${query ? `?${query}` : ""}`;
}

/**
 * GET 요청. 실패하면 ApiError 를 던진다.
 * - 4xx/5xx: 계약 1.4절 에러 본문의 code(형식이 어긋나면 HTTP 상태로 추정)
 * - fetch 자체 실패: NETWORK_ERROR
 * - JSON 이 아니거나 arrayFields 가 배열이 아님: INVALID_RESPONSE
 * - 취소(AbortError)는 그대로 다시 던진다(TanStack Query 가 처리)
 */
export function apiGet<T>(
  path: string,
  params?: Record<string, QueryValue>,
  options: RequestOptions = {},
): Promise<T> {
  return request<T>(
    buildUrl(path, params),
    { method: "GET", headers: { Accept: "application/json" }, signal: options.signal },
    options.arrayFields,
  );
}

/** POST(JSON 본문) 요청. 실패 규칙은 apiGet 과 같다. */
export function apiPost<T>(path: string, body: unknown, options: RequestOptions = {}): Promise<T> {
  return request<T>(
    buildUrl(path),
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: options.signal,
    },
    options.arrayFields,
  );
}

async function request<T>(
  url: string,
  init: RequestInit,
  arrayFields: readonly string[] = [],
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError({
      code: "NETWORK_ERROR",
      status: 0,
      message: "network request failed",
      cause: error,
    });
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  const body = await readJsonBody(response);
  const value = body.ok ? body.value : undefined;
  if (!isRecord(value)) {
    throw invalidResponse(response.status, "response body is not a JSON object");
  }
  for (const field of arrayFields) {
    if (!Array.isArray(value[field])) {
      throw invalidResponse(response.status, `response field "${field}" is not an array`);
    }
  }
  return value as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  const body = await readJsonBody(response);
  const parsed = body.ok ? parseErrorBody(body.value) : null;
  const code = parsed?.code ?? codeFromStatus(response.status);
  return new ApiError({
    code,
    status: response.status,
    message: parsed?.message ?? `HTTP ${response.status}`,
    details: parsed?.details ?? [],
    retryAfterSec:
      code === "RATE_LIMITED" ? parseRetryAfter(response.headers.get("Retry-After")) : null,
  });
}

type JsonResult = { ok: true; value: unknown } | { ok: false };

async function readJsonBody(response: Response): Promise<JsonResult> {
  const text = await response.text();
  if (!text) return { ok: false };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    // 본문이 JSON 이 아니면 호출부가 INVALID_RESPONSE 또는 상태 기반 코드로 바꾼다.
    return { ok: false };
  }
}

function parseErrorBody(
  value: unknown,
): { code: ApiErrorCode; message: string; details: ApiErrorDetail[] } | null {
  if (!isRecord(value) || !isRecord(value.error)) return null;
  const { code, message, details } = value.error;
  if (typeof code !== "string" || !isServerErrorCode(code)) return null;
  return {
    code,
    message: typeof message === "string" ? message : "",
    details: Array.isArray(details) ? details.filter(isErrorDetail) : [],
  };
}

function isServerErrorCode(code: string): code is ApiErrorCode {
  return (SERVER_ERROR_CODES as readonly string[]).includes(code);
}

function isErrorDetail(value: unknown): value is ApiErrorDetail {
  return isRecord(value) && typeof value.field === "string" && typeof value.reason === "string";
}

function codeFromStatus(status: number): ApiErrorCode {
  if (status === 400) return "VALIDATION_FAILED";
  if (status === 404) return "NOT_FOUND";
  if (status === 405) return "METHOD_NOT_ALLOWED";
  if (status === 429) return "RATE_LIMITED";
  return "INTERNAL_ERROR";
}

function parseRetryAfter(header: string | null): number | null {
  if (header === null) return null;
  const seconds = Number(header.trim());
  return Number.isInteger(seconds) && seconds >= 0 ? seconds : null;
}

function invalidResponse(status: number, message: string): ApiError {
  return new ApiError({ code: "INVALID_RESPONSE", status, message });
}

function isAbortError(error: unknown): boolean {
  return isRecord(error) && error.name === "AbortError";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
