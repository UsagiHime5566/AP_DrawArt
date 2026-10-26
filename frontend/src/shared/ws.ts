// 自動重連的 WebSocket：斷線後以退避間隔（0.5 秒起、最長 5 秒）重連。

export interface SocketHandlers<TIn> {
  onOpen?: () => void;
  onMessage: (message: TIn) => void;
  onClose?: () => void;
}

export class ReconnectingSocket<TIn, TOut> {
  private socket: WebSocket | null = null;
  private retryDelayMs = 500;
  private retryTimer: number | null = null;
  private closed = false;

  constructor(
    private readonly path: string,
    private readonly handlers: SocketHandlers<TIn>,
  ) {
    this.connect();
  }

  get connected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  send(message: TOut): boolean {
    if (!this.connected || !this.socket) {
      return false;
    }
    this.socket.send(JSON.stringify(message));
    return true;
  }

  close(): void {
    this.closed = true;
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
    }
    this.socket?.close();
  }

  private connect(): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}${this.path}`);
    this.socket = socket;

    socket.onopen = () => {
      this.retryDelayMs = 500;
      this.handlers.onOpen?.();
    };
    socket.onmessage = (event) => {
      try {
        this.handlers.onMessage(JSON.parse(String(event.data)) as TIn);
      } catch (error) {
        console.error('WebSocket 訊息解析失敗', error);
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = null;
      this.handlers.onClose?.();
      if (!this.closed) {
        this.retryTimer = window.setTimeout(() => this.connect(), this.retryDelayMs);
        this.retryDelayMs = Math.min(this.retryDelayMs * 2, 5_000);
      }
    };
    socket.onerror = () => socket.close();
  }
}
