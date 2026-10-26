// 結束展覽：提醒剩餘待播件數，確認後要求啟動器關閉系統。

import { apiPost } from '../../shared/api';
import { ActionFeedback, ConfirmButton, Notice, Section } from '../components';
import { useAction } from '../hooks';
import type { OverviewResponse } from '../types';

export function StopExhibition(props: { data: OverviewResponse | null; onChanged: () => void }) {
  const stop = useAction();
  const data = props.data;
  const counts = data?.counts;
  const unplayed = counts?.playback_unplayed ?? 0;
  const generating = (counts?.ai_queued ?? 0) + (counts?.ai_running ?? 0);
  const shuttingDown = Boolean(data?.health.launcher_requests?.shutdown);

  const confirmStop = () =>
    stop.run(async () => {
      await apiPost('/api/admin/exhibition/stop');
      props.onChanged();
      return '已送出結束要求：接待已關閉，啟動器將保存狀態並關閉展示視窗與服務。';
    });

  return (
    <Section id="stop" title="結束展覽">
      <ol className="adm-steps">
        <li className={data && !data.intake_enabled ? 'adm-steps__done' : undefined}>
          先在「接待」區塊暫停接待{data && !data.intake_enabled ? '（已暫停）' : ''}。
        </li>
        <li className={data && unplayed === 0 ? 'adm-steps__done' : undefined}>
          等待剩餘作品生成並播完{data ? `（待播 ${unplayed} 件，其中 ${generating} 件尚在生成或排隊）` : ''}。
        </li>
        <li>按下「結束展覽」。</li>
      </ol>
      {data && data.intake_enabled ? <Notice tone="warn">目前仍在接待中，結束展覽會同時關閉接待。</Notice> : null}
      {unplayed > 0 ? (
        <Notice tone="warn">還有 {unplayed} 件作品尚未播完；現在結束不會刪除它們，下次啟動時會從這裡續播。</Notice>
      ) : null}
      {shuttingDown ? <Notice tone="info">已要求結束展覽，等待啟動器關閉系統。</Notice> : null}
      <div className="adm-row">
        <ConfirmButton
          label="結束展覽"
          confirmLabel={unplayed > 0 ? `仍有 ${unplayed} 件待播，確定結束？再按一次` : '確定結束展覽？再按一次'}
          variant="danger"
          disabled={!data || shuttingDown}
          pending={stop.pending}
          onConfirm={() => void confirmStop()}
        />
      </div>
      <ActionFeedback error={stop.error} message={stop.message} />
    </Section>
  );
}
