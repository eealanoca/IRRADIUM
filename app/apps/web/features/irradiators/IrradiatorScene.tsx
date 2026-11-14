'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, RotateCcw } from 'lucide-react';
import type { IrradiatorType } from '../../lib/types';

import { irradiatorModel, projectIrradiator, type Point } from './irradiatorModels';

const INITIAL_VIEW = { yaw: -.52, pitch: .62 };
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export default function IrradiatorScene({ type }: { type?: IrradiatorType }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const view = useRef({ ...INITIAL_VIEW });
  const dragging = useRef<{ x: number; y: number } | null>(null);
  const dirty = useRef(true);
  const motion = useRef(true);
  const elapsed = useRef(0);
  const [playing, setPlaying] = useState(true);
  const model = useMemo(() => irradiatorModel(type), [type]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    motion.current = !reduced.matches;
    const syncMotion = () => { motion.current = !reduced.matches; dirty.current = true; };
    reduced.addEventListener('change', syncMotion);
    let width = 0, height = 0, frame = 0, lastFrame = -1;
    let animationTime = elapsed.current, lastTime = 0;
    const resize = new ResizeObserver(([entry]) => {
      width = entry.contentRect.width;
      height = entry.contentRect.height;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      dirty.current = true;
    });
    resize.observe(canvas);

    const render = (time: number) => {
      frame = window.requestAnimationFrame(render);
      const animate = playing && motion.current && !document.hidden;
      if (lastTime) animationTime += animate ? Math.min(time - lastTime, 80) : 0;
      lastTime = time;
      elapsed.current = animationTime;
      if (!width || !height || document.hidden || (!animate && !dirty.current) || (animate && time - lastFrame < 32 && !dirty.current)) return;
      dirty.current = false;
      lastFrame = time;
      const seconds = animationTime / 1000;
      const yaw = view.current.yaw + (!dragging.current ? Math.sin(seconds * .42) * .105 : 0);
      const pitch = view.current.pitch;
      const { scale, project, visibleFaces } = projectIrradiator(model, width, height, yaw, pitch, type?.id);
      const path = (points: Point[], close = false) => {
        context.beginPath();
        points.forEach((point, index) => {
          const projected = project(point);
          if (!index) context.moveTo(projected.x, projected.y); else context.lineTo(projected.x, projected.y);
        });
        if (close) context.closePath();
      };
      context.clearRect(0, 0, width, height);
      const floor = project([0, 0, 0]);
      context.save();
      context.translate(floor.x, floor.y);
      context.scale(1, .4);
      const shadow = context.createRadialGradient(0, 0, 0, 0, 0, scale * 3.8);
      shadow.addColorStop(0, 'rgba(0,0,0,.5)'); shadow.addColorStop(1, 'rgba(0,0,0,0)');
      context.fillStyle = shadow; context.fillRect(-scale * 4, -scale * 4, scale * 8, scale * 8);
      context.restore();
      context.lineWidth = .6;
      for (let index = -7; index <= 7; index++) {
        context.strokeStyle = `rgba(110,154,183,${index === 0 ? .19 : .065})`;
        path([[index, -.025, -6], [index, -.025, 6]]); context.stroke();
        path([[-6, -.025, index], [6, -.025, index]]); context.stroke();
      }
      context.lineJoin = 'round';
      for (const { polygon, fill, stroke, edges } of visibleFaces) {
        const surface = new Path2D();
        polygon.forEach(({ x, y }, i) => { if (i === 0) surface.moveTo(x, y); else surface.lineTo(x, y); });
        surface.closePath();
        context.fillStyle = fill;
        context.fill(surface);

        const boundary = edges ? new Path2D() : surface;
        const seams = new Path2D();
        edges?.forEach((isBoundary, i) => {
          const line = isBoundary ? boundary : seams;
          const start = polygon[i], end = polygon[(i + 1) % polygon.length];
          line.moveTo(start.x, start.y);
          line.lineTo(end.x, end.y);
        });
        // Solid faces overlap by a fraction of a pixel along artificial splits.
        if (!fill.startsWith('rgba(') && (edges || stroke === 'transparent')) {
          context.lineWidth = .72;
          context.strokeStyle = fill;
          context.stroke(stroke === 'transparent' ? surface : seams);
        }
        if (stroke !== 'transparent') {
          context.lineWidth = .65;
          context.strokeStyle = stroke;
          context.stroke(boundary);
        }
      }
    };
    frame = window.requestAnimationFrame(render);
    return () => { window.cancelAnimationFrame(frame); resize.disconnect(); reduced.removeEventListener('change', syncMotion); };
  }, [model, playing, type?.id]);

  return <div className="splash-scene">
    <canvas ref={canvasRef} tabIndex={0} role="img" aria-label="Vista Conceptual Interactiva de un Irradiador Gamma. Arrastra Para Girar o Utiliza las Flechas del Teclado." aria-describedby="scene-instructions"
      onPointerDown={(event) => { dragging.current = { x: event.clientX, y: event.clientY }; event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.focus(); }}
      onPointerMove={(event) => { const previous = dragging.current; if (!previous) return; view.current.yaw = clamp(view.current.yaw + (event.clientX - previous.x) * .009, -1.22, .26); view.current.pitch = clamp(view.current.pitch + (event.clientY - previous.y) * .005, .25, .82); dragging.current = { x: event.clientX, y: event.clientY }; dirty.current = true; }}
      onPointerUp={(event) => { dragging.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerCancel={() => { dragging.current = null; }}
      onKeyDown={(event) => {
        if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(event.key)) {
          event.preventDefault();
          if (event.key === 'ArrowLeft') view.current.yaw = clamp(view.current.yaw - .12, -1.22, .26);
          if (event.key === 'ArrowRight') view.current.yaw = clamp(view.current.yaw + .12, -1.22, .26);
          if (event.key === 'ArrowUp') view.current.pitch = clamp(view.current.pitch + .06, .25, .82);
          if (event.key === 'ArrowDown') view.current.pitch = clamp(view.current.pitch - .06, .25, .82);
          if (event.key === 'Home') view.current = { ...INITIAL_VIEW };
          dirty.current = true;
        }
      }}/>
    <span className="scene-instructions" id="scene-instructions">Arrastra Para Explorar</span>
    <div className="scene-controls">
      <button type="button" title="Restablecer Vista" aria-label="Restablecer Vista del Irradiador" onClick={() => { view.current = { ...INITIAL_VIEW }; dirty.current = true; }}><RotateCcw size={13}/></button>
      <button type="button" title={playing ? 'Pausar Animación' : 'Reanudar Animación'} aria-label={playing ? 'Pausar Animación' : 'Reanudar Animación'} aria-pressed={!playing} onClick={() => { setPlaying((value) => !value); dirty.current = true; }}>{playing ? <Pause size={13}/> : <Play size={13}/>}</button>
    </div>
  </div>;
}
