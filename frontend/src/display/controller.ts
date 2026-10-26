// 展示頁同步控制：連線、預載、依 switch_at 切換，並向主機回報 ready / presented / heartbeat。
// 切換時機一律由主機決定，展示頁不自行計時或決定下一件。

import { apiGet } from '../shared/api';
import { msUntil, syncServerClock } from '../shared/clock';
import type {
  ClientMessage,
  DisplayCurrentResponse,
  DisplayRole,
  DisplaySnapshot,
  Progress,
  ServerMessage,
} from '../shared/types';
import { ReconnectingSocket } from '../shared/ws';
import { preloadImage } from './preload';
import { imageUrlForRole, makeClientId } from './role';

const HEARTBEAT_MS = 5_000;
const HEALTH_POLL_MS = 10_000;
const ERROR_REPORT_INTERVAL_MS = 5_000;

export interface ViewState {
  /** 目前顯示的快照；null 表示尚未取得任何畫面 */
  shown: DisplaySnapshot | null;
  /** 目前畫面的圖片是否成功載入；失敗時以黑畫面代替 */
  shownOk: boolean;
  progress: Progress | null;
  healthStatus: string | null;
  adminUrls: string[];
}

interface PreparedEntry {
  snapshot: DisplaySnapshot;
  loaded: boolean;
  ok: boolean;
}

interface HealthResponse {
  status: string;
  admin_urls?: string[];
}

export class DisplayController {
  private state: ViewState = {
    shown: null,
    shownOk: true,
    progress: null,
    healthStatus: null,
    adminUrls: [],
  };
  private readonly listeners = new Set<() => void>();
  private readonly socket: ReconnectingSocket<ServerMessage, ClientMessage>;
  private readonly prepared = new Map<number, PreparedEntry>();
  private pendingCommit: { revision: number; switchAt: string } | null = null;
  private switchTimer: number | null = null;
  /** 每次要求換畫面就遞增；非同步載入完成時比對，過期的要求不再生效 */
  private showToken = 0;
  private lastErrorReportAt = 0;
  private recovering = false;

