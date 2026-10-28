// 螢幕配置：偵測到的螢幕、三角色對應與重新定位展示視窗。

import { useEffect, useState } from 'react';
import { apiGet, apiPatch, apiPost } from '../../shared/api';
import { ActionFeedback, Badge, ConfirmButton, Notice, Section } from '../components';
import { DISPLAY_ROLES, ROLE_LABELS, describeError, formatAgo } from '../format';
import { useAction } from '../hooks';
import type { DisplaysConfig, DisplaysResponse, MonitorRole } from '../types';

export function Displays() {
  const [data, setData] = useState<DisplaysResponse | null>(null);
  const [draft, setDraft] = useState<DisplaysConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const save = useAction();
  const relaunch = useAction();

  const load = async () => {
    try {
      const response = await apiGet<DisplaysResponse>('/api/admin/displays');
      setData(response);
      setDraft(response.config);
      setLoadError(null);
    } catch (error) {
      setLoadError(describeError(error));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const dirty = data !== null && draft !== null && JSON.stringify(data.config) !== JSON.stringify(draft);
  const monitorIndexes = draft ? DISPLAY_ROLES.map((role) => draft[role].monitor) : [];
  const duplicated = new Set(monitorIndexes).size !== monitorIndexes.length;
  const monitors = data?.monitors ?? [];

  const setMonitor = (role: MonitorRole, monitor: number) => {
    if (draft) {
      setDraft({ ...draft, [role]: { ...draft[role], monitor } });
      save.clear();
    }
  };

  const submit = () =>
    save.run(async () => {
      if (!draft) {
        return;
      }
      const response = await apiPatch<DisplaysResponse>('/api/admin/displays', draft);
      setData(response);
      setDraft(response.config);
      return '已儲存。按「重新定位展示視窗」套用到展覽螢幕。';
    });

  const requestRelaunch = () =>
    relaunch.run(async () => {
      await apiPost('/api/admin/displays/relaunch');
      return '已要求啟動器重新定位展示視窗。';
    });

  return (
    <Section
      id="displays"
      title="螢幕配置"
      aside={
        <button type="button" className="adm-btn adm-btn--small" onClick={() => void load()}>
          重新偵測
        </button>
      }
    >
      {loadError ? <Notice tone="warn">讀取失敗：{loadError}</Notice> : null}
      {!data || !draft ? (
        <p className="adm-muted">載入中…</p>
      ) : (
        <>
          <h3 className="adm-subhead">偵測到的螢幕（由左到右）</h3>
          {monitors.length === 0 ? (
            <Notice tone="warn">沒有偵測到螢幕資訊；可直接輸入螢幕編號（由左到右，從 0 開始）。</Notice>
          ) : (
            <ul className="adm-list">
              {monitors.map((monitor) => (
                <li key={monitor.index} className="adm-list__row">
                  <div className="adm-list__main">
                    <strong>#{monitor.index}</strong>
                    <span>
                      {monitor.width} × {monitor.height}
                    </span>
                    {monitor.primary ? <Badge tone="idle">主螢幕</Badge> : null}
                    {monitor.width !== 1920 || monitor.height !== 1080 ? <Badge tone="warn">非 1920×1080</Badge> : null}
                  </div>
                  <div className="adm-list__meta">
                    {monitor.name}・位置 ({monitor.x}, {monitor.y})
                  </div>
                </li>
              ))}
            </ul>
          )}
          {monitors.length === 0 ? (
            <Notice tone="warn">尚未偵測到展覽螢幕，請確認 Windows 顯示設定。</Notice>
          ) : null}

          <h3 className="adm-subhead">角色對應</h3>
          <div className="adm-form adm-form--compact">
            {DISPLAY_ROLES.map((role) => {
              const client = data.clients?.[role];
              return (
                <div key={role} className="adm-field">
                  <label htmlFor={`display-${role}`}>
                    {ROLE_LABELS[role]}
                    <span className={client?.connected ? 'adm-text-ok' : 'adm-text-bad'}>
                      {client?.connected ? '・已連線' : '・未連線'}
                    </span>
                  </label>
                  {monitors.length > 0 ? (
                    <select
                      id={`display-${role}`}
                      value={draft[role].monitor}
                      onChange={(event) => setMonitor(role, Number(event.target.value))}
                    >
                      {monitors.map((monitor) => (
                        <option key={monitor.index} value={monitor.index}>
                          #{monitor.index}（{monitor.width}×{monitor.height}，x={monitor.x}）
                        </option>
                      ))}
                      {monitors.some((monitor) => monitor.index === draft[role].monitor) ? null : (
                        <option value={draft[role].monitor}>#{draft[role].monitor}（未偵測到）</option>
                      )}
                    </select>
                  ) : (
                    <input
                      id={`display-${role}`}
                      type="number"
                      min={0}
                      step={1}
                      value={draft[role].monitor}
                      onChange={(event) => setMonitor(role, Math.max(0, Math.floor(Number(event.target.value) || 0)))}
                    />
                  )}
                  <p className="adm-field__help">
                    顯示版本 {client?.shown_revision ?? '—'}・心跳 {formatAgo(client?.last_heartbeat_at)}
                  </p>
                </div>
              );
            })}
            <div className="adm-field">
              <label htmlFor="display-browser">展示瀏覽器</label>
              <input
                id="display-browser"
                type="text"
                spellCheck={false}
                value={draft.browser}
                onChange={(event) => {
                  setDraft({ ...draft, browser: event.target.value });
                  save.clear();
                }}
              />
              <p className="adm-field__help">auto 會依序尋找交付包內瀏覽器、Edge、Chrome；也可填入瀏覽器執行檔的完整路徑。</p>
            </div>
          </div>
          {duplicated ? <Notice tone="warn">有兩個角色指定到同一個螢幕，請確認對應是否正確。</Notice> : null}
          <div className="adm-row">
            <button type="button" className="adm-btn adm-btn--primary" disabled={!dirty || save.pending} onClick={() => void submit()}>
              {save.pending ? '儲存中…' : '儲存對應'}
            </button>
            <button type="button" className="adm-btn" disabled={!dirty || save.pending} onClick={() => setDraft(data.config)}>
              放棄修改
            </button>
          </div>
          <ActionFeedback error={save.error} message={save.message} />

          <h3 className="adm-subhead">重新定位</h3>
          <p className="adm-muted adm-small">重新開啟單一展覽視窗。主機保留場次與首播鎖，重開後重新計算尚未播完的段落。</p>
          <div className="adm-row">
            <ConfirmButton
              label="重新定位展示視窗"
              confirmLabel="確定重開展覽視窗？再按一次"
              pending={relaunch.pending}
              onConfirm={() => void requestRelaunch()}
            />
          </div>
          <ActionFeedback error={relaunch.error} message={relaunch.message} />
        </>
      )}
    </Section>
  );
}
