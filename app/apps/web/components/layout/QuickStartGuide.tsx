'use client';
import { useEffect, useState } from 'react';
import { BookOpen, ChevronLeft, ChevronRight, X } from 'lucide-react';
import DialogFrame from '../ui/DialogFrame';
import { API_URL, apiGet, apiJson } from '../../lib/api';
import type { AppSection } from '../../lib/types';

const steps: Array<{section: AppSection; title: string; text: string}> = [
  {section:'load',title:'Prepara la geometría de la carga',text:'Importa un STEP con cuerpos sólidos y nombres de material. El ejemplo incluido es un kit de fluidos con 54 cuerpos y cinco materiales, de 480 × 480 × 900 mm. También puedes elegir una referencia homogénea de agua. Sus fuentes y coordenadas están documentadas como reconstrucción didáctica.'},
  {section:'sources',title:'Revisa las fuentes y la fecha',text:'Las fuentes cilíndricas se definen con sus extremos y actividad de referencia. La fecha de irradiación determina el decaimiento. La configuración global se copia a cada caso nuevo; no cambia los casos existentes.'},
  {section:'positions',title:'Comprueba el recorrido',text:'Revisa los centros, distancias y giros en la vista de posiciones. En el ejemplo comercial hay 68 posiciones reconstruidas, con sus coordenadas y supuestos en el archivo de procedencia. Las vistas conservan los ejes reales; las sumas registran los giros.'},
  {section:'simulation',title:'Elige el cálculo y sus recursos',text:'OpenMC rápido calcula flujo y conversión, recomendado para referencias en aire. El cálculo intensivo transporta la energía en materiales. PHITS es opcional y se instala aparte. Una prueba corta permite estimar el coste; el número de historias no garantiza por sí solo la precisión.'},
  {section:'results',title:'Estudia tasas, dosis y tiempos',text:'Calcula el recorrido completo o posiciones seleccionadas. Revisa progreso, mapas, DUR y error estadístico antes de integrar. Dosis = suma de tasa × tiempo. Para una dosis objetivo, elige mínimo, media o máximo conscientemente. Pausar conserva las posiciones terminadas.'},
];

export default function QuickStartGuide({open, onOpenChange, onNavigate, onExample}: {open:boolean; onOpenChange:(open:boolean)=>void; onNavigate:(section:AppSection)=>void; onExample:(caseId:string)=>void}) {
  const [step,setStep]=useState(0);
  const [busy,setBusy]=useState(false);
  const [variant,setVariant]=useState('medical_kit');
  const [error,setError]=useState('');
  useEffect(()=>{
    let cancelled=false;
    apiGet<{dismissed:boolean}>('/api/onboarding').then(value=>{if(!cancelled&&!value.dismissed) onOpenChange(true);}).catch(()=>{});
    return ()=>{cancelled=true;};
  },[onOpenChange]);
  const close=async()=>{
    setError('');
    try { await apiJson('/api/onboarding','PUT',{dismissed:true,version:1}); onOpenChange(false); }
    catch { setError('No se pudo guardar tu preferencia. Reintenta cuando vuelva la conexión.'); }
  };
  const move=(next:number)=>{setStep(next);onNavigate(steps[next].section);};
  const create=async()=>{
    setBusy(true);setError('');
    try {
      const value=await apiJson<{case_id:string}>('/api/learning/example','POST',{variant});
      await apiJson('/api/onboarding','PUT',{dismissed:true,version:1});
      onOpenChange(false);onExample(value.case_id);
    } catch(e) {setError(e instanceof Error?e.message:'No se pudo crear el ejemplo.');}
    finally{setBusy(false);}
  };
  if(!open)return null;
  return <DialogFrame label="Primeros pasos con IRRADIUM" onClose={()=>{if(!busy)void close();}}>
    <div className="quickstart-card">
      <div className="quickstart-heading"><BookOpen size={24}/><span>PRIMEROS PASOS · {step+1} / {steps.length}</span><button className="icon-button" disabled={busy} aria-label="Cerrar guía" onClick={()=>void close()}><X size={18}/></button></div>
      <h2>{steps[step].title}</h2><p>{steps[step].text}</p>
      <div className="quickstart-progress" aria-label={`Paso ${step+1} de ${steps.length}`}>{steps.map((s,i)=><span key={s.title} className={i<=step?'done':''}/>)}</div>
      <p className="quickstart-note">Versión inicial · Categoría IV. Ejemplo educativo, sin validación operativa del fabricante.</p>
      {step===steps.length-1&&<label className="field"><span>Carga didáctica</span><select value={variant} disabled={busy} onChange={e=>setVariant(e.target.value)}><option value="medical_kit">Medical-fluid kit · 54 bodies · 5 materials</option><option value="water">Water reference · homogeneous block</option></select></label>}
      {error&&<p role="alert">{error}</p>}
      <div className="quickstart-actions">
        <a href={`${API_URL}/api/learning/guide`} target="_blank" rel="noreferrer">Guía PDF</a>
        <button disabled={busy} onClick={()=>void close()}>Omitir guía</button>
        {step>0&&<button disabled={busy} onClick={()=>move(step-1)}><ChevronLeft size={14}/>Anterior</button>}
        {step<steps.length-1?<button className="primary" disabled={busy} onClick={()=>move(step+1)}>Siguiente<ChevronRight size={14}/></button>:<button className="primary" disabled={busy} onClick={()=>void create()}>{busy?'Preparando…':'Crear ejemplo didáctico'}</button>}
      </div>
    </div>
  </DialogFrame>;
}
