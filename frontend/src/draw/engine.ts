// 繪圖引擎：以 1920×1080 邏輯座標記錄筆畫，畫面上的取樣倍率另外處理。
// 進行中的筆畫逐段增量繪製；復原、重做、清空與縮放時由操作紀錄完整重播。

import { CANVAS_HEIGHT, CANVAS_WIDTH, randomId, type Op, type Stroke, type Tool } from './model';
import { ERASER_SCALE } from './palette';

/** 與前一點距離小於此值（邏輯像素）的點略過，兼作基本平滑 */
const MIN_STEP = 1.5;

/** 畫面取樣倍率上限，避免高解析平板佔用過多記憶體 */
const MAX_BACKING_SCALE = 2;

interface ActiveStroke {
  stroke: Stroke;
  /** 已繪製到的位置，下一段曲線從這裡接續 */
  endX: number;
  endY: number;
}

export interface EngineStatus {
  canUndo: boolean;
  canRedo: boolean;
  canClear: boolean;
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}

function lineWidth(stroke: Stroke): number {
  return stroke.tool === 'eraser' ? stroke.size * ERASER_SCALE : stroke.size;
}

function applyStyle(ctx: CanvasRenderingContext2D, stroke: Stroke): void {
  const eraser = stroke.tool === 'eraser';
  ctx.globalCompositeOperation = eraser ? 'destination-out' : 'source-over';
  const color = eraser ? '#000000' : stroke.color;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = lineWidth(stroke);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
}

function drawDot(ctx: CanvasRenderingContext2D, stroke: Stroke, x: number, y: number): void {
  applyStyle(ctx, stroke);
  ctx.beginPath();
  ctx.arc(x, y, lineWidth(stroke) / 2, 0, Math.PI * 2);
  ctx.fill();
}

/** 完整繪製一筆：以相鄰點中點為端點的二次曲線串接 */
export function renderStroke(ctx: CanvasRenderingContext2D, stroke: Stroke): void {
  const p = stroke.points;
  const count = p.length / 2;
  if (count === 0) {
    return;
  }
  if (count === 1) {
    drawDot(ctx, stroke, p[0], p[1]);
    return;
  }
  applyStyle(ctx, stroke);
  ctx.beginPath();
  ctx.moveTo(p[0], p[1]);
  for (let i = 1; i < count - 1; i += 1) {
    const cx = p[i * 2];
    const cy = p[i * 2 + 1];
    ctx.quadraticCurveTo(cx, cy, (cx + p[i * 2 + 2]) / 2, (cy + p[i * 2 + 3]) / 2);
  }
  ctx.lineTo(p[(count - 1) * 2], p[(count - 1) * 2 + 1]);
  ctx.stroke();
}

/** 最後一次清空之後的筆畫 */
function visibleStrokes(ops: readonly Op[]): Stroke[] {
  let start = 0;
  for (let i = ops.length - 1; i >= 0; i -= 1) {
    if (ops[i].kind === 'clear') {
      start = i + 1;
      break;
    }
  }
  const strokes: Stroke[] = [];
  for (const op of ops.slice(start)) {
    if (op.kind === 'stroke') {
      strokes.push(op.stroke);
    }
  }
  return strokes;
}

export function renderOps(ctx: CanvasRenderingContext2D, ops: readonly Op[]): void {
  for (const stroke of visibleStrokes(ops)) {
    renderStroke(ctx, stroke);
  }
  ctx.globalCompositeOperation = 'source-over';
}

/** 依操作紀錄產生 1920×1080 透明背景的 strokes.png */
export async function exportPng(ops: readonly Op[]): Promise<ArrayBuffer> {
  const canvas = document.createElement('canvas');
  canvas.width = CANVAS_WIDTH;
  canvas.height = CANVAS_HEIGHT;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    throw new Error('無法建立匯出畫布');
  }
  renderOps(ctx, ops);
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((result) => (result ? resolve(result) : reject(new Error('匯出 PNG 失敗'))), 'image/png');
  });
  return blob.arrayBuffer();
}

export class DrawingEngine {
  tool: Tool = 'pen';
  color: string;
  size: number;

