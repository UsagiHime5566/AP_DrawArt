// 完成／換場頁：依場次結果顯示訊息，等到主機給的下場開放時間後回到待機頁。

import { useEffect, useRef, useState } from 'react';
import { msUntil } from '../shared/clock';
import type { SessionView } from '../shared/types';

interface TurnaroundScreenProps {
  session: SessionView;
  onDone: () => void;
}

function availableAt(session: SessionView): string | null {
  if (session.next_session_available_at) {
    return session.next_session_available_at;
  }
  if (session.ended_at) {
    const ended = Date.parse(session.ended_at);
    return new Date(ended + session.settings.turnaround_seconds * 1000).toISOString();
  }
  return null;
}

function messages(session: SessionView): { title: string; body: string; queue: string | null } {
  switch (session.status) {
    case 'submitted': {
      const ahead = session.submission?.queue_ahead ?? 0;
      return {
        title: '作品已交給機器',
        body: '機器正在回話⋯ 請看前方的大螢幕。',
        queue: ahead > 0 ? `前面還有 ${ahead} 件作品` : '下一件就是你的作品',
      };
    }
    // 磁碟不足而捨棄時，觀眾端與一般收件相同，只是不顯示件數
    case 'discarded':
      return { title: '作品已交給機器', body: '機器正在回話⋯ 請看前方的大螢幕。', queue: null };
    case 'empty':
      return { title: '畫布上沒有留下筆跡', body: '這一回不送出，換下一位吧。', queue: null };
    default:
      return { title: '本場已結束', body: '謝謝參與，下一位即將開始。', queue: null };
  }
}

export function TurnaroundScreen({ session, onDone }: TurnaroundScreenProps) {
  const target = availableAt(session);
  const [msLeft, setMsLeft] = useState(() => (target ? msUntil(target) : 0));
  const doneRef = useRef(false);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    const tick = () => {
      const left = target ? msUntil(target) : 0;
      setMsLeft(left);
      if (left <= 0 && !doneRef.current) {
        doneRef.current = true;
        onDoneRef.current();
      }
    };
    tick();
    const timer = window.setInterval(tick, 200);
    return () => window.clearInterval(timer);
  }, [target]);

  const text = messages(session);
  const seconds = Math.max(0, Math.ceil(msLeft / 1000));

  return (
    <div className={`turnaround-screen turnaround-screen--${session.status}`}>
      <div className="collage" aria-hidden="true">
        <span className="collage-block collage-block--a" />
        <span className="collage-block collage-block--c" />
        <span className="collage-block collage-block--d" />
      </div>
      <main className="turnaround-card" role="status">
        <h1>{text.title}</h1>
        <p className="turnaround-body">{text.body}</p>
        {text.queue && <p className="turnaround-queue">{text.queue}</p>}
        <p className="turnaround-next">
          下一位可在 <strong>{seconds}</strong> 秒後開始
        </p>
      </main>
    </div>
  );
}
