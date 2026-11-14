'use client';
import {useEffect,useRef,useState} from 'react';
import {apiGet,apiJson} from '../../lib/api';
import {localize,type Language} from '../../lib/i18n/translate';
export default function LanguageSelector(){
 const [language,setLanguage]=useState<Language>('es');
 const [error,setError]=useState('');
 const records=useRef(new WeakMap<Node,{source:string;last:string}>());
 const attributes=useRef(new WeakMap<Element,Map<string,{source:string;last:string}>>());
 useEffect(()=>{let live=true;apiGet<{language:Language}>('/api/language').then(v=>{if(live)setLanguage(v.language);}).catch(()=>{});return()=>{live=false;};},[]);
 useEffect(()=>{
  let active=true;
  document.documentElement.lang=language;
  window.dispatchEvent(new Event('irradium:language'));
  const root=document.body;
  let scheduled=false;
  const observer=new MutationObserver(()=>{if(!scheduled){scheduled=true;queueMicrotask(apply);}});
  function apply(){if(!active)return;observer.disconnect();scheduled=false;localize(root,language,records.current,attributes.current);observer.observe(root,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['title','aria-label','placeholder','alt']});}
  apply();return()=>{active=false;observer.disconnect();};
 },[language]);
 const change=async(next:Language)=>{setError('');try{await apiJson('/api/language','PUT',{language:next});setLanguage(next);}catch{setError('Could not save language / No se pudo guardar el idioma');}};
 return <label className="language-switch" data-i18n-skip><span className="sr-only">Language / Idioma</span><select aria-label="Language / Idioma" value={language} onChange={e=>void change(e.target.value as Language)}><option value="en">English</option><option value="es">Español</option></select>{error&&<span role="alert">{error}</span>}</label>;
}
