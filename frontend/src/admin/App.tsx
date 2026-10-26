// 後臺單頁：頂部狀態列與區塊導覽，各區塊依序排列。

import { apiGet } from '../shared/api';
import { Badge, type Tone } from './components';
import { formatAgo, formatTime } from './format';
import { usePolling, useTick } from './hooks';
import { AiTest } from './sections/AiTest';
import { Displays } from './sections/Displays';
import { Intake } from './sections/Intake';
import { Logs } from './sections/Logs';
import { OpenAiKey } from './sections/OpenAiKey';
import { Overview } from './sections/Overview';
import { Queues } from './sections/Queues';
import { Settings } from './sections/Settings';
import { StopExhibition } from './sections/StopExhibition';
import type { HealthStatus, OverviewResponse } from './types';

const NAV: { id: string; label: string }[] = [
  { id: 'overview', label: '總覽' },
  { id: 'intake', label: '接待' },
  { id: 'queues', label: '佇列' },
  { id: 'settings', label: '設定' },
  { id: 'ai-test', label: '試生成' },
  { id: 'openai-key', label: '金鑰' },
  { id: 'displays', label: '螢幕' },
  { id: 'logs', label: '日誌' },
  { id: 'stop', label: '結束' },
];

const STATUS_TEXT: Record<HealthStatus, { tone: Tone; text: string }> = {
  ok: { tone: 'ok', text: '運作正常' },
  degraded: { tone: 'warn', text: '部分異常' },
  setup_required: { tone: 'bad', text: '需要設定' },
};

export function App() {
  const overview = usePolling(() => apiGet<OverviewResponse>('/api/admin/overview'), 2000);
  useTick(1000);

  const data = overview.data;
  const offline = overview.error !== null;
  const status = data ? STATUS_TEXT[data.health.status] ?? { tone: 'idle' as Tone, text: data.health.status } : null;

  return (
    <div className="adm">
      <header className="adm-top">
        <div className="adm-top__title">
          <h1>人工對畫(話)・後臺</h1>
          <div className="adm-top__status">
            {offline ? <Badge tone="bad">離線</Badge> : status ? <Badge tone={status.tone}>{status.text}</Badge> : null}
            {data ? (
              <Badge tone={data.intake_enabled ? 'ok' : 'warn'}>{data.intake_enabled ? '接待中' : '已暫停接待'}</Badge>
            ) : null}
            {data ? <span className="adm-muted adm-small">主機時間 {formatTime(data.server_now)}</span> : null}
          </div>
        </div>
        <nav className="adm-nav" aria-label="區塊">
          {NAV.map((item) => (
            <a key={item.id} href={`#${item.id}`}>
              {item.label}
            </a>
          ))}
        </nav>
      </header>

      {offline ? (
        <div className="adm-offline" role="alert">
          後臺暫時無法連線，會自動重試。
          {overview.lastOkAt ? `最後成功更新：${formatAgo(new Date(overview.lastOkAt).toISOString())}` : ''}
        </div>
      ) : null}

      <main className="adm-main">
        <Overview data={data} />
        <Intake data={data} onChanged={overview.refresh} />
        <Queues />
        <Settings overviewVersionId={data?.settings_version_id ?? null} onSaved={overview.refresh} />
        <AiTest />
        <OpenAiKey />
        <Displays />
        <Logs />
        <StopExhibition data={data} onChanged={overview.refresh} />
      </main>
    </div>
  );
}
