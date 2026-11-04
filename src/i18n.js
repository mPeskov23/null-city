/* Presentation-only localization: simulation state, commands and save IDs stay canonical. */
(function(root){
 'use strict';
 const messages=root.NULL_CITY_MESSAGES||{},supported=['ru','en','es'],key='nullcity-language';
 let language='ru';try{const saved=root.localStorage?.getItem(key);if(supported.includes(saved))language=saved;}catch{}
 try{const requested=new URL(root.location.href).searchParams.get('lang');if(supported.includes(requested))language=requested;}catch{}
 const escape=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const keys=Object.keys(messages).filter(s=>/[А-Яа-яЁё]/.test(s)).sort((a,b)=>b.length-a.length);
 // Whole words prevent short labels from corrupting longer, unrelated words.
 const pattern=new RegExp(keys.map(escape).join('|'),'gu');
 const cache=new Map();
 function t(value,lang=language){
  if(typeof value!=='string'||lang==='ru'||!supported.includes(lang))return value;
  const index=lang==='en'?0:1;
  if(Object.hasOwn(messages,value))return messages[value][index];
  const trimmed=value.trim();
  if(Object.hasOwn(messages,trimmed))return value.slice(0,value.indexOf(trimmed))+messages[trimmed][index]+value.slice(value.indexOf(trimmed)+trimmed.length);
  const cacheKey=lang+'\0'+value;if(cache.has(cacheKey))return cache.get(cacheKey);
  const output=value.replace(pattern,(s,offset,whole)=>{
   const word=c=>!!c&&/[\p{L}\p{N}_]/u.test(c);
   if((word(s[0])&&word(whole[offset-1]))||(word(s.at(-1))&&word(whole[offset+s.length])))return s;
   return messages[s][index];
  });
  if(cache.size>=1024)cache.clear();cache.set(cacheKey,output);return output;
 }
 const cover={
 'EVERY SYSTEM HAS A WEAKNESS.':['У КАЖДОЙ СИСТЕМЫ ЕСТЬ СЛАБОЕ МЕСТО.','EVERY SYSTEM HAS A WEAKNESS.','TODO SISTEMA TIENE UNA DEBILIDAD.'],
 'Somewhere between code and chaos, a city is waiting.':['Где-то между кодом и хаосом тебя ждёт город.','Somewhere between code and chaos, a city is waiting.','En algún lugar entre el código y el caos, una ciudad te espera.'],
 'Explore its streets. Uncover hidden networks. Break through firewalls. Discover the secrets buried beneath the system.':['Исследуй улицы. Находи скрытые сети. Преодолевай межсетевые экраны. Раскрой тайны, спрятанные внутри системы.','Explore its streets. Uncover hidden networks. Break through firewalls. Discover the secrets buried beneath the system.','Explora sus calles. Descubre redes ocultas. Atraviesa cortafuegos. Desvela los secretos enterrados bajo el sistema.'],
 'Every terminal is a challenge. Every vulnerability tells a story. Every choice leaves a trace.':['Каждый терминал — испытание. Каждая уязвимость — история. Каждый выбор оставляет след.','Every terminal is a challenge. Every vulnerability tells a story. Every choice leaves a trace.','Cada terminal es un reto. Cada vulnerabilidad cuenta una historia. Cada decisión deja una huella.'],
 'Learn to hack. Explore the unknown. Rewrite the rules.':['Учись взлому. Исследуй неизвестное. Перепиши правила.','Learn to hack. Explore the unknown. Rewrite the rules.','Aprende a hackear. Explora lo desconocido. Reescribe las reglas.'],
 '[CLICK ANYWHERE TO ENTER]':['[НАЖМИ В ЛЮБОМ МЕСТЕ, ЧТОБЫ ВОЙТИ]','[CLICK ANYWHERE TO ENTER]','[HAZ CLIC EN CUALQUIER LUGAR PARA ENTRAR]'],
 '[CONNECTING TO THE CITY…]':['[ПОДКЛЮЧЕНИЕ К ГОРОДУ…]','[CONNECTING TO THE CITY…]','[CONECTANDO CON LA CIUDAD…]']
 };
 function coverText(s){return cover[s.replace(/\s+/g,' ').trim()]?.[supported.indexOf(language)]||t(s);}
 function select(lang){
  if(!supported.includes(lang)||lang===language)return;
  let persisted=true;try{root.localStorage.setItem(key,lang);}catch{persisted=false;}
  root.dispatchEvent(new Event('null-city-language-changing'));
  const url=new URL(root.location.href);
  if(!persisted||url.searchParams.has('lang')){url.searchParams.set('lang',lang);root.location.assign(url.href);}
  else root.location.reload();
 }
 root.NCI18n={t,coverText,select,get language(){return language;},supported};
 if(!root.document)return;
 const document=root.document,rendered=new WeakMap();
 const skip='script,style,textarea,[translate="no"],[data-no-i18n],.terminal-line.command,[data-cover-line],#cover-story';
 function translateNode(n){
  if(!n.parentElement||n.parentElement.closest(skip)||rendered.get(n)===n.nodeValue)return;
  const value=t(n.nodeValue);if(value!==n.nodeValue)n.nodeValue=value;rendered.set(n,value);
 }
 function render(target){
  if(target.nodeType===3){translateNode(target);return;}
  if(!target.querySelectorAll||target.closest?.(skip))return;
  const walker=document.createTreeWalker(target,NodeFilter.SHOW_TEXT);let n;while((n=walker.nextNode()))translateNode(n);
  for(const el of [target,...target.querySelectorAll('[aria-label],[placeholder],[title],[alt]')]){
   if(el.closest?.('[translate="no"],[data-no-i18n],script,style'))continue;
   for(const attr of ['aria-label','placeholder','title','alt'])if(el.hasAttribute?.(attr)){const old=el.getAttribute(attr),value=t(old);if(value!==old)el.setAttribute(attr,value);}
  }
 }
 function picker(parent){
  const label=document.createElement('label');label.className='language-picker';label.setAttribute('translate','no');
  label.append(document.createTextNode(language==='ru'?'Язык':language==='es'?'Idioma':'Language'));
  const s=document.createElement('select');s.setAttribute('aria-label',language==='ru'?'Язык игры':language==='es'?'Idioma del juego':'Game language');
  for(const [value,name]of [['ru','Русский'],['en','English'],['es','Español']]){const o=document.createElement('option');o.value=value;o.textContent=name;o.selected=value===language;s.append(o);}
  s.addEventListener('change',()=>select(s.value));label.addEventListener('click',e=>e.stopPropagation());label.addEventListener('keydown',e=>e.stopPropagation());label.append(s);parent.append(label);
 }
 document.documentElement.lang=language;
 const opening=document.getElementById('opening-cover');
 if(opening){opening.lang=language;opening.setAttribute('aria-label',language==='ru'?'Войти в NULL CITY':language==='es'?'Entrar en NULL CITY':'Enter NULL CITY');for(const line of opening.querySelectorAll('[data-cover-line],.cover-enter'))line.textContent=coverText(line.textContent.trim());const story=document.getElementById('cover-story');if(story)story.textContent=[...opening.querySelectorAll('[data-cover-line]')].map(n=>n.textContent).join(' ');}
 const host=document.createElement('div');host.className='language-dock';picker(host);document.body.append(host);
 render(document);
 // Existing UI creates DOM fragments dynamically. Translate only changed subtrees;
 // never write into form values, user content or the simulation data model.
 const observer=new MutationObserver(records=>{
  const targets=new Set();for(const record of records){if(record.type==='characterData')targets.add(record.target);else if(record.type==='attributes')targets.add(record.target);else for(const n of record.addedNodes)targets.add(n);}
  for(const target of targets){render(target);if(target.nodeType===1){const dialogs=target.matches('dialog,.modal-card')?[target]:target.querySelectorAll('dialog,.modal-card');for(const d of dialogs)if(!d.querySelector('.language-picker'))picker(d);}}
 });
 observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-label','placeholder','title','alt']});
})(globalThis);
