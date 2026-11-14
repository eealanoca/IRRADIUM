'use client';

import { useEffect, useState, type ReactNode } from 'react';
import {
  Atom, Box, Boxes, ChevronRight, ChevronsUpDown, FileOutput, FlaskConical,
  Layers3, Moon, PanelLeft, SlidersHorizontal, Sun, BookOpen,
} from 'lucide-react';
import type { AppSection, IrradiatorType } from '../../lib/types';
import { API_URL, apiGet, apiJson } from '../../lib/api';
import { APPLICATION_NAME, APPLICATION_SUBTITLE, APPLICATION_FULL_TITLE, APPLICATION_VERSION } from '../../lib/branding';
import StudyActivityRibbon from './StudyActivityRibbon';
import QuickStartGuide from './QuickStartGuide';
import LanguageSelector from './LanguageSelector';

const navigation: Array<{ id: AppSection; label: string; icon: typeof Box; group: 'operation' | 'configuration' }> = [
  { id: 'load', label: 'Carga CAD', icon: Box, group: 'operation' },
  { id: 'cases', label: 'Casos de irradiación', icon: Layers3, group: 'operation' },
  { id: 'results', label: 'Resultados', icon: FileOutput, group: 'operation' },
  { id: 'sources', label: 'Fuentes', icon: Atom, group: 'configuration' },
  { id: 'simulation', label: 'Parámetros de simulación', icon: SlidersHorizontal, group: 'configuration' },
  { id: 'positions', label: 'Posiciones', icon: Boxes, group: 'configuration' },
  { id: 'materials', label: 'Calibración del simulador', icon: FlaskConical, group: 'configuration' },
  { id: 'sections', label: 'Secciones de trabajo', icon: Boxes, group: 'configuration' },
];
const titles: Record<AppSection, { eyebrow: string; title: string }> = {
  cases: { eyebrow: 'Operación', title: 'Casos de irradiación' },
  load: { eyebrow: 'Geometría de entrada', title: 'Carga CAD' },
  results: { eyebrow: 'Análisis', title: 'Resultados' },
  sources: { eyebrow: 'Configuración del irradiador', title: 'Fuentes radiactivas' },
  simulation: { eyebrow: 'Configuración de cálculo', title: 'Parámetros de simulación' },
  positions: { eyebrow: 'Geometría del irradiador', title: 'Posiciones del recorrido' },
  materials: { eyebrow: 'Verificación de métodos', title: 'Calibración del simulador' },
  sections: { eyebrow: 'Posicionamiento', title: 'Secciones de trabajo' },
};

