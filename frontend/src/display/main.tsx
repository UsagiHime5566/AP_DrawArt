// 展示頁入口：/display/visitor、/display/ai、/display/artist。

import { createRoot } from 'react-dom/client';
import '../shared/theme.css';
import './display.css';
import { App } from './App';
import { DisplayController } from './controller';
import { resolveRole } from './role';

document.addEventListener('contextmenu', (event) => event.preventDefault());

const role = resolveRole();
const root = createRoot(document.getElementById('root')!);

if (role) {
  document.title = `人工對畫(話) · ${role}`;
  root.render(<App controller={new DisplayController(role)} role={role} />);
} else {
  root.render(<p className="unknown-role">未知的展示角色，請使用 /display/visitor、/display/ai 或 /display/artist</p>);
}
