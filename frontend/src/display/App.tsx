import { useSyncExternalStore } from 'react';
import type { DisplayRole } from '../shared/types';
import type { DisplayController } from './controller';
import { ProgressOverlay } from './ProgressOverlay';
import { SetupNotice } from './SetupNotice';
import { Stage } from './Stage';

export function App({ controller, role }: { controller: DisplayController; role: DisplayRole }) {
  const state = useSyncExternalStore(controller.subscribe, controller.getState);
  const noWorksYet = !state.shown || state.shown.kind === 'standby';
  const needsSetup = state.healthStatus === 'setup_required' && noWorksYet;

  return (
    <main className={`display display--${role}`}>
      <Stage role={role} snapshot={state.shown} ok={state.shownOk} />
      {role === 'ai' && <ProgressOverlay progress={state.progress} />}
      {needsSetup && <SetupNotice adminUrls={state.adminUrls} />}
    </main>
  );
}
