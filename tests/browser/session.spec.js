const { test, expect } = require('@playwright/test');
async function create(page, name='Host') {
  await page.goto('/');
  await page.locator('#create-form input[name=name]').fill(name);
  await page.locator('#create-form button[type=submit]').click();
  await expect(page.locator('#table')).toBeVisible();
  return page.locator('#room-title').textContent();
}
async function me(page) { return page.evaluate(async()=> (await (await fetch('/api/rooms/'+session.code, {headers:{Authorization:'Bearer '+session.token}})).json()).me); }
async function players(page) { return page.locator('#players .player').count(); }

test('create, refresh, new tab and webview-like tab restart keep one identity; URL has code only', async ({page, context})=>{
  const code=await create(page), id=await me(page);
  await expect(page).toHaveURL(new RegExp('/room/'+code+'$'));
  await page.reload(); await expect(page.locator('#table')).toBeVisible();
  expect(await me(page)).toBe(id);
  const next=await context.newPage(); await next.goto('/room/'+code);
  await expect(next.locator('#table')).toBeVisible(); expect(await me(next)).toBe(id);
  expect(await players(next)).toBe(1);
  await page.close(); await next.close();
  const reopened=await context.newPage(); await reopened.goto('/');
  await expect(reopened.locator('#table')).toBeVisible(); expect(await me(reopened)).toBe(id);
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await reopened.locator('#copy-link').click();
  expect(await reopened.evaluate(()=>navigator.clipboard.readText())).toBe('http://127.0.0.1:18089/room/'+code);
});

test('explicit different-room invitation beats saved identity, switching back resumes each seat', async ({page, browser})=>{
  const codeA=await create(page), idA=await me(page);
  const other=await browser.newContext(); const host=await other.newPage();
  const codeB=await create(host,'Other');
  await page.goto('/room/'+codeB); await expect(page.locator('#welcome')).toBeVisible();
  await expect(page.locator('#join-form input[name=code]')).toHaveValue(codeB);
  await page.locator('#join-form input[name=name]').fill('Guest');
  await page.locator('#join-form button[type=submit]').click();
  await expect(page.locator('#table')).toBeVisible(); const idB=await me(page);
  await page.goto('/room/'+codeA); await expect(page.locator('#table')).toBeVisible(); expect(await me(page)).toBe(idA);
  await page.goto('/room/'+codeB); await expect(page.locator('#table')).toBeVisible(); expect(await me(page)).toBe(idB);
  expect(await players(page)).toBe(2); await other.close();
});

test('legacy hash and sessionStorage migration resume original player', async ({page})=>{
  const code=await create(page), id=await me(page);
  await page.evaluate(()=>{const raw=localStorage.getItem('jone-room-'+session.code); localStorage.clear(); sessionStorage.clear(); sessionStorage.setItem('jone-session',raw);});
  await page.goto('/#'+code); await expect(page.locator('#table')).toBeVisible();
  expect(await me(page)).toBe(id); await expect(page).toHaveURL(new RegExp('/room/'+code+'$'));
  expect(await page.evaluate(()=>sessionStorage.getItem('jone-session'))).toBeNull();
});

test('corrupt and malformed browser data do not crash or override invites', async ({page})=>{
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{ localStorage.setItem('jone-room-AABBCCDD','{bad'); localStorage.setItem('jone-active','AABBCCDD'); sessionStorage.setItem('jone-session','{"code":{},"token":4}'); });
  await page.goto('/room/AABBCCDD'); await expect(page.locator('#welcome')).toBeVisible();
  await expect(page.locator('#join-form input[name=code]')).toHaveValue('AABBCCDD'); expect(errors).toEqual([]);
});

