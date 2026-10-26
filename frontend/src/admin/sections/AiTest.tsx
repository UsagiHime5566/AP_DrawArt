// 試生成：以上傳圖片或最新作品呼叫一次 AI，結果不進播放佇列。

import { useEffect, useRef, useState } from 'react';
import { ApiRequestError, apiGet, apiPostForm } from '../../shared/api';
import { Notice, Section } from '../components';
import { describeError, formatDurationMs, formatSeconds } from '../format';
import { useTick } from '../hooks';
import type { AiTestResult, SettingsResponse } from '../types';

type Source = 'latest' | 'upload';

interface Overrides {
  ai_model: string;
  ai_quality: string;
  ai_size: string;
  ai_prompt: string;
}

export function AiTest() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [source, setSource] = useState<Source>('latest');
  const [file, setFile] = useState<File | null>(null);
  const [useOverrides, setUseOverrides] = useState(false);
  const [overrides, setOverrides] = useState<Overrides | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [running, setRunning] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [result, setResult] = useState<AiTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  useTick(running ? 1000 : 60_000);

  useEffect(() => {
    apiGet<SettingsResponse>('/api/admin/settings')
      .then((response) => {
        setSettings(response);
        setOverrides({
          ai_model: response.settings.ai_model,
          ai_quality: response.settings.ai_quality,
          ai_size: response.settings.ai_size,
          ai_prompt: response.settings.ai_prompt,
        });
      })
      .catch((caught) => setError(`設定讀取失敗：${describeError(caught)}`));
  }, []);

  const canSubmit = acknowledged && !running && (source === 'latest' || file !== null);

  const submit = async () => {
    if (!canSubmit) {
      return;
    }
    const form = new FormData();
    if (source === 'upload' && file) {
      form.append('image', file);
    }
    if (useOverrides && overrides) {
      form.append('ai_model', overrides.ai_model);
      form.append('ai_quality', overrides.ai_quality);
      form.append('ai_size', overrides.ai_size);
      form.append('ai_prompt', overrides.ai_prompt);
    }
    const timeoutSeconds = settings?.settings.ai_timeout_seconds ?? 180;
    setRunning(true);
    setStartedAt(Date.now());
    setResult(null);
    setError(null);
    try {
      // 等待時間需涵蓋 AI 逾時再加上傳與存檔的餘裕
      const response = await apiPostForm<AiTestResult>('/api/admin/ai-test', form, (timeoutSeconds + 120) * 1000);
      setResult(response);
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.code === 'no_test_image') {
        setError('目前沒有可用的作品，請改為上傳圖片。');
      } else {
        setError(describeError(caught));
      }
    } finally {
      setRunning(false);
      setAcknowledged(false);
    }
  };

  const elapsed = running && startedAt !== null ? (Date.now() - startedAt) / 1000 : null;

  return (
    <Section id="ai-test" title="試生成">
      <p className="adm-muted">用來和藝術家調整提示詞與參數；結果只顯示在這裡，不會進入播放佇列。</p>

      <fieldset className="adm-fieldset" disabled={running}>
        <legend>圖片來源</legend>
        <label className="adm-radio">
          <input type="radio" name="ai-test-source" checked={source === 'latest'} onChange={() => setSource('latest')} />
          使用最新作品
        </label>
        <label className="adm-radio">
          <input type="radio" name="ai-test-source" checked={source === 'upload'} onChange={() => setSource('upload')} />
          上傳圖片
        </label>
        {source === 'upload' ? (
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        ) : null}
      </fieldset>

      <fieldset className="adm-fieldset" disabled={running}>
        <legend>參數</legend>
        <label className="adm-check-line">
          <input type="checkbox" checked={useOverrides} onChange={(event) => setUseOverrides(event.target.checked)} />
          覆寫目前設定（不會儲存）
        </label>
        {useOverrides && overrides && settings ? (
          <div className="adm-form adm-form--compact">
            <div className="adm-field">
              <label htmlFor="test-model">圖片模型</label>
              <select
                id="test-model"
                value={overrides.ai_model}
                onChange={(event) => setOverrides({ ...overrides, ai_model: event.target.value })}
              >
                {settings.allowed_models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </div>
            <div className="adm-field">
              <label htmlFor="test-quality">AI 品質</label>
              <select
                id="test-quality"
                value={overrides.ai_quality}
                onChange={(event) => setOverrides({ ...overrides, ai_quality: event.target.value })}
              >
                {settings.allowed_qualities.map((quality) => (
                  <option key={quality} value={quality}>
                    {quality}
                  </option>
                ))}
              </select>
            </div>
            <div className="adm-field">
              <label htmlFor="test-size">輸出尺寸</label>
              <input
                id="test-size"
                type="text"
                value={overrides.ai_size}
                onChange={(event) => setOverrides({ ...overrides, ai_size: event.target.value })}
              />
            </div>
            <div className="adm-field">
              <label htmlFor="test-prompt">提示詞</label>
              <textarea
                id="test-prompt"
                rows={6}
                value={overrides.ai_prompt}
                onChange={(event) => setOverrides({ ...overrides, ai_prompt: event.target.value })}
              />
            </div>
          </div>
        ) : (
          <p className="adm-muted adm-small">
            使用目前設定：{settings ? `${settings.settings.ai_model}・${settings.settings.ai_quality}・${settings.settings.ai_size}` : '載入中…'}
          </p>
        )}
      </fieldset>

      <Notice tone="warn">
        <strong>每次試生成都會呼叫 OpenAI 並產生 API 費用。</strong>
        生成可能需要數分鐘，期間請勿重複送出。
      </Notice>
      <label className="adm-check-line">
        <input
          type="checkbox"
          checked={acknowledged}
          disabled={running}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        我了解這會產生 API 費用
      </label>
      <div className="adm-row">
        <button type="button" className="adm-btn adm-btn--primary" disabled={!canSubmit} onClick={() => void submit()}>
          {running ? `生成中… ${elapsed === null ? '' : formatSeconds(elapsed)}` : '送出試生成'}
        </button>
      </div>

      {error ? <Notice tone="bad">{error}</Notice> : null}
      {result ? (
        <div className="adm-card">
          <p className="adm-kv">
            <span>結果</span>
            <strong className={result.status === 'succeeded' ? 'adm-text-ok' : 'adm-text-bad'}>
              {result.status === 'succeeded' ? '成功' : '失敗'}
            </strong>
          </p>
          <p className="adm-kv">
            <span>耗時</span>
            <strong>{formatDurationMs(result.duration_ms)}</strong>
          </p>
          <p className="adm-kv">
            <span>參數</span>
            <strong>
              {result.model ?? '—'}・{result.quality ?? '—'}・{result.size ?? '—'}
            </strong>
          </p>
          {result.status === 'succeeded' && result.result_url ? (
            <a href={result.result_url} target="_blank" rel="noreferrer" className="adm-result-image">
              <img src={result.result_url} alt="試生成結果" />
            </a>
          ) : (
            <Notice tone="bad">
              {result.error_code ?? 'error'}：{result.error_message ?? '沒有詳細訊息'}
            </Notice>
          )}
        </div>
      ) : null}
    </Section>
  );
}
