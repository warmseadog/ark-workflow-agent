const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu']});
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'mira-cases-'));
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const width of [1440,1024,768,390,320]){
   await page.setViewportSize({width,height:900});await page.goto('http://127.0.0.1:4173/mira-cover.html');
   await page.locator('#menu-trigger').click();
   assert.equal(await page.locator('[data-i18n="menuCases"]').textContent(),'案例');
   assert.equal(await page.locator('#site-menu [data-case]').count(),5);
   if(width===1440)await page.screenshot({path:path.join(dir,'menu.png')});
   await page.locator('#site-menu [data-case="yoyo"]').click();
   assert.equal(await page.locator('#case-name').textContent(),'Yoyo');
   assert.equal(await page.locator('#film').evaluate(v=>v.paused),true);
   for(const id of ['zheng-yunshu','yu-jia','shi-yun','pei-zhiyou','yoyo']){
    await page.locator('.case-back').click();
    const entry=page.locator(`#site-menu [data-case="${id}"]`),name=await entry.textContent();
    await entry.click();
    assert.equal(await page.locator('#case-name').textContent(),name);
    assert.equal(await page.locator('#case-dialog [data-case],#case-dialog aside').count(),0);
    for(const kind of ['images','links','videos']){await page.locator(`[data-case-filter="${kind}"]`).click();assert.equal(await page.locator(`[data-case-filter="${kind}"]`).getAttribute('aria-pressed'),'true');assert.equal(await page.locator('.case-empty').count(),1)}
   }
   assert.ok(await page.locator('#case-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth));
   if(width===1440||width===390)await page.screenshot({path:path.join(dir,`case-${width}.png`)});
   await page.keyboard.press('Escape');assert.equal(await page.locator('#case-dialog').isVisible(),false);
   assert.equal(await page.locator('#menu-trigger').evaluate(e=>e===document.activeElement),true);
   await page.waitForFunction(()=>!document.querySelector('#film').paused);
   console.log(JSON.stringify({width,passed:true}));
  }
  await page.locator('#menu-trigger').click();await page.locator('#site-menu [data-language="en"]').click();await page.locator('#site-menu [data-case="yoyo"]').click();
  assert.equal(await page.locator('[data-case-filter="videos"]').textContent(),'Films');
  await page.locator('.case-back').click();assert.equal(await page.locator('#site-menu').isVisible(),true);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,dir}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
