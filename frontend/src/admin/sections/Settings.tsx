// 設定：編輯所有後臺設定，送出時只送有變動的欄位並建立新版本。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiRequestError, apiGet, apiPatch } from '../../shared/api';
import { ActionFeedback, Notice, Section } from '../components';
import { describeError } from '../format';
import { useAction } from '../hooks';
import type { AdminSettings, SettingsResponse } from '../types';

type FieldKey = keyof AdminSettings;

interface FieldSpec {
  key: FieldKey;
  label: string;
  help: string;
  unit?: string;
  kind: 'int' | 'decimal' | 'percent' | 'model' | 'quality' | 'size' | 'prompt';
  min?: number;
  max?: number;
}

const FIELDS: FieldSpec[] = [
  { key: 'drawing_duration_seconds', label: '作畫時間', unit: '秒', kind: 'int', min: 10, max: 600, help: '新場次使用新設定，進行中的倒數不受影響。' },
  { key: 'turnaround_seconds', label: '換場等待', unit: '秒', kind: 'int', min: 0, max: 300, help: '每場開始時綁定，場次結束後由主機計算開放時間。' },
  { key: 'visitor_seconds', label: '觀眾作品展示', unit: '秒', kind: 'int', min: 1, max: 600, help: 'AI 未就緒時延長停留在本人作品。' },
  { key: 'ai_seconds', label: 'AI 圖片展示', unit: '秒', kind: 'int', min: 1, max: 600, help: 'AI 成功圖片與失敗文字版使用相同時間。' },
  { key: 'artist_seconds', label: '藝術家原圖展示', unit: '秒', kind: 'int', min: 1, max: 600, help: '本人原圖段播完，才開放下一位觸筆。' },
  { key: 'fade_seconds', label: '換段淡入', unit: '秒', kind: 'decimal', min: 0, max: 10, help: '觀眾、AI、原圖與下一件之間以淡入切換，可到小數一位；0 為直接切換。淡入完成才開始計算每段秒數。' },
  { key: 'gallery_limit', label: '最近輪播作品數', unit: '件', kind: 'int', min: 1, max: 1000, help: '只影響歷史重播，不刪除本地作品、不取消首播。' },
  { key: 'reference_opacity', label: '參考圖不透明度', unit: '%', kind: 'percent', min: 0, max: 100, help: '只影響作畫底圖，輪播原圖固定 100%。' },
  { key: 'ai_model', label: '圖片模型', kind: 'model', help: '已開啟的場次與排隊中的作品沿用原設定。' },
  { key: 'ai_quality', label: 'AI 品質', kind: 'quality', help: '品質越高通常越慢、費用越高。' },
  { key: 'ai_size', label: 'AI 輸出尺寸', kind: 'size', help: 'WIDTHxHEIGHT，寬高須為 16 的倍數；2048x1152 正好是 16:9。' },
  { key: 'ai_timeout_seconds', label: 'AI 逾時', unit: '秒', kind: 'int', min: 30, max: 900, help: '超過即判定失敗並顯示「此刻，機器沉默。」，不重試。' },
  { key: 'ai_prompt', label: 'AI 提示詞', kind: 'prompt', help: '每件作品綁定送出當時的提示詞。' },
];

const SIZE_PRESETS = ['2048x1152', '1536x864', '2560x1440', '1536x1024', '1024x1024'];

/** 表單值一律以字串保存，送出時再轉型 */
type FormValues = Record<FieldKey, string>;

function toForm(settings: AdminSettings): FormValues {
  return {
    drawing_duration_seconds: String(settings.drawing_duration_seconds),
    turnaround_seconds: String(settings.turnaround_seconds),
    display_min_seconds: String(settings.display_min_seconds),
    visitor_seconds: String(settings.visitor_seconds),
    ai_seconds: String(settings.ai_seconds),
    artist_seconds: String(settings.artist_seconds),
    gallery_limit: String(settings.gallery_limit),
    fade_seconds: String(settings.fade_seconds),
    reference_opacity: String(Math.round(settings.reference_opacity * 100)),
    ai_model: settings.ai_model,
    ai_prompt: settings.ai_prompt,
    ai_quality: settings.ai_quality,
    ai_size: settings.ai_size,
    ai_timeout_seconds: String(settings.ai_timeout_seconds),
  };
}

