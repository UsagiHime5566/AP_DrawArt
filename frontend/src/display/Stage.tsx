// 作品畫面與整組轉場：新畫面在 switch_at 掛上並淡入，舊畫面淡入完成後移除。

import { useEffect, useRef, useState } from 'react';
import type { DisplayRole, DisplaySnapshot } from '../shared/types';
import { Standby } from './Standby';

export const FADE_MS = 700;

interface Layer {
  id: number;
  snapshot: DisplaySnapshot | null;
  ok: boolean;
}

function FitImage({ url }: { url: string }) {
  return (
    <div className="fit">
      <img className="fit__image" src={url} alt="" draggable={false} />
    </div>
  );
}

function FailureCard({ text }: { text: string }) {
  return (
    <div className="failure">
      <p className="failure__text">{text}</p>
    </div>
  );
}

function Panel({ role, snapshot, ok }: { role: DisplayRole; snapshot: DisplaySnapshot | null; ok: boolean }) {
  if (!ok) {
    return <div className="blank" />;
  }
  if (role === 'artist') {
    return snapshot ? <FitImage url={snapshot.artist.url} /> : <div className="blank" />;
  }
  if (!snapshot || snapshot.kind === 'standby') {
    return <Standby role={role} />;
  }
  if (role === 'visitor') {
    return snapshot.visitor ? <FitImage url={snapshot.visitor.url} /> : <div className="blank" />;
  }
  switch (snapshot.ai.type) {
    case 'image':
      return <FitImage url={snapshot.ai.url} />;
    case 'failure':
      return <FailureCard text={snapshot.ai.text} />;
    case 'standby':
      return <Standby role={role} />;
  }
}

export function Stage({ role, snapshot, ok }: { role: DisplayRole; snapshot: DisplaySnapshot | null; ok: boolean }) {
  const nextId = useRef(1);
  const [layers, setLayers] = useState<Layer[]>(() => [{ id: 0, snapshot, ok }]);

  useEffect(() => {
    setLayers((previous) => {
      const top = previous[previous.length - 1];
      if (top && top.snapshot === snapshot && top.ok === ok) {
        return previous;
      }
      const layer: Layer = { id: nextId.current, snapshot, ok };
      nextId.current += 1;
      return [...previous.slice(-1), layer];
    });
  }, [snapshot, ok]);

  useEffect(() => {
    if (layers.length < 2) {
      return undefined;
    }
    const timer = window.setTimeout(() => setLayers((previous) => previous.slice(-1)), FADE_MS + 100);
    return () => window.clearTimeout(timer);
  }, [layers]);

  return (
    <div className="stage">
      {layers.map((layer) => (
        <div key={layer.id} className="stage__layer">
          <Panel role={role} snapshot={layer.snapshot} ok={layer.ok} />
        </div>
      ))}
    </div>
  );
}
