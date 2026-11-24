'use strict';
// Menu actions share the existing account API and campaign model.
function initGameMenu(api,controls){
 const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 const button=(text,fn)=>{const b=make('button',text,'secondary');b.type='button';b.onclick=fn;return b;};
 let account=null;
 async function request(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',credentials:'same-origin',signal:AbortSignal.timeout(10000),headers:{'Content-Type':'application/json','X-NullCity':'1'},...(body?{body:JSON.stringify(body)}:{})});let value;try{value=await r.json();}catch{throw Error('Сервер недоступен. Открой HTTPS-версию игры.');}if(!r.ok){const error=Error(value.error||'Не удалось подключиться.');error.status=r.status;error.retryAfter=value.retryAfter;throw error;}return value;}
 function panel(title){const d=make('dialog',undefined,'menu-dialog');d.setAttribute('aria-label',title);const head=make('div',undefined,'menu-dialog-head');head.append(make('h2',title),button('Закрыть',()=>d.close()));d.append(head);d.addEventListener('close',()=>d.remove());document.body.append(d);d.showModal();return d;}
 const multiplayer=button('Мультиплеер · 2–4 игрока',()=>{
  const d=panel('Ночная смена / онлайн');d.append(make('p','Пять раундов. Общие вопросы, один ответ на раунд. Побеждает точность.','menu-lead'));
  for(const [title,description,url] of [['Быстрый матч','Подбор открытого лобби. Перед стартом все подтверждают готовность.','hub.html?play=quick'],['Играть с друзьями','Создай комнату, выбери тему и таймер, передай ссылку друзьям.','hub.html'],['Смотреть матч','Живые комнаты, счёт и разбор решений после финала.','hub.html?play=watch']]){
   const card=make('article',undefined,'menu-mode');card.append(make('h3',title),make('p',description));const a=make('a','Открыть →','menu-link');a.href=url;card.append(a);d.append(card);
  }
  if(!account)d.append(make('p','Для сетевой игры войди или создай аккаунт. Одиночная история доступна без регистрации.','menu-note'));
 });
 window.addEventListener('null-city-open-account',()=>showAccount());
 const login=button('Войти / Регистрация',()=>showAccount());
 const uiLang=()=>globalThis.NCI18n?.language||'ru';
 function finish(profile,d){account=profile;login.textContent='Оператор: '+account.name;d.close();}
 // Email 2FA step: the challenge is kept in this closure only and dies with the dialog.
 function twoFactor(d,stage,loginForm,info,loginStatus){
  let challenge=info.challenge,recovery=false,timer=null;
  stage.hidden=true;
  const step=make('div');d.append(step);
  const title=make('h3','Подтверждение входа');title.tabIndex=-1;
  const intro=make('p','Мы отправили код на адрес ','menu-note');const hint=make('strong',info.emailHint||'');hint.setAttribute('translate','no');intro.append(hint,document.createTextNode('. Код действует 10 минут.'));
  const f=make('form');f.noValidate=true;const label=make('label'),caption=make('span','Шестизначный код из письма'),input=make('input');input.name='code';input.type='text';input.inputMode='numeric';input.autocomplete='one-time-code';input.maxLength=64;input.required=true;input.spellcheck=false;input.setAttribute('autocapitalize','off');label.append(caption,input);
  const status=make('p','','menu-note');status.setAttribute('role','status');status.setAttribute('aria-live','polite');input.setAttribute('aria-describedby','menu-2fa-status');status.id='menu-2fa-status';
  const submit=make('button','Подтвердить','primary');submit.type='submit';
  const resend=button('Отправить код ещё раз',()=>sendAgain());
  const cooldown=make('p','','menu-note');cooldown.hidden=true;const cooldownLabel=make('span','Повторная отправка через'),timerText=make('span');timerText.setAttribute('translate','no');cooldown.append(cooldownLabel,document.createTextNode(' '),timerText);
  const toggle=button('Использовать резервный код',()=>{recovery=!recovery;input.value='';input.inputMode=recovery?'text':'numeric';caption.textContent=recovery?'Резервный код':'Шестизначный код из письма';toggle.textContent=recovery?'Использовать код из письма':'Использовать резервный код';status.textContent='';input.focus();});
  const leave=message=>{clearInterval(timer);step.remove();stage.hidden=false;loginStatus.textContent=message||'';loginForm.elements.password.focus();};
  const back=button('Назад',()=>leave());
  f.append(label,submit,resend,toggle,back);step.append(title,intro,f,cooldown,status);
  const setBusy=on=>{for(const b of [submit,toggle,back])b.disabled=on;resend.disabled=on||!!timer;};
  function cool(seconds){clearInterval(timer);timer=null;let left=seconds;const tick=()=>{if(left<=0){clearInterval(timer);timer=null;resend.disabled=false;cooldown.hidden=true;return;}resend.disabled=true;cooldown.hidden=false;timerText.textContent=Math.floor(left/60)+':'+String(left%60).padStart(2,'0');left--;};tick();timer=setInterval(tick,1000);}
  async function sendAgain(){setBusy(true);status.textContent='Отправляем код…';try{await request('login/2fa/resend',{challenge,lang:uiLang()});status.textContent='Новый код отправлен. Проверь почту.';cool(30);input.focus();}catch(e){if(e.status===410)return leave(e.message);status.textContent=e.message;if(e.retryAfter)cool(e.retryAfter);}finally{setBusy(false);}}
  f.onsubmit=async e=>{e.preventDefault();const raw=input.value.trim(),code=recovery?raw:raw.replace(/\s+/g,'');if(!recovery&&!/^\d{6}$/.test(code)||!code){status.textContent=recovery?'Введи резервный код.':'Введи шестизначный код из письма.';input.focus();return;}setBusy(true);status.textContent='Проверяем код…';try{finish(await request('login/2fa',{challenge,code}),d);}catch(error){if(error.status===410)return leave(error.message);status.textContent=error.message;input.select();setBusy(false);}};
  if(info.mailFailed){status.textContent=info.mailLimited?'Лимит писем с кодом на этот час исчерпан. Используй резервный код или попробуй позже.':'Письмо с кодом не отправлено. Дождись таймера и нажми «Отправить код ещё раз» или используй резервный код.';if(info.retryAfter)cool(info.retryAfter);}else{status.textContent='Код отправлен. Проверь почту.';cool(30);}
  d.addEventListener('close',()=>{clearInterval(timer);challenge=null;});
  input.focus();
 }
 function showAccount(register=false){
  const d=panel(account?'Пропуск оператора':register?'Регистрация':'Вход в аккаунт');
  if(account){d.append(make('p',account.name,'menu-lead'));const a=make('a','Профиль, друзья и результаты →','menu-link');a.href='hub.html';d.append(a,button('Выйти из аккаунта',async()=>{try{await request('logout',{});account=null;login.textContent='Войти / Регистрация';d.close();}catch(e){d.append(make('p',e.message));}}));return;}
  if(location.protocol!=='https:'){
   d.append(make('p','Этот адрес открывает только файлы игры. Регистрация и вход работают на защищённом сервере.','menu-note'));
   const targetHost=location.hostname||'localhost';
   const online=make('a','Перейти к регистрации / входу →','primary menu-link');online.href='https://'+targetHost+':8443/';d.append(online);
   d.append(make('p','Если браузер сообщает о недоверенном сертификате, подтверди исключение безопасности для адреса сервера или настрой доверие к сертификату.','menu-note'));
   return;
  }
  const stage=make('div');d.append(stage);const form=make('form');for(const [name,label,type,min,max]of [['email','Email','email',3,254],['password','Пароль · 12–128 символов','password',12,128],...(register?[['name','Имя оператора','text',2,32]]:[])]){const l=make('label',label),i=make('input');i.name=name;i.type=type;i.minLength=min;i.maxLength=max;i.required=true;i.autocomplete=name==='password'?(register?'new-password':'current-password'):name==='name'?'nickname':'email';l.append(i);form.append(l);}
  const status=make('p','','menu-note');status.setAttribute('role','status');const submit=make('button',register?'Создать аккаунт':'Войти','primary');submit.type='submit';form.append(submit,status);stage.append(form,button(register?'Уже есть аккаунт':'Создать аккаунт',()=>{d.close();showAccount(!register);}));
  const policy=make('a','Конфиденциальность','menu-link');policy.href='privacy.html';stage.append(policy);

  form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;status.textContent='Подключение…';try{const result=await request(register?'register':'login',{...Object.fromEntries(new FormData(form)),...(register?{}:{lang:uiLang()})});if(!register&&result.challenge){form.elements.password.value='';status.textContent='';twoFactor(d,stage,form,result,status);return;}finish(result,d);}catch(error){status.textContent=error.message;}finally{submit.disabled=false;}};
 }
 const dossier=button('Досье / достижения',()=>{
  const d=panel('Досье оператора'),m=api.getModel(),n=m.done.length+(m.capital?.completed.length||0),total=api.caseList.length+3;
  d.append(make('p',n+' / '+total+' операций завершено · '+m.xp+' XP','menu-lead'));const progress=make('progress');progress.max=total;progress.value=n;progress.setAttribute('aria-label','Прогресс сюжетной кампании');d.append(progress);
  const next=api.caseList.find(q=>!m.done.includes(q.id));d.append(make('p',next?'Следующая цель: '+next.title:m.capital?.completed.includes('corporation')?'REGENT остановлен. Вернись к историям жителей или попробуй сетевую смену.':'Следующая цель: открой Столицу и продвигайся к NEXUS.'));
  const badges=[['Первый след',m.done.includes('first'),'Заверши первую смену'],['Голос квартала',m.done.includes('broadcast'),'Верни жителям эфир'],['Связь восстановлена',m.capital?.completed.includes('mind'),'Заверши Сады связи'],['Город помнит',m.capital?.completed.includes('corporation'),'Останови REGENT'],['Свой стиль',(m.ownedOutfits?.length||0)>1,'Купи образ в NEON THREAD'],['Письмо домой',!!m.story?.letterReturned,'Верни посылку Юне']];
  for(const [title,done,desc]of badges){const row=make('article',undefined,'menu-badge'+(done?' earned':''));row.append(make('strong',(done?'✓ ':'◇ ')+title),make('p',desc));d.append(row);}
  d.append(make('p','Каждое дело открывает следующее. В арене есть отдельные уровни и ежедневная цель: три верных ответа в завершённых матчах. Пропуски дней не отнимают прогресс.','menu-note'));
 });
 multiplayer.classList.add('main-mode');controls.insertBefore(multiplayer,controls.children[1]);controls.append(login,dossier);controls.classList.add('expanded-menu');
 if(location.protocol==='https:')request('me').then(u=>{account=u;login.textContent='Оператор: '+u.name;}).catch(()=>{});
}
