import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const read=f=>fs.readFileSync(new URL('../'+f,import.meta.url),'utf8');
const hubJs=read('src/hub.js'),menuJs=read('src/menu.js'),hubHtml=read('hub.html'),privacy=read('privacy.html');
function i18n(){const box={localStorage:{getItem:()=>'ru'}};vm.createContext(box);for(const f of ['i18n-catalog','i18n'])vm.runInContext(read('src/'+f+'.js'),box);return box.NCI18n;}
test('every element id used by the 2FA code exists in hub.html',()=>{
 const ids=[...hubJs.matchAll(/\$\('#(twofa-[a-z-]+|security-panel)'\)/g)].map(m=>m[1]);
 assert.ok(ids.length>10);
 for(const id of new Set(ids))assert.ok(hubHtml.includes('id="'+id+'"'),'missing #'+id);
});
test('both login UIs discriminate on challenge and call the 2FA endpoints',()=>{
 for(const [name,src] of [['hub',hubJs],['menu',menuJs]]){
  assert.match(src,/\.challenge/,name);assert.ok(src.includes('login/2fa'),name);assert.ok(src.includes('login/2fa/resend'),name);assert.match(src,/lang/,name);
 }
 for(const ep of ['2fa/setup','2fa/enable','2fa/disable'])assert.ok(hubJs.includes(ep),ep);
});
test('challenge, password and recovery codes are never persisted or injected as HTML',()=>{
 for(const src of [hubJs,menuJs]){
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML/.test(src.replace(/\/\/.*$/gm,'')) || !/2fa|twofa|challenge/i.test(src.match(/.*(?:innerHTML|outerHTML|insertAdjacentHTML).*/g).join('')));
  for(const line of src.split('\n'))if(/(localStorage|sessionStorage)\.setItem/.test(line))assert.ok(!/challenge|recovery|password|codes/i.test(line),line.slice(0,160));
 }
});
test('code inputs are accessible one-time-code fields',()=>{
 assert.match(hubHtml,/id="twofa-code"[^>]*autocomplete="one-time-code"/);
 assert.match(hubHtml,/id="twofa-manage-code"[^>]*autocomplete="one-time-code"/);
 assert.match(hubHtml,/id="twofa-status"[^>]*role="status"[^>]*aria-live="polite"/);
 assert.ok(menuJs.includes("autocomplete='one-time-code'")&&menuJs.includes("'aria-live'"));
});
test('2FA interface and privacy text translate fully to English and Spanish',()=>{
 const t=i18n(),texts=[];
 for(const m of hubHtml.matchAll(/>([^<>]*[А-Яа-яЁё][^<>]*)</g))if(/2fa|Двухфактор|код|Код/.test(m[1]))texts.push(m[1]);
 for(const src of [hubJs,menuJs])for(const m of src.matchAll(/'([^'\n]*[А-Яа-яЁё][^'\n]*)'/g))if(/код|Код|2FA|Двухфактор|письм|Назад|Подтвер/.test(m[1]))texts.push(m[1]);
 const p=privacy.match(/<h2>Двухфакторная[^]*?<\/p>/)[0];texts.push(...[...p.matchAll(/>([^<>]+)</g)].map(m=>m[1]));
 assert.ok(texts.length>25);
 for(const s of texts)for(const lang of ['en','es'])assert.ok(!/[А-Яа-яЁё]/.test(t.t(s,lang)),lang+': '+s+' => '+t.t(s,lang));
});

