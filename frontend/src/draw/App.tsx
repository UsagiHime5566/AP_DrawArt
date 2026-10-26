// 平板流程：載入 → 待機 → 作畫 → 送出中 → 換場 → 待機。
// 平板只用 HTTP 輪詢；所有狀態以主機回覆為準，只有主機確認收件才離開送出中。

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiRequestError, NetworkError, apiGet, apiPost, apiPostForm } from '../shared/api';
import { msUntil } from '../shared/clock';
import type {
  ConfigResponse,
  CurrentSessionResponse,
  SessionResponse,
  SessionView,
  SubmitResponse,
} from '../shared/types';
import {
  clearStoredSessionId,
  getTabletId,
  preloadImage,
  readStoredSessionId,
  requestFullscreen,
  sleep,
  storeSessionId,
} from './device';
import { deleteDraft, deleteDraftsExcept, getDraft, logDraftError, putDraft } from './draftStore';
import { DrawingScreen } from './DrawingScreen';
import { HotZone, MaintenancePanel } from './Maintenance';
import { randomId, type Draft, type FreezeReason, type Op } from './model';
import { StandbyScreen } from './StandbyScreen';
import { TurnaroundScreen } from './TurnaroundScreen';

type Phase =
  | { name: 'boot' }
  | { name: 'standby' }
  | { name: 'drawing'; session: SessionView; draft: Draft }
  | { name: 'submitting'; session: SessionView; draft: Draft }
  | { name: 'turnaround'; session: SessionView };

const PHASE_LABELS: Record<Phase['name'], string> = {
  boot: '連線中',
  standby: '待機',
  drawing: '作畫中',
  submitting: '送出中',
  turnaround: '換場中',
};

const STANDBY_POLL_MS = 2000;
const CONFIG_REFRESH_EVERY = 5;
const DRAWING_POLL_MS = 3000;
const DRAFT_SAVE_DELAY_MS = 300;
const SUBMIT_TIMEOUT_MS = 60_000;

interface MaintenanceState {
  open: boolean;
  busy: boolean;
  message: string | null;
}

function startErrorMessage(code: string): string {
  switch (code) {
    case 'intake_paused':
      return '目前暫停接待';
    case 'session_active':
      return '上一場尚未結束';
    case 'turnaround':
      return '請稍候，下一位即將開放';
    case 'artwork_unavailable':
      return '原圖尚未就緒，請通知工作人員';
    default:
      return '暫時無法開始，請稍候再試';
  }
}

/** 409 session_closed 時主機回傳的場次 */
function closedSessionFrom(error: unknown): SessionView | null {
  if (error instanceof ApiRequestError && error.status === 409 && error.code === 'session_closed') {
    const session = error.details.session;
    if (session && typeof session === 'object') {
      return session as SessionView;
    }
  }
  return null;
}

/** 依請求結果更新連線狀態：NetworkError 表示連不到主機，其他回覆都代表主機在線 */
async function tracked<T>(promise: Promise<T>, setOnline: (online: boolean) => void): Promise<T> {
  try {
    const result = await promise;
    setOnline(true);
    return result;
  } catch (error) {
    setOnline(!(error instanceof NetworkError));
    throw error;
  }
}

function BootScreen({ message }: { message: string }) {
  return (
    <div className="boot-screen" role="status">
      <h1>
        人工對畫<span>(話)</span>
      </h1>
      <p>{message}</p>
    </div>
  );
}

