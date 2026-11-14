'use client';
import { uiLocale } from '../../lib/i18n/number';

import { useRef, useState } from 'react';

type Point = {
  name: string; mean_kgy_h: number; minimum_kgy_h: number; maximum_kgy_h: number;
  relative_error_maximum?: number | null;
  precision?: { meets_initial: boolean; meets_recommended: boolean };
};
const number = (value: number) => value.toLocaleString(uiLocale(), { maximumSignificantDigits: 4 });

function download(content: string, mime: string, name: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function RouteRateChart({ points, selected, onSelect, deterministic = false }: { points: Point[]; deterministic?: boolean; selected?: number; onSelect: (index: number) => void }) {
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number>();
  if (!points.length) return null;
  const width = 900, height = 208, left = 62, right = 20, top = 12, bottom = 34;
  const maximum = Math.max(...points.map((point) => point.maximum_kgy_h), 1e-12) * 1.06;
  const x = (index: number) => left + (width - left - right) * (points.length === 1 ? .5 : index / (points.length - 1));
  const y = (value: number) => height - bottom - (height - top - bottom) * value / maximum;
  const line = points.map((point, index) => `${x(index)},${y(point.mean_kgy_h)}`).join(' ');
  const band = points.map((point, index) => `${x(index)},${y(point.maximum_kgy_h)}`).join(' ') + ' '
    + points.map((_, index) => { const reverse = points.length - index - 1; return `${x(reverse)},${y(points[reverse].minimum_kgy_h)}`; }).join(' ');
  const chosen = points[hover ?? selected ?? 0] ?? points[0];
  function csv() {
    const columns = ['posicion', 'tasa_media_kGy_h', 'tasa_minima_kGy_h', 'tasa_maxima_kGy_h'];
    const errorColumns = ['error_relativo_maximo', 'cumple_10', 'cumple_5'];
    const rows = [(deterministic ? columns : columns.concat(errorColumns)).join(',')];
    for (const point of points) {
      const values = [`"${point.name.replaceAll('"', '""')}"`, point.mean_kgy_h, point.minimum_kgy_h, point.maximum_kgy_h];
      rows.push((deterministic ? values : [...values, point.relative_error_maximum ?? '',
        point.precision?.meets_initial ?? false, point.precision?.meets_recommended ?? false]).join(','));
    }
    download('\uFEFF' + rows.join('\n'), 'text/csv;charset=utf-8', 'IRRADIUM-tasas-por-posicion.csv');
  }
  return <div className="route-rate-chart">
    <header><div><strong>Perfil de tasas · kGy/h</strong><span>Banda: mínimo–máximo · Línea: media</span></div><div>
      <button type="button" className="button ghost" onClick={csv}>Exportar CSV</button>
      <button type="button" className="button ghost" onClick={() => { if (svg.current) download(new XMLSerializer().serializeToString(svg.current), 'image/svg+xml', 'IRRADIUM-perfil-tasas.svg'); }}>Exportar gráfico</button>
    </div></header>
    <svg ref={svg} xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Perfil interactivo de tasas por posición" style={{ background: '#20262f', borderRadius: 8, fontFamily: 'Arial, sans-serif' }}>
      <title>IRRADIUM · Tasas de {points[0].name} a {points[points.length - 1].name} [kGy/h]</title>
      <desc>La línea muestra la tasa media; la banda, el mínimo y máximo por posición. {!deterministic && 'Verde: precisión recomendada. Ámbar: inicial. Rosa: insuficiente.'}</desc>
      <text x={left + 6} y={top + 10} fill="#bdc9db" fontSize="10">Tasa [kGy/h]</text>
      {[0, 1, 2, 3].map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick * maximum / 3)} y2={y(tick * maximum / 3)} stroke="#778398" strokeOpacity=".22"/><text x={left - 10} y={y(tick * maximum / 3) + 4} textAnchor="end" fill="#bdc9db" fontSize="11">{number(tick * maximum / 3)}</text></g>)}
      <polygon points={band} fill="#62a7ff" fillOpacity=".14"/>
      <polyline points={line} fill="none" stroke="#62a7ff" strokeWidth="2"/>
      {points.map((point, index) => <g key={point.name}>
        <circle cx={x(index)} cy={y(point.mean_kgy_h)} r={index === selected ? 5 : 3.5} fill={deterministic ? '#62a7ff' : point.precision?.meets_recommended ? '#55d6a0' : point.precision?.meets_initial ? '#f6c16b' : '#ff8495'} stroke={index === selected ? '#fff' : 'transparent'} strokeWidth="2" tabIndex={0} role="button" aria-label={`Ver posición ${point.name}`} onMouseEnter={() => setHover(index)} onMouseLeave={() => setHover(undefined)} onFocus={() => setHover(index)} onBlur={() => setHover(undefined)} onClick={() => onSelect(index)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(index); } }}>
          <title>{point.name}: {number(point.mean_kgy_h)} kGy/h</title>
        </circle>
        {(index === 0 || index === points.length - 1 || index % Math.max(1, Math.ceil(points.length / 10)) === 0) && <text x={x(index)} y={height - 12} textAnchor="middle" fill="#bdc9db" fontSize="11">{point.name}</text>}
      </g>)}
    </svg>
    <footer><span>{chosen.name} · Media {number(chosen.mean_kgy_h)} kGy/h{!deterministic && <> · Error máximo {chosen.relative_error_maximum == null ? 'sin estimación' : `${number(chosen.relative_error_maximum * 100)} %`}</>}</span>{!deterministic && <span>● Verde &lt;5 % · Ámbar &lt;10 % · Rosa: insuficiente</span>}</footer>
  </div>;
}
