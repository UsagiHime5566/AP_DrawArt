// 尚無任何作品時的待機構圖（暫時視覺）。左右兩螢幕並排讀作「人工｜對畫(話)」。

import type { DisplayRole } from '../shared/types';

const COPY: Record<'visitor' | 'ai', { title: string; caption: string }> = {
  visitor: { title: '人工', caption: '人的筆跡，將在這裡出現' },
  ai: { title: '對畫(話)', caption: '機器的回話，將在這裡出現' },
};

export function Standby({ role }: { role: DisplayRole }) {
  const copy = COPY[role === 'ai' ? 'ai' : 'visitor'];
  return (
    <div className={`standby standby--${role}`}>
      <span className="standby__shape standby__shape--circle" />
      <span className="standby__shape standby__shape--slab" />
      <span className="standby__shape standby__shape--stripe" />
      <span className="standby__shape standby__shape--grid" />
      <div className="standby__copy">
        <h1 className="standby__title">{copy.title}</h1>
        <p className="standby__caption">{copy.caption}</p>
      </div>
    </div>
  );
}
