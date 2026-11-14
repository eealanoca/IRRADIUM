'use client';

import { useEffect, useState } from 'react';
import { ArrowRight, Atom, Check, ChevronRight, Droplets, Eye, Layers3, LockKeyhole, RefreshCw } from 'lucide-react';
import { SERVER_URL } from '../../lib/api';
import { APPLICATION_NAME, APPLICATION_SUBTITLE, APPLICATION_FULL_TITLE, APPLICATION_VERSION } from '../../lib/branding';
import type { IrradiatorType } from '../../lib/types';
import IrradiatorScene from './IrradiatorScene';
import LanguageSelector from '../../components/layout/LanguageSelector';

const categoryLabels: Record<string, string> = {
  I: 'Autoblindado · Almacenamiento Seco',
  II: 'Panorámico · Almacenamiento Seco',
  III: 'Autoblindado · Almacenamiento Húmedo',
  IV: 'Panorámico · Almacenamiento Húmedo',
};
const frameworkTitle = APPLICATION_FULL_TITLE.replace(`${APPLICATION_NAME} — `, '');
const [frameworkLead, frameworkScope] = frameworkTitle.split(' for ', 2);

function WorkspaceBackdrop() {
  return <div className="splash-workbench" aria-hidden="true">
    <div className="splash-workbench-bar"><Atom size={17}/><strong>{APPLICATION_NAME}</strong><span>Modelado</span><span>Simulación</span><span>Resultados</span><i/></div>
    <div className="splash-workbench-sidebar"><div>Espacio de Trabajo</div>{['Carga CAD', 'Casos de Irradiación', 'Resultados', 'Fuentes', 'Materiales'].map((label, index) => <p key={label}><span className={`backdrop-nav-icon icon-${index}`}/>{label}</p>)}<section><span/><span/><span/></section></div>
    <div className="splash-workbench-viewport"><div className="backdrop-floor"/><div className="backdrop-wirebox"><span/><span/><span/></div><div className="backdrop-axis">Y <span>Z</span> X</div></div>
    <div className="splash-workbench-properties"><div>Irradiador</div>{['Geometría', 'Configuración', 'Materiales', 'Distribución'].map((label) => <section key={label}><strong>{label}</strong><i/><i/><i/></section>)}</div>
    <div className="splash-workbench-timeline"><span>IRRADIUM</span><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/></div>
  </div>;
}

