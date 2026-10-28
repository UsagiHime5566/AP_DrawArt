import { useEffect, useRef, useState } from 'react';
import { App as DrawingApp } from '../draw/App';
import { HotZone, MaintenancePanel } from '../draw/Maintenance';
import { ProgressOverlay } from '../display/ProgressOverlay';
import { preloadImage, sleep } from '../draw/device';
import { randomId } from '../draw/model';
import { apiGet, apiPost } from '../shared/api';
import type { ConfigResponse, SessionResponse, SessionView } from '../shared/types';
import { escapeSequence, isTipContact, type ExhibitionState } from './model';

const LABELS = { visitor: '你的筆跡', ai: '機器的回話', artist: '原作' };

export function App() {
  const [view, setView] = useState<ExhibitionState | null>(null);
  const [shown, setShown] = useState<ExhibitionState | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [online, setOnline] = useState(true);
  const [artworkReady, setArtworkReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [maintenance, setMaintenance] = useState(false);
  const [stopping, setStopping] = useState(false);
  const viewRef = useRef(view);
  const drawingRef = useRef(false);
  const starting = useRef(false);
  const pointerDown = useRef(false);
  const stopped = useRef(false);
  const previewRef = useRef<string | null>(null);
  const saveDraft = useRef<() => Promise<void>>(async () => {});
  const clientId = useRef(sessionStorage.getItem('exhibition-client') ?? randomId());
  const finishAt = useRef(0);
  // 換段淡入：from 為淡出前的畫面（null 表示從黑畫面淡入）；淡入完成前不回報「已顯示」
  const [fade, setFade] = useState<{ from: ExhibitionState | null; ms: number; key: string } | null>(null);
  const shownRef = useRef<ExhibitionState | null>(null);
  const fadeUntil = useRef(0);
  viewRef.current = view;

  const present = (next: ExhibitionState) => {
    const previous = shownRef.current;
    shownRef.current = next;
    setShown(next);
    const ms = Math.max(0, (next.fade_seconds ?? 0) * 1000);
    if (ms > 0 && contentKey(previous) !== contentKey(next)) {
      fadeUntil.current = performance.now() + ms;
      setFade({ from: previous, ms, key: contentKey(next) });
    }
  };

  const finishDrawing = (_session: SessionView | null) => {
    drawingRef.current = false;
    setDrawing(false);
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = null;
    setPreview(null);
    finishAt.current = performance.now();
  };

  useEffect(() => {
    sessionStorage.setItem('exhibition-client', clientId.current);
    let cancelled = false;
    void (async () => {
      while (!cancelled) {
        try {
          const config = await apiGet<ConfigResponse>('/api/config');
          await preloadImage(config.artwork.url);
          if (!cancelled) setArtworkReady(true);
          break;
        } catch {
          await sleep(1000);
        }
      }
    })();
    void (async () => {
      while (!cancelled) {
        try {
          const next = await apiGet<ExhibitionState>('/api/exhibition');
          if (cancelled) break;
          setView(next);
          setOnline(true);
          if (next.mode === 'drawing' && !drawingRef.current &&
              performance.now() - finishAt.current > 1200) {
            drawingRef.current = true;
            setDrawing(true);
          }
        } catch {
          if (!cancelled) setOnline(false);
        }
        await sleep(500);
      }
    })();
    return () => {
      cancelled = true;
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    };
  }, []);

  // 僅以版本／圖片切換預載，不因每次輪詢回傳新物件就重開展示計時。
  const displayKey = view ? `${view.revision}:${view.image_url}:${view.failure_text}` : '';
  useEffect(() => {
    if (!view || drawing) return undefined;
    const candidate = view;
    let cancelled = false;
    void (async () => {
      while (!cancelled) {
        try {
          if (candidate.image_url) await preloadImage(candidate.image_url);
          if (!cancelled) present(candidate);
          return;
        } catch {
          setNotice('畫面正在重新連結⋯');
          await sleep(1500);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [displayKey, drawing]);

  // 淡入結束後移除淡出的舊畫面
  useEffect(() => {
    if (!fade) return undefined;
    const timer = window.setTimeout(() => setFade(null), fade.ms);
    return () => window.clearTimeout(timer);
  }, [fade]);

  // 作畫期間不顯示輪播；結束後的第一個畫面從黑畫面淡入，不會閃回作畫前的舊圖
  useEffect(() => {
    if (!drawing) return;
    shownRef.current = null;
    setShown(null);
    setFade(null);
  }, [drawing]);

  useEffect(() => {
    if (!shown || drawing || preview) return undefined;
    const report = () => {
      const visible = document.visibilityState === 'visible';
      // 淡入完成才算開始顯示，每段才會完整停留滿設定秒數
      if (visible && performance.now() < fadeUntil.current) return;
      void apiPost('/api/exhibition/presented', {
        revision: shown.revision,
        client_id: clientId.current,
        visible,
      }).catch(() => {});
    };
    let frame = 0;
    const start = window.setTimeout(() => {
      frame = requestAnimationFrame(() => requestAnimationFrame(report));
    }, Math.max(0, fadeUntil.current - performance.now()));
    const timer = window.setInterval(report, 2000);
    document.addEventListener('visibilitychange', report);
    return () => {
      window.clearTimeout(start);
      cancelAnimationFrame(frame);
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', report);
    };
  }, [shown?.revision, drawing, preview]);

  useEffect(() => {
    const sequence = escapeSequence();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!sequence(performance.now(), event.repeat) || stopped.current) return;
      stopped.current = true;
      setStopping(true);
      void (async () => {
        try {
          await saveDraft.current();
          await apiPost('/api/admin/exhibition/stop?source=escape');
        } catch {
          stopped.current = false;
          setStopping(false);
          setNotice('暫時無法結束，請再按五次 ESC 或通知工作人員。');
        }
      })();
    };
    const onUp = () => { pointerDown.current = false; };
    const onDown = (event: PointerEvent) => {
      if (!isTipContact(event)) return;
      const wasDown = pointerDown.current;
      pointerDown.current = true;
      const current = viewRef.current;
      if (wasDown || drawingRef.current || starting.current || stopped.current ||
          !current?.can_start || !artworkReady || maintenance ||
          (event.target as HTMLElement).closest('.hotzone, .maintenance-backdrop')) return;
      event.preventDefault();
      event.stopPropagation();
      starting.current = true;
      void (async () => {
        try {
          const config = await apiGet<ConfigResponse>('/api/config');
          await preloadImage(config.artwork.url);
          await apiPost<SessionResponse>('/api/sessions', { tablet_id: 'local-pen' });
          drawingRef.current = true;
          setDrawing(true);
          setNotice(null);
        } catch {
          setNotice('請稍候，再觸筆開始。');
        } finally {
          starting.current = false;
        }
      })();
    };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('pointerdown', onDown, true);
    window.addEventListener('pointerup', onUp, true);
    window.addEventListener('pointercancel', onUp, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('pointerup', onUp, true);
      window.removeEventListener('pointercancel', onUp, true);
    };
  }, [artworkReady, maintenance]);

  const state = shown;
  // 本人送出後到 AI 有結果前：以時間估算的進度提示，不冒充 API 的真實百分比
  const waitingForAi = Boolean(preview) || Boolean(view?.waiting_for_ai);
  const progress = waitingForAi ? view?.progress ?? null : null;
  return (
    <main className={`exhibition-app${drawing ? ' exhibition-app--drawing' : ''}`}>
      {drawing && (
        <DrawingApp
          embedded
          onFinished={finishDrawing}
          registerSave={(save) => { saveDraft.current = save; }}
          onPreview={(png) => {
            if (previewRef.current) URL.revokeObjectURL(previewRef.current);
            const url = URL.createObjectURL(new Blob([png], { type: 'image/png' }));
            previewRef.current = url;
            setPreview(url);
          }}
        />
      )}
      {(!drawing || preview) && (
        <section className={`exhibition-stage${preview ? ' exhibition-stage--preview' : ''}`}>
          {preview ? (
            <img src={preview} className="exhibition-image" alt="你的畫作" />
          ) : (
            <>
              {fade?.from && (
                <div className="exhibition-layer">
                  <StageContent state={fade.from} locked={Boolean(view?.locked)} />
                </div>
              )}
              <div
                key={contentKey(state)}
                className={`exhibition-layer${fade?.key === contentKey(state) ? ' exhibition-layer--in' : ''}`}
                style={fade?.key === contentKey(state) ? { animationDuration: `${fade.ms}ms` } : undefined}
              >
                <StageContent state={state} locked={Boolean(view?.locked)} />
              </div>
            </>
          )}
          <ProgressOverlay progress={progress} />
          {/* 排在其他作品之後、尚未開始生成時，進度條不出現，仍保留文字提示 */}
          {waitingForAi && !progress?.active && <p className="exhibition-wait">機器正在回話⋯</p>}
        </section>
      )}
      {!online && <p className="exhibition-notice">正在重新連結⋯</p>}
      {notice && <p className="exhibition-notice">{notice}</p>}
      {!drawing && <HotZone onTrigger={() => setMaintenance(true)} />}
      {maintenance && (
        <MaintenancePanel sessionId={null} phaseLabel="輪播" online={online} busy={false}
          message={null} onAbandon={() => {}} onReload={() => location.reload()}
          onAdmin={() => location.assign('/admin')} onClose={() => setMaintenance(false)} />
      )}
      {stopping && <div className="exhibition-ending" role="status">對話暫歇。</div>}
    </main>
  );
}

/** 畫面內容相同（同一張圖或同一段文字）時不淡入，只有真的換畫面才淡入。 */
function contentKey(state: ExhibitionState | null): string {
  if (!state) return 'none';
  const content = state.failure_text ?? state.image_url ?? 'idle';
  return `${state.playback_item_id ?? 'idle'}:${state.stage}:${content}`;
}

function StageContent(props: { state: ExhibitionState | null; locked: boolean }) {
  const { state, locked } = props;
  return (
    <>
      {state?.failure_text ? (
        <p className="exhibition-silence">{state.failure_text}</p>
      ) : state?.image_url ? (
        <img src={state.image_url} className="exhibition-image" alt={LABELS[state.stage]} />
      ) : <div className="exhibition-loading">人工對畫<span>(話)</span></div>}

      {!state?.playback_item_id && (
        <div className="exhibition-invitation">
          <span className="exhibition-mark" aria-hidden="true" />
          <h1>人工對畫<span>(話)</span></h1>
          <p>讓筆尖落下，開始一場對話。</p>
        </div>
      )}
      {state?.playback_item_id && (
        <div className="exhibition-caption">
          <span>{LABELS[state.stage]}</span>
          <span>{locked ? '讓這一輪對話，完整落下。' : '觸筆，加入對話。'}</span>
        </div>
      )}
    </>
  );
}
