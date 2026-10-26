// HTTP 請求工具：統一錯誤格式並自動以 server_now 校正時鐘。

import { syncServerClock } from './clock';
import type { ApiErrorBody } from './types';

/** 主機回覆錯誤（4xx / 5xx） */
export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

/** 無法連線到主機（斷線、逾時） */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

async function request<T>(method: string, path: string, body?: BodyInit, json = false, timeoutMs = 15_000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = Date.now();
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      body,
      headers: json ? { 'Content-Type': 'application/json' } : undefined,
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (error) {
    throw new NetworkError(error instanceof Error ? error.message : String(error));
  } finally {
    window.clearTimeout(timer);
  }

  let data: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const error = (data as ApiErrorBody | null)?.error;
    throw new ApiRequestError(
      response.status,
      error?.code ?? `http_${response.status}`,
      error?.message ?? response.statusText,
      error?.details ?? {},
    );
  }

  const serverNow = (data as { server_now?: unknown } | null)?.server_now;
  if (typeof serverNow === 'string') {
    syncServerClock(serverNow, startedAt);
  }
  return data as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>('GET', path);
}

export function apiPost<T>(path: string, payload: unknown = {}): Promise<T> {
  return request<T>('POST', path, JSON.stringify(payload), true);
}

export function apiPatch<T>(path: string, payload: unknown): Promise<T> {
  return request<T>('PATCH', path, JSON.stringify(payload), true);
}

export function apiPut<T>(path: string, payload: unknown): Promise<T> {
  return request<T>('PUT', path, JSON.stringify(payload), true);
}

/** multipart 上傳；大檔可傳較長的逾時 */
export function apiPostForm<T>(path: string, form: FormData, timeoutMs = 30_000): Promise<T> {
  return request<T>('POST', path, form, false, timeoutMs);
}