export default function IrradiatorSelector({ onSelect }: { onSelect: (type: IrradiatorType) => void }) {
  const [types, setTypes] = useState<IrradiatorType[]>([]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [selectedId, setSelectedId] = useState('IV');
  const [previews, setPreviews] = useState(true);
  const available = types.find((type) => type.enabled);
  const selected = types.find((type) => type.id === selectedId && (type.enabled || previews)) ?? available;
  const previewOnly = !!selected && !selected.enabled;
  useEffect(() => {
    let cancelled = false;
    fetch(`${SERVER_URL}/api/irradiators`, { cache: 'no-store' })
      .then((response) => { if (!response.ok) throw new Error(); return response.json() as Promise<{ irradiators: IrradiatorType[] }>; })
      .then((data) => { if (!cancelled) { setTypes(data.irradiators); setError(''); } })
      .catch(() => { if (!cancelled) setError('No se Pudo Conectar con el Catálogo de Irradiadores.'); });
    return () => { cancelled = true; };
  }, [attempt]);

  return <main className="irradium-splash-root">
    <WorkspaceBackdrop/>
    <div className="irradium-splash-overlay">
      <section className="irradium-splash" role="dialog" aria-modal="true" aria-labelledby="splash-heading">
        <div className="splash-artwork">
          <div className="splash-artwork-grain" aria-hidden="true"/>
          <IrradiatorScene type={selected}/>
          <header className="splash-brand"><div className="splash-brand-name"><Atom size={27} strokeWidth={1.4}/><strong>{APPLICATION_NAME}</strong></div><em>{APPLICATION_SUBTITLE}</em></header>
          <span className="splash-version">{APPLICATION_VERSION}<span>Fase 01</span></span>
          <h1 className="splash-artwork-copy" id="splash-heading" lang="en" aria-label={frameworkTitle}>
            <span className="splash-framework-lead">{frameworkLead} for</span>{' '}
            <span className="splash-framework-scope">{frameworkScope}</span>
          </h1>
          <span className="splash-scene-label"><span/> {selected?.id === 'I' ? 'Vista Exterior' : 'Corte Ilustrativo'} · Categoría {selected?.id ?? 'IV'}</span>
        </div>

        <div className="splash-content">
          <section className="splash-category-panel" aria-labelledby="splash-categories-heading">
            <div className="splash-panel-heading"><h2 id="splash-categories-heading">Comenzar</h2><LanguageSelector/><button type="button" className="splash-preview-toggle" aria-pressed={previews} title="Mostrar u Ocultar las Vistas Previas de las Categorías I, II y III" onClick={() => { if (previews && previewOnly && available) setSelectedId(available.id); setPreviews(!previews); }}><Eye size={13}/> Vistas Previas</button></div>
            {error ? <div className="splash-error" role="alert"><p>{error}</p><button type="button" onClick={() => { setError(''); setAttempt((value) => value + 1); }}><RefreshCw size={13}/> Reintentar Conexión</button></div>
              : <div className="splash-categories" aria-busy={types.length === 0}>
                {!types.length && <div className="splash-loading"><span/> Cargando Irradiadores…</div>}
                {types.map((type) => <button key={type.id} type="button" disabled={!type.enabled && !previews} aria-pressed={selected?.id === type.id}
                  aria-label={`Categoría ${type.id}: ${type.name_es}${type.enabled ? '' : previews ? '. Vista Previa' : '. Próximamente'}`}
                  title={`${type.name_es}\n${type.name_en}${type.enabled ? '' : previews ? '\nSolo Vista Previa' : '\nNo Habilitado en Esta Fase'}`}
                  className={`splash-category ${type.enabled || previews ? 'is-enabled' : 'is-locked'} ${selected?.id === type.id ? 'is-selected' : ''}`}
                  onClick={() => setSelectedId(type.id)}>
                  <span className="splash-category-number">{type.id}</span>
                  <span className="splash-category-name">{categoryLabels[type.id] ?? type.name_es}{!type.enabled && <small>{previews ? 'Vista Previa' : 'Próximamente'}</small>}</span>
                  {selected?.id === type.id ? <Check size={14}/> : type.enabled ? <ChevronRight size={14}/> : previews ? <Eye size={13}/> : <LockKeyhole size={12}/>}
                </button>)}
              </div>}
          </section>

          <section className="splash-current-panel" aria-labelledby="splash-current-heading">
            <div className="splash-current-top">
              <span className="splash-current-category">{selected ? `Categoría ${selected.id}` : 'Espacio de Simulación'}</span>
              {selected && <span className={`splash-current-status${previewOnly ? ' is-preview' : ''}`}>{previewOnly ? 'Vista Previa' : 'Disponible'}</span>}
            </div>
            <div className="splash-current-details">
              <h2 id="splash-current-heading">{selected?.name_es ?? 'Preparando tu Espacio'}</h2>
              {selected && <p className="splash-current-english" lang="en">{selected.name_en}</p>}
            </div>
            <div className="splash-current-actions">
              {selected && <div className="splash-type-tags"><span><Layers3 size={12}/>{selected.geometry === 'self-contained' ? 'Autoblindado' : 'Panorámico'}</span><span><Droplets size={12}/>{selected.source_storage === 'dry' ? 'Almacenamiento Seco' : 'Almacenamiento Húmedo'}</span></div>}
              <button type="button" className="splash-enter" disabled={!selected?.enabled || !!error} onClick={() => { if (selected?.enabled) onSelect(selected); }}>
                <span>{previewOnly ? 'Simulación Próximamente' : `Entrar en Categoría ${selected?.id ?? 'IV'}`}</span>{previewOnly ? <LockKeyhole size={15}/> : <ArrowRight size={17}/>}
              </button>
            </div>
          </section>
        </div>
        <footer className="splash-footer"><span>{APPLICATION_FULL_TITLE}</span><span className="splash-footer-status"><span/> Entorno Local</span><ChevronRight size={12}/></footer>
      </section>
    </div>
  </main>;
}