export default function AppShell({ active, onNavigate, onOpenStudy, children, irradiator, onChooseIrradiator }: { active: AppSection; onNavigate: (section: AppSection) => void; onOpenStudy: (caseId: string, calibrationCaseId?: string) => void; children: ReactNode; irradiator: IrradiatorType; onChooseIrradiator: () => void }) {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [themeReady, setThemeReady] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking');

  useEffect(() => {
    let cancelled = false;
    const preferred = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    apiGet<{ theme: 'light' | 'dark' | null }>('/api/preferences')
      .then((data) => { if (!cancelled) setTheme(data.theme ?? preferred); })
      .catch(() => { if (!cancelled) setTheme(preferred); })
      .finally(() => { if (!cancelled) setThemeReady(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    if (themeReady) apiJson('/api/preferences', 'PUT', { theme }).catch(() => {});
  }, [theme, themeReady]);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      apiGet('/api/health')
        .then(() => { if (!cancelled) setConnection('online'); })
        .catch(() => { if (!cancelled) setConnection('offline'); });
    };
    check();
    const timer = window.setInterval(check, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (irradiator.transport === 'suspended_litters') return;
    const stopLocalJobs = () => { navigator.sendBeacon(`${API_URL}/api/simulations/cancel-all`); };
    window.addEventListener('beforeunload', stopLocalJobs);
    return () => window.removeEventListener('beforeunload', stopLocalJobs);
  }, [irradiator.transport]);

  return (
    <div className={`app-frame ${collapsed ? 'is-sidebar-collapsed' : ''}`}>
      <a className="skip-link" href="#main-workspace">Ir al contenido</a>
      <aside className="app-sidebar" aria-label="Barra lateral">
        <div className="brand-lockup">
          <div className="brand-symbol"><Atom size={21} strokeWidth={1.55}/></div>
          <div className="brand-copy"><strong title={APPLICATION_FULL_TITLE}>{APPLICATION_NAME}</strong><span>{APPLICATION_SUBTITLE}</span></div>
        </div>
        <nav className="sidebar-navigation" aria-label="Navegación principal">
          {(['operation', 'configuration'] as const).map((group) => (
            <div className="nav-group" key={group}>
              <p>{group === 'operation' ? 'Operación' : 'Configuración'}</p>
              {navigation.filter((item) => item.group === group
                && (item.id !== 'positions' || irradiator.transport === 'suspended_litters')
                && (item.id !== 'sections' || irradiator.transport !== 'suspended_litters')).map((item) => {
                const Icon = item.icon;
                return <button key={item.id} className={active === item.id ? 'nav-button active' : 'nav-button'}
                  aria-current={active === item.id ? 'page' : undefined} aria-label={item.label} title={collapsed ? item.label : undefined}
                  onClick={() => onNavigate(item.id)}><Icon size={16} strokeWidth={1.7}/><span>{item.label}</span>{active === item.id && <ChevronRight className="nav-arrow" size={12}/>}</button>;
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className={`runtime-card ${connection}`} role="status" title={connection === 'online' ? 'Conexión con IRRADIUM disponible' : connection === 'offline' ? 'No se pudo contactar con IRRADIUM' : 'Verificando la conexión'}>
            <span className="runtime-dot"/><div><strong>{connection === 'online' ? 'Conexión local' : connection === 'offline' ? 'Sin conexión' : 'Conectando…'}</strong><small>Categoría {irradiator.id}</small></div><span className="app-version">{APPLICATION_VERSION}</span>
          </div>
        </div>
      </aside>
      <main className="app-main" id="main-workspace" tabIndex={-1}>
        <header className="app-header">
          <div className="toolbar-leading">
            <button type="button" className="icon-button sidebar-toggle" aria-label={collapsed ? 'Mostrar barra lateral' : 'Ocultar barra lateral'} aria-expanded={!collapsed} onClick={() => setCollapsed((value) => !value)}><PanelLeft size={19} strokeWidth={1.6}/></button>
            <span className="toolbar-separator" aria-hidden="true"/>
            <div className="toolbar-title"><span className="page-eyebrow">{titles[active].eyebrow}</span><h1>{titles[active].title}</h1></div>
          </div>
          <div className="header-actions">
            <LanguageSelector/>
            <button className="environment-pill" onClick={()=>setGuideOpen(true)} aria-label="Abrir guía de primeros pasos"><BookOpen size={14}/><span>Primeros pasos</span></button>
            <button className="environment-pill irradiator-switch" onClick={onChooseIrradiator} title="Cambiar tipo de irradiador" aria-label={`Categoría ${irradiator.id}. Cambiar tipo de irradiador`}><Layers3 size={13}/><span>Categoría {irradiator.id}</span><ChevronsUpDown size={11}/></button>
            <span className="toolbar-separator" aria-hidden="true"/>
            <div className="appearance-control" role="group" aria-label="Apariencia">
              <button type="button" aria-label="Activar modo claro" title="Modo claro" aria-pressed={theme === 'light'} onClick={() => setTheme('light')}><Sun size={14} strokeWidth={1.7}/></button>
              <button type="button" aria-label="Activar modo oscuro" title="Modo oscuro" aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}><Moon size={14} strokeWidth={1.7}/></button>
            </div>
          </div>
        </header>
        {irradiator.transport === 'suspended_litters' && <StudyActivityRibbon hidden={active === 'results'} onOpen={onOpenStudy}/>}
        <div className="app-content">
          {['sources', 'simulation', 'positions', 'materials'].includes(active) && <aside className="configuration-scope-note"><Layers3 size={15}/><div><strong>Configuración del irradiador</strong><span>Los casos guardados conservan su propia copia. Los cambios se aplican a los nuevos casos.</span></div></aside>}
          {children}
        </div>
      </main>
      <QuickStartGuide open={guideOpen} onOpenChange={setGuideOpen} onNavigate={onNavigate} onExample={(caseId)=>onOpenStudy(caseId)}/>
    </div>
  );
}