test('blocked persistent storage falls back to tab storage and warns; fully blocked storage still enters', async ({browser})=>{
  for(const both of [false,true]) {
    const context=await browser.newContext();
    await context.addInitScript(both=>{Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked')}}); if(both) Object.defineProperty(window,'sessionStorage',{get(){throw new Error('blocked')}});},both);
    const page=await context.newPage(), errors=[]; page.on('pageerror',e=>errors.push(e.message));
    const code=await create(page), id=await me(page);
    await expect(page.locator('#notice')).toContainText('не сохраняет');
    if(!both) {await page.reload(); await expect(page.locator('#table')).toBeVisible(); expect(await me(page)).toBe(id);}
    await page.waitForTimeout(2200); expect(await players(page)).toBe(1); expect(errors).toEqual([]); await context.close();
  }
});

test('poll connection failures and 429 retain identity and recover without join POSTs', async ({page})=>{
  const code=await create(page), id=await me(page); let joins=0;
  page.on('request',req=>{if(req.method()==='POST' && req.url().endsWith('/join')) joins++;});
  await page.route('**/api/rooms/'+code, route=>route.abort('failed'));
  await expect(page.locator('#connection')).toContainText('Нет связи');
  await page.route('**/api/rooms/'+code, route=>route.fulfill({status:429,contentType:'application/json',body:'{"error":"rate limit"}'}));
  await page.waitForTimeout(2300); await expect(page.locator('#table')).toBeVisible();
  await page.unroute('**/api/rooms/'+code);
  await expect(page.locator('#connection')).toContainText('Связь установлена');
  expect(await me(page)).toBe(id); expect(await players(page)).toBe(1); expect(joins).toBe(0);
});

test('expired/removed identity offers explicit join once, not endless polling or autojoin', async ({page})=>{
  const code=await create(page); let calls=0;
  await page.route('**/api/rooms/'+code, route=>{calls++; return route.fulfill({status:400,contentType:'application/json',body:'{"error":"Сеанс не найден"}'});});
  await expect(page.locator('#welcome')).toBeVisible();
  await expect(page.locator('#join-form input[name=code]')).toHaveValue(code);
  await page.waitForTimeout(2300); expect(calls).toBe(1);
  expect(await page.evaluate(code=>localStorage.getItem('jone-room-'+code),code)).toBeNull();
});

test('late poll/action after leaving cannot put an old room back on screen', async ({page})=>{
  const code=await create(page); let release;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/api/rooms/'+code,async route=>{await held; await route.continue();});
  await page.waitForTimeout(2200);
  page.once('dialog',dialog=>dialog.accept()); await page.locator('#leave').click();
  release(); await page.waitForTimeout(500); await expect(page.locator('#welcome')).toBeVisible();
  await expect(page).toHaveURL('http://127.0.0.1:18089/');
  await page.reload(); await expect(page.locator('#welcome')).toBeVisible();
});

test('real browser game reconnect keeps guesser secret and helpers authenticated', async ({browser})=>{
  const contexts=await Promise.all([1,2,3].map(()=>browser.newContext()));
  const pages=await Promise.all(contexts.map(c=>c.newPage())); const code=await create(pages[0]);
  for(let i=1;i<3;i++) {await pages[i].goto('/room/'+code); await pages[i].locator('#join-form input[name=name]').fill('Guest'+i); await pages[i].locator('#join-form button[type=submit]').click(); await expect(pages[i].locator('#table')).toBeVisible();}
  const start=pages[0].getByRole('button',{name:'Начать партию'}); await expect(start).toBeEnabled(); await start.click();
  await expect(pages[0].locator('.word-card')).toHaveText('•••••');
  await pages[0].reload(); await expect(pages[0].locator('.word-card')).toHaveText('•••••');
  for(let i=1;i<3;i++) {await expect(pages[i].locator('form.entry')).toBeVisible(); await pages[i].locator('form.entry input').fill(i===1?'лес':'море'); await pages[i].locator('form.entry button[type=submit]').click();}
  await expect(pages[0].getByRole('button',{name:'Пас — не знаю'})).toBeVisible();
  await pages[0].getByRole('button',{name:'Пас — не знаю'}).click();
  await expect(pages[0].locator('.word-card')).not.toHaveText('•••••');
  for(const context of contexts) await context.close();
});
