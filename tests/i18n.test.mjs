import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=JSON.parse(fs.readFileSync(new URL('../locales/source.json',import.meta.url)));
function runtime(language='ru'){
 const box={localStorage:{getItem:()=>language}};vm.createContext(box);
 for(const file of ['i18n-catalog','i18n'])vm.runInContext(fs.readFileSync(new URL('../src/'+file+'.js',import.meta.url),'utf8'),box);
 return box.NCI18n;
}
test('all registered story, UI, server and question messages have complete English and Spanish text',()=>{
 const i18n=runtime();
 for(const [group,strings]of Object.entries(source))for(const s of strings)for(const language of ['en','es']){
  const output=i18n.t(s,language);
  assert.ok(!/[А-Яа-яЁё]/.test(output),group+': '+s.slice(0,120)+' => '+output.slice(0,120));
 }
});
test('dynamic counters translate without changing numbers, IPs or commands',()=>{
 const i18n=runtime('es');
 assert.equal(i18n.t('Уровень 2 · 100 XP арены · побед 1 · ничьих 0 · поражений 0'),'Nivel 2 · 100 XP de arena · victorias 1 · empates 0 · derrotas 0');
 for(const command of ['set dns 10.24.4.53','grep ghost-17 /evidence/access.log','publish community','ping 10.61.9.40'])assert.equal(i18n.t(command),command);
 assert.equal(i18n.t('Импульс использован'),'Pulso utilizado');
});
test('Russian is unchanged and unsupported stored preferences safely fall back',()=>{
 assert.equal(runtime().t('Начать смену'),'Начать смену');assert.equal(runtime('xx').language,'ru');
});
test('both translations preserve every reference network address and pathname',()=>{
 const i18n=runtime();
 for(const s of [...source.simulator,...source.campaign])for(const lang of ['en','es']){
  const translated=i18n.t(s,lang);
  for(const token of s.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b|\/(?:etc|var|evidence)\/[a-zA-Z0-9_./-]+/g)||[])assert.ok(translated.includes(token),lang+' lost '+token);
 }
});

test('composed dialogue fragments translate before a translated reply',()=>{
 const i18n=runtime('es');
 assert.equal(i18n.t('Твой ответ: «Сервер отвечает. Проверю, почему портал не открывается.»'),'Tu respuesta: «El servidor responde. Comprobaré por qué no se abre el portal.»');
});
