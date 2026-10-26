// 日誌：增量輪詢 runtime.txt，WARNING / ERROR 上色，可暫停自動捲動與下載。

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { apiGet } from '../../shared/api';
import { Notice, Section } from '../components';
import { describeError } from '../format';
import type { LogsResponse } from '../types';

const MAX_LINES = 2000;
const POLL_MS = 2000;

type Level = 'INFO' | 'WARNING' | 'ERROR';

interface LogLine {
  id: number;
  text: string;
  level: Level;
}

/** 解析等級；例外堆疊等縮排的續行沿用上一行的等級 */
function parseLines(text: string, previousLevel: Level, startId: number): { lines: LogLine[]; lastLevel: Level } {
  const raw = text.split('\n');
  if (raw.length > 0 && raw[raw.length - 1] === '') {
    raw.pop();
  }
  let level = previousLevel;
  const lines = raw.map((line, index) => {
    if (!line.startsWith(' ')) {
      level = line.includes(' [ERROR] ') ? 'ERROR' : line.includes(' [WARNING] ') ? 'WARNING' : 'INFO';
    }
    return { id: startId + index, text: line, level };
  });
  return { lines, lastLevel: level };
}

export function Logs() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const offsetRef = useRef<number | null>(null);
  const lastLevelRef = useRef<Level>('INFO');
  const nextIdRef = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;

    const poll = async () => {
      try {
        const query = offsetRef.current === null ? '' : `?offset=${offsetRef.current}`;
        const response = await apiGet<LogsResponse>(`/api/admin/logs${query}`);
        if (cancelled) {
          return;
        }
        // 主機改回最後一段（例如檔案被替換）時整段重設
        const reset = offsetRef.current === null || response.offset !== offsetRef.current;
        const parsed = parseLines(response.text, reset ? 'INFO' : lastLevelRef.current, nextIdRef.current);
        nextIdRef.current += parsed.lines.length;
        lastLevelRef.current = parsed.lastLevel;
        offsetRef.current = response.next_offset;
        if (reset || parsed.lines.length > 0) {
          setLines((previous) => {
            const merged = reset ? parsed.lines : [...previous, ...parsed.lines];
            return merged.length > MAX_LINES ? merged.slice(merged.length - MAX_LINES) : merged;
          });
        }
        setError(null);
      } catch (caught) {
        if (!cancelled) {
          setError(describeError(caught));
        }
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(() => void poll(), POLL_MS);
        }
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  const visible = useMemo(() => (onlyProblems ? lines.filter((line) => line.level !== 'INFO') : lines), [lines, onlyProblems]);

  useLayoutEffect(() => {
    const box = boxRef.current;
    if (box && autoScroll) {
      box.scrollTop = box.scrollHeight;
    }
  }, [visible, autoScroll]);

  // 使用者往上捲動時自動暫停，捲回底部時恢復
  const handleScroll = () => {
    const box = boxRef.current;
    if (!box) {
      return;
    }
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
    if (atBottom !== autoScroll) {
      setAutoScroll(atBottom);
    }
  };

  return (
    <Section
      id="logs"
      title="日誌"
      aside={
        <a className="adm-btn adm-btn--small" href="/api/admin/logs/download" download="runtime.txt">
          下載 TXT
        </a>
      }
    >
      <div className="adm-row">
        <button type="button" className="adm-btn adm-btn--small" onClick={() => setAutoScroll((value) => !value)}>
          {autoScroll ? '暫停自動捲動' : '恢復自動捲動'}
        </button>
        <label className="adm-check-line">
          <input type="checkbox" checked={onlyProblems} onChange={(event) => setOnlyProblems(event.target.checked)} />
          只看 WARNING / ERROR
        </label>
      </div>
      {error ? <Notice tone="warn">日誌讀取失敗：{error}</Notice> : null}
      <div className="adm-log" ref={boxRef} onScroll={handleScroll} role="log" aria-live="off">
        {visible.length === 0 ? <div className="adm-muted">（目前沒有日誌）</div> : null}
        {visible.map((line) => (
          <div key={line.id} className={`adm-log__line adm-log__line--${line.level.toLowerCase()}`}>
            {line.text}
          </div>
        ))}
      </div>
      <p className="adm-muted adm-small">畫面最多保留最新 {MAX_LINES} 行；完整內容請下載 TXT。</p>
    </Section>
  );
}