  private readonly ctx: CanvasRenderingContext2D;
  private scale = 1;
  private ops: Op[] = [];
  private redoStack: Op[] = [];
  private readonly active = new Map<number, ActiveStroke>();
  private enabled = true;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly onChange: () => void,
    initialColor: string,
    initialSize: number,
  ) {
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      throw new Error('無法建立繪圖畫布');
    }
    this.ctx = ctx;
    this.color = initialColor;
    this.size = initialSize;
  }

  /** 依畫布在螢幕上的實際大小調整取樣倍率並重繪 */
  resize(cssWidth: number, devicePixelRatio: number): void {
    const scale = Math.min(MAX_BACKING_SCALE, Math.max(0.25, (cssWidth * devicePixelRatio) / CANVAS_WIDTH));
    const width = Math.max(1, Math.round(CANVAS_WIDTH * scale));
    const height = Math.max(1, Math.round(CANVAS_HEIGHT * scale));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    this.scale = width / CANVAS_WIDTH;
    this.redraw();
  }

  load(ops: readonly Op[]): void {
    this.ops = [...ops];
    this.redoStack = [];
    this.redraw();
  }

  getOps(): Op[] {
    return [...this.ops];
  }

  status(): EngineStatus {
    return {
      canUndo: this.enabled && this.ops.length > 0,
      canRedo: this.enabled && this.redoStack.length > 0,
      canClear: this.enabled && visibleStrokes(this.ops).length > 0,
    };
  }

  isActive(pointerId: number): boolean {
    return this.active.has(pointerId);
  }

  /** 結束所有進行中的筆畫並停止接受輸入 */
  freeze(): void {
    this.endAll();
    this.enabled = false;
    this.onChange();
  }

  begin(pointerId: number, x: number, y: number): void {
    if (!this.enabled || this.active.has(pointerId)) {
      return;
    }
    const stroke: Stroke = {
      id: randomId(),
      tool: this.tool,
      color: this.color,
      size: this.size,
      points: [round(x), round(y)],
    };
    this.active.set(pointerId, { stroke, endX: stroke.points[0], endY: stroke.points[1] });
    this.useLogicalTransform();
    drawDot(this.ctx, stroke, stroke.points[0], stroke.points[1]);
    this.ctx.globalCompositeOperation = 'source-over';
  }

  /** coords 為 [x, y, x, y, ...]（可含合併的高頻事件） */
  extend(pointerId: number, coords: readonly number[]): void {
    const current = this.active.get(pointerId);
    if (!current || !this.enabled) {
      return;
    }
    const { stroke } = current;
    const p = stroke.points;
    const ctx = this.ctx;
    this.useLogicalTransform();
    applyStyle(ctx, stroke);
    for (let i = 0; i + 1 < coords.length; i += 2) {
      const x = round(coords[i]);
      const y = round(coords[i + 1]);
      if (Math.hypot(x - p[p.length - 2], y - p[p.length - 1]) < MIN_STEP) {
        continue;
      }
      p.push(x, y);
      const count = p.length / 2;
      if (count < 3) {
        continue;
      }
      const cx = p[(count - 2) * 2];
      const cy = p[(count - 2) * 2 + 1];
      const mx = (cx + x) / 2;
      const my = (cy + y) / 2;
      ctx.beginPath();
      ctx.moveTo(current.endX, current.endY);
      ctx.quadraticCurveTo(cx, cy, mx, my);
      ctx.stroke();
      current.endX = mx;
      current.endY = my;
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  end(pointerId: number): void {
    const current = this.active.get(pointerId);
    if (!current) {
      return;
    }
    this.active.delete(pointerId);
    const p = current.stroke.points;
    if (p.length >= 4) {
      this.useLogicalTransform();
      applyStyle(this.ctx, current.stroke);
      this.ctx.beginPath();
      this.ctx.moveTo(current.endX, current.endY);
      this.ctx.lineTo(p[p.length - 2], p[p.length - 1]);
      this.ctx.stroke();
      this.ctx.globalCompositeOperation = 'source-over';
    }
    // 多指同時畫時，依結束順序逐筆加入紀錄
    this.ops.push({ kind: 'stroke', stroke: current.stroke });
    this.redoStack = [];
    this.onChange();
  }

  endAll(): void {
    for (const pointerId of [...this.active.keys()]) {
      this.end(pointerId);
    }
  }

  undo(): void {
    const op = this.enabled ? this.ops.pop() : undefined;
    if (!op) {
      return;
    }
    this.redoStack.push(op);
    this.redraw();
    this.onChange();
  }

  redo(): void {
    const op = this.enabled ? this.redoStack.pop() : undefined;
    if (!op) {
      return;
    }
    this.ops.push(op);
    this.redraw();
    this.onChange();
  }

  /** 清空也記為一次操作，可以復原 */
  clear(): void {
    if (!this.status().canClear) {
      return;
    }
    this.ops.push({ kind: 'clear' });
    this.redoStack = [];
    this.redraw();
    this.onChange();
  }

  private useLogicalTransform(): void {
    this.ctx.setTransform(this.scale, 0, 0, this.scale, 0, 0);
  }

  private redraw(): void {
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = 'source-over';
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.useLogicalTransform();
    renderOps(ctx, this.ops);
    for (const current of this.active.values()) {
      renderStroke(ctx, current.stroke);
      const p = current.stroke.points;
      const count = p.length / 2;
      if (count >= 2) {
        current.endX = (p[(count - 2) * 2] + p[(count - 1) * 2]) / 2;
        current.endY = (p[(count - 2) * 2 + 1] + p[(count - 1) * 2 + 1]) / 2;
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }
}
