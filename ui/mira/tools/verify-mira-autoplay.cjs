// Run against the local preview. No user gestures before the first complete cycle.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu']});
 const output=fs.mkdtempSync(path.join(os.tmpdir(),'mira-autoplay-'));
 const url='http://127.0.0.1:4173/mira-cover.html';
 const errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);
  for(const expected of ['01','02','03','04','05','01']){
   await page.waitForFunction(n=>document.querySelector('#current').textContent===n&&document.querySelector('#film').currentTime>.2&&!document.querySelector('#film').paused,expected,{timeout:30000});
   const state=await page.locator('#film').evaluate(v=>({src:v.currentSrc,time:v.currentTime,muted:v.muted,error:v.error?.code,loop:v.loop}));
   assert.equal(state.muted,true);assert.equal(state.loop,false);assert.equal(state.error,undefined);
   console.log(JSON.stringify({auto:expected,...state}));
  }
  await page.screenshot({path:path.join(output,'desktop.png')});
  await page.locator('#play').click();
  const paused=await page.locator('#film').evaluate(v=>v.currentTime);
  await page.waitForTimeout(1000);
  assert.equal(await page.locator('#film').evaluate(v=>v.paused),true);
  assert.ok(Math.abs(await page.locator('#film').evaluate(v=>v.currentTime)-paused)<.1);
  await page.locator('#next').click();
  await page.waitForFunction(()=>document.querySelector('#current').textContent==='02'&&document.querySelector('#film').currentTime>.2&&!document.querySelector('#film').paused);
  for(let i=0;i<8;i++)await page.locator('#next').click();
  await page.waitForFunction(()=>document.querySelector('#film').currentTime>.2&&!document.querySelector('#film').paused);
  assert.equal(await page.locator('#preview-note').isVisible(),false);
  const mobile=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await mobile.goto(url);
  await mobile.waitForFunction(()=>document.querySelector('#film').currentTime>.5&&!document.querySelector('#film').paused);
  assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await mobile.screenshot({path:path.join(output,'mobile.png')});
  const reduced=await browser.newPage({reducedMotion:'reduce'});
  await reduced.goto(url);
  await reduced.waitForTimeout(500);
  assert.equal(await reduced.locator('#film').evaluate(v=>v.paused),true);
  await reduced.locator('#play').click();
  await reduced.waitForFunction(()=>document.querySelector('#film').currentTime>.2);
  const broken=await browser.newPage();
  await broken.route('**/film-03-original.mp4',route=>route.fulfill({status:404,body:'Unavailable'}));
  await broken.goto(url);
  await broken.waitForFunction(()=>document.querySelector('#current').textContent==='02'&&document.querySelector('#film').currentTime>.2,{},{timeout:15000});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,fullCycle:true,pause:true,manualNext:true,rapidNext:true,mobile:true,reducedMotion:true,failedSourceSkipped:true,output}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
