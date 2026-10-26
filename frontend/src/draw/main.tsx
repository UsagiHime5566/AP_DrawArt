// 平板繪圖頁 /draw
import { createRoot } from 'react-dom/client';
import '../shared/theme.css';
import './draw.css';
import { App } from './App';
import { installDeviceGuards } from './device';

installDeviceGuards();
createRoot(document.getElementById('root')!).render(<App />);
