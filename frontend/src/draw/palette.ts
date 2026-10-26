// 暫時色票與粗細級距；實際配色依藝術家與展覽視覺定稿後替換。
// 色票不含純白：白筆在白底上看不見，卻會被判定為非空白。

export interface Swatch {
  name: string;
  value: string;
}

export const COLORS: readonly Swatch[] = [
  { name: '墨黑', value: '#111111' },
  { name: '朱紅', value: '#e4352b' },
  { name: '洋紅', value: '#ff3b6b' },
  { name: '橙', value: '#ff8a00' },
  { name: '鉻黃', value: '#ffd400' },
  { name: '薄荷綠', value: '#00b38a' },
  { name: '鈷藍', value: '#1f5bff' },
  { name: '紫', value: '#7b2ff7' },
];

export interface BrushSize {
  name: string;
  value: number;
}

export const SIZES: readonly BrushSize[] = [
  { name: '細', value: 8 },
  { name: '中', value: 20 },
  { name: '粗', value: 44 },
];

/** 橡皮擦比同級畫筆略粗，方便手指擦除 */
export const ERASER_SCALE = 1.6;
