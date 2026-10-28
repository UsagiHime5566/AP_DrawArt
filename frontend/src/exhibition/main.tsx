import { createRoot } from 'react-dom/client';
import '../shared/theme.css';
import '../draw/draw.css';
import './exhibition.css';
import { App } from './App';
import { disableFullscreenRequests, installDeviceGuards } from '../draw/device';

// 全螢幕由啟動器的 kiosk 視窗負責，頁面才能完整收到 Esc（五秒內五次結束展覽）
disableFullscreenRequests();
installDeviceGuards();
createRoot(document.getElementById('root')!).render(<App />);
