'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useEffect, useRef, useState } from 'react';
import { ScanLine } from 'lucide-react';
import { HIDDEN_CELL, orientDoseImage, readDoseImage, type DoseImage } from './doseViewFrame';

const colors = [[68, 1, 84], [49, 104, 142], [53, 183, 121], [253, 231, 37]];
const format = (value: number) => value.toLocaleString(uiLocale(), { maximumSignificantDigits: 4 });

export function DoseCut({ image, plane, unit, title, scale, interactive = false }: {
  image: DoseImage; plane: 'XY' | 'YZ'; unit: string;
  title?: string; scale?: [number, number]; interactive?: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [limits, setLimits] = useState<[number, number]>();
  const [horizontal, vertical, normal] = plane === 'XY' ? [0, 1, 2] : [1, 2, 0];
  const [slice, setSlice] = useState(Math.floor(image.dimensions[normal] / 2));
  const selectedSlice = interactive ? Math.min(slice, image.dimensions[normal] - 1) : Math.floor(image.dimensions[normal] / 2);
  const [nx, ny] = image.dimensions;
  const width = Math.min(640, image.dimensions[horizontal]);
  const height = Math.min(640, image.dimensions[vertical]);
  const low = (axis: number) => image.origin[axis];
  const high = (axis: number) => low(axis) + image.dimensions[axis] * image.spacing[axis];

  useEffect(() => {
    const context = canvas.current?.getContext('2d');
    if (!context) return;
    const pixels = context.createImageData(width, height);
    const values = new Float64Array(width * height).fill(NaN);
    const ijk = [0, 0, 0];
    ijk[normal] = selectedSlice;
    let min = Infinity;
    let max = -Infinity;
    for (let row = 0; row < height; row++) {
      ijk[vertical] = Math.floor((height - row - .5) * image.dimensions[vertical] / height);
      for (let col = 0; col < width; col++) {
        ijk[horizontal] = Math.floor((col + .5) * image.dimensions[horizontal] / width);
        const index = ijk[0] + nx * (ijk[1] + ny * ijk[2]);
        const value = image.values[index];
        if ((image.ghosts && (image.ghosts[index] & HIDDEN_CELL)) || !Number.isFinite(value)) continue;
        values[col + width * row] = value;
        min = Math.min(min, value);
        max = Math.max(max, value);
      }
    }
    const [lowValue, highValue] = scale ?? [min, max];
    values.forEach((value, i) => {
      if (!Number.isFinite(value)) return;
      const fraction = highValue > lowValue ? (value - lowValue) / (highValue - lowValue) : .5;
      const scaled = 3 * Math.max(0, Math.min(1, fraction));
      const segment = Math.min(2, Math.floor(scaled));
      const mix = scaled - segment;
      colors[segment].forEach((component, channel) => {
        pixels.data[i * 4 + channel] = component + mix * (colors[segment + 1][channel] - component);
      });
      pixels.data[i * 4 + 3] = 255;
    });
    context.putImageData(pixels, 0, 0);
    setLimits(Number.isFinite(min) ? [lowValue, highValue] : undefined);
  }, [height, horizontal, image, normal, nx, ny, scale, selectedSlice, vertical, width]);

  return <figure>
    <div><ScanLine size={15}/><strong>{title ?? `Corte ${plane} del irradiador`}</strong></div>
    {interactive && <label className="dose-cut-position"><span>{'XYZ'[normal]} = {format(low(normal) + (selectedSlice + .5) * image.spacing[normal])} cm · Centro de la celda</span>
      <input type="range" aria-label={`Posición del corte ${plane}`} min={0} max={image.dimensions[normal] - 1} step={1} value={selectedSlice} onChange={(event) => setSlice(Number(event.target.value))}/>
    </label>}
    <section className="position-dose-cut">
      <span>+{plane[1]} ↑ · {format(low(vertical))} a {format(high(vertical))} cm</span>
      <canvas ref={canvas} width={width} height={height} aria-label={title ?? `Mapa de ${unit === 'kGy' ? 'dosis' : 'tasa'} en el plano ${plane}`}
        style={{ aspectRatio: (high(horizontal) - low(horizontal)) / (high(vertical) - low(vertical)) }}/>
      <span>+{plane[0]} → · {format(low(horizontal))} a {format(high(horizontal))} cm</span>
      {limits ? <p><span>{format(limits[0])}</span><i aria-hidden="true"/><span>{format(limits[1])} {unit}</span></p> : <small>Sin celdas activas en el corte.</small>}
    </section>
  </figure>;
}

export default function PositionDoseCuts({ url, yaw, unit = 'kGy/h' }: { url: string; yaw: number; unit?: string }) {
  const [image, setImage] = useState<DoseImage>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('No fue posible descargar la malla de los cortes.');
        return response.arrayBuffer();
      })
      .then(readDoseImage)
      .then((data) => { if (!controller.signal.aborted) setImage(orientDoseImage(data, yaw)); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [url, yaw]);
  if (!image) return <p className="inline-notice" role="status">{error || 'Preparando los cortes…'}</p>;
  return <div className="cut-grid"><DoseCut image={image} plane="XY" unit={unit} interactive/><DoseCut image={image} plane="YZ" unit={unit} interactive/></div>;
}
