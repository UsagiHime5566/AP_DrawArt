// 後臺共用 hook：輪詢、時鐘刷新與單次操作狀態。

import { useCallback, useEffect, useRef, useState } from 'react';
import { describeError } from './format';

export interface PollingState<T> {
  data: T | null;
  error: string | null;
  lastOkAt: number | null;
  refresh: () => void;
}

/** 依固定間隔輪詢；上一次完成後才排下一次，不會重疊請求 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number, enabled = true): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);
  const triggerRef = useRef<() => void>(() => undefined);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    let cancelled = false;
    let timer: number | null = null;
    let running = false;

    const run = async () => {
      if (cancelled || running) {
        return;
      }
      running = true;
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
          setLastOkAt(Date.now());
        }
      } catch (caught) {
        if (!cancelled) {
          setError(describeError(caught));
        }
      } finally {
        running = false;
        if (!cancelled) {
          timer = window.setTimeout(run, intervalMs);
        }
      }
    };

    triggerRef.current = () => {
      void run();
    };
    void run();
    return () => {
      cancelled = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
      triggerRef.current = () => undefined;
    };
  }, [intervalMs, enabled]);

  const refresh = useCallback(() => triggerRef.current(), []);
  return { data, error, lastOkAt, refresh };
}

/** 每隔一段時間重新渲染，用於倒數與「幾秒前」 */
export function useTick(intervalMs = 1000): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((value) => value + 1), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return tick;
}

export interface ActionState {
  pending: boolean;
  error: string | null;
  message: string | null;
  run: (action: () => Promise<string | void>) => Promise<boolean>;
  clear: () => void;
}

/** 按鈕操作：追蹤進行中、錯誤與成功訊息 */
export function useAction(): ActionState {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const run = useCallback(async (action: () => Promise<string | void>) => {
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      const result = await action();
      setMessage(result ?? null);
      return true;
    } catch (caught) {
      setError(describeError(caught));
      return false;
    } finally {
      setPending(false);
    }
  }, []);

  const clear = useCallback(() => {
    setError(null);
    setMessage(null);
  }, []);

  return { pending, error, message, run, clear };
}
