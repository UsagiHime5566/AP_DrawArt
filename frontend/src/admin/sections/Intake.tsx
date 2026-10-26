// 接待：暫停／恢復接待、目前場次、下場開放時間、放棄場次。

import { apiPatch, apiPost } from '../../shared/api';
import { msUntil } from '../../shared/clock';
import { ActionFeedback, Badge, ConfirmButton, Section } from '../components';
import { SESSION_STATUS_LABELS, formatSeconds, formatTime, label, shortId } from '../format';
import { useAction, useTick } from '../hooks';
import type { OverviewResponse } from '../types';

export function Intake(props: { data: OverviewResponse | null; onChanged: () => void }) {
  useTick(1000);
  const toggle = useAction();
  const abandon = useAction();
  const data = props.data;

  if (!data) {
    return (
      <Section id="intake" title="接待">
        <p className="adm-muted">載入中…</p>
      </Section>
    );
  }

  const session = data.active_session;
  const nextAt = data.next_session_available_at;
  const waitMs = nextAt ? msUntil(nextAt) : 0;
  const deadlineMs = session ? msUntil(session.deadline_at) : 0;

  const setIntake = (enabled: boolean) =>
    toggle.run(async () => {
      await apiPatch('/api/admin/intake', { intake_enabled: enabled });
      props.onChanged();
      return enabled ? '已恢復接待。' : '已暫停接待：進行中的場次會照常完成，平板不再開放新場次。';
    });

  const abandonSession = (sessionId: string) =>
    abandon.run(async () => {
      await apiPost(`/api/sessions/${sessionId}/abandon`, { reason: 'staff' });
      props.onChanged();
      return '已放棄目前場次，平板會進入換場等待。';
    });

  return (
    <Section
      id="intake"
      title="接待"
      aside={<Badge tone={data.intake_enabled ? 'ok' : 'warn'}>{data.intake_enabled ? '接待中' : '已暫停'}</Badge>}
    >
      <div className="adm-row">
        {data.intake_enabled ? (
          <ConfirmButton
            label="暫停接待"
            confirmLabel="確定暫停接待？再按一次"
            variant="danger"
            pending={toggle.pending}
            onConfirm={() => void setIntake(false)}
          />
        ) : (
          <ConfirmButton
            label="恢復接待"
            confirmLabel="確定恢復接待？再按一次"
            variant="primary"
            pending={toggle.pending}
            onConfirm={() => void setIntake(true)}
          />
        )}
      </div>
      <ActionFeedback error={toggle.error} message={toggle.message} />

      <div className="adm-card">
        <h3 className="adm-subhead">目前場次</h3>
        {session ? (
          <>
            <p className="adm-kv">
              <span>場次</span>
              <strong>
                {shortId(session.id)}・{label(SESSION_STATUS_LABELS, session.status)}
              </strong>
            </p>
            <p className="adm-kv">
              <span>開始</span>
              <strong>{formatTime(session.started_at)}</strong>
            </p>
            <p className="adm-kv">
              <span>截止</span>
              <strong>
                {formatTime(session.deadline_at)}（{deadlineMs > 0 ? `剩 ${formatSeconds(deadlineMs / 1000)}` : '已過截止，等待平板送出'}）
              </strong>
            </p>
            <p className="adm-kv">
              <span>原圖</span>
              <strong>#{session.artwork.id}</strong>
            </p>
            <div className="adm-row">
              <ConfirmButton
                label="放棄目前場次"
                confirmLabel="確定放棄？觀眾作品不會保存，再按一次"
                variant="danger"
                pending={abandon.pending}
                onConfirm={() => void abandonSession(session.id)}
              />
            </div>
            <p className="adm-muted adm-small">只在平板無法恢復時使用；放棄後照常進入換場等待。</p>
          </>
        ) : (
          <p className="adm-muted">目前沒有作畫中的場次。</p>
        )}
        <ActionFeedback error={abandon.error} message={abandon.message} />
        <p className="adm-kv">
          <span>下一位開放</span>
          <strong>{nextAt && waitMs > 0 ? `${formatTime(nextAt)}（${formatSeconds(waitMs / 1000)}後）` : '已開放'}</strong>
        </p>
      </div>
    </Section>
  );
}
