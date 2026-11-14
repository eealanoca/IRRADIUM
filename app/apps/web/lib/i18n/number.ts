export const uiLocale=()=>typeof document!=='undefined'&&document.documentElement.lang==='es'?'es-BO':'en-US';
export const numberFormatter=(options:Intl.NumberFormatOptions)=>({format:(value:number)=>new Intl.NumberFormat(uiLocale(),options).format(value)});
