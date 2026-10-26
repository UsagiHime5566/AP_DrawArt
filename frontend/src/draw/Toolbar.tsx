// 作畫工具列：畫筆／橡皮擦、色票、粗細、復原／重做／清空、送出。
// 選取狀態同時以外框、文字標示呈現，不只靠顏色。

import type { CSSProperties } from 'react';
import type { EngineStatus } from './engine';
import { ClearIcon, EraserIcon, PenIcon, RedoIcon, SendIcon, UndoIcon } from './icons';
import type { Tool } from './model';
import { COLORS, SIZES } from './palette';

interface ToolbarProps {
  mode: 'side' | 'bottom';
  style: CSSProperties;
  tool: Tool;
  color: string;
  size: number;
  status: EngineStatus;
  disabled: boolean;
  onTool: (tool: Tool) => void;
  onColor: (color: string) => void;
  onSize: (size: number) => void;
  onUndo: () => void;
  onRedo: () => void;
  onClear: () => void;
  onSubmit: () => void;
}

export function Toolbar(props: ToolbarProps) {
  const { tool, color, size, status, disabled } = props;
  return (
    <div className={`toolbar toolbar--${props.mode}`} style={props.style} aria-disabled={disabled}>
      <div className="toolbar-group toolbar-group--tools" role="group" aria-label="工具">
        <button
          type="button"
          className={`tool-btn${tool === 'pen' ? ' is-active' : ''}`}
          aria-pressed={tool === 'pen'}
          disabled={disabled}
          onClick={() => props.onTool('pen')}
        >
          <PenIcon />
          <span>畫筆</span>
        </button>
        <button
          type="button"
          className={`tool-btn${tool === 'eraser' ? ' is-active' : ''}`}
          aria-pressed={tool === 'eraser'}
          disabled={disabled}
          onClick={() => props.onTool('eraser')}
        >
          <EraserIcon />
          <span>橡皮擦</span>
        </button>
      </div>

      <div className="toolbar-group toolbar-group--colors" role="group" aria-label="顏色">
        {COLORS.map((swatch) => {
          const active = tool === 'pen' && swatch.value === color;
          return (
            <button
              key={swatch.value}
              type="button"
              className={`swatch-btn${active ? ' is-active' : ''}`}
              aria-label={swatch.name}
              aria-pressed={active}
              disabled={disabled}
              onClick={() => props.onColor(swatch.value)}
            >
              <span className="swatch-chip" style={{ background: swatch.value }} />
            </button>
          );
        })}
      </div>

      <div className="toolbar-group toolbar-group--sizes" role="group" aria-label="粗細">
        {SIZES.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`size-btn${option.value === size ? ' is-active' : ''}`}
            aria-pressed={option.value === size}
            disabled={disabled}
            onClick={() => props.onSize(option.value)}
          >
            <span
              className="size-dot"
              style={{ width: Math.max(6, option.value * 0.6), height: Math.max(6, option.value * 0.6) }}
            />
            <span>{option.name}</span>
          </button>
        ))}
      </div>

      <div className="toolbar-group toolbar-group--history" role="group" aria-label="編輯">
        <button type="button" className="tool-btn" disabled={disabled || !status.canUndo} onClick={props.onUndo}>
          <UndoIcon />
          <span>復原</span>
        </button>
        <button type="button" className="tool-btn" disabled={disabled || !status.canRedo} onClick={props.onRedo}>
          <RedoIcon />
          <span>重做</span>
        </button>
        <button type="button" className="tool-btn" disabled={disabled || !status.canClear} onClick={props.onClear}>
          <ClearIcon />
          <span>清空</span>
        </button>
      </div>

      <button type="button" className="submit-btn" disabled={disabled} onClick={props.onSubmit}>
        <SendIcon />
        <span>送出</span>
      </button>
    </div>
  );
}
