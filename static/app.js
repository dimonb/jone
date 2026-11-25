'use strict';
const $ = id => document.getElementById(id);
const labels = {easy:'☀ Лёгкая колода', hard:'✦ Сложная колода', adult:'☾ Колода 18+'};
const node = (tag, text, cls) => { const e = document.createElement(tag); if(text !== undefined) e.textContent = text; if(cls) e.className = cls; return e; };
let session = null, state = null, busy = false, polling = null, lastScreen = '', noticeTimer;
// Tokens never enter a URL. Storage access can throw in restricted webviews.
const storage = {
  get(kind, key) { try { return window[kind].getItem(key); } catch (_) { return null; } },
  set(kind, key, value) { try { window[kind].setItem(key, value); return true; } catch (_) { return false; } },
  remove(kind, key) { try { window[kind].removeItem(key); } catch (_) {} }
};
const roomCode = value => typeof value === 'string' && /^[A-Fa-f0-9]{8}$/.test(value) ? value.toUpperCase() : null;
function identity(raw) {
  try {
    const value = JSON.parse(raw);
    return value && roomCode(value.code) && typeof value.token === 'string' && /^[A-Za-z0-9_-]{43}$/.test(value.token)
      ? {code:roomCode(value.code), token:value.token} : null;
  } catch (_) { return null; }
}
function remembered(code) {
  for(const kind of ['localStorage', 'sessionStorage']) {
    const saved = identity(storage.get(kind, 'jone-room-'+code));
    if(saved && saved.code === code) return saved;
  }
  return null;
}
function remember(value) {
  const raw = JSON.stringify(value);
  const durable = storage.set('localStorage', 'jone-room-'+value.code, raw);
  storage.set('sessionStorage', 'jone-room-'+value.code, raw);
  storage.set('localStorage', 'jone-active', value.code);
  storage.set('sessionStorage', 'jone-active', value.code);
  return durable;
}
function forget(value) {
  if(!value) return;
  for(const kind of ['localStorage', 'sessionStorage']) {
    storage.remove(kind, 'jone-room-'+value.code);
    if(storage.get(kind, 'jone-active') === value.code) storage.remove(kind, 'jone-active');
  }
}
function showWelcome(code) {
  state=null; lastScreen='';
  $('table').classList.add('hidden'); $('welcome').classList.remove('hidden');
  $('join-form').elements.code.value=code || '';
}
function roomURL(code) { return '/room/'+code; }