function parseField(spec: FieldSpec, raw: string): { value?: unknown; error?: string } {
  const text = raw.trim();
  if (spec.kind === 'int' || spec.kind === 'percent' || spec.kind === 'decimal') {
    const pattern = spec.kind === 'decimal' ? /^\d+(\.\d)?$/ : /^\d+$/;
    if (!pattern.test(text)) {
      return { error: spec.kind === 'decimal' ? '請輸入數字（最多小數一位）' : '請輸入整數' };
    }
    const number = Number(text);
    if ((spec.min !== undefined && number < spec.min) || (spec.max !== undefined && number > spec.max)) {
      return { error: `必須介於 ${spec.min} 到 ${spec.max}` };
    }
    return { value: spec.kind === 'percent' ? number / 100 : number };
  }
  if (spec.kind === 'size' && !/^\d+x\d+$/.test(text)) {
    return { error: '格式必須是 WIDTHxHEIGHT' };
  }
  if (spec.kind === 'prompt' && !text) {
    return { error: '不可為空白' };
  }
  return { value: spec.kind === 'prompt' ? raw : text };
}

export function Settings(props: { overviewVersionId: number | null; onSaved: () => void }) {
  const [loaded, setLoaded] = useState<SettingsResponse | null>(null);
  const [form, setForm] = useState<FormValues | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const save = useAction();

  const load = useCallback(async () => {
    try {
      const response = await apiGet<SettingsResponse>('/api/admin/settings');
      setLoaded(response);
      setForm(toForm(response.settings));
      setFieldErrors({});
      setLoadError(null);
    } catch (error) {
      setLoadError(describeError(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const baseline = useMemo(() => (loaded ? toForm(loaded.settings) : null), [loaded]);
  const dirtyKeys = useMemo(
    () => (form && baseline ? FIELDS.filter((spec) => form[spec.key] !== baseline[spec.key]).map((spec) => spec.key) : []),
    [form, baseline],
  );
  const dirty = dirtyKeys.length > 0;
  const outdated = loaded !== null && props.overviewVersionId !== null && props.overviewVersionId !== loaded.version_id;

  // 其他裝置更新了設定且本機沒有未儲存的修改時，自動重新載入
  useEffect(() => {
    if (outdated && !dirty && !save.pending) {
      void load();
    }
  }, [outdated, dirty, save.pending, load]);

  const update = (key: FieldKey, value: string) => {
    setForm((previous) => (previous ? { ...previous, [key]: value } : previous));
    setFieldErrors((previous) => ({ ...previous, [key]: undefined }));
    save.clear();
  };

  const submit = () => {
    if (!form || !loaded) {
      return;
    }
    const patch: Record<string, unknown> = {};
    const errors: Partial<Record<FieldKey, string>> = {};
    for (const spec of FIELDS) {
      if (!dirtyKeys.includes(spec.key)) {
        continue;
      }
      const parsed = parseField(spec, form[spec.key]);
      if (parsed.error) {
        errors[spec.key] = parsed.error;
      } else {
        patch[spec.key] = parsed.value;
      }
    }
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      return;
    }
    void save.run(async () => {
      try {
        const response = await apiPatch<SettingsResponse>('/api/admin/settings', patch);
        setLoaded(response);
        setForm(toForm(response.settings));
        props.onSaved();
        return `已儲存為版本 v${response.version_id}，之後的新場次使用新設定。`;
      } catch (error) {
        if (error instanceof ApiRequestError && error.status === 422) {
          const field = error.details.field;
          if (typeof field === 'string' && field in (form ?? {})) {
            setFieldErrors({ [field as FieldKey]: error.message });
          }
        }
        throw error;
      }
    });
  };

  const fillDefaults = () => {
    if (loaded) {
      setForm(toForm(loaded.defaults));
      setFieldErrors({});
      save.clear();
    }
  };

  const discard = () => {
    if (baseline) {
      setForm(baseline);
      setFieldErrors({});
      save.clear();
    }
  };

  return (
    <Section id="settings" title="設定" aside={loaded ? <span className="adm-muted">目前版本 v{loaded.version_id}</span> : null}>
      {loadError ? <Notice tone="warn">設定讀取失敗：{loadError}</Notice> : null}
      {outdated && dirty ? (
        <Notice tone="warn">設定已在其他裝置更新為 v{props.overviewVersionId}；儲存會以你的修改覆蓋這些欄位，可先「放棄修改」重新載入。</Notice>
      ) : null}
      {!form || !loaded ? (
        <p className="adm-muted">載入中…</p>
      ) : (
        <form
          className="adm-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          {FIELDS.map((spec) => {
            const error = fieldErrors[spec.key];
            const id = `setting-${spec.key}`;
            const changed = dirtyKeys.includes(spec.key);
            return (
              <div key={spec.key} className={`adm-field${error ? ' adm-field--error' : ''}${changed ? ' adm-field--changed' : ''}`}>
                <label htmlFor={id}>
                  {spec.label}
                  {spec.unit ? <span className="adm-muted">（{spec.unit}）</span> : null}
                  {changed ? <span className="adm-changed">已修改</span> : null}
                </label>
                {spec.kind === 'model' ? (
                  <select id={id} value={form[spec.key]} onChange={(event) => update(spec.key, event.target.value)}>
                    {withCurrent(loaded.allowed_models, form[spec.key]).map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                ) : spec.kind === 'quality' ? (
                  <select id={id} value={form[spec.key]} onChange={(event) => update(spec.key, event.target.value)}>
                    {withCurrent(loaded.allowed_qualities, form[spec.key]).map((quality) => (
                      <option key={quality} value={quality}>
                        {quality}
                      </option>
                    ))}
                  </select>
                ) : spec.kind === 'prompt' ? (
                  <textarea id={id} rows={8} value={form[spec.key]} onChange={(event) => update(spec.key, event.target.value)} />
                ) : spec.kind === 'size' ? (
                  <>
                    <input
                      id={id}
                      type="text"
                      inputMode="text"
                      list={`${id}-presets`}
                      value={form[spec.key]}
                      onChange={(event) => update(spec.key, event.target.value)}
                    />
                    <datalist id={`${id}-presets`}>
                      {SIZE_PRESETS.map((size) => (
                        <option key={size} value={size} />
                      ))}
                    </datalist>
                  </>
                ) : (
                  <input
                    id={id}
                    type="number"
                    inputMode={spec.kind === 'decimal' ? 'decimal' : 'numeric'}
                    min={spec.min}
                    max={spec.max}
                    step={spec.kind === 'decimal' ? 0.1 : 1}
                    value={form[spec.key]}
                    onChange={(event) => update(spec.key, event.target.value)}
                  />
                )}
                {error ? <p className="adm-field__error">{error}</p> : null}
                <p className="adm-field__help">{spec.help}</p>
              </div>
            );
          })}
          <div className="adm-row adm-row--sticky">
            <button type="submit" className="adm-btn adm-btn--primary" disabled={!dirty || save.pending}>
              {save.pending ? '儲存中…' : dirty ? `儲存 ${dirtyKeys.length} 項修改` : '沒有修改'}
            </button>
            <button type="button" className="adm-btn" onClick={discard} disabled={!dirty || save.pending}>
              放棄修改
            </button>
            <button type="button" className="adm-btn" onClick={fillDefaults} disabled={save.pending}>
              填入預設值
            </button>
          </div>
          <ActionFeedback error={save.error} message={save.message} />
        </form>
      )}
    </Section>
  );
}

/** 目前值不在允許清單時仍保留在選項中，避免表單默默改值 */
function withCurrent(options: string[], current: string): string[] {
  return options.includes(current) ? options : [current, ...options];
}
