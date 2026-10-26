// OpenAI 金鑰：只顯示是否已設定與來源，設定後清空輸入，不回顯金鑰。

import { useState } from 'react';
import { apiGet, apiPut } from '../../shared/api';
import { ActionFeedback, Badge, Notice, Section } from '../components';
import { useAction, usePolling } from '../hooks';
import type { OpenAiKeyStatus } from '../types';

const SOURCE_LABELS: Record<string, string> = {
  env: '環境變數 OPENAI_API_KEY',
  file: '設定檔 config/openai.local.json',
};

export function OpenAiKey() {
  const status = usePolling(() => apiGet<OpenAiKeyStatus>('/api/admin/openai-key'), 15_000);
  const [value, setValue] = useState('');
  const save = useAction();
  const data = status.data;

  const submit = () => {
    const apiKey = value.trim();
    if (!apiKey) {
      return;
    }
    void save.run(async () => {
      await apiPut<OpenAiKeyStatus>('/api/admin/openai-key', { api_key: apiKey });
      setValue('');
      status.refresh();
      return '已儲存金鑰。下一件作品開始生成時生效。';
    });
  };

  return (
    <Section
      id="openai-key"
      title="OpenAI 金鑰"
      aside={data ? <Badge tone={data.configured ? 'ok' : 'bad'}>{data.configured ? '已設定' : '未設定'}</Badge> : null}
    >
      {status.error ? <Notice tone="warn">讀取失敗：{status.error}</Notice> : null}
      {data ? (
        <p className="adm-kv">
          <span>來源</span>
          <strong>{data.source ? SOURCE_LABELS[data.source] ?? data.source : '—'}</strong>
        </p>
      ) : null}
      {data?.source === 'env' ? (
        <Notice tone="info">目前使用環境變數中的金鑰；在這裡設定的金鑰會存入設定檔，但只要環境變數存在就以環境變數為準。</Notice>
      ) : null}
      <form
        className="adm-form adm-form--compact"
        autoComplete="off"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="adm-field">
          <label htmlFor="openai-key-input">設定新金鑰</label>
          <input
            id="openai-key-input"
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            placeholder="sk-…"
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              save.clear();
            }}
          />
          <p className="adm-field__help">金鑰只寫入主機設定檔，不會顯示在任何畫面或日誌。</p>
        </div>
        <div className="adm-row">
          <button type="submit" className="adm-btn adm-btn--primary" disabled={!value.trim() || save.pending}>
            {save.pending ? '儲存中…' : '儲存金鑰'}
          </button>
        </div>
      </form>
      <ActionFeedback error={save.error} message={save.message} />
    </Section>
  );
}