  constructor(private readonly role: DisplayRole) {
    const clientId = makeClientId();
    this.socket = new ReconnectingSocket<ServerMessage, ClientMessage>(
      `/ws/events?role=${role}&client_id=${clientId}`,
      { onMessage: (message) => this.handleMessage(message) },
    );
    window.setInterval(() => this.sendHeartbeat(), HEARTBEAT_MS);
    window.setInterval(() => void this.pollHealth(), HEALTH_POLL_MS);
    void this.pollHealth();
    window.addEventListener('error', (event) => this.reportError(`前端錯誤：${event.message}`));
    window.addEventListener('unhandledrejection', (event) =>
      this.reportError(`未處理的 Promise 錯誤：${String(event.reason)}`),
    );
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly getState = (): ViewState => this.state;

  private setState(patch: Partial<ViewState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private get shownRevision(): number | null {
    return this.state.shown?.revision ?? null;
  }

  private send(message: ClientMessage): void {
    this.socket.send(message);
  }

  private handleMessage(message: ServerMessage): void {
    syncServerClock(message.server_now);
    switch (message.type) {
      case 'hello':
        this.setState({ progress: message.progress });
        if (message.current) {
          if (message.current.revision === this.shownRevision) {
            // 重新連線：畫面已是同一版本，重新回報讓主機恢復計時
            this.send({ type: 'presented', revision: message.current.revision });
          } else {
            void this.showWhenLoaded(message.current);
          }
        }
        if (message.pending) {
          this.handlePrepare(message.pending);
        }
        break;
      case 'prepare':
        this.handlePrepare(message.snapshot);
        break;
      case 'commit':
        this.handleCommit(message.revision, message.switch_at);
        break;
      case 'progress':
        this.setState({ progress: message.progress });
        break;
    }
  }

  private handlePrepare(snapshot: DisplaySnapshot): void {
    const existing = this.prepared.get(snapshot.revision);
    if (existing) {
      if (existing.loaded) {
        this.send({ type: 'ready', revision: snapshot.revision });
      }
      return;
    }
    const entry: PreparedEntry = { snapshot, loaded: false, ok: true };
    this.prepared.set(snapshot.revision, entry);
    this.prunePrepared();
    void this.loadPrepared(entry);
  }

  private async loadPrepared(entry: PreparedEntry): Promise<void> {
    const url = imageUrlForRole(entry.snapshot, this.role);
    entry.ok = url === null ? true : await preloadImage(url, (message) => this.reportError(message));
    entry.loaded = true;
    if (this.prepared.get(entry.snapshot.revision) !== entry) {
      return;
    }
    this.send({ type: 'ready', revision: entry.snapshot.revision });
    if (this.pendingCommit?.revision === entry.snapshot.revision) {
      this.scheduleSwitch(entry, this.pendingCommit.switchAt);
    }
  }

  private handleCommit(revision: number, switchAt: string): void {
    if (revision === this.shownRevision) {
      this.send({ type: 'presented', revision });
      return;
    }
    const entry = this.prepared.get(revision);
    if (!entry) {
      // 漏收 prepare：改向主機查詢目前狀態
      void this.recoverFromServer();
      return;
    }
    this.pendingCommit = { revision, switchAt };
    if (entry.loaded) {
      this.scheduleSwitch(entry, switchAt);
    }
  }

  private scheduleSwitch(entry: PreparedEntry, switchAt: string): void {
    if (this.switchTimer !== null) {
      window.clearTimeout(this.switchTimer);
    }
    const token = ++this.showToken;
    this.switchTimer = window.setTimeout(() => {
      this.switchTimer = null;
      if (token === this.showToken) {
        this.present(entry.snapshot, entry.ok);
      }
    }, Math.max(0, msUntil(switchAt)));
  }

  /** 載入後立即顯示（hello 的目前畫面、或漏收 prepare 時的補救） */
  private async showWhenLoaded(snapshot: DisplaySnapshot): Promise<void> {
    const token = ++this.showToken;
    const url = imageUrlForRole(snapshot, this.role);
    const ok = url === null ? true : await preloadImage(url, (message) => this.reportError(message));
    if (token === this.showToken) {
      this.present(snapshot, ok);
    }
  }

  private present(snapshot: DisplaySnapshot, ok: boolean): void {
    this.pendingCommit = null;
    this.setState({ shown: snapshot, shownOk: ok });
    this.prunePrepared();
    // 等畫面實際繪出後才回報 presented
    window.requestAnimationFrame(() =>
      window.requestAnimationFrame(() => this.send({ type: 'presented', revision: snapshot.revision })),
    );
  }

  private async recoverFromServer(): Promise<void> {
    if (this.recovering) {
      return;
    }
    this.recovering = true;
    try {
      const response = await apiGet<DisplayCurrentResponse>('/api/display/current');
      this.setState({ progress: response.progress });
      if (response.current && response.current.revision !== this.shownRevision) {
        await this.showWhenLoaded(response.current);
      } else if (response.current) {
        this.send({ type: 'presented', revision: response.current.revision });
      }
      if (response.pending) {
        this.handlePrepare(response.pending);
      }
    } catch (error) {
      this.reportError(`查詢目前播放狀態失敗：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      this.recovering = false;
    }
  }

  private prunePrepared(): void {
    const shown = this.shownRevision ?? -1;
    for (const revision of this.prepared.keys()) {
      if (revision <= shown) {
        this.prepared.delete(revision);
      }
    }
  }

  private sendHeartbeat(): void {
    this.send({ type: 'heartbeat', shown_revision: this.shownRevision });
  }

  private async pollHealth(): Promise<void> {
    try {
      const health = await apiGet<HealthResponse>('/api/health');
      this.setState({ healthStatus: health.status, adminUrls: health.admin_urls ?? [] });
    } catch {
      // 斷線時保持目前畫面，不顯示錯誤
    }
  }

  private reportError(message: string): void {
    const now = Date.now();
    if (now - this.lastErrorReportAt < ERROR_REPORT_INTERVAL_MS) {
      return;
    }
    this.lastErrorReportAt = now;
    this.send({ type: 'client_error', message: `[${this.role}] ${message}`.slice(0, 500) });
  }
}
