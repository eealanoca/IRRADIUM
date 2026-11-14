'use client';

import { useEffect, useState } from 'react';
import AppShell from '../components/layout/AppShell';
import CasesView from '../features/cases/CasesView';
import MaterialsView from '../features/config/MaterialsView';
import SimulationParametersView from '../features/config/SimulationParametersView';
import WorkSectionsView from '../features/config/WorkSectionsView';
import LoadView from '../features/load/LoadView';
import SavedLoadsView from '../features/load/SavedLoadGeometry';
import ResultsView from '../features/results/ResultsView';
import SourcesView from '../features/sources/SourcesView';
import type { AppSection, IrradiatorType, StepMaterial } from '../lib/types';
import IrradiatorSelector from '../features/irradiators/IrradiatorSelector';
import RouteCasesView from '../features/route/RouteCasesView';
import RouteConfigurationView from '../features/route/RouteConfigurationView';
import RouteResultsView from '../features/route/RouteResultsView';
import RoutePositionsView from '../features/route/RoutePositionsView';
import { selectIrradiator } from '../lib/api';

export default function Home() {
  const [, refreshLocale] = useState(0);
  useEffect(()=>{const refresh=()=>refreshLocale(v=>v+1);window.addEventListener('irradium:language',refresh);return()=>window.removeEventListener('irradium:language',refresh);},[]);
  const [irradiator, setIrradiator] = useState<IrradiatorType | null>(null);
  if (!irradiator) return <IrradiatorSelector onSelect={(type) => {
    selectIrradiator(type.id);
    setIrradiator(type);
  }}/>;
  return <Workspace key={irradiator.id} irradiator={irradiator} onChooseIrradiator={() => setIrradiator(null)}/>;
}

