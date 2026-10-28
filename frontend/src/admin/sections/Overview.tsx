// 狀態總覽：健康檢查、磁碟、Worker、三螢幕、播放與生成進度、計數。

import { Badge, Notice, ProgressBar, Section, type Tone } from '../components';
import {
  DISPLAY_ROLES,
  ROLE_LABELS,
  formatAgo,
  formatBytes,
  formatSeconds,
  label,
  secondsSince,
} from '../format';
import { useTick } from '../hooks';
import type { HealthChecks, OverviewResponse } from '../types';

const CHECK_LABELS: Record<keyof HealthChecks, string> = {
  database: '資料庫',
  frontend: '前端檔案',
  artwork: '藝術家原圖',
  worker: 'AI Worker',
  displays: '展示螢幕',
  disk: '磁碟空間',
  log: '執行日誌',
  openai_key: 'OpenAI 金鑰',
};

function checkDetail(name: keyof HealthChecks, checks: Partial<HealthChecks>): string {
  // 單項檢查本身出錯時，後端只回 ok=false 與 error
  const check = checks[name] as { ok?: boolean; error?: string | null } | undefined;
  if (check && !check.ok && check.error && name !== 'openai_key') {
    return `檢查失敗：${check.error}`;
  }
  switch (name) {
    case 'database':
      return checks.database?.ok ? '正常' : '無法存取';
    case 'frontend': {
      const missing = checks.frontend?.missing ?? [];
      return missing.length ? `缺少：${missing.join('、')}` : '建置檔完整';
    }
    case 'artwork':
      return checks.artwork?.artwork_id ? `原圖版本 #${checks.artwork.artwork_id}` : '沒有可用原圖';
    case 'worker':
      return checks.worker?.last_heartbeat_at
        ? `心跳 ${formatAgo(checks.worker.last_heartbeat_at)}${checks.worker.pid ? `・PID ${checks.worker.pid}` : ''}`
        : '沒有心跳';
    case 'displays': {
      const roles = checks.displays?.roles ?? {};
      const online = DISPLAY_ROLES.filter((role) => roles[role]?.connected).length;
      return `${online} / 1 已連線`;
    }
    case 'disk': {
      const disk = checks.disk;
      if (!disk) {
        return '—';
      }
      return `可用 ${formatBytes(disk.free_bytes)}（${(disk.free_ratio * 100).toFixed(1)}%）`;
    }
    case 'log':
      return checks.log?.last_error ? `寫入異常：${checks.log.last_error}` : `正常（${checks.log?.mode ?? '—'}）`;
    case 'openai_key':
      if (checks.openai_key?.ok) {
        return `已設定（${checks.openai_key.source ?? '—'}）`;
      }
      return checks.openai_key?.error ? `設定檔格式錯誤：${checks.openai_key.error}` : '尚未設定';
  }
}

