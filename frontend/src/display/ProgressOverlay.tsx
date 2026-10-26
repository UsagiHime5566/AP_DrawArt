// 中螢幕生成進度提示（架構 5.6）：疊加在目前畫面上，不屬於播放快照。
// API 不回報真實進度，進度＝經過時間 ÷ 估計耗時，最多 95%；超過估計改為不定進度。

import { useEffect, useRef, useState } from 'react';
import { serverNow } from '../shared/clock';
import type { Progress } from '../shared/types';

const MAX_RATIO = 0.95;
const FINISH_MS = 1_000; // 補滿 400ms ＋ 淡出 600ms

type Phase = 'hidden' | 'running' | 'finishing';

interface Job {
  startedAt: number;
  estimateMs: number;
}

export function ProgressOverlay({ progress }: { progress: Progress | null }) {
  const [phase, setPhase] = useState<Phase>('hidden');
  const [job, setJob] = useState<Job | null>(null);
  const [queuedAfter, setQueuedAfter] = useState(0);
  const [indeterminate, setIndeterminate] = useState(false);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (progress?.active) {
      const startedAt = progress.started_at ? Date.parse(progress.started_at) : serverNow();
      const estimateMs = Math.max(1, progress.estimate_seconds) * 1_000;
      setJob((previous) =>
        previous && previous.startedAt === startedAt && previous.estimateMs === estimateMs
          ? previous
          : { startedAt, estimateMs },
      );
      setQueuedAfter(progress.queued_after);
      setPhase('running');
    } else {
      setPhase((previous) => (previous === 'running' ? 'finishing' : previous));
    }
  }, [progress]);

  useEffect(() => {
    if (phase !== 'finishing') {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      setPhase('hidden');
      setJob(null);
      setIndeterminate(false);
    }, FINISH_MS);
    return () => window.clearTimeout(timer);
  }, [phase]);

  useEffect(() => {
    if (phase !== 'running' || !job) {
      return undefined;
    }
    let frame = 0;
    let wasOver = false;
    const tick = () => {
      const ratio = (serverNow() - job.startedAt) / job.estimateMs;
      const over = ratio > 1;
      if (over !== wasOver) {
        wasOver = over;
        setIndeterminate(over);
      }
      if (barRef.current) {
        barRef.current.style.transform = `scaleX(${Math.min(Math.max(ratio, 0), MAX_RATIO)})`;
      }
      frame = window.requestAnimationFrame(tick);
    };
    setIndeterminate(false);
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [phase, job]);

  const classes = ['progress', `progress--${phase}`, indeterminate ? 'progress--indeterminate' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} aria-hidden={phase === 'hidden'}>
      <div className="progress__label">
        <span className="progress__title">機器正在回話⋯</span>
        {queuedAfter > 0 && <span className="progress__queue">之後還有 {queuedAfter} 件等待重繪</span>}
      </div>
      <div className="progress__track">
        <div key={job?.startedAt ?? 0} ref={barRef} className="progress__bar" />
        <div className="progress__sweep" />
      </div>
    </div>
  );
}
