'use client';
import { uiLocale } from '../../lib/i18n/number';

import DialogFrame from '../../components/ui/DialogFrame';

import { useEffect, useState } from 'react';
import { Edit3, MapPin, Plus, Trash2, X } from 'lucide-react';
import { apiGet, apiJson } from '../../lib/api';
import type { WorkSectionsResponse } from '../../lib/types';

type SectionEntry = { name: string; position: [number, number, number] };

function SectionForm({ section, onClose, onSaved }: { section: SectionEntry; onClose: () => void; onSaved: () => void }) {
  const original = section.name;
  const [name, setName] = useState(section.name);
  const [position, setPosition] = useState(section.position.join(', '));
  const [error, setError] = useState('');
  const save = async () => {
    const vector = position.split(',').map((item) => Number(item.trim()));
    if (vector.length !== 3 || vector.some(Number.isNaN)) { setError('Escribe tres coordenadas numéricas.'); return; }
    try { await apiJson(original ? `/api/config/work-sections/${encodeURIComponent(original)}` : '/api/config/work-sections', original ? 'PUT' : 'POST', { name, position: vector }); onSaved(); }
    catch { setError('No fue posible guardar la sección.'); }
  };
  const remove = async () => { if (!window.confirm(`¿Eliminar la sección ${original}?`)) return; try { await apiJson(`/api/config/work-sections/${encodeURIComponent(original)}`, 'DELETE'); onSaved(); } catch { setError('No fue posible eliminar la sección.'); } };
  return <DialogFrame onClose={onClose} label={original ? `Editar sección ${original}` : 'Nueva sección'}><section className="editor-modal"><header><div><span className="section-label">Posición de trabajo</span><h2>{original ? `Editar sección ${original}` : 'Nueva sección'}</h2></div><button className="icon-button" onClick={onClose} aria-label="Cerrar diálogo"><X size={18} /></button></header><div className="editor-grid"><label className="field"><span>Identificador</span><input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field"><span>Posición [x, y, z] mm</span><input value={position} onChange={(event) => setPosition(event.target.value)} /></label></div>{error && <p className="form-error">{error}</p>}<footer>{original && <button className="danger-button" onClick={remove}><Trash2 size={15} /> Eliminar</button>}<span /><button className="button ghost" onClick={onClose}>Cancelar</button><button className="button primary" onClick={save}>Guardar sección</button></footer></section></DialogFrame>;
}

export default function WorkSectionsView() {
  const [sections, setSections] = useState<SectionEntry[]>([]);
  const [editor, setEditor] = useState<SectionEntry | null>(null);
  const [error, setError] = useState('');
  const applySections = (response: WorkSectionsResponse) => {
    setSections(Object.entries(response.positions).map(([name, position]) => ({ name, position })));
    setError('');
  };
  const load = async () => {
    try { applySections(await apiGet<WorkSectionsResponse>('/api/config/work-sections')); }
    catch { setError('No fue posible cargar las secciones de trabajo.'); }
  };
  useEffect(() => {
    apiGet<WorkSectionsResponse>('/api/config/work-sections')
      .then(applySections)
      .catch(() => setError('No fue posible cargar las secciones de trabajo.'));
  }, []);
  return <div className="view-stack"><section className="hero-row"><div><span className="section-label">Coordenadas del sistema</span><h2>Secciones de trabajo</h2><p>Define las posiciones de referencia disponibles para ubicar la carga.</p></div><button className="button primary" onClick={() => setEditor({ name: '', position: [0, 0, 0] })}><Plus size={16} /> Añadir sección</button></section>{error ? <div className="empty-state"><p>{error}</p><button className="button primary" onClick={load}>Reintentar</button></div> : <div className="section-grid">{sections.map((section) => <article className="surface section-card" key={section.name}><header><div className="section-letter">{section.name}</div><div><span className="section-label">Sección</span><h3>Posición {section.name}</h3></div><button className="icon-button" aria-label={`Editar sección ${section.name}`} onClick={() => setEditor(section)}><Edit3 size={16} /></button></header><div className="coordinate-triplet">{(['X', 'Y', 'Z'] as const).map((axis, index) => <div key={axis}><span>{axis}</span><strong>{section.position[index].toLocaleString(uiLocale())}</strong><small>mm</small></div>)}</div><div className="section-location"><MapPin size={15} /> [{section.position.join(', ')}] mm</div><button className="button secondary full" onClick={() => setEditor(section)}><Edit3 size={15} /> Editar posición</button></article>)}</div>}{editor && <SectionForm section={editor} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); void load(); }} />}</div>;
}