export function App() {
  const [phase, setPhaseState] = useState<Phase>({ name: 'boot' });
  const phaseRef = useRef<Phase>(phase);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [current, setCurrent] = useState<CurrentSessionResponse | null>(null);
  const [online, setOnline] = useState(true);
  const [bootMessage, setBootMessage] = useState('正在連線主機⋯');
  const [readyArtworkUrl, setReadyArtworkUrl] = useState<string | null>(null);
  const [preloadAttempt, setPreloadAttempt] = useState(0);
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const resumingRef = useRef(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitNote, setSubmitNote] = useState<string | null>(null);
  const [maintenance, setMaintenance] = useState<MaintenanceState>({ open: false, busy: false, message: null });
  const draftRef = useRef<Draft | null>(null);
  const saveTimerRef = useRef<number | null>(null);

  // 以下函式只使用 ref 與穩定的 setter，舊閉包與新閉包行為相同
  const setPhase = (next: Phase) => {
    phaseRef.current = next;
    setPhaseState(next);
  };
  const track = <T,>(promise: Promise<T>) => tracked(promise, setOnline);

  const cancelPendingSave = () => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
  };

  /** 場次已在主機結束：清除本機草稿並進入換場 */
  const finishSession = async (session: SessionView) => {
    cancelPendingSave();
    draftRef.current = null;
    clearStoredSessionId();
    setSubmitNote(null);
    setPhase({ name: 'turnaround', session });
    await deleteDraft(session.id).catch(logDraftError);
  };

  const enterSession = (session: SessionView, draft: Draft) => {
    storeSessionId(session.id);
    draftRef.current = draft;
    setPhase(draft.frozen ? { name: 'submitting', session, draft } : { name: 'drawing', session, draft });
  };

  /** 恢復主機上仍在作畫中的場次（頁面重載、或本機資料遺失） */
  const resumeSession = async (session: SessionView) => {
    const stored = await getDraft(session.id).catch((error: unknown) => {
      logDraftError(error);
      return null;
    });
    if (stored) {
      enterSession(session, stored);
      return;
    }
    if (msUntil(session.deadline_at) <= 0) {
      // 草稿遺失且已過截止時間，沒有作品可補送，結束場次
      try {
        const res = await track(
          apiPost<SessionResponse>(`/api/sessions/${session.id}/abandon`, { reason: 'draft_lost' }),
        );
        await finishSession(res.session);
      } catch (error) {
        const closed = closedSessionFrom(error);
        if (!closed) {
          throw error;
        }
        await finishSession(closed);
      }
      return;
    }
    // 尚未截止：以空白畫布接續倒數
    const draft: Draft = {
      sessionId: session.id,
      submissionKey: randomId(),
      ops: [],
      frozen: null,
      updatedAt: Date.now(),
    };
    await putDraft(draft).catch(logDraftError);
    enterSession(session, draft);
  };

  // 啟動：取得設定與目前場次，必要時恢復上一場
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      while (!cancelled) {
        try {
          const [cfg, cur] = await Promise.all([
            track(apiGet<ConfigResponse>('/api/config')),
            track(apiGet<CurrentSessionResponse>('/api/sessions/current')),
          ]);
          if (cancelled) {
            return;
          }
          setConfig(cfg);
          setCurrent(cur);
          const active = cur.active_session?.status === 'drawing' ? cur.active_session : null;
          const storedId = readStoredSessionId();
          await deleteDraftsExcept(active?.id ?? null).catch(logDraftError);
          if (active) {
            await resumeSession(active);
            return;
          }
          if (storedId) {
            // 上一場可能在回覆遺失時已收件；仍在換場等待內就接續顯示
            clearStoredSessionId();
            const ended = await track(apiGet<SessionResponse>(`/api/sessions/${storedId}`)).catch(() => null);
            const next = ended?.session.next_session_available_at;
            if (ended && ended.session.status !== 'drawing' && next && msUntil(next) > 0) {
              setPhase({ name: 'turnaround', session: ended.session });
              return;
            }
          }
          setPhase({ name: 'standby' });
          return;
        } catch (error) {
          if (error instanceof ApiRequestError) {
            setBootMessage(`主機尚未就緒：${error.message}`);
          } else {
            setBootMessage('正在連線主機⋯');
            if (!(error instanceof NetworkError)) {
              console.error('啟動失敗，稍後重試', error);
            }
          }
          await sleep(2000);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // 只在掛載時執行一次
  }, []);

  // 預載原圖，完成後才開放開始
  const artworkUrl = config?.artwork.url ?? null;
  useEffect(() => {
    if (!artworkUrl) {
      return undefined;
    }
    let cancelled = false;
    let retryTimer: number | null = null;
    preloadImage(artworkUrl)
      .then(() => {
        if (!cancelled) {
          setReadyArtworkUrl(artworkUrl);
        }
      })
      .catch((error: unknown) => {
        console.warn('原圖預載失敗，稍後重試', error);
        if (!cancelled) {
          retryTimer = window.setTimeout(() => setPreloadAttempt((value) => value + 1), 3000);
        }
      });
    return () => {
      cancelled = true;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
    };
  }, [artworkUrl, preloadAttempt]);

  // 待機：每 2 秒確認接待狀態與換場時間，定期更新原圖
  useEffect(() => {
    if (phase.name !== 'standby') {
      return undefined;
    }
    let cancelled = false;
    let count = 0;
    const poll = async () => {
      try {
        const cur = await track(apiGet<CurrentSessionResponse>('/api/sessions/current'));
        if (cancelled) {
          return;
        }
        setCurrent(cur);
        const active = cur.active_session;
        if (active?.status === 'drawing' && !startingRef.current && !resumingRef.current) {
          resumingRef.current = true;
          try {
            await resumeSession(active);
          } finally {
            resumingRef.current = false;
          }
          return;
        }
        if (count % CONFIG_REFRESH_EVERY === 0) {
          const cfg = await track(apiGet<ConfigResponse>('/api/config'));
          if (!cancelled) {
            setConfig(cfg);
          }
        }
        count += 1;
      } catch (error) {
        if (!(error instanceof NetworkError)) {
          console.warn('待機輪詢失敗', error);
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), STANDBY_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [phase.name]);

  // 作畫中：定期確認場次仍在進行（例如工作人員已從後臺放棄）
  const drawingSessionId = phase.name === 'drawing' ? phase.session.id : null;
  useEffect(() => {
    if (!drawingSessionId) {
      return undefined;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await track(apiGet<SessionResponse>(`/api/sessions/${drawingSessionId}`));
        const now = phaseRef.current;
        if (cancelled || now.name !== 'drawing' || now.session.id !== drawingSessionId) {
          return;
        }
        if (res.session.status !== 'drawing') {
          await finishSession(res.session);
        }
      } catch (error) {
        if (!(error instanceof NetworkError)) {
          console.warn('場次狀態查詢失敗', error);
        }
      }
    };
    const timer = window.setInterval(() => void poll(), DRAWING_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [drawingSessionId]);

  // 送出中：以同一份快照與提交識別碼重送，直到主機明確回覆
  useEffect(() => {
    if (phase.name !== 'submitting' || !phase.draft.frozen) {
      return undefined;
    }
    const { session, draft } = phase;
    const frozen = phase.draft.frozen;
    let cancelled = false;
    setSubmitNote('作品傳送中⋯');
    void (async () => {
      for (let attempt = 0; !cancelled; attempt += 1) {
        try {
          const form = new FormData();
          form.append('submission_key', draft.submissionKey);
          form.append('reason', frozen.reason);
          form.append('strokes', new Blob([frozen.png], { type: 'image/png' }), 'strokes.png');
          const res = await track(
            apiPostForm<SubmitResponse>(`/api/sessions/${session.id}/submit`, form, SUBMIT_TIMEOUT_MS),
          );
          if (!cancelled) {
            await finishSession(res.session);
          }
          return;
        } catch (error) {
          if (cancelled) {
            return;
          }
          const closed = closedSessionFrom(error);
          if (closed) {
            await finishSession(closed);
            return;
          }
          if (error instanceof ApiRequestError && error.status === 409) {
            // 例如內容衝突：以主機上的場次狀態為準
            const res = await track(apiGet<SessionResponse>(`/api/sessions/${session.id}`)).catch(() => null);
            if (cancelled) {
              return;
            }
            if (res && res.session.status !== 'drawing') {
              await finishSession(res.session);
              return;
            }
          }
          if (error instanceof ApiRequestError && error.status === 404) {
            // 主機已沒有這個場次，無法補送
            cancelPendingSave();
            draftRef.current = null;
            clearStoredSessionId();
            await deleteDraft(session.id).catch(logDraftError);
            setNotice('主機找不到上一場，已重新開始');
            setPhase({ name: 'standby' });
            return;
          }
          setSubmitNote(
            error instanceof NetworkError
              ? '連線中斷，恢復後會自動送出⋯'
              : `主機暫時無法收件，稍後自動重試（${error instanceof ApiRequestError ? error.code : '未知錯誤'}）`,
          );
          await sleep(Math.min(10_000, 1000 * 2 ** Math.min(attempt, 4)));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [phase]);

  const handleOpsChange = useCallback((ops: Op[]) => {
    const draft = draftRef.current;
    if (!draft || draft.frozen) {
      return;
    }
    draftRef.current = { ...draft, ops, updatedAt: Date.now() };
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      const latest = draftRef.current;
      if (latest && !latest.frozen) {
        putDraft(latest).catch(logDraftError);
      }
    }, DRAFT_SAVE_DELAY_MS);
  }, []);

  const handleFreeze = useCallback((reason: FreezeReason, ops: Op[], png: ArrayBuffer) => {
    const before = phaseRef.current;
    if (before.name !== 'drawing') {
      return;
    }
    cancelPendingSave();
    const draft: Draft = {
      ...(draftRef.current ?? before.draft),
      ops,
      frozen: { reason, png },
      updatedAt: Date.now(),
    };
    draftRef.current = draft;
    void putDraft(draft)
      .catch(logDraftError)
      .then(() => {
        const now = phaseRef.current;
        if (now.name === 'drawing' && now.session.id === before.session.id) {
          setPhase({ name: 'submitting', session: before.session, draft });
        }
      });
    // 只使用 ref 與穩定 setter
  }, []);

  const handleStart = async () => {
    requestFullscreen();
    if (startingRef.current) {
      return;
    }
    startingRef.current = true;
    setStarting(true);
    setNotice(null);
    try {
      const res = await track(apiPost<SessionResponse>('/api/sessions', { tablet_id: getTabletId() }));
      const draft: Draft = {
        sessionId: res.session.id,
        submissionKey: randomId(),
        ops: [],
        frozen: null,
        updatedAt: Date.now(),
      };
      await putDraft(draft).catch(logDraftError);
      enterSession(res.session, draft);
    } catch (error) {
      if (error instanceof ApiRequestError) {
        setNotice(startErrorMessage(error.code));
      } else if (error instanceof NetworkError) {
        setNotice('無法連線主機，請稍候再試');
      } else {
        console.error('開始場次失敗', error);
        setNotice('暫時無法開始，請稍候再試');
      }
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  };

  const activeSessionId =
    phase.name === 'drawing' || phase.name === 'submitting'
      ? phase.session.id
      : current?.active_session?.status === 'drawing'
        ? current.active_session.id
        : null;

  const handleAbandon = async () => {
    if (!activeSessionId) {
      return;
    }
    setMaintenance((state) => ({ ...state, busy: true, message: null }));
    try {
      const res = await track(apiPost<SessionResponse>(`/api/sessions/${activeSessionId}/abandon`, { reason: 'staff' }));
      await finishSession(res.session);
      setMaintenance({ open: false, busy: false, message: null });
    } catch (error) {
      const closed = closedSessionFrom(error);
      if (closed) {
        await finishSession(closed);
        setMaintenance({ open: false, busy: false, message: null });
        return;
      }
      setMaintenance((state) => ({
        ...state,
        busy: false,
        message:
          error instanceof NetworkError
            ? '無法連線主機，請稍後再試'
            : `放棄失敗：${error instanceof Error ? error.message : String(error)}`,
      }));
    }
  };

  return (
    <div className="draw-app">
      {phase.name === 'boot' && <BootScreen message={bootMessage} />}

      {phase.name === 'standby' && (
        <StandbyScreen
          config={config}
          current={current}
          online={online}
          artworkReady={config !== null && readyArtworkUrl === config.artwork.url}
          starting={starting}
          notice={notice}
          onStart={() => void handleStart()}
        />
      )}

      {(phase.name === 'drawing' || phase.name === 'submitting') && (
        <DrawingScreen
          key={phase.session.id}
          session={phase.session}
          initialOps={phase.draft.ops}
          frozen={phase.name === 'submitting'}
          statusText={phase.name === 'submitting' ? (submitNote ?? '作品傳送中⋯') : null}
          onOpsChange={handleOpsChange}
          onFreeze={handleFreeze}
        />
      )}

      {phase.name === 'turnaround' && (
        <TurnaroundScreen key={phase.session.id} session={phase.session} onDone={() => setPhase({ name: 'standby' })} />
      )}

      {!online && phase.name !== 'boot' && (
        <div className="offline-banner" role="status">
          與主機的連線中斷，正在重新連線⋯
        </div>
      )}

      <HotZone onTrigger={() => setMaintenance({ open: true, busy: false, message: null })} />

      {maintenance.open && (
        <MaintenancePanel
          sessionId={activeSessionId}
          phaseLabel={PHASE_LABELS[phase.name]}
          online={online}
          busy={maintenance.busy}
          message={maintenance.message}
          onAbandon={() => void handleAbandon()}
          onReload={() => window.location.reload()}
          onAdmin={() => window.location.assign('/admin')}
          onClose={() => setMaintenance({ open: false, busy: false, message: null })}
        />
      )}
    </div>
  );
}
