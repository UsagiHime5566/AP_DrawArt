// 平板裝置相關：全螢幕、停用瀏覽器手勢、本機識別與小工具。

import { randomId } from './model';

const SESSION_KEY = 'ap-drawart.draw.sessionId';
const TABLET_KEY = 'ap-drawart.draw.tabletId';

let fullscreenUnsupported = false;
let fullscreenDisabled = false;

type FullscreenDocument = Document & { webkitFullscreenElement?: Element | null };
type FullscreenElement = HTMLElement & { webkitRequestFullscreen?: () => void };

/**
 * 展覽視窗已由啟動器以 kiosk 全螢幕開啟，不再進入 HTML5 全螢幕：
 * 在 HTML5 全螢幕中，瀏覽器會攔下 Esc 用來離開全螢幕，頁面收不到按鍵。
 */
export function disableFullscreenRequests(): void {
  fullscreenDisabled = true;
}

/** 請求全螢幕；需在使用者手勢內呼叫，失敗或不支援時直接略過 */
export function requestFullscreen(): void {
  if (fullscreenUnsupported || fullscreenDisabled) {
    return;
  }
  const doc = document as FullscreenDocument;
  if (doc.fullscreenElement || doc.webkitFullscreenElement) {
    return;
  }
  const element = document.documentElement as FullscreenElement;
  try {
    if (typeof element.requestFullscreen === 'function') {
      element.requestFullscreen({ navigationUI: 'hide' }).catch(() => undefined);
    } else if (typeof element.webkitRequestFullscreen === 'function') {
      element.webkitRequestFullscreen();
    } else {
      fullscreenUnsupported = true;
    }
  } catch {
    // 部分瀏覽器在非手勢情境會直接拋錯，略過即可
  }
}

/** 停用捲動、縮放、長按選單與文字選取；觸控抬起時嘗試進入全螢幕 */
export function installDeviceGuards(): void {
  const prevent = (event: Event) => event.preventDefault();
  document.addEventListener('touchmove', prevent, { passive: false });
  document.addEventListener('gesturestart', prevent);
  document.addEventListener('gesturechange', prevent);
  document.addEventListener('contextmenu', prevent);
  document.addEventListener('selectstart', prevent);
  document.addEventListener('dblclick', prevent, { passive: false });
  // 觸控的 pointerdown 不算可啟動全螢幕的手勢，pointerup 才算
  document.addEventListener('pointerup', () => requestFullscreen(), true);
}

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 儲存空間不可用時略過
  }
}

function safeRemove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // 儲存空間不可用時略過
  }
}

export function readStoredSessionId(): string | null {
  return safeGet(SESSION_KEY);
}

export function storeSessionId(sessionId: string): void {
  safeSet(SESSION_KEY, sessionId);
}

export function clearStoredSessionId(): void {
  safeRemove(SESSION_KEY);
}

export function getTabletId(): string {
  let id = safeGet(TABLET_KEY);
  if (!id) {
    id = randomId();
    safeSet(TABLET_KEY, id);
  }
  return id;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** 預載並解碼圖片，完成後才算可開始作畫 */
export async function preloadImage(url: string): Promise<void> {
  const image = new Image();
  image.decoding = 'async';
  image.src = url;
  await image.decode();
}
