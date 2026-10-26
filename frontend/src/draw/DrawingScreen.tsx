// 作畫畫面：倒數、分層畫布與工具列。倒數歸零或確認送出時凍結並匯出作品，交給 App 上傳。

import { useCallback, useEffect, useRef, useState } from 'react';
import type { SessionView } from '../shared/types';
import { Countdown } from './Countdown';
import { sleep } from './device';
import { DrawingEngine, exportPng, type EngineStatus } from './engine';
import { computeLayout, rectStyle, useViewport } from './layout';
import { CANVAS_HEIGHT, CANVAS_WIDTH, type FreezeReason, type Op, type Tool } from './model';
import { COLORS, SIZES } from './palette';
import { Toolbar } from './Toolbar';

interface DrawingScreenProps {
  session: SessionView;
  initialOps: Op[];
  /** 已凍結（送出中）：畫布保留內容但不能再修改 */
  frozen: boolean;
  statusText: string | null;
  onOpsChange: (ops: Op[]) => void;
  onFreeze: (reason: FreezeReason, ops: Op[], png: ArrayBuffer) => void;
}

const EMPTY_STATUS: EngineStatus = { canUndo: false, canRedo: false, canClear: false };

export function DrawingScreen(props: DrawingScreenProps) {
  const { session } = props;
  const viewport = useViewport();
  const layout = computeLayout(viewport.width, viewport.height);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<DrawingEngine | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const freezingRef = useRef(props.frozen);
  const pendingFreezeRef = useRef<FreezeReason | null>(null);
  const mountedRef = useRef(true);

  const [status, setStatus] = useState<EngineStatus>(EMPTY_STATUS);
  const [tool, setTool] = useState<Tool>('pen');
  const [color, setColor] = useState(COLORS[0].value);
  const [size, setSize] = useState(SIZES[1].value);
  const [confirm, setConfirm] = useState<'clear' | 'submit' | null>(null);
  const [locked, setLocked] = useState(props.frozen);

  /** 中斷所有筆畫、停用操作、凍結快照並匯出 PNG（同一場次只執行一次） */
  const freeze = useCallback((reason: FreezeReason) => {
    const engine = engineRef.current;
    if (!engine) {
      // 倒數元件可能比畫布先完成掛載，等引擎建立後再凍結
      pendingFreezeRef.current = reason;
      return;
    }
    if (freezingRef.current) {
      return;
    }
    freezingRef.current = true;
    setLocked(true);
    setConfirm(null);
    engine.freeze();
    const ops = engine.getOps();
    void (async () => {
      while (mountedRef.current) {
        try {
          const png = await exportPng(ops);
          if (mountedRef.current) {
            latest.current.onFreeze(reason, ops, png);
          }
          return;
        } catch (error) {
          console.error('匯出作品失敗，稍後重試', error);
          await sleep(1000);
        }
      }
    })();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const canvas = canvasRef.current;
    if (!canvas) {
      return undefined;
    }
    const engine = new DrawingEngine(
      canvas,
      () => {
        setStatus(engine.status());
        latest.current.onOpsChange(engine.getOps());
      },
      COLORS[0].value,
      SIZES[1].value,
    );
    engine.load(latest.current.initialOps);
    if (latest.current.frozen) {
      engine.freeze();
    }
    engineRef.current = engine;
    setStatus(engine.status());

    const toLogical = (event: PointerEvent, rect: DOMRect): [number, number] => [
      ((event.clientX - rect.left) * CANVAS_WIDTH) / rect.width,
      ((event.clientY - rect.top) * CANVAS_HEIGHT) / rect.height,
    ];

    const onDown = (event: PointerEvent) => {
      if (event.pointerType === 'mouse' && event.button !== 0) {
        return;
      }
      event.preventDefault();
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch {
        // 部分瀏覽器不支援捕捉，筆畫仍可在畫布內進行
      }
      const [x, y] = toLogical(event, canvas.getBoundingClientRect());
      engine.begin(event.pointerId, x, y);
    };
    const onMove = (event: PointerEvent) => {
      if (!engine.isActive(event.pointerId)) {
        return;
      }
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const samples = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [];
      const coords: number[] = [];
      for (const sample of samples.length > 0 ? samples : [event]) {
        coords.push(...toLogical(sample, rect));
      }
      engine.extend(event.pointerId, coords);
    };
    const onEnd = (event: PointerEvent) => engine.end(event.pointerId);

    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onEnd);
    canvas.addEventListener('pointercancel', onEnd);
    canvas.addEventListener('lostpointercapture', onEnd);

    const resize = () => engine.resize(canvas.getBoundingClientRect().width, window.devicePixelRatio || 1);
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    if (pendingFreezeRef.current) {
      const reason = pendingFreezeRef.current;
      pendingFreezeRef.current = null;
      freeze(reason);
    }

    return () => {
      mountedRef.current = false;
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onEnd);
      canvas.removeEventListener('pointercancel', onEnd);
      canvas.removeEventListener('lostpointercapture', onEnd);
      observer.disconnect();
      engineRef.current = null;
    };
  }, [freeze]);

  useEffect(() => {
    const engine = engineRef.current;
    if (engine) {
      engine.tool = tool;
      engine.color = color;
      engine.size = size;
    }
  }, [tool, color, size, status]);

  const handleColor = (value: string) => {
    setColor(value);
    setTool('pen');
  };

  return (
    <div className={`drawing-screen drawing-screen--${layout.mode}`}>
      <header className="drawing-header" style={rectStyle(layout.header)}>
        <Countdown
          deadline={session.deadline_at}
          totalSeconds={session.settings.drawing_duration_seconds}
          stopped={locked}
          onExpire={() => freeze('timeout')}
        />
        <div className="drawing-title" aria-hidden="true">
          <span>人工對畫</span>
          <span>(話)</span>
        </div>
      </header>

      <div className="stage" style={rectStyle(layout.stage)}>
        <img
          className="stage-reference"
          src={session.artwork.url}
          alt=""
          draggable={false}
          style={{ opacity: session.settings.reference_opacity }}
        />
        <canvas ref={canvasRef} className="stage-strokes" />
        {locked && (
          <div className="stage-lock" role="status">
            <p>{props.statusText ?? '整理作品中⋯'}</p>
          </div>
        )}
      </div>

      <Toolbar
        mode={layout.mode}
        style={rectStyle(layout.toolbar)}
        tool={tool}
        color={color}
        size={size}
        status={status}
        disabled={locked}
        onTool={setTool}
        onColor={handleColor}
        onSize={setSize}
        onUndo={() => engineRef.current?.undo()}
        onRedo={() => engineRef.current?.redo()}
        onClear={() => setConfirm('clear')}
        onSubmit={() => setConfirm('submit')}
      />

      {confirm && !locked && (
        <div className="confirm-backdrop" role="dialog" aria-modal="true">
          <div className="confirm-card">
            {confirm === 'clear' ? (
              <>
                <h2>清空畫布？</h2>
                <p>底圖會保留；清空後仍可按「復原」找回。</p>
                <div className="confirm-actions">
                  <button
                    type="button"
                    className="big-btn big-btn--danger"
                    onClick={() => {
                      engineRef.current?.clear();
                      setConfirm(null);
                    }}
                  >
                    清空
                  </button>
                  <button type="button" className="big-btn" onClick={() => setConfirm(null)}>
                    取消
                  </button>
                </div>
              </>
            ) : (
              <>
                <h2>送出作品？</h2>
                <p>送出後就不能再修改，作品會交給機器重繪。</p>
                <div className="confirm-actions">
                  <button type="button" className="big-btn big-btn--primary" onClick={() => freeze('manual')}>
                    送出
                  </button>
                  <button type="button" className="big-btn" onClick={() => setConfirm(null)}>
                    繼續畫
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
