// 後臺共用元件。

import { useEffect, useRef, useState, type ReactNode } from 'react';

export function Section(props: { id: string; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="adm-section" id={props.id} aria-labelledby={`${props.id}-title`}>
      <header className="adm-section__head">
        <h2 id={`${props.id}-title`}>{props.title}</h2>
        {props.aside ? <div className="adm-section__aside">{props.aside}</div> : null}
      </header>
      {props.children}
    </section>
  );
}

export type Tone = 'ok' | 'warn' | 'bad' | 'idle';

export function Badge(props: { tone: Tone; children: ReactNode }) {
  return <span className={`adm-badge adm-badge--${props.tone}`}>{props.children}</span>;
}

export function Notice(props: { tone: 'info' | 'warn' | 'bad' | 'ok'; children: ReactNode }) {
  return (
    <div className={`adm-notice adm-notice--${props.tone}`} role={props.tone === 'bad' ? 'alert' : 'status'}>
      {props.children}
    </div>
  );
}

/** 顯示操作結果（成功或錯誤） */
export function ActionFeedback(props: { error: string | null; message: string | null }) {
  if (props.error) {
    return <Notice tone="bad">{props.error}</Notice>;
  }
  if (props.message) {
    return <Notice tone="ok">{props.message}</Notice>;
  }
  return null;
}

/**
 * 兩段式確認按鈕：第一次按下改為「再按一次確認」，5 秒內再按才執行。
 * 避免手機上誤觸造成暫停接待、放棄場次等操作。
 */
export function ConfirmButton(props: {
  label: string;
  confirmLabel?: string;
  onConfirm: () => void;
  disabled?: boolean;
  pending?: boolean;
  variant?: 'primary' | 'danger' | 'default';
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
      }
    },
    [],
  );

  const handleClick = () => {
    if (props.disabled || props.pending) {
      return;
    }
    if (!armed) {
      setArmed(true);
      timer.current = window.setTimeout(() => setArmed(false), 5000);
      return;
    }
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
    }
    setArmed(false);
    props.onConfirm();
  };

  const variant = props.variant ?? 'default';
  return (
    <button
      type="button"
      className={`adm-btn adm-btn--${variant}${armed ? ' adm-btn--armed' : ''}`}
      onClick={handleClick}
      disabled={props.disabled || props.pending}
    >
      {props.pending ? '處理中…' : armed ? props.confirmLabel ?? `確定${props.label}？再按一次` : props.label}
    </button>
  );
}

/** 進度條，value 為 0–1；indeterminate 時顯示往返動畫 */
export function ProgressBar(props: { value: number | null; indeterminate?: boolean; tone?: Tone }) {
  const width = props.value === null ? 0 : Math.min(1, Math.max(0, props.value)) * 100;
  return (
    <div className={`adm-progress adm-progress--${props.tone ?? 'ok'}`} aria-hidden="true">
      <div
        className={`adm-progress__bar${props.indeterminate ? ' adm-progress__bar--indeterminate' : ''}`}
        style={props.indeterminate ? undefined : { width: `${width}%` }}
      />
    </div>
  );
}

/** 作品對照燈箱 */
export function Lightbox(props: {
  title: string;
  visitorUrl: string | null;
  aiUrl: string | null;
  failureText?: string | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        props.onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [props]);

  return (
    <div className="adm-lightbox" role="dialog" aria-modal="true" aria-label={props.title} onClick={props.onClose}>
      <div className="adm-lightbox__inner" onClick={(event) => event.stopPropagation()}>
        <header className="adm-lightbox__head">
          <strong>{props.title}</strong>
          <button type="button" className="adm-btn" onClick={props.onClose}>
            關閉
          </button>
        </header>
        <div className="adm-lightbox__grid">
          <figure>
            <figcaption>觀眾畫作</figcaption>
            {props.visitorUrl ? <img src={props.visitorUrl} alt="觀眾畫作" /> : <div className="adm-lightbox__empty">無圖片</div>}
          </figure>
          <figure>
            <figcaption>AI 重繪</figcaption>
            {props.aiUrl ? (
              <img src={props.aiUrl} alt="AI 重繪" />
            ) : (
              <div className="adm-lightbox__empty adm-lightbox__empty--night">{props.failureText ?? '尚無結果'}</div>
            )}
          </figure>
        </div>
      </div>
    </div>
  );
}
