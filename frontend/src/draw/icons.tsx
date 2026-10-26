// 工具列圖示（內嵌 SVG，不依賴外部資源）。

const common = {
  width: 28,
  height: 28,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2.2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

export function PenIcon() {
  return (
    <svg {...common}>
      <path d="M4 20l4-1L19 8l-3-3L5 16l-1 4z" />
      <path d="M14 7l3 3" />
    </svg>
  );
}

export function EraserIcon() {
  return (
    <svg {...common}>
      <path d="M8 20h12" />
      <path d="M4.5 15.5l9-9a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L11 19H8l-3.5-3.5z" />
      <path d="M9 11l5 5" />
    </svg>
  );
}

export function UndoIcon() {
  return (
    <svg {...common}>
      <path d="M9 14L4 9l5-5" />
      <path d="M4 9h10a6 6 0 0 1 0 12h-3" />
    </svg>
  );
}

export function RedoIcon() {
  return (
    <svg {...common}>
      <path d="M15 14l5-5-5-5" />
      <path d="M20 9H10a6 6 0 0 0 0 12h3" />
    </svg>
  );
}

export function ClearIcon() {
  return (
    <svg {...common}>
      <path d="M4 7h16" />
      <path d="M9 7V4h6v3" />
      <path d="M6 7l1 13h10l1-13" />
    </svg>
  );
}

export function SendIcon() {
  return (
    <svg {...common}>
      <path d="M4 12l16-8-6 16-3-7-7-1z" />
    </svg>
  );
}
