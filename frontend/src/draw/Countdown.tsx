// 作畫倒數：以主機時間計算剩餘秒數，數字、文字與進度條並列，不只靠顏色表示。

import { useEffect, useRef, useState } from 'react';
import { msUntil } from '../shared/clock';

interface CountdownProps {
  deadline: string;
  totalSeconds: number;
  stopped: boolean;
  onExpire: () => void;
}

export function Countdown({ deadline, totalSeconds, stopped, onExpire }: CountdownProps) {
  const [msLeft, setMsLeft] = useState(() => msUntil(deadline));
  const firedRef = useRef(false);
  const latest = useRef({ stopped, onExpire });
  latest.current = { stopped, onExpire };

  useEffect(() => {
    const tick = () => {
      const left = msUntil(deadline);
      setMsLeft(left);
      if (left <= 0 && !firedRef.current && !latest.current.stopped) {
        firedRef.current = true;
        latest.current.onExpire();
      }
    };
    tick();
    const timer = window.setInterval(tick, 100);
    // 切到背景後返回時立即重新計算
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [deadline]);

  const seconds = Math.max(0, Math.ceil(msLeft / 1000));
  const ratio = Math.min(1, Math.max(0, msLeft / (totalSeconds * 1000)));
  const urgent = !stopped && seconds <= 10;
  const label = stopped ? '時間停止' : seconds === 0 ? '時間到' : urgent ? '即將自動送出' : '剩餘時間';

  return (
    <div className={`countdown${urgent ? ' is-urgent' : ''}${stopped ? ' is-stopped' : ''}`} role="timer">
      <span className="countdown-label">{label}</span>
      <span className="countdown-number">
        {seconds}
        <small>秒</small>
      </span>
      <span className="countdown-bar" aria-hidden="true">
        <span style={{ transform: `scaleX(${ratio})` }} />
      </span>
    </div>
  );
}