function Workspace({ irradiator, onChooseIrradiator }: { irradiator: IrradiatorType; onChooseIrradiator: () => void }) {
  const routeWorkflow = irradiator.transport === 'suspended_litters';
  const [section, setSection] = useState<AppSection>('load');
  const [visitedSections, setVisitedSections] = useState<Set<AppSection>>(() => new Set(['load']));
  const [stepFile, setStepFile] = useState<File | null>(null);
  const [stlFile, setStlFile] = useState<File | null>(null);
  const [stepMaterials, setStepMaterials] = useState<StepMaterial[]>([]);
  const [stepMaterialTags, setStepMaterialTags] = useState<string[]>([]);
  const [stepSize, setStepSize] = useState<[number, number, number]>();
  const [configurationRevision, setConfigurationRevision] = useState(0);
  const [resultsCaseId, setResultsCaseId] = useState('');
  const [resultsRequest, setResultsRequest] = useState<{ caseId: string }>();
  const [calibrationRequest, setCalibrationRequest] = useState<{ caseId: string }>();
  const [resultsRevision, setResultsRevision] = useState(0);
  const [cadMode, setCadMode] = useState<'saved' | 'draft'>('saved');
  const [savedCaseId, setSavedCaseId] = useState('');

  const navigate = (nextSection: AppSection, resultCase?: string) => {
    window.scrollTo({ top: 0, behavior: 'instant' });
    if (nextSection === 'results') {
      setResultsCaseId(resultCase ?? (savedCaseId || resultsCaseId));
      setResultsRevision((current) => current + 1);
    }
    setVisitedSections((current) => {
      if (current.has(nextSection)) return current;
      const next = new Set(current);
      next.add(nextSection);
      return next;
    });
    setSection(nextSection);
  };

  const openResults = (caseId?: string) => {
    if (caseId) { setResultsCaseId(caseId); setSavedCaseId(caseId); setResultsRequest({ caseId }); }
    navigate('results', caseId);
  };

  const openCalibration = (caseId: string) => {
    setCalibrationRequest({ caseId });
    navigate('materials');
  };

  const inspectSavedLoad = (caseId: string) => {
    setSavedCaseId(caseId);
    setCadMode('saved');
    navigate('load');
  };

  const preparingLoad = !routeWorkflow || cadMode === 'draft';

  return (
    <AppShell active={section} onNavigate={navigate} onOpenStudy={(caseId, referenceId) => {
      if (referenceId) openCalibration(referenceId);
      else openResults(caseId);
    }} irradiator={irradiator} onChooseIrradiator={onChooseIrradiator}>
      {visitedSections.has('load') && <div hidden={section !== 'load'}>
        {routeWorkflow && <div className="result-analysis-tabs cad-mode-tabs" role="tablist" aria-label="Origen de la carga CAD">
          <button role="tab" aria-selected={cadMode === 'saved'} onClick={() => setCadMode('saved')}>Cargas guardadas</button>
          <button role="tab" aria-selected={cadMode === 'draft'} onClick={() => setCadMode('draft')}>Preparar carga</button>
        </div>}
        {section === 'load' && !preparingLoad && <SavedLoadsView selectedId={savedCaseId} onSelect={setSavedCaseId} onResults={openResults}/>}
        <div hidden={!preparingLoad}><LoadView visible={section === 'load' && preparingLoad} routeWorkflow={routeWorkflow} stepFile={stepFile} stlFile={stlFile} stepMaterials={stepMaterials} onStepChange={setStepFile} onStepSizeChange={setStepSize} onStlChange={setStlFile} onStepMaterialsChange={setStepMaterials} onMaterialTagsChange={setStepMaterialTags} onContinue={() => navigate('cases')} /></div>
      </div>}
      {visitedSections.has('cases') && <div hidden={section !== 'cases'}>{routeWorkflow ? <RouteCasesView visible={section === 'cases'} selectedId={savedCaseId} onSelect={setSavedCaseId} onInspect={inspectSavedLoad} stepFile={stepFile} materials={stepMaterials} materialTags={stepMaterialTags} revision={configurationRevision} onLoad={() => { setCadMode('draft'); navigate('load'); }} onConfigure={() => navigate('simulation')} onResults={openResults}/> : <CasesView stepFile={stepFile} stlFile={stlFile} stepMaterials={stepMaterials} stepMaterialTags={stepMaterialTags} configurationRevision={configurationRevision} onOpenLoad={() => navigate('load')} onOpenResults={openResults} />}</div>}
      {visitedSections.has('results') && <div hidden={section !== 'results'}>{routeWorkflow ? <RouteResultsView requestedCase={resultsRequest} visible={section === 'results'} preferredCaseId={resultsCaseId} revision={resultsRevision} onOpenCalibration={openCalibration} onInspect={inspectSavedLoad} onSelectCase={(caseId) => { setResultsCaseId(caseId); setSavedCaseId(caseId); }}/> : <ResultsView preferredCaseId={resultsCaseId} refreshKey={resultsRevision} irradiator={irradiator} />}</div>}
      {visitedSections.has('sources') && <div hidden={section !== 'sources'}><SourcesView configurationRevision={configurationRevision} /></div>}
      {visitedSections.has('simulation') && <div hidden={section !== 'simulation'}>{routeWorkflow ? <RouteConfigurationView revision={configurationRevision} onPositions={() => navigate('positions')} onCalibration={() => navigate('materials')} onSave={() => setConfigurationRevision((current) => current + 1)}/> : <SimulationParametersView onProfilesChanged={() => setConfigurationRevision((current) => current + 1)} />}</div>}
      {section === 'positions' && <RoutePositionsView revision={configurationRevision} size={stepSize} onSaved={() => setConfigurationRevision((value) => value + 1)}/>}
      {visitedSections.has('materials') && <div hidden={section !== 'materials'}><MaterialsView routeWorkflow={routeWorkflow} calibrationRequest={calibrationRequest} visible={section === 'materials'}/></div>}
      {visitedSections.has('sections') && <div hidden={section !== 'sections'}><WorkSectionsView /></div>}
    </AppShell>
  );
}
