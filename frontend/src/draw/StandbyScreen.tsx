// 待機頁：作品名稱、簡短邀請與開始入口；不能開始時顯示原因。

import { useEffect, useState } from 'react';
import { msUntil } from '../shared/clock';
import type { ConfigResponse, CurrentSessionResponse } from '../shared/types';

interface StandbyScreenProps {
  config: ConfigResponse | null;
  current: CurrentSessionResponse | null;
  online: boolean;
  artworkReady: boolean;
  starting: boolean;
  notice: string | null;
  onStart: () => void;
}

function useTicker(intervalMs: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((value) => value + 1), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
}

function blockedReason(props: StandbyScreenProps): string | null {
  const { current, config } = props;
  if (!props.online || !current || !config) {
    return '正在連線主機⋯';
  }
  if (!current.intake_enabled) {
    return '目前暫停接待';
  }
  if (current.active_session) {
    return '上一場尚未結束';
  }
  if (current.next_session_available_at) {
    const seconds = Math.ceil(msUntil(current.next_session_available_at) / 1000);
    if (seconds > 0) {
      return `請稍候 ${seconds} 秒`;
    }
  }
  if (!props.artworkReady) {
    return '畫布準備中⋯';
  }
  if (props.starting) {
    return '準備開始⋯';
  }
  return null;
}

export function StandbyScreen(props: StandbyScreenProps) {
  useTicker(500);
  const reason = blockedReason(props);
  const seconds = props.config?.settings.drawing_duration_seconds ?? 90;

  return (
    <div className="standby-screen">
      <div className="collage" aria-hidden="true">
        <span className="collage-block collage-block--a" />
        <span className="collage-block collage-block--b" />
        <span className="collage-block collage-block--c" />
        <span className="collage-block collage-block--d" />
        {props.config && <img className="collage-artwork" src={props.config.artwork.url} alt="" draggable={false} />}
      </div>

      <main className="standby-content">
        <h1 className="standby-title">
          <span className="standby-title-main">人工對畫</span>
          <span className="standby-title-aside">(話)</span>
        </h1>
        <p className="standby-lead">沿著半透明的原畫描下你的線條，或者乾脆畫出自己的版本。</p>
        <p className="standby-lead standby-lead--minor">
          你有 <strong>{seconds}</strong> 秒。送出後，機器會在前方的大螢幕回應你的畫。
        </p>

        <button type="button" className="start-btn" disabled={reason !== null} onClick={props.onStart}>
          開始作畫
        </button>
        <p className="standby-status" role="status">
          {reason ?? props.notice ?? '用手指輕觸「開始作畫」'}
        </p>
      </main>
    </div>
  );
}
