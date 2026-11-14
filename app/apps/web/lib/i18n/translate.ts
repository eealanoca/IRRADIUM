import {phrases} from './en';
export type Language='en'|'es';
const fragments:Record<string,string>={
 'En curso':'Running','Finalizado':'Completed','Parcial':'Partial','Disponible':'Available','Preparando':'Preparing','Calculando':'Calculating','Guardando':'Saving','Detenido':'Stopped','Detenida':'Stopped','Pausado':'Paused','Pausada':'Paused','Refinando':'Refining','Verificando':'Checking','Cargando':'Loading','Conectando':'Connecting','Generando':'Generating','Posición':'Position','Posiciones':'Positions','posición':'position','posiciones':'positions','fuentes':'sources','fuente':'source','carga':'load','cargas':'loads','geometría':'geometry','geometrías':'geometries','celdas':'cells','celda':'cell','vóxeles':'voxels','vóxel':'voxel','materiales':'materials','material':'material','medias':'means','media':'mean','mínimo':'minimum','máximo':'maximum','mínima':'minimum','máxima':'maximum','mínimas':'minimum','máximas':'maximum','inferior':'lower','superior':'upper','mapas':'maps','mapa':'map','tasas':'dose rates','tasa':'dose rate','dosis':'dose','tiempos':'times','tiempo':'time','parámetros':'parameters','parámetro':'parameter','objetivo':'target','recorrido':'route','trayectoria':'trajectory','trayectorias':'trajectories','historias':'histories','historia':'history','lotes':'batches','lote':'batch','paso':'step','pasos':'steps','resultado':'result','resultados':'results','estudio':'study','estudios':'studies','simulación':'simulation','simulaciones':'simulations','error relativo':'relative error','error máximo':'maximum error','intervalo':'interval','intervalos':'intervals','acumulada':'accumulated','acumulado':'accumulated','por posición':'per position','con igual tiempo de estancia':'with equal dwell time','malla':'mesh','mallas':'meshes','eje':'axis','nivel':'level','niveles':'levels','corte':'slice','cortes':'slices','historial':'history','distancias':'distances','distancia':'distance','giros':'rotations','giro':'rotation','restablecer':'reset','visualizada':'displayed','seleccionada':'selected','seleccionado':'selected','seleccionadas':'selected','seleccionados':'selected','calculadas':'calculated','calculados':'calculated','calculado':'calculated','guardadas':'saved','guardados':'saved','guardada':'saved','guardado':'saved','configuradas':'configured','configurados':'configured','configurada':'configured','configurado':'configured','completadas':'completed','completados':'completed','completa':'complete','completo':'complete','disponibles':'available','disponible':'available','visibles':'visible','visible':'visible','activos':'active','activas':'active','activo':'active','activa':'active','totales':'total','total':'total','número':'number','núcleos':'cores','datos':'data','archivo':'file','archivos':'files','nombre':'name','descripción':'description','configuración':'configuration','referencia':'reference','referencias':'references','en curso':'running','de referencia':'reference','recomendado':'recommended','recomendada':'recommended','insuficiente':'insufficient','precisión':'precision','físico':'physical','física':'physical','estadístico':'statistical','estadística':'statistical','días':'days','por':'per','hasta':'up to','sin':'without','y':'and','con':'with','de':'of','del':'of the','en':'in','la':'the','el':'the','los':'the','las':'the','un':'a','una':'a','cada':'each','para':'for','este':'this','esta':'this','estos':'these','estas':'these','se':'','su':'its',
 'Irradiador':'Irradiator','irradiador':'irradiator','irradiadores':'irradiators','simulador':'simulator','autoblindado':'self-shielded','panorámico':'panoramic','almacenamiento':'storage','húmedo':'wet','seco':'dry','aire':'air','agua':'water','rápido':'fast','intensivo':'intensive','determinista':'deterministic','calibración':'calibration','aprendizaje':'learning','interactivo':'interactive','generar':'generate','editar':'edit','añadir':'add','guardar':'save','ver':'view','vista':'view','vistas':'views','buscar':'search','limpiar':'clear','actualizar':'update','reemplazar':'replace','reducir':'reduce','ampliación':'expansion','seleccionar':'select','reintentar':'retry','convergencia':'convergence','atenuación':'attenuation','dispersión':'scattering','energía':'energy','flujo':'flux','conversión':'conversion','absorción':'absorption','longitud':'length','centro':'center','inicial':'initial','fecha':'date','actividad':'activity','inventario':'inventory','lectura':'reading','elementos':'elements','fracciones':'fractions','composición':'composition','densidad':'density','tamaño':'size','promedio':'average','técnico':'technical','diagnóstico':'diagnostics','suma':'sum','superposición':'superposition','presupuesto':'budget','recursos':'resources','memoria':'memory','peso':'weight','pesos':'weights','fotónica':'photon','fotones':'photons','transporte':'transport','profundidad':'depth','opacidad':'opacity','uniformidad':'uniformity','línea':'line','banda':'band','superficies':'surfaces','superficie':'surface','aportes':'contributions','aporte':'contribution','cálculo':'calculation','cálculos':'calculations','diámetro':'diameter','revolución':'revolution','ángulo':'angle','ángulos':'angles','coordenadas':'coordinates','método':'method','métodos':'methods','fijo':'fixed','etapa':'stage','cancelado':'cancelled','cancelada':'cancelled','pendiente':'pending','alcanzado':'reached','alcanzada':'reached','interrumpido':'interrupted','interrumpida':'interrupted',
};
const dictionary={...fragments,...phrases};
const normalized=new Map([...Object.entries(fragments),...Object.entries(phrases)].map(([key,value])=>[key.normalize('NFC').toLocaleLowerCase('es'),value]));
const escaped=Object.keys(dictionary).sort((a,b)=>b.length-a.length).map(s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
const pattern=new RegExp(`(?<![\\p{L}\\p{N}_])(?:${escaped.join('|')})(?![\\p{L}\\p{N}_])`,'giu');
export function translate(source:string,language:Language):string{
 if(language==='es'||!source.trim()||/^[A-Z][a-z]?\d*$/.test(source.trim()))return source;
 const key=source.trim().normalize('NFC').toLocaleLowerCase('es');
 const exact=phrases[source.trim()]??normalized.get(key);
 if(exact!==undefined)return source.replace(source.trim(),exact);
 return source.replace(pattern,match=>/^[XYZ]$/.test(match)?match:(normalized.get(match.normalize('NFC').toLocaleLowerCase('es'))??match));
}
/** Text-node updates preserve React's node identity and never touch input values. */
export function localize(root:HTMLElement,language:Language,records:WeakMap<Node,{source:string;last:string}>,attributes:WeakMap<Element,Map<string,{source:string;last:string}>>){
 const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
 let node:Node|null;
 while((node=walk.nextNode())){
  const parent=node.parentElement;
  if(!parent||parent.closest('script,style,textarea,input,code,pre,[data-i18n-skip]'))continue;
  if(parent instanceof HTMLOptionElement && /^\d{8}T/.test(parent.value))continue;
  const text=node.nodeValue??'';let record=records.get(node);
  if(!record||text!==record.last)record={source:text,last:text};
  const value=translate(record.source,language);if(text!==value)node.nodeValue=value;
  record.last=value;records.set(node,record);
 }
 for(const element of Array.from(root.querySelectorAll('*'))){
  if(element.closest('[data-i18n-skip]'))continue;
  let map=attributes.get(element);if(!map){map=new Map();attributes.set(element,map);}
  for(const name of ['aria-label','title','placeholder','alt']){
   const text=element.getAttribute(name);if(text===null)continue;
   let record=map.get(name);if(!record||text!==record.last)record={source:text,last:text};
   const value=translate(record.source,language);if(value!==text)element.setAttribute(name,value);
   record.last=value;map.set(name,record);
  }
  if(element instanceof HTMLAnchorElement&&element.href.includes('/api/learning/guide')){
   const url=new URL(element.href);url.searchParams.set('language',language);element.href=url.toString();
  }
 }
}