// Behavioural checks of src/hub.js against a stubbed DOM and fetch.
function hubHarness(){
 const els={},calls=[],intervals=[];let next={};
 const mk=id=>({id,hidden:false,textContent:'',value:'',disabled:false,dataset:{},classList:{toggle(){},add(){}},elements:new Proxy({},{get:(t,k)=>t[k]??={value:'',focus(){},select(){}}}),querySelectorAll:()=>[],querySelector:()=>null,replaceChildren(){},append(){},prepend(){},reset(){},focus(){},select(){},setAttribute(){}});
 const E=s=>els[s]??=mk(s);
 const ctx={console,document:{querySelector:E,querySelectorAll:()=>[],createElement:mk},location:{protocol:'https:',search:'',hostname:'x',href:'https://x/'},window:{},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},localStorage:{},EventSource:class{close(){}},AbortSignal,URLSearchParams,URL,FormData:class{*[Symbol.iterator](){}},performance,history:{},setTimeout,clearInterval(){},setInterval:(fn,ms)=>intervals.push(ms),
  fetch:async(path,o)=>{calls.push((o?.method||'GET')+' '+path+' '+(o?.body||''));const r=next[path]??{s:200,b:{}};return {ok:r.s<400,status:r.s,json:async()=>r.b};}};
 const tick=()=>new Promise(r=>setTimeout(r,30));
 const boot=async(twofa=false)=>{next={'/api/me':{s:200,b:{id:1,name:'Bob',bio:'',twofa}},'/api/rooms':{s:200,b:[]},'/api/stats':{s:200,b:{badges:[],rank:'',level:1,xp:0,daily:{}}}};vm.createContext(ctx);E('#twofa-confirm-form');vm.runInContext('(function(){'+hubJs+'\n})()',ctx);await tick();};
 const submit=async(id,fields={})=>{const f=E(id);for(const [k,v] of Object.entries(fields))f.elements[k].value=v;f.onsubmit({preventDefault(){},currentTarget:f,submitter:{value:'login'}});await tick();};
 return {E,calls,intervals,boot,submit,reply:(path,s,b)=>{next[path]={s,b};}};
}
test('wrong password or code on 2FA routes (403) keeps the session; only 401 signs out',async()=>{
 const h=hubHarness();await h.boot();
 h.reply('/api/2fa/setup',403,{error:'Неверный пароль.'});await h.submit('#twofa-manage-form',{password:'x'});
 assert.equal(h.E('#club').hidden,false);assert.equal(h.E('#twofa-manage-status').textContent,'Неверный пароль.');
 h.reply('/api/2fa/setup',401,{error:'Войди в аккаунт.'});await h.submit('#twofa-manage-form',{password:'x'});
 assert.equal(h.E('#club').hidden,true);
});
test('disable accepts a recovery code and reports an unsent mail',async()=>{
 const h=hubHarness();await h.boot(true);
 h.reply('/api/2fa/disable',200,{challenge:'c',emailHint:'x',mailFailed:true,retryAfter:20});await h.submit('#twofa-manage-form',{password:'p'});
 assert.match(h.E('#twofa-manage-status').textContent,/не отправлено/);assert.equal(h.E('#twofa-confirm-form').hidden,false);
 h.reply('/api/2fa/disable',200,{ok:true});await h.submit('#twofa-confirm-form',{code:'abcde-fghjk'});
 assert.ok(h.calls.some(c=>c.includes('"code":"abcde-fghjk"')));assert.match(h.E('#twofa-manage-status').textContent,/отключена/);
 assert.match(hubHtml,/id="twofa-manage-code"[^>]*maxlength="64"/);
});
test('a dead challenge (410) returns to the password step in the profile and to the login form at sign-in',async()=>{
 let h=hubHarness();await h.boot();
 h.reply('/api/2fa/setup',200,{challenge:'c',emailHint:'x'});await h.submit('#twofa-manage-form',{password:'p'});assert.equal(h.E('#twofa-confirm-form').hidden,false);
 h.reply('/api/2fa/enable',410,{error:'Код недействителен или истёк. Начни заново.'});await h.submit('#twofa-confirm-form',{code:'123456'});
 assert.equal(h.E('#twofa-confirm-form').hidden,true);assert.equal(h.E('#twofa-manage-form').hidden,false);assert.match(h.E('#twofa-manage-status').textContent,/Начни заново/);
 h=hubHarness();await h.boot();h.E('#club').hidden=true;
 h.reply('/api/login',200,{twofa:true,challenge:'c',emailHint:'x'});await h.submit('#auth-form',{email:'a@b.c',password:'p'});assert.equal(h.E('#twofa-step').hidden,false);
 h.reply('/api/login/2fa',410,{error:'Код недействителен или истёк. Начни заново.'});await h.submit('#twofa-form',{code:'123456'});
 assert.equal(h.E('#twofa-step').hidden,true);assert.equal(h.E('#auth').hidden,false);assert.match(h.E('#notice').textContent,/Начни заново/);
});
test('an unsent login challenge shows an accurate message and counts down retryAfter',async()=>{
 const h=hubHarness();await h.boot();h.E('#club').hidden=true;
 h.reply('/api/login',200,{twofa:true,challenge:'c',emailHint:'x',mailFailed:true,retryAfter:17});await h.submit('#auth-form',{email:'a@b.c',password:'p'});
 assert.equal(h.E('#twofa-timer').textContent,'0:17');assert.equal(h.E('#twofa-resend').disabled,true);assert.doesNotMatch(h.E('#twofa-status').textContent,/Код отправлен/);assert.match(h.E('#twofa-status').textContent,/не отправлено/);
 h.reply('/api/login',200,{twofa:true,challenge:'d',emailHint:'x',mailFailed:true,mailLimited:true});await h.submit('#auth-form',{email:'a@b.c',password:'p'});
 assert.match(h.E('#twofa-status').textContent,/Лимит писем/);assert.equal(h.E('#twofa-resend').disabled,false);
 h.reply('/api/login/2fa/resend',429,{error:'Повторная отправка письма пока недоступна.',retryAfter:9});await h.E('#twofa-resend').onclick();await new Promise(r=>setTimeout(r,60));
 assert.equal(h.E('#twofa-timer').textContent,'0:09');
});
test('menu sign-in goes back to the login form on 410 and honours retryAfter',()=>{
 assert.match(menuJs,/status===410\)return leave\(/);assert.match(menuJs,/info\.retryAfter\)cool\(info\.retryAfter\)/);assert.match(menuJs,/e\.retryAfter\)cool\(e\.retryAfter\)/);assert.match(menuJs,/error\.retryAfter=value\.retryAfter/);
});
