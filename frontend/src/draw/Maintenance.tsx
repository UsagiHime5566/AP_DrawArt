// 維護入口：右上角不可見熱區，5 秒內連點 10 次開啟維護頁。

import { useRef } from 'react';

const TAP_WINDOW_MS = 5000;
const TAP_COUNT = 10;

export function HotZone({ onTrigger }: { onTrigger: () => void }) {
  const taps = useRef<number[]>([]);
  return (
    <div
      className="hotzone"
      aria-hidden="true"
      onPointerDown={(event) => {
        event.preventDefault();
        event.stopPropagation();
        const now = performance.now();
        taps.current = taps.current.filter((time) => now - time <= TAP_WINDOW_MS);
        taps.current.push(now);
        if (taps.current.length >= TAP_COUNT) {
          taps.current = [];
          onTrigger();
        }
      }}
    />
  );
}

interface MaintenancePanelProps {
  sessionId: string | null;
  phaseLabel: string;
  online: boolean;
  busy: boolean;
  message: string | null;
  onAbandon: () => void;
  onReload: () => void;
  onAdmin: () => void;
  onClose: () => void;
}

export function MaintenancePanel(props: MaintenancePanelProps) {
  return (
    <div className="maintenance-backdrop" role="dialog" aria-modal="true" aria-label="維護">
      <div className="maintenance-panel">
        <h2>維護</h2>
        <dl className="maintenance-info">
          <dt>目前狀態</dt>
          <dd>{props.phaseLabel}</dd>
          <dt>場次</dt>
          <dd>{props.sessionId ? props.sessionId.slice(0, 8) : '無'}</dd>
          <dt>主機連線</dt>
          <dd>{props.online ? '正常' : '中斷，正在重新連線'}</dd>
        </dl>
        {props.message && <p className="maintenance-message">{props.message}</p>}
        <div className="maintenance-actions">
          <button
            type="button"
            className="big-btn big-btn--danger"
            disabled={props.busy || !props.sessionId}
            onClick={props.onAbandon}
          >
            放棄目前場次
          </button>
          <button type="button" className="big-btn" disabled={props.busy} onClick={props.onReload}>
            重新載入
          </button>
          <button type="button" className="big-btn" disabled={props.busy} onClick={props.onAdmin}>
            前往後臺
          </button>
          <button type="button" className="big-btn big-btn--primary" onClick={props.onClose}>
            返回
          </button>
        </div>
        <p className="maintenance-note">開啟維護頁不會暫停作畫倒數。</p>
      </div>
    </div>
  );
}