function notice(text) { $('notice').textContent = text; $('notice').classList.remove('hidden'); clearTimeout(noticeTimer); noticeTimer = setTimeout(()=>$('notice').classList.add('hidden'), 6000); }
async function api(path, body, current=session) {
  const controller = new AbortController(), timer = setTimeout(()=>controller.abort(), 8000);
  try {
    const response = await fetch(path, {method:body ? 'POST':'GET', headers:{'Content-Type':'application/json', ...(current ? {Authorization:'Bearer '+current.token}:{})}, body:body ? JSON.stringify(body):undefined, signal:controller.signal});
    const data = await response.json();
    if(!response.ok) { const error=new Error(data.error || 'Ошибка сервера'); error.status=response.status; throw error; }
    return data;
  } finally { clearTimeout(timer); }
}
async function enter(form, create) {
  if(busy) return; busy = true;
  const f = new FormData(form), data = {name:f.get('name'), adult:f.get('adult') === 'on'};
  if(create) Object.assign(data, {mode:f.get('mode'), rounds:Number(f.get('rounds'))});
  try {
    const code=roomCode(String(f.get('code')).trim());
    // Returning to a known room resumes its seat, never silently creates another.
    const saved=!create && code ? remembered(code) : null;
    session = saved || await api(create ? '/api/rooms':'/api/rooms/'+code+'/join', data, null);
    const durable=remember(session);
    history.replaceState(null, '', roomURL(session.code)); showWelcome(session.code);
    await poll();
    if(!durable) notice('Браузер не сохраняет сеанс надолго. Не закрывайте эту вкладку.');
  } catch(e) { notice(e.message); } finally { busy=false; }
}
$('create-form').addEventListener('submit', e=>{e.preventDefault(); enter(e.target,true);});
$('join-form').addEventListener('submit', e=>{e.preventDefault(); enter(e.target,false);});
$('create-form').addEventListener('change', ()=>{
  const adult = new FormData($('create-form')).get('mode') === 'adult';
  $('create-age').classList.toggle('hidden',!adult);
  $('create-form').elements.adult.required = adult;
});
$('rules-open').onclick=()=>$('rules').showModal();
$('rules-close').onclick=()=>$('rules').close();
$('leave').onclick=()=>{if(!confirm('Выйти из комнаты? Место станет неактивным через 15 секунд.')) return; forget(session); session=null; history.replaceState(null,'','/'); showWelcome();};
$('copy-link').onclick=async()=>{
  const link=location.origin+roomURL(state.code);
  try { await navigator.clipboard.writeText(link); notice('Ссылка скопирована'); } catch(_) { prompt('Скопируйте ссылку:',link); }
};
function button(text, action, cls='secondary', extra={}) {
  const b=node('button',text,cls); b.type='button'; b.onclick=()=>act({action,...extra}); return b;
}
async function act(data) {
  if(busy || !session) return; busy=true;
  try { const current=session; const next=await api('/api/rooms/'+current.code+'/action',data,current); if(session!==current) return; state=next; render(); }
  catch(e) { notice(e.message); } finally { busy=false; }
}
function entry(kind, placeholder, text, maximum) {
  const form=node('form',undefined,'entry'), row=node('div',undefined,'entry-row'), label=node('label',text), input=node('input');
  input.name='text'; input.placeholder=placeholder; input.maxLength=maximum; input.required=true; input.autocomplete='off';
  label.append(input); row.append(label); const submit=node('button','Отправить','primary'); submit.type='submit'; row.append(submit); form.append(row);
  if(kind==='hint') { const help=node('p','Одно слово. Нельзя менять после отправки.','helper'); form.append(help); }
  if(kind==='guess') form.append(button('Пас — не знаю','pass'));
  form.onsubmit=e=>{e.preventDefault(); act({action:kind,text:input.value});}; return form;
}
function render() {
  const s=state, host=s.me===s.host, guessing=s.me===s.guesser;
  $('welcome').classList.add('hidden'); $('table').classList.remove('hidden');
  $('room-title').textContent=s.code; $('mode-label').textContent=labels[s.mode];
  $('round-label').textContent='Раунд '+s.number+' / '+s.total; $('score-label').textContent='Команда: '+s.score;
  $('players').replaceChildren(...s.players.map(p=>{
    const item=node('div',undefined,'player'+(!p.online?' offline':'')+(p.id===s.guesser?' guesser':''));
    const name=node('span',p.name+(p.id===s.me?' (вы)':''),'player-name'); item.append(name);
    item.append(node('span',p.id===s.guesser?'Угадывает':p.id===s.host?'Ведущий':p.online?'●':'○','badge'));
    if(host && s.stage==='lobby' && p.id!==s.me) {const b=button('×','remove','kick',{id:p.id}); b.setAttribute('aria-label','Удалить '+p.name); item.append(b);} return item;
  }));
  // Do not rebuild entry forms on each poll: preserve typing, focus and mobile keyboard.
  const mine=s.hints.find(h=>h.id===s.me), screen=[s.code,s.number,s.stage,s.guesser,Boolean(mine?.submitted),s.host].join(':');
  const content=$('round-content');
  if(screen!==lastScreen) {
    lastScreen=screen; content.replaceChildren();
    const stage=node('div',undefined,'word-stage');
    if(s.stage==='lobby') {
      stage.append(node('div','✎','lobby-symbol'),node('h2','Все за одним столом'),node('p','Пригласите друзей по ссылке. Ведущий начнёт, когда будут хотя бы трое онлайн.'));
    } else if(s.stage==='finished') {
      stage.append(node('div','✦','result-mark'),node('h2','Партия завершена!'),node('p','Ваш командный результат: '+s.score+' из '+s.total));
    } else {
      stage.append(node('span','','timer'));
      const headline=s.stage==='clues' ? (guessing?'Ваша очередь угадывать':'Напишите ассоциацию'):s.stage==='guess' ? (guessing?'Какое слово загадано?':'Теперь слово за угадывающим'):s.result.correct?'Верно! +1 команде':'В этот раз не получилось';
      stage.append(node('h2',headline),node('div',s.word || '•••••','word-card'+(!s.word?' secret':'')));
      if(s.stage==='clues') stage.append(node('p',guessing?'Слово скрыто. Друзья готовят подсказки.':'Не сговаривайтесь. Одинаковые подсказки исчезнут.'));
      if(s.stage==='reveal') stage.append(node('p',s.guess?'Ответ: '+s.guess:(s.result.reason || 'Нет ответа')));
    }
    content.append(stage);
    if(['clues','guess','reveal'].includes(s.stage)) content.append(node('div',undefined,'hints'));
    if(s.stage==='clues' && !guessing && !mine?.submitted) content.append(entry('hint','Ваша ассоциация','Одна подсказка',40));
    if(s.stage==='guess' && guessing) content.append(entry('guess','Ваш ответ','Одна попытка',60));
  }
  const timer=content.querySelector('.timer'); if(timer) {timer.textContent=s.seconds===null?'Раунд завершён':Math.floor(s.seconds/60)+':'+String(s.seconds%60).padStart(2,'0');}
  const hints=content.querySelector('.hints'); if(hints) hints.replaceChildren(...s.hints.map(h=>{
    const paper=node('div',undefined,'paper'+(h.duplicate?' duplicate':'')+(!h.text?' waiting':''));
    paper.append(node('span',h.text || (h.duplicate?'Совпадение':h.submitted?'✓ Принято':'…'),'hint-text'),node('small',s.players.find(p=>p.id===h.id)?.name || 'Игрок')); return paper;
  }));
  $('host-controls').replaceChildren();
  if(host) {
    if(s.stage==='lobby') {const b=button('Начать партию','start','primary'); b.disabled=s.players.filter(p=>p.online).length<3; $('host-controls').append(b);}
    if(s.stage==='reveal') $('host-controls').append(button(s.number===s.total?'Итоги партии':'Следующий раунд','next','primary'));
    if(s.stage!=='lobby') {const b=button('Вернуться в лобби','reset'); b.onclick=()=>{if(confirm('Завершить партию и обнулить счёт?')) act({action:'reset'});}; $('host-controls').append(b);}
  }
  $('history-list').replaceChildren(...s.history.map(h=>node('li',h.word+' — '+(h.correct?'✓':'×'))));
}
async function poll() {
  if(!session || polling===session) return;
  const current=session; polling=current;
  try {
    const next=await api('/api/rooms/'+current.code, undefined, current);
    if(session!==current) return;
    state=next; render(); $('connection').textContent='Связь установлена · обновление каждые 2 секунды';
  } catch(e) {
    if(session!==current) return;
    if(e.status===400) {
      // Expired room/removed seat is terminal; network errors and 429 are not.
      forget(current); session=null; showWelcome(current.code);
      notice(e.message); return;
    }
    $('connection').textContent='Нет связи: '+e.message+'. Пробуем снова…';
    if(!state) notice('Не удалось восстановить сеанс: '+e.message);
  } finally { if(polling===current) polling=null; }
}
function restore() {
  const path=location.pathname.match(/^\/room\/([A-Fa-f0-9]{8})\/?$/);
  const invite=roomCode(path ? path[1] : location.hash.slice(1));
  // Migrate old same-tab credentials without overriding an explicit invitation.
  const legacy=identity(storage.get('sessionStorage', 'jone-session'));
  if(legacy) remember(legacy);
  storage.remove('sessionStorage', 'jone-session');
  const code=invite || roomCode(storage.get('sessionStorage','jone-active')) || roomCode(storage.get('localStorage','jone-active'));
  session=code ? remembered(code) : null;
  showWelcome(code);
  if(code) history.replaceState(null,'',roomURL(code));
  poll();
}
restore();
window.addEventListener('popstate', restore);
window.addEventListener('hashchange', restore);
setInterval(poll,2000); document.addEventListener('visibilitychange',()=>{if(!document.hidden) poll();});
