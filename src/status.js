'use strict';
(function(){
 const $=id=>document.getElementById(id),t=globalThis.NCI18n.t;
 const states={ok:'Работает',degraded:'Требует внимания',starting:'Первое копирование',disabled:'Не включено',failed:'Ошибка'};
 let busy=false;
 async function refresh(){
  if(busy)return;busy=true;$('refresh-status').disabled=true;
  try{
   if(location.protocol!=='https:')throw Error('Для проверки открой HTTPS-версию сервера.');
   const response=await fetch('/api/status',{signal:AbortSignal.timeout(8000)});
   if(![200,503].includes(response.status))throw Error('Не удалось получить состояние сервера.');
   const state=await response.json();
   $('service-status').textContent=t(states[state.status]||'Требует внимания');
   $('db-status').textContent=t(states[state.database]||'Ошибка');
   $('backup-status').textContent=t(states[state.backups.status]||'Не включено');
   $('last-backup').textContent=state.backups.lastSuccess?new Intl.DateTimeFormat(NCI18n.language,{dateStyle:'medium',timeStyle:'short'}).format(new Date(state.backups.lastSuccess*1000)):t('Копия ещё не создана');
   $('backup-period').textContent=state.backups.intervalSeconds?String(state.backups.intervalSeconds/3600)+' h':'—';
   $('backup-retain').textContent=state.backups.retainedCopies||'—';$('status-error').textContent='';
  }catch(e){$('service-status').textContent=t('Сервер недоступен');$('status-error').textContent=t(e.message);for(const id of ['db-status','backup-status','last-backup','backup-period','backup-retain'])$(id).textContent='—';}
  finally{busy=false;$('refresh-status').disabled=false;}
 }
 $('refresh-status').onclick=refresh;refresh();setInterval(()=>{if(!document.hidden)refresh();},30000);
})();
