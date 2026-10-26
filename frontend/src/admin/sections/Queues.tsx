// 佇列：生成、播放、播放紀錄、場次四個分頁。

import { useMemo, useState } from 'react';
import { apiGet } from '../../shared/api';
import { Badge, Lightbox, Notice, Section, type Tone } from '../components';
import {
  AI_STATUS_LABELS,
  END_REASON_LABELS,
  PLAYBACK_STATUS_LABELS,
  RESULT_KIND_LABELS,
  RUN_RESULT_LABELS,
  SESSION_STATUS_LABELS,
  formatDurationMs,
  formatTime,
  label,
  shortId,
} from '../format';
import { usePolling } from '../hooks';
import type { PlaybackItemRow, QueuesResponse } from '../types';

type Tab = 'ai' | 'playback' | 'runs' | 'sessions';

const TABS: { id: Tab; label: string }[] = [
  { id: 'ai', label: '生成' },
  { id: 'playback', label: '播放' },
  { id: 'runs', label: '播放紀錄' },
  { id: 'sessions', label: '場次' },
];

const AI_TONES: Record<string, Tone> = { queued: 'idle', running: 'warn', succeeded: 'ok', failed: 'bad' };
const PLAYBACK_TONES: Record<string, Tone> = {
  waiting_for_ai: 'idle',
  ready: 'warn',
  preparing: 'warn',
  playing: 'ok',
  played: 'idle',
};
const SESSION_TONES: Record<string, Tone> = {
  drawing: 'warn',
  submitted: 'ok',
  empty: 'idle',
  discarded: 'bad',
  abandoned: 'bad',
};

const FAILURE_TEXT = '此刻，機器沉默。';

export function Queues() {
  const [tab, setTab] = useState<Tab>('ai');
  const [preview, setPreview] = useState<PlaybackItemRow | null>(null);
  const queues = usePolling(() => apiGet<QueuesResponse>('/api/admin/queues?limit=50'), 4000);

  const itemsBySubmission = useMemo(() => {
    const map = new Map<string, PlaybackItemRow>();
    for (const item of queues.data?.playback_items ?? []) {
      map.set(item.submission_id, item);
    }
    return map;
  }, [queues.data]);

  const data = queues.data;

  return (
    <Section
      id="queues"
      title="佇列"
      aside={
        <button type="button" className="adm-btn adm-btn--small" onClick={queues.refresh}>
          重新整理
        </button>
      }
    >
      <div className="adm-tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={`adm-tab${tab === item.id ? ' adm-tab--active' : ''}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {queues.error ? <Notice tone="warn">佇列讀取失敗：{queues.error}</Notice> : null}
      {!data ? <p className="adm-muted">載入中…</p> : null}

      {data && tab === 'ai' ? (
        <ul className="adm-list">
          {data.ai_jobs.length === 0 ? <li className="adm-muted">尚無生成工作。</li> : null}
          {data.ai_jobs.map((job) => {
            const item = itemsBySubmission.get(job.submission_id);
            return (
              <li key={job.id} className="adm-list__row">
                <div className="adm-list__main">
                  <strong>No.{job.receipt_order}</strong>
                  <Badge tone={AI_TONES[job.status] ?? 'idle'}>{label(AI_STATUS_LABELS, job.status)}</Badge>
                  <span className="adm-muted adm-small">{job.model ?? '—'}</span>
                </div>
                <div className="adm-list__meta">
                  開始 {formatTime(job.started_at)}・耗時 {formatDurationMs(job.duration_ms)}
                </div>
                {job.status === 'failed' && (job.error_code || job.error_message) ? (
                  <details className="adm-details">
                    <summary>失敗原因：{job.error_code ?? '未知'}</summary>
                    <pre>{job.error_message ?? '（沒有詳細訊息）'}</pre>
                  </details>
                ) : null}
                {item ? (
                  <button type="button" className="adm-btn adm-btn--small" onClick={() => setPreview(item)}>
                    看作品
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {data && tab === 'playback' ? (
        <ul className="adm-list">
          {data.playback_items.length === 0 ? <li className="adm-muted">尚無播放項目。</li> : null}
          {data.playback_items.map((item) => (
            <li key={item.id} className="adm-list__row">
              <div className="adm-list__main">
                <strong>No.{item.receipt_order}</strong>
                <Badge tone={PLAYBACK_TONES[item.status] ?? 'idle'}>{label(PLAYBACK_STATUS_LABELS, item.status)}</Badge>
                <span className="adm-muted adm-small">{label(RESULT_KIND_LABELS, item.result_kind)}</span>
              </div>
              <div className="adm-list__meta">播畢 {formatTime(item.played_at)}</div>
              <button type="button" className="adm-thumb" onClick={() => setPreview(item)} aria-label={`查看 No.${item.receipt_order}`}>
                {item.visitor_url ? <img src={item.visitor_url} alt="" loading="lazy" /> : null}
                {item.ai_url ? (
                  <img src={item.ai_url} alt="" loading="lazy" />
                ) : (
                  <span className="adm-thumb__placeholder">{item.result_kind === 'failure_card' ? '失敗卡' : '等待 AI'}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {data && tab === 'runs' ? (
        <ul className="adm-list">
          {data.playback_runs.length === 0 ? <li className="adm-muted">尚無播放紀錄。</li> : null}
          {data.playback_runs.map((run) => (
            <li key={run.id} className="adm-list__row">
              <div className="adm-list__main">
                <strong>No.{run.receipt_order ?? '—'}</strong>
                <Badge tone={run.result === 'completed' ? 'ok' : run.result === 'interrupted' ? 'bad' : 'warn'}>
                  {run.result ? label(RUN_RESULT_LABELS, run.result) : '進行中'}
                </Badge>
                <span className="adm-muted adm-small">最低 {run.min_display_seconds ?? '—'} 秒</span>
              </div>
              <div className="adm-list__meta">
                計時開始 {formatTime(run.timing_started_at)}・結束 {formatTime(run.ended_at)}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {data && tab === 'sessions' ? (
        <ul className="adm-list">
          {data.sessions.length === 0 ? <li className="adm-muted">尚無場次。</li> : null}
          {data.sessions.map((session) => (
            <li key={session.id} className="adm-list__row">
              <div className="adm-list__main">
                <strong>{shortId(session.id)}</strong>
                <Badge tone={SESSION_TONES[session.status] ?? 'idle'}>{label(SESSION_STATUS_LABELS, session.status)}</Badge>
                <span className="adm-muted adm-small">{label(END_REASON_LABELS, session.end_reason)}</span>
              </div>
              <div className="adm-list__meta">
                開始 {formatTime(session.started_at)}・結束 {formatTime(session.ended_at)}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {preview ? (
        <Lightbox
          title={`作品 No.${preview.receipt_order}`}
          visitorUrl={preview.visitor_url}
          aiUrl={preview.ai_url}
          failureText={preview.result_kind === 'failure_card' ? FAILURE_TEXT : '尚無結果'}
          onClose={() => setPreview(null)}
        />
      ) : null}
    </Section>
  );
}
