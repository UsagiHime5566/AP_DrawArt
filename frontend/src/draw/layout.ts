// 作畫畫面的版面計算：畫布固定 16:9 等比例置中，工具列放在剩餘空間。
// 右上角 HOTZONE_SIZE 的區域保留給維護入口，不放畫布或工具。

import { useEffect, useState, type CSSProperties } from 'react';

export const HEADER_HEIGHT = 80;
export const HOTZONE_SIZE = 80;
const MARGIN = 12;
const SIDE_PANEL_WIDTH = 232;
const BOTTOM_BAR_HEIGHT = 164;
const MIN_STAGE_WIDTH = 320;

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface DrawLayout {
  /** side：工具列在右側；bottom：工具列在下方（例如 4:3 的 iPad） */
  mode: 'side' | 'bottom';
  header: Rect;
  stage: Rect;
  toolbar: Rect;
}

function fit16x9(availableWidth: number, availableHeight: number): { width: number; height: number } {
  const unit = Math.max(MIN_STAGE_WIDTH / 16, Math.min(availableWidth / 16, availableHeight / 9));
  return { width: Math.floor(unit * 16), height: Math.floor(unit * 9) };
}

export function computeLayout(viewportWidth: number, viewportHeight: number): DrawLayout {
  const header: Rect = { left: 0, top: 0, width: Math.max(0, viewportWidth - HOTZONE_SIZE), height: HEADER_HEIGHT };
  const bodyTop = HEADER_HEIGHT;
  const bodyHeight = viewportHeight - HEADER_HEIGHT - MARGIN;

  const sideArea = { width: viewportWidth - SIDE_PANEL_WIDTH - MARGIN * 3, height: bodyHeight };
  const bottomArea = { width: viewportWidth - MARGIN * 2, height: bodyHeight - BOTTOM_BAR_HEIGHT - MARGIN };
  const side = fit16x9(sideArea.width, sideArea.height);
  const bottom = fit16x9(bottomArea.width, bottomArea.height);

  if (side.width >= bottom.width) {
    return {
      mode: 'side',
      header,
      stage: {
        left: MARGIN + Math.max(0, (sideArea.width - side.width) / 2),
        top: bodyTop + Math.max(0, (sideArea.height - side.height) / 2),
        width: side.width,
        height: side.height,
      },
      toolbar: {
        left: viewportWidth - SIDE_PANEL_WIDTH - MARGIN,
        top: bodyTop,
        width: SIDE_PANEL_WIDTH,
        height: bodyHeight,
      },
    };
  }

  return {
    mode: 'bottom',
    header,
    stage: {
      left: MARGIN + Math.max(0, (bottomArea.width - bottom.width) / 2),
      top: bodyTop + Math.max(0, (bottomArea.height - bottom.height) / 2),
      width: bottom.width,
      height: bottom.height,
    },
    toolbar: {
      left: MARGIN,
      top: viewportHeight - BOTTOM_BAR_HEIGHT - MARGIN,
      width: viewportWidth - MARGIN * 2,
      height: BOTTOM_BAR_HEIGHT,
    },
  };
}

export function rectStyle(rect: Rect): CSSProperties {
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

/** 視窗尺寸（含旋轉、全螢幕切換） */
export function useViewport(): { width: number; height: number } {
  const [size, setSize] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }));
  useEffect(() => {
    const update = () => setSize({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', update);
    window.addEventListener('orientationchange', update);
    document.addEventListener('fullscreenchange', update);
    window.visualViewport?.addEventListener('resize', update);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('orientationchange', update);
      document.removeEventListener('fullscreenchange', update);
      window.visualViewport?.removeEventListener('resize', update);
    };
  }, []);
  return size;
}