export function Overview(props: { data: OverviewResponse | null }) {
  useTick(1000);
  const data = props.data;
  if (!data) {
    return (
      <Section id="overview" title="狀態總覽">
        <p className="adm-muted">載入中…</p>
      </Section>
    );
  }

  const checks = data.health.checks ?? {};
  const disk = checks.disk;
  const playback = data.playback;
  const current = playback?.playback_item_id ? playback : null;
  const progress = data.progress;
  const counts = data.counts;

  const elapsed = current?.started_at ? secondsSince(current.started_at) : null;
  const minSeconds = current?.seconds ?? null;
  const aiElapsed = progress?.active ? secondsSince(progress.started_at) : null;

  return (
    <Section id="overview" title="狀態總覽">
      {data.health.status === 'setup_required' ? (
        <Notice tone="bad">
          {checks.openai_key?.error ? 'OpenAI 金鑰設定檔格式錯誤' : '需要首次設定：尚未設定 OpenAI 金鑰'}
          ，AI 無法生成。請到 <a href="#openai-key">OpenAI 金鑰</a> 區塊設定。
        </Notice>
      ) : null}
      {disk?.discarding ? (
        <Notice tone="bad">
          磁碟可用空間低於 2%（剩 {formatBytes(disk.free_bytes)}）：新送出的作品會被直接捨棄。已收件作品照常生成與播放，請盡快清出空間。
        </Notice>
      ) : null}
      {data.health.launcher_requests?.shutdown ? <Notice tone="warn">已要求結束展覽，啟動器正在關閉系統。</Notice> : null}
      {data.health.launcher_requests?.relaunch_displays ? (
        <Notice tone="info">已要求重新定位展示視窗，等待啟動器處理。</Notice>
      ) : null}

      <div className="adm-grid adm-grid--checks">
        {(Object.keys(CHECK_LABELS) as (keyof HealthChecks)[]).map((name) => {
          const check = checks[name];
          const tone: Tone = check === undefined ? 'idle' : check.ok ? 'ok' : 'bad';
          return (
            <div key={name} className={`adm-check adm-check--${tone}`}>
              <div className="adm-check__head">
                <span>{CHECK_LABELS[name]}</span>
                <Badge tone={tone}>{check === undefined ? '未知' : check.ok ? '正常' : '異常'}</Badge>
              </div>
              <div className="adm-check__detail">{checkDetail(name, checks)}</div>
            </div>
          );
        })}
      </div>

      <h3 className="adm-subhead">展覽頁連線</h3>
      <div className="adm-grid adm-grid--roles">
        {DISPLAY_ROLES.map((role) => {
          const client = checks.displays?.roles?.[role];
          const connected = Boolean(client?.connected);
          return (
            <div key={role} className={`adm-role ${connected ? 'adm-role--on' : 'adm-role--off'}`}>
              <strong>{ROLE_LABELS[role]}</strong>
              <span>{connected ? '已連線' : '未連線'}</span>
              <small>
                版本 {client?.shown_revision ?? '—'}・心跳 {formatAgo(client?.last_heartbeat_at)}
              </small>
            </div>
          );
        })}
      </div>

      <div className="adm-grid adm-grid--two">
        <div className="adm-card">
          <h3 className="adm-subhead">目前播放</h3>
          <p className="adm-kv">
            <span>階段</span>
            <strong>{playback?.mode === 'drawing' ? '作畫中' : playback?.locked ? '本人首播・輸入鎖定' : '一般輪播'}</strong>
          </p>
          {current ? (
            <>
              <p className="adm-kv">
                <span>作品</span>
                <strong>
                  No.{current.receipt_order}・{label({ visitor: '觀眾', ai: 'AI', artist: '原圖' }, current.stage)}
                </strong>
              </p>
              <p className="adm-kv">
                <span>展示時間</span>
                <strong>
                  {elapsed === null ? '等待畫面顯示確認' : `${formatSeconds(elapsed)} / ${minSeconds ?? '—'} 秒`}
                </strong>
              </p>
              {elapsed !== null && minSeconds ? (
                <ProgressBar value={elapsed / minSeconds} />
              ) : null}
            </>
          ) : (
            <p className="adm-muted">尚未播放任何作品（待機畫面）。</p>
          )}
          {playback?.waiting_for_ai ? (
            <p className="adm-kv">
              <span>目前等待</span>
              <strong>保留本人作品，等待 AI 回話</strong>
            </p>
          ) : null}
        </div>

        <div className="adm-card">
          <h3 className="adm-subhead">AI 生成</h3>
          {progress?.active ? (
            <>
              <p className="adm-kv">
                <span>生成中</span>
                <strong>
                  {aiElapsed === null ? '—' : formatSeconds(aiElapsed)}／預估 {progress.estimate_seconds} 秒
                </strong>
              </p>
              <ProgressBar
                value={aiElapsed === null ? 0 : Math.min(0.95, aiElapsed / Math.max(1, progress.estimate_seconds))}
                indeterminate={aiElapsed !== null && aiElapsed > progress.estimate_seconds}
                tone={aiElapsed !== null && aiElapsed > progress.estimate_seconds ? 'warn' : 'ok'}
              />
            </>
          ) : (
            <p className="adm-muted">目前沒有生成中的作品。</p>
          )}
          <p className="adm-kv">
            <span>之後排隊</span>
            <strong>{progress?.queued_after ?? 0} 件</strong>
          </p>
        </div>
      </div>

      {counts ? (
        <div className="adm-grid adm-grid--counts">
          <Stat label="AI 排隊" value={counts.ai_queued} />
          <Stat label="AI 生成中" value={counts.ai_running} />
          <Stat label="待播" value={counts.playback_unplayed} highlight={counts.playback_unplayed > 0} />
          <Stat label="已播" value={counts.played_total} />
          <Stat label="收件總數" value={counts.submissions_total} />
          <Stat label="空白" value={counts.empty_total} />
          <Stat label="磁碟捨棄" value={counts.discarded_total} highlight={counts.discarded_total > 0} />
          <Stat label="放棄場次" value={counts.abandoned_total} />
        </div>
      ) : null}

      {data.health.admin_urls?.length ? (
        <p className="adm-muted adm-small">後臺網址：{data.health.admin_urls.join('、')}</p>
      ) : null}
    </Section>
  );
}

function Stat(props: { label: string; value: number | undefined; highlight?: boolean }) {
  return (
    <div className={`adm-stat${props.highlight ? ' adm-stat--hl' : ''}`}>
      <span className="adm-stat__value">{props.value ?? '—'}</span>
      <span className="adm-stat__label">{props.label}</span>
    </div>
  );
}
